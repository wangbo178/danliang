"""
配置路由：平台/渠道/店铺/仓库/日历 CRUD
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from api.database import get_db
from api import models, schemas
from api.services.integration_settings import (
    public_feishu_schedule_config,
    save_feishu_schedule_config,
)

router = APIRouter()

CHANNEL_TYPES = {"stable", "event", "affiliate"}
MAINTENANCE_MODES = {"manual", "auto"}


@router.get("/feishu-schedule", response_model=dict)
def get_feishu_schedule_config(db: Session = Depends(get_db)):
    """返回可公开的配置；App Secret 明文永不下发。"""
    return public_feishu_schedule_config(db)


@router.put("/feishu-schedule", response_model=dict)
def update_feishu_schedule_config(
    payload: schemas.FeishuScheduleConfigUpdate,
    db: Session = Depends(get_db),
):
    values = payload.model_dump()
    for field, label in (("app_id", "App ID"),):
        if not str(values[field] or "").strip():
            raise HTTPException(422, f"{label} 不能为空")
    if not 60 <= payload.interval_seconds <= 86400:
        raise HTTPException(422, "同步间隔必须在 60 秒到 24 小时之间")
    result = save_feishu_schedule_config(values, db)
    from api.services.feishu_schedule_sync import reload_schedule_sync
    reload_schedule_sync()
    return {**result, "message": "飞书同步配置已加密保存到数据库并重新加载"}


@router.post("/feishu-schedule/test", response_model=dict)
def test_saved_feishu_schedule_config():
    """验证当前已保存配置，只读取飞书，不修改数据库排期。"""
    from api.services.feishu_schedule_sync import test_feishu_connection
    try:
        return test_feishu_connection()
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


def _binding_response(binding, shop_name: str | None = None) -> dict:
    return {
        "id": binding.id,
        "name": binding.name,
        "shop_id": binding.shop_id,
        "shop_name": shop_name,
        "base_token": binding.base_token,
        "table_id": binding.table_id,
        "enabled": bool(binding.enabled),
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
    }


def _reload_feishu_schedule_sync() -> None:
    from api.services.feishu_schedule_sync import reload_schedule_sync
    reload_schedule_sync()


@router.get("/feishu-schedule/bindings", response_model=list[dict])
def list_feishu_schedule_bindings(db: Session = Depends(get_db)):
    shops = {row.shop_id: row.name for row in db.query(models.Shop).all()}
    rows = db.query(models.FeishuScheduleBinding).order_by(
        models.FeishuScheduleBinding.id
    ).all()
    return [_binding_response(row, shops.get(row.shop_id)) for row in rows]


def _validate_binding(payload: schemas.FeishuScheduleBindingCreate, db: Session) -> dict:
    values = payload.model_dump()
    for field, label in (("shop_id", "店铺"), ("base_token", "Base Token"), ("table_id", "Table ID")):
        values[field] = str(values[field] or "").strip()
        if not values[field]:
            raise HTTPException(422, f"{label} 不能为空")
    if not db.query(models.Shop).filter(models.Shop.shop_id == values["shop_id"]).first():
        raise HTTPException(422, "选择的店铺不存在")
    values["name"] = str(values.get("name") or "").strip() or None
    return values


@router.post("/feishu-schedule/bindings", response_model=dict, status_code=201)
def create_feishu_schedule_binding(
    payload: schemas.FeishuScheduleBindingCreate,
    db: Session = Depends(get_db),
):
    values = _validate_binding(payload, db)
    row = models.FeishuScheduleBinding(**values)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该 Table ID 已绑定，请直接编辑原绑定") from exc
    db.refresh(row)
    result = _binding_response(row, db.query(models.Shop).filter(models.Shop.shop_id == row.shop_id).first().name)
    _reload_feishu_schedule_sync()
    return result


@router.put("/feishu-schedule/bindings/{binding_id}", response_model=dict)
def update_feishu_schedule_binding(
    binding_id: int,
    payload: schemas.FeishuScheduleBindingCreate,
    db: Session = Depends(get_db),
):
    row = db.query(models.FeishuScheduleBinding).filter(
        models.FeishuScheduleBinding.id == binding_id
    ).first()
    if not row:
        raise HTTPException(404, "表格绑定不存在")
    values = _validate_binding(payload, db)
    for key, value in values.items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该 Table ID 已绑定，请检查后重试") from exc
    db.refresh(row)
    result = _binding_response(row, db.query(models.Shop).filter(models.Shop.shop_id == row.shop_id).first().name)
    _reload_feishu_schedule_sync()
    return result


@router.delete("/feishu-schedule/bindings/{binding_id}", response_model=schemas.Msg)
def delete_feishu_schedule_binding(binding_id: int, db: Session = Depends(get_db)):
    row = db.query(models.FeishuScheduleBinding).filter(
        models.FeishuScheduleBinding.id == binding_id
    ).first()
    if not row:
        raise HTTPException(404, "表格绑定不存在")
    db.delete(row)
    db.commit()
    _reload_feishu_schedule_sync()
    return {"message": "表格绑定已删除；已同步的历史与排期数据均保留"}


def _validate_maintenance_mode(value: str | None) -> str:
    mode = value or "manual"
    if mode not in MAINTENANCE_MODES:
        raise HTTPException(422, f"维护方式 '{mode}' 不受支持")
    return mode


def _validate_channels(channels):
    """规范渠道标识，并在写库前阻止请求体内部的重复项。"""
    result = []
    seen = set()
    for ch in channels or []:
        channel_id = (ch.channel_id or "").strip()
        if not channel_id:
            raise HTTPException(422, "渠道 ID 不能为空")
        channel_key = channel_id.casefold()
        if channel_key in seen:
            raise HTTPException(422, f"渠道 ID '{channel_id}' 在当前平台中重复")
        channel_type = ch.channel_type or "stable"
        if channel_type not in CHANNEL_TYPES:
            raise HTTPException(422, f"渠道类型 '{channel_type}' 不受支持")
        maintenance_mode = _validate_maintenance_mode(ch.maintenance_mode)
        seen.add(channel_key)
        result.append((
            channel_id, ch.name, channel_type,
            bool(ch.include_in_forecast), maintenance_mode,
        ))
    return result


def _platform_response(platform, channels):
    return {
        "id": platform.id,
        "platform_id": platform.platform_id,
        "name": platform.name,
        "maintenance_mode": platform.maintenance_mode or "manual",
        "created_at": platform.created_at,
        "channels": [
            {
                "id": channel.id,
                "platform_id": channel.platform_id,
                "channel_id": channel.channel_id,
                "name": channel.name,
                "channel_type": channel.channel_type,
                "include_in_forecast": bool(channel.include_in_forecast),
                "maintenance_mode": channel.maintenance_mode or "manual",
            }
            for channel in channels
        ],
    }


# ──────────────────────────────────────────────
# 平台 & 渠道
# ──────────────────────────────────────────────

@router.get("/platforms", response_model=List[schemas.PlatformOut])
def list_platforms(db: Session = Depends(get_db)):
    # 一次加载平台和渠道，避免原来的 1 + N 查询（17 个平台会产生 18 次 SQL）。
    platforms = db.query(models.Platform).all()
    channels = db.query(models.Channel).order_by(
        models.Channel.platform_id, models.Channel.channel_id
    ).all()
    channels_by_platform = {}
    for channel in channels:
        channels_by_platform.setdefault(channel.platform_id, []).append(channel)

    return [
        {
            "id": platform.id,
            "platform_id": platform.platform_id,
            "name": platform.name,
            "maintenance_mode": platform.maintenance_mode or "manual",
            "created_at": platform.created_at,
            "channels": [
                {
                    "id": channel.id,
                    "platform_id": channel.platform_id,
                    "channel_id": channel.channel_id,
                    "name": channel.name,
                    "channel_type": channel.channel_type,
                    "include_in_forecast": bool(channel.include_in_forecast),
                    "maintenance_mode": channel.maintenance_mode or "manual",
                }
                for channel in channels_by_platform.get(platform.platform_id, [])
            ],
        }
        for platform in platforms
    ]


@router.post("/platforms", response_model=schemas.PlatformOut, status_code=201)
def create_platform(payload: schemas.PlatformCreate, db: Session = Depends(get_db)):
    normalized_platform_id = payload.platform_id.strip()
    existing = db.query(models.Platform).filter(models.Platform.platform_id == normalized_platform_id).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"platform_id '{payload.platform_id}' 已存在")
    channel_payloads = _validate_channels(payload.channels)
    p = models.Platform(
        platform_id=normalized_platform_id,
        name=payload.name,
        maintenance_mode=_validate_maintenance_mode(payload.maintenance_mode),
    )
    db.add(p)
    db.flush()
    channels = []
    for channel_id, name, channel_type, include_in_forecast, maintenance_mode in channel_payloads:
        c = models.Channel(platform_id=p.platform_id, channel_id=channel_id,
                           name=name, channel_type=channel_type,
                           include_in_forecast=include_in_forecast,
                           maintenance_mode=maintenance_mode)
        db.add(c)
        channels.append(c)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "平台或渠道标识已存在，请检查后重试") from exc
    db.refresh(p)
    return _platform_response(p, channels)


@router.put("/platforms/{platform_id}", response_model=schemas.PlatformOut)
def update_platform(platform_id: str, payload: schemas.PlatformCreate, db: Session = Depends(get_db)):
    p = db.query(models.Platform).filter(models.Platform.platform_id == platform_id).first()
    if not p:
        raise HTTPException(404, "平台不存在")
    if payload.platform_id.strip() != platform_id:
        raise HTTPException(422, "平台 ID 创建后不可修改")

    channel_payloads = _validate_channels(payload.channels)
    p.name = payload.name
    p.maintenance_mode = _validate_maintenance_mode(payload.maintenance_mode)

    # 差量同步：保留已有记录并更新属性，只新增真正的新渠道。
    # 原来的“删旧建新”在同一 flush 中可能先 INSERT 后 DELETE，从而撞上唯一约束。
    existing_channels = {
        channel.channel_id.casefold(): channel
        for channel in db.query(models.Channel).filter(
            models.Channel.platform_id == platform_id
        ).all()
    }
    incoming_ids = set()
    for channel_id, name, channel_type, include_in_forecast, maintenance_mode in channel_payloads:
        channel_key = channel_id.casefold()
        incoming_ids.add(channel_key)
        channel = existing_channels.get(channel_key)
        if channel is None:
            channel = models.Channel(platform_id=platform_id, channel_id=channel_id)
            db.add(channel)
            existing_channels[channel_key] = channel
        channel.name = name
        channel.channel_type = channel_type
        channel.include_in_forecast = include_in_forecast
        channel.maintenance_mode = maintenance_mode

    for channel_key, channel in existing_channels.items():
        if channel_key not in incoming_ids:
            db.delete(channel)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "渠道标识发生冲突，请刷新配置后重试") from exc

    db.refresh(p)
    channels = db.query(models.Channel).filter(
        models.Channel.platform_id == platform_id
    ).order_by(models.Channel.channel_id).all()
    return _platform_response(p, channels)


@router.delete("/platforms/{platform_id}", response_model=schemas.Msg)
def delete_platform(platform_id: str, db: Session = Depends(get_db)):
    p = db.query(models.Platform).filter(models.Platform.platform_id == platform_id).first()
    if not p:
        raise HTTPException(404, "平台不存在")
    db.delete(p)
    db.commit()
    return {"message": f"平台 {platform_id} 已删除"}


# ──────────────────────────────────────────────
# 店铺
# ──────────────────────────────────────────────

@router.get("/shops", response_model=List[schemas.ShopOut])
def list_shops(db: Session = Depends(get_db)):
    return db.query(models.Shop).all()


@router.post("/shops", response_model=schemas.ShopOut, status_code=201)
def create_shop(payload: schemas.ShopCreate, db: Session = Depends(get_db)):
    if db.query(models.Shop).filter(models.Shop.shop_id == payload.shop_id).first():
        raise HTTPException(400, f"shop_id '{payload.shop_id}' 已存在")
    values = payload.model_dump()
    values["maintenance_mode"] = _validate_maintenance_mode(payload.maintenance_mode)
    s = models.Shop(**values)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.put("/shops/{shop_id}", response_model=schemas.ShopOut)
def update_shop(shop_id: str, payload: schemas.ShopCreate, db: Session = Depends(get_db)):
    s = db.query(models.Shop).filter(models.Shop.shop_id == shop_id).first()
    if not s:
        raise HTTPException(404, "店铺不存在")
    values = payload.model_dump()
    values["maintenance_mode"] = _validate_maintenance_mode(payload.maintenance_mode)
    for k, v in values.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/shops/{shop_id}", response_model=schemas.Msg)
def delete_shop(shop_id: str, db: Session = Depends(get_db)):
    s = db.query(models.Shop).filter(models.Shop.shop_id == shop_id).first()
    if not s:
        raise HTTPException(404, "店铺不存在")
    db.delete(s)
    db.commit()
    return {"message": f"店铺 {shop_id} 已删除"}


# ──────────────────────────────────────────────
# 仓库
# ──────────────────────────────────────────────

@router.get("/warehouses", response_model=List[schemas.WarehouseOut])
def list_warehouses(db: Session = Depends(get_db)):
    from api.services.query_source import logical_warehouses
    names = logical_warehouses()
    existing = {row.warehouse_id for row in db.query(models.Warehouse).all()}
    for name in names:
        if name not in existing:
            db.add(models.Warehouse(warehouse_id=name, name=name, region=name.removesuffix("仓"), is_active=True))
    db.commit()
    return db.query(models.Warehouse).filter(models.Warehouse.warehouse_id.in_(names)).all()


@router.post("/warehouses", response_model=schemas.WarehouseOut, status_code=201)
def create_warehouse(payload: schemas.WarehouseCreate, db: Session = Depends(get_db)):
    if db.query(models.Warehouse).filter(models.Warehouse.warehouse_id == payload.warehouse_id).first():
        raise HTTPException(400, f"warehouse_id '{payload.warehouse_id}' 已存在")
    w = models.Warehouse(**payload.model_dump())
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


@router.put("/warehouses/{warehouse_id}", response_model=schemas.WarehouseOut)
def update_warehouse(warehouse_id: str, payload: schemas.WarehouseCreate, db: Session = Depends(get_db)):
    w = db.query(models.Warehouse).filter(models.Warehouse.warehouse_id == warehouse_id).first()
    if not w:
        raise HTTPException(404, "仓库不存在")
    for k, v in payload.model_dump().items():
        setattr(w, k, v)
    db.commit()
    db.refresh(w)
    return w


@router.delete("/warehouses/{warehouse_id}", response_model=schemas.Msg)
def delete_warehouse(warehouse_id: str, db: Session = Depends(get_db)):
    w = db.query(models.Warehouse).filter(models.Warehouse.warehouse_id == warehouse_id).first()
    if not w:
        raise HTTPException(404, "仓库不存在")
    db.delete(w)
    db.commit()
    return {"message": f"仓库 {warehouse_id} 已删除"}


# ──────────────────────────────────────────────
# 大促日历
# ──────────────────────────────────────────────

@router.get("/calendar", response_model=List[schemas.CampaignCalendarOut])
def list_calendar(db: Session = Depends(get_db)):
    return db.query(models.CampaignCalendar).order_by(models.CampaignCalendar.start_date).all()


@router.get("/bootstrap", response_model=dict)
def bootstrap_config(db: Session = Depends(get_db)):
    """基础配置首屏快照：一次 HTTP 返回四类配置，避免前端并发调度四个接口。"""
    from api.services.query_source import ensure_dimensions
    ensure_dimensions(db)
    routing = export_config(db)
    effective_bindings = {binding["shop_id"]: binding for binding in routing["shop_warehouse_binding"]}

    def shop_snapshot(row):
        result = schemas.ShopOut.model_validate(row).model_dump(mode="json")
        binding = effective_bindings.get(row.shop_id, {})
        for key in ("binding_type", "warehouse_id"):
            if key in binding:
                result[key] = binding[key]
        return result
    return {
        "platforms": [
            schemas.PlatformOut.model_validate(row).model_dump(mode="json")
            for row in list_platforms(db)
        ],
        "shops": [
            shop_snapshot(row)
            for row in db.query(models.Shop).all()
        ],
        "warehouses": [
            schemas.WarehouseOut.model_validate(row).model_dump(mode="json")
            for row in list_warehouses(db)
        ],
        "calendar": [
            schemas.CampaignCalendarOut.model_validate(row).model_dump(mode="json")
            for row in db.query(models.CampaignCalendar).order_by(
                models.CampaignCalendar.start_date
            ).all()
        ],
    }


@router.post("/calendar", response_model=schemas.CampaignCalendarOut, status_code=201)
def create_calendar(payload: schemas.CampaignCalendarCreate, db: Session = Depends(get_db)):
    c = models.CampaignCalendar(**payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.put("/calendar/{cal_id}", response_model=schemas.CampaignCalendarOut)
def update_calendar(cal_id: int, payload: schemas.CampaignCalendarCreate, db: Session = Depends(get_db)):
    c = db.query(models.CampaignCalendar).filter(models.CampaignCalendar.id == cal_id).first()
    if not c:
        raise HTTPException(404, "日历记录不存在")
    for k, v in payload.model_dump().items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/calendar/{cal_id}", response_model=schemas.Msg)
def delete_calendar(cal_id: int, db: Session = Depends(get_db)):
    c = db.query(models.CampaignCalendar).filter(models.CampaignCalendar.id == cal_id).first()
    if not c:
        raise HTTPException(404, "日历记录不存在")
    db.delete(c)
    db.commit()
    return {"message": f"日历 {cal_id} 已删除"}


# ──────────────────────────────────────────────
# 配置整体导出（供 ML 核心使用）
# ──────────────────────────────────────────────

@router.get("/export", response_model=schemas.ConfigExport)
def export_config(db: Session = Depends(get_db)):
    """将数据库配置转换为 ML 核心需要的 config_dict 格式"""
    platforms = db.query(models.Platform).all()
    plat_list = []
    for p in platforms:
        channels = db.query(models.Channel).filter(models.Channel.platform_id == p.platform_id).all()
        plat_list.append({
            "platform_id": p.platform_id,
            "name": p.name,
            "maintenance_mode": p.maintenance_mode or "manual",
            "channels": [{
                "channel_id": c.channel_id,
                "name": c.name,
                "include_in_forecast": bool(c.include_in_forecast),
                "maintenance_mode": c.maintenance_mode or "manual",
            } for c in channels],
        })

    shops = db.query(models.Shop).all()
    shop_list = [{"shop_id": s.shop_id, "name": s.name,
                  "platform_id": s.platform_id, "campaign_mode": s.campaign_mode}
                 for s in shops]

    warehouses = db.query(models.Warehouse).all()
    wh_list = [{"warehouse_id": w.warehouse_id, "name": w.name, "region": w.region,
                "is_active": bool(w.is_active)}
               for w in warehouses]

    calendar = db.query(models.CampaignCalendar).all()
    cal_list = [{"name": c.name, "start_date": str(c.start_date),
                 "end_date": str(c.end_date), "campaign_type": c.campaign_type}
                for c in calendar]

    # 从所有渠道的 channel_type 字段组装 channel_type_map
    # 同时用 channel_id（英文代码，新批次格式）和 name（中文名，旧批次格式）作为 key，
    # 确保两种上传格式的渠道都能正确分类到 stable/event/affiliate。
    all_channels = db.query(models.Channel).all()
    channel_type_map: dict = {}

    def _set_ch_type(key: str, ctype: str) -> None:
        """非 stable 优先写入；避免被 stable 渠道覆盖 event/affiliate"""
        if not key:
            return
        if ctype != "stable" or key not in channel_type_map:
            channel_type_map[key] = ctype

    for c in all_channels:
        ctype = c.channel_type or "stable"
        _set_ch_type(c.channel_id, ctype)  # 英文 ID：tm_zb, dy_hj, tm_cps ...（新批次）
        _set_ch_type(c.name, ctype)        # 中文名：直播, 货架, CPS ...（旧批次）

    excluded_channels = [
        {
            "platform_id": c.platform_id,
            "channel_id": c.channel_id,
            "channel_name": c.name or "",
        }
        for c in all_channels
        if c.include_in_forecast is False
    ]

    # 补充上传时经别名映射后存入 channel_orders 的标准名（如 douyin_union），
    # 这些名称在 channels 表中没有直接对应行，需要在此硬编码兜底。
    _ALIAS_TYPE_FALLBACK = {
        "douyin_union": "affiliate",  # 精选联盟上传时映射为 douyin_union
        "达播":          "event",      # 达人带货直播
        "其他":          "stable",
    }
    for alias, ctype in _ALIAS_TYPE_FALLBACK.items():
        channel_type_map.setdefault(alias, ctype)

    # shop_warehouse_binding：供 ML 仓库路由引擎使用，替代硬编码的 routing_config
    shop_bindings = [
        {
            "shop_id": s.shop_id,
            "binding_type": s.binding_type or "multi",
            "warehouse_id": s.warehouse_id,
            "effective_date": str(s.effective_date) if s.effective_date else None,
        }
        for s in shops
    ]

    from api.services.query_source import effective_routing_config
    return effective_routing_config({
        "platforms": plat_list,
        "shops": shop_list,
        "warehouses": wh_list,
        "campaign_calendar": cal_list,
        "channel_type_map": channel_type_map,
        "excluded_channels": excluded_channels,
        "shop_warehouse_binding": shop_bindings,
    })
