"""
数据路由：文件上传、预览、渠道映射
"""
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Form, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database import get_db
from api import models
from api.services import query_source
from api.channel_mapper import auto_map_columns, resolve_shop_id, auto_map_channel, get_channel_source

router = APIRouter()

_CHANNEL_UPLOAD_CACHE: dict[str, dict] = {}
_CHANNEL_UPLOAD_CACHE_LOCK = threading.Lock()
_CHANNEL_UPLOAD_CACHE_TTL_SECONDS = 60 * 60
_CHANNEL_UPLOAD_THRESHOLD = 5_000
_CHANNEL_SAVE_CHUNK_SIZE = 10_000
_WAREHOUSE_UPLOAD_CACHE: dict[str, dict] = {}
_WAREHOUSE_UPLOAD_CACHE_LOCK = threading.Lock()
_WAREHOUSE_UPLOAD_CACHE_TTL_SECONDS = 60 * 60
_WAREHOUSE_UPLOAD_THRESHOLD = 5_000
_WAREHOUSE_SAVE_CHUNK_SIZE = 10_000
_DATA_SUMMARY_CACHE_TTL_SECONDS = 30
_DATA_SUMMARY_CACHE_LOCK = threading.Lock()
_DATA_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}

_BATCH_TYPES = {
    "channel": (models.ChannelOrder, "渠道订单", models.ChannelOrder.date),
    "warehouse": (models.WarehouseOrder, "仓库订单", models.WarehouseOrder.date),
    "schedule": (models.LiveSchedule, "直播排期", models.LiveSchedule.date),
    "commission": (models.Commission, "联盟佣金", None),
    "anchor": (models.AnchorLiveOrder, "达人直播", models.AnchorLiveOrder.date),
    "presale": (models.PresaleHistory, "预售历史", models.PresaleHistory.payment_date),
}


def _get_data_summary_cache(key: str):
    now = time.monotonic()
    with _DATA_SUMMARY_CACHE_LOCK:
        entry = _DATA_SUMMARY_CACHE.get(key)
        if entry is not None and now - entry[0] <= _DATA_SUMMARY_CACHE_TTL_SECONDS:
            return entry[1]
        if entry is not None:
            _DATA_SUMMARY_CACHE.pop(key, None)
    return None


def _set_data_summary_cache(key: str, value: dict) -> None:
    with _DATA_SUMMARY_CACHE_LOCK:
        _DATA_SUMMARY_CACHE[key] = (time.monotonic(), value)


def _invalidate_data_summary_cache() -> None:
    with _DATA_SUMMARY_CACHE_LOCK:
        _DATA_SUMMARY_CACHE.clear()


def _latest_batch_id(db: Session, data_type: str, model) -> Optional[str]:
    """返回最新有效上传批次；旧库没有批次日志时回退到最新明细。"""
    latest_log = db.query(models.UploadBatchLog.batch_id).filter(
        models.UploadBatchLog.data_type == data_type,
        models.UploadBatchLog.row_count > 0,
    ).order_by(
        models.UploadBatchLog.uploaded_at.desc(),
        models.UploadBatchLog.id.desc(),
    ).first()
    if latest_log:
        return latest_log[0]
    latest_row = db.query(model.upload_batch).filter(
        model.upload_batch.isnot(None)
    ).order_by(model.created_at.desc(), model.id.desc()).first()
    return latest_row[0] if latest_row else None


def _as_date(value):
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _record_upload_batch(
    db: Session,
    *,
    data_type: str,
    batch_id: str,
    row_count: int,
    date_values=None,
    source_filename: str | None = None,
    increment: bool = False,
) -> None:
    """记录真实保存事件；业务明细 upsert 不再影响批次上传时间。"""
    dates = [item for item in (_as_date(value) for value in (date_values or [])) if item]
    data_start = min(dates) if dates else None
    data_end = max(dates) if dates else None
    row = db.query(models.UploadBatchLog).filter(
        models.UploadBatchLog.data_type == data_type,
        models.UploadBatchLog.batch_id == batch_id,
    ).first()
    if row is None:
        row = models.UploadBatchLog(
            data_type=data_type,
            batch_id=batch_id,
            row_count=max(0, int(row_count)),
            data_start=data_start,
            data_end=data_end,
            source_filename=(source_filename or "").strip() or None,
            uploaded_at=datetime.now(),
            time_source="exact",
        )
        db.add(row)
    else:
        row.row_count = (row.row_count + int(row_count)) if increment else int(row_count)
        if data_start and (row.data_start is None or data_start < row.data_start):
            row.data_start = data_start
        if data_end and (row.data_end is None or data_end > row.data_end):
            row.data_end = data_end
        if source_filename:
            row.source_filename = source_filename.strip()
        # 分块上传以最后一块提交完成的时间作为批次上传完成时间。
        row.uploaded_at = datetime.now()
        row.time_source = "exact"
    db.commit()


def _reconcile_batch_logs(db: Session, *, data_type: str, model, date_column=None) -> None:
    """让会移动批次归属的 upsert 表保持当前记录数准确。"""
    from sqlalchemy import func

    selected = [model.upload_batch, func.count(model.id)]
    if date_column is not None:
        selected += [func.min(date_column), func.max(date_column)]
    rows = db.query(*selected).filter(
        model.upload_batch.isnot(None)
    ).group_by(model.upload_batch).all()
    current = {row[0]: row for row in rows}
    logs = db.query(models.UploadBatchLog).filter(
        models.UploadBatchLog.data_type == data_type
    ).all()
    for log in logs:
        aggregate = current.get(log.batch_id)
        if aggregate is None:
            db.delete(log)
            continue
        log.row_count = int(aggregate[1])
        if date_column is not None:
            log.data_start, log.data_end = aggregate[2], aggregate[3]
    db.commit()


# 明细查询只允许访问这些模型/字段。显式白名单既避免动态 SQL 注入，也让前端列顺序稳定。
_DETAIL_CONFIG = {
    "channel": (models.ChannelOrder, [
        "id", "date", "shop_id", "platform", "channel", "order_count", "payment_amount",
        "is_sample", "upload_batch", "created_at",
    ]),
    "warehouse": (models.WarehouseOrder, [
        "id", "date", "shop_id", "platform", "channel", "warehouse_id",
        "order_count", "is_sample", "upload_batch", "created_at",
    ]),
    "schedule": (models.LiveSchedule, [
        "id", "shop_id", "date", "event_type", "anchor_tier", "anchor_name",
        "broadcast_date", "expected_viewers", "expected_orders", "expected_revenue",
        "session_type", "platform", "channel", "mcn", "scheduled_at", "source_type",
        "source_record_id", "products", "sales_amount", "sales_basis", "upload_batch", "created_at",
    ]),
    "commission": (models.Commission, [
        "id", "shop_id", "month", "commission_rate", "active_affiliates",
        "upload_batch", "created_at",
    ]),
    "anchor": (models.AnchorLiveOrder, [
        "id", "date", "anchor_name", "shop_id", "order_count",
        "upload_batch", "created_at",
    ]),
    "presale": (models.PresaleHistory, [
        "id", "shop_id", "channel", "broadcast_date", "payment_date",
        "order_count", "campaign_type", "upload_batch", "created_at",
    ]),
}


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def _read_upload(file: UploadFile) -> pd.DataFrame:
    # UploadFile 使用可落盘的 SpooledTemporaryFile，直接交给 Pandas，避免大文件
    # 先 read() 成 bytes 后又复制一份到 BytesIO 造成双倍内存占用。
    file.file.seek(0)
    name = (file.filename or "").lower()
    if name.endswith(".parquet"):
        return pd.read_parquet(file.file)
    elif name.endswith(".csv"):
        return pd.read_csv(file.file, encoding="utf-8-sig")
    else:
        # Calamine 直接使用 Rust 解析 xlsx/xls，十万行文件通常比 openpyxl 快一个数量级。
        # 给 Rust 引擎传路径可避免它通过 Python 文件对象反复跨语言读取。
        # 保留 Pandas 默认引擎回退，兼容加密或少见格式的历史文件。
        suffix = os.path.splitext(name)[1] or ".xlsx"
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix="ecom-upload-", suffix=suffix)
            with os.fdopen(fd, "wb") as temp_file:
                shutil.copyfileobj(file.file, temp_file, length=1024 * 1024)
            return pd.read_excel(temp_path, engine="calamine")
        except (ImportError, ValueError, TypeError):
            file.file.seek(0)
            return pd.read_excel(file.file)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass


def _get_cfg(db: Session) -> dict:
    """从数据库读取完整配置 dict（供 channel_mapper 使用）"""
    platforms = db.query(models.Platform).all()
    plat_list = []
    for p in platforms:
        channels = db.query(models.Channel).filter(models.Channel.platform_id == p.platform_id).all()
        plat_list.append({
            "platform_id": p.platform_id,
            "name": p.name,
            "channels": [{"channel_id": c.channel_id, "name": c.name} for c in channels],
        })
    shops = db.query(models.Shop).all()
    warehouses = db.query(models.Warehouse).all()
    return {
        "platforms": plat_list,
        "shops": [{"shop_id": s.shop_id, "name": s.name} for s in shops],
        "warehouses": [{"warehouse_id": w.warehouse_id} for w in warehouses],
    }


# ──────────────────────────────────────────────
# 各类型上传的必填列（中文列名 → 英文标准列名）
# ──────────────────────────────────────────────
_REQUIRED_COLS = {
    "channel":   {"日期": "date", "店铺": "shop_id", "订单量": "order_count"},
    "warehouse": {"日期": "date", "店铺": "shop_id", "仓库ID": "warehouse_id", "订单量": "order_count"},
    "presale":   {"直播时间": "broadcast_date", "尾款时间": "payment_date", "单量": "order_count"},
    "anchor":    {"达人名称": "anchor_name", "日期": "date", "订单量": "order_count"},
}


def _check_required(df: pd.DataFrame, kind: str) -> None:
    """校验必填列，缺失时以中文列名抛出异常"""
    required = _REQUIRED_COLS[kind]
    missing_cn = [cn for cn, std in required.items() if std not in df.columns]
    if missing_cn:
        raise HTTPException(400, f"缺少必填列：{'、'.join(missing_cn)}")


def _clean_text(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


_PLATFORM_IMPORT_ALIASES = {
    # 同一电商平台的常见代码/中文名在导入阶段统一。后续仍保留
    # platform 字段参与渠道识别、训练特征、排除配置和结果展示。
    "tb": "TM",
    "tm": "TM",
    "taobao": "TM",
    "tmall": "TM",
    "tmfx": "TM",
    "淘宝": "TM",
    "天猫": "TM",
    "淘宝天猫": "TM",
    "dy": "DOUYIN",
    "douyin": "DOUYIN",
    "抖音": "DOUYIN",
    "jd": "JD",
    "jingdong": "JD",
    "jdcs": "JD",
    "jdcz": "JD",
    "京东": "JD",
    "pdd": "PINDUODUO",
    "pinduoduo": "PINDUODUO",
    "拼多多": "PINDUODUO",
    "ks": "KUAISHOU",
    "kuaishou": "KUAISHOU",
    "快手": "KUAISHOU",
    "xhs": "XIAOHONGSHU",
    "xiaohongshu": "XIAOHONGSHU",
    "小红书": "XIAOHONGSHU",
    "vph": "VIP",
    "vip": "VIP",
    "唯品会": "VIP",
    "dewu": "POIZON",
    "poizon": "POIZON",
    "得物": "POIZON",
    "yz": "YOUZAN",
    "youzan": "YOUZAN",
    "有赞": "YOUZAN",
}


def _normalize_import_platform(value) -> str:
    """将上传文件中的平台别名转换为系统标准平台标识。"""
    cleaned = _clean_text(value)
    return _PLATFORM_IMPORT_ALIASES.get(cleaned.casefold(), cleaned)


def _normalize_import_platforms(frame: pd.DataFrame) -> pd.DataFrame:
    """批量规范上传数据的平台列；没有平台列时保持原样。"""
    if "platform" not in frame.columns:
        return frame
    normalized = frame.copy()
    normalized["platform"] = normalized["platform"].map(_normalize_import_platform)
    return normalized


def _clean_multiple_platforms_per_shop(data) -> tuple[pd.DataFrame, dict]:
    """同一店铺出现多平台时，保留记录行数最多的平台。

    行数相同时，先比较订单量合计，仍相同则保留文件中先出现的平台。
    店铺和平台标识均去除首尾空格并忽略大小写。
    """
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    # 清洗冲突前先合并业务同义平台，避免把同一平台的不同代码当成冲突
    # 并按记录数误删其中一边。
    frame = _normalize_import_platforms(frame)
    summary = {
        "original_rows": len(frame),
        "kept_rows": len(frame),
        "removed_rows": 0,
        "affected_shops": 0,
        "conflicts": [],
    }
    if frame.empty or not {"shop_id", "platform"}.issubset(frame.columns):
        return frame, summary

    work = pd.DataFrame(index=frame.index)
    work["shop_id"] = frame["shop_id"].map(_clean_text)
    work["platform"] = frame["platform"].map(_clean_text)
    work["shop_key"] = work["shop_id"].str.casefold()
    work["platform_key"] = work["platform"].str.casefold()
    if "order_count" in frame.columns:
        work["order_count"] = pd.to_numeric(
            frame["order_count"], errors="coerce"
        ).fillna(0)
    else:
        work["order_count"] = 0
    work["source_order"] = range(len(work))
    valid = work[(work["shop_key"] != "") & (work["platform_key"] != "")]
    if valid.empty:
        return frame, summary

    stats = valid.groupby(
        ["shop_key", "platform_key"], sort=False, as_index=False
    ).agg(
        rows=("platform_key", "size"),
        total_orders=("order_count", "sum"),
        first_order=("source_order", "min"),
        shop_id=("shop_id", "first"),
        platform=("platform", "first"),
    )
    conflict_shops = stats.groupby("shop_key")["platform_key"].nunique()
    conflict_keys = set(conflict_shops[conflict_shops > 1].index)
    if not conflict_keys:
        return frame, summary

    conflict_stats = stats[stats["shop_key"].isin(conflict_keys)].copy()
    winners = (
        conflict_stats.sort_values(
            ["shop_key", "rows", "total_orders", "first_order"],
            ascending=[True, False, False, True],
        )
        .drop_duplicates("shop_key")
        .set_index("shop_key")
    )
    winner_map = winners["platform_key"].to_dict()
    remove_mask = work["shop_key"].isin(conflict_keys) & (
        work["platform_key"] != work["shop_key"].map(winner_map)
    )
    cleaned = frame.loc[~remove_mask].copy().reset_index(drop=True)

    shop_names = {}
    if "shop_name" in frame.columns:
        for index, value in frame["shop_name"].items():
            shop_key = work.at[index, "shop_key"]
            name = _clean_text(value)
            if shop_key and name and shop_key not in shop_names:
                shop_names[shop_key] = name

    conflicts = []
    for shop_key in sorted(conflict_keys):
        shop_stats = conflict_stats[conflict_stats["shop_key"] == shop_key]
        winner = winners.loc[shop_key]
        removed = shop_stats[shop_stats["platform_key"] != winner["platform_key"]]
        conflicts.append({
            "shop_id": winner["shop_id"],
            "shop_name": shop_names.get(shop_key, ""),
            "kept_platform": winner["platform"],
            "kept_rows": int(winner["rows"]),
            "removed_rows": int(removed["rows"].sum()),
            "removed_platforms": [
                {"platform": row.platform, "rows": int(row.rows)}
                for row in removed.sort_values("rows", ascending=False).itertuples()
            ],
        })

    summary.update({
        "kept_rows": len(cleaned),
        "removed_rows": int(remove_mask.sum()),
        "affected_shops": len(conflicts),
        "conflicts": conflicts,
    })
    return cleaned, summary


def _number_or_none(value):
    text = _clean_text(value).replace(",", "").replace("¥", "").replace("￥", "")
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _build_aov_history(df: pd.DataFrame) -> list[dict]:
    """按日汇总付款金额/订单量；不能对各行客单价做简单平均。"""
    if "payment_amount" not in df.columns or "date" not in df.columns:
        return []
    frame = df[["date", "order_count", "payment_amount"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["order_count"] = pd.to_numeric(frame["order_count"], errors="coerce").fillna(0)
    frame["payment_amount"] = pd.to_numeric(frame["payment_amount"], errors="coerce")
    frame = frame.dropna(subset=["date", "payment_amount"])
    if frame.empty:
        return []
    # 必须显式创建分组列。部分 Pandas 版本对 ``groupby(Series,
    # as_index=False)`` 不会把外部 Series 写回结果，随后 sort_values("date")
    # 会触发 KeyError。
    frame["_aov_date"] = frame["date"].dt.date
    daily = frame.groupby("_aov_date", as_index=False).agg(
        order_count=("order_count", "sum"),
        payment_amount=("payment_amount", "sum"),
    ).rename(columns={"_aov_date": "date"})
    daily["avg_order_value"] = daily["payment_amount"] / daily["order_count"].replace(0, pd.NA)
    result = []
    for _, row in daily.sort_values("date").iterrows():
        if pd.isna(row["avg_order_value"]):
            continue
        result.append({
            "date": str(row["date"]),
            "order_count": int(round(row["order_count"])),
            "payment_amount": round(float(row["payment_amount"]), 2),
            "avg_order_value": round(float(row["avg_order_value"]), 2),
        })
    return result


def _cleanup_channel_upload_cache() -> None:
    cutoff = time.time() - _CHANNEL_UPLOAD_CACHE_TTL_SECONDS
    with _CHANNEL_UPLOAD_CACHE_LOCK:
        expired = [
            token for token, entry in _CHANNEL_UPLOAD_CACHE.items()
            if entry["created_at"] < cutoff
        ]
        for token in expired:
            _CHANNEL_UPLOAD_CACHE.pop(token, None)


def _cache_large_channel_upload(df: pd.DataFrame) -> tuple[str, str]:
    _cleanup_channel_upload_cache()
    token = uuid.uuid4().hex
    batch_id = uuid.uuid4().hex[:12]
    with _CHANNEL_UPLOAD_CACHE_LOCK:
        _CHANNEL_UPLOAD_CACHE[token] = {
            "df": df.copy(),
            "created_at": time.time(),
            "batch_id": batch_id,
            "saved_chunks": {},
        }
    return token, batch_id


def _cleanup_warehouse_upload_cache() -> None:
    cutoff = time.time() - _WAREHOUSE_UPLOAD_CACHE_TTL_SECONDS
    with _WAREHOUSE_UPLOAD_CACHE_LOCK:
        expired = [
            token for token, entry in _WAREHOUSE_UPLOAD_CACHE.items()
            if entry["created_at"] < cutoff
        ]
        for token in expired:
            _WAREHOUSE_UPLOAD_CACHE.pop(token, None)


def _cache_large_warehouse_upload(df: pd.DataFrame) -> tuple[str, str]:
    """大仓库文件留在服务端，避免数万行 JSON 在浏览器和接口间往返。"""
    _cleanup_warehouse_upload_cache()
    token = uuid.uuid4().hex
    batch_id = uuid.uuid4().hex[:12]
    with _WAREHOUSE_UPLOAD_CACHE_LOCK:
        _WAREHOUSE_UPLOAD_CACHE[token] = {
            "df": df.copy(),
            "created_at": time.time(),
            "batch_id": batch_id,
            "saved_chunks": {},
        }
    return token, batch_id


def _apply_manual_channel_mapping(records: list[dict], mapping: dict) -> list[dict]:
    if not mapping:
        return records
    result = []
    for record in records:
        row = dict(record)
        channel = _clean_text(row.get("channel"))
        platform = _clean_text(row.get("platform"))
        if channel in mapping:
            row["channel"] = mapping[channel]
        elif platform in mapping and not channel:
            row["channel"] = mapping[platform]
        result.append(row)
    return result


def _ensure_channel_config(db: Session, records: list[dict]) -> dict:
    """根据有效渠道订单增量建档，不覆盖已有人工作出的配置。"""
    platforms = {p.platform_id.casefold(): p for p in db.query(models.Platform).all()}
    channels = {
        (c.platform_id.casefold(), c.channel_id.casefold()): c
        for c in db.query(models.Channel).all()
    }
    channel_names = {
        (c.platform_id.casefold(), (c.name or "").casefold()): c
        for c in channels.values() if c.name
    }
    shops = {s.shop_id.casefold(): s for s in db.query(models.Shop).all()}
    created = {"platforms": [], "channels": [], "shops": []}

    for record in records:
        platform_value = _clean_text(record.get("platform"))
        channel_value = _clean_text(record.get("channel"))
        shop_id = _clean_text(record.get("shop_id"))
        shop_name = _clean_text(record.get("shop_name")) or shop_id

        platform = platforms.get(platform_value.casefold()) if platform_value else None
        if platform is None and platform_value:
            platform = models.Platform(
                platform_id=platform_value,
                name=platform_value,
                maintenance_mode="auto",
            )
            db.add(platform)
            db.flush()
            platforms[platform_value.casefold()] = platform
            created["platforms"].append(platform_value)
        if platform is not None:
            record["platform"] = platform.platform_id

        channel = None
        if platform is not None and channel_value:
            key = (platform.platform_id.casefold(), channel_value.casefold())
            channel = channels.get(key) or channel_names.get(key)
            if channel is None:
                channel = models.Channel(
                    platform_id=platform.platform_id,
                    channel_id=channel_value,
                    name=channel_value,
                    channel_type="stable",
                    include_in_forecast=True,
                    maintenance_mode="auto",
                )
                db.add(channel)
                db.flush()
                channels[(platform.platform_id.casefold(), channel_value.casefold())] = channel
                channel_names[(platform.platform_id.casefold(), channel_value.casefold())] = channel
                created["channels"].append(f"{platform.platform_id}/{channel_value}")
            record["channel"] = channel.channel_id

        shop = shops.get(shop_id.casefold()) if shop_id else None
        if shop is None and shop_id:
            shop = models.Shop(
                shop_id=shop_id,
                name=shop_name,
                platform_id=platform.platform_id if platform is not None else None,
                campaign_mode="auto",
                binding_type="multi",
                warehouse_id=None,
                maintenance_mode="auto",
            )
            db.add(shop)
            db.flush()
            shops[shop_id.casefold()] = shop
            created["shops"].append(shop_id)
        elif shop is not None:
            if not shop.name and shop_name:
                shop.name = shop_name
            if not shop.platform_id and platform is not None:
                shop.platform_id = platform.platform_id
        if shop is not None:
            record["shop_id"] = shop.shop_id

    return created


# ──────────────────────────────────────────────
# 渠道订单上传
# ──────────────────────────────────────────────

@router.post("/upload/channel")
def upload_channel_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.post("/save/channel")
async def save_channel_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


# ──────────────────────────────────────────────
# 仓库订单上传
# ──────────────────────────────────────────────

@router.post("/upload/warehouse")
async def upload_warehouse_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.post("/save/warehouse")
async def save_warehouse_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


# ──────────────────────────────────────────────
# 直播排期上传
# ──────────────────────────────────────────────

@router.post("/upload/schedule")
async def upload_live_schedules(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        df = _read_upload(file)
    except Exception as e:
        raise HTTPException(400, f"文件读取失败：{e}")
    df = auto_map_columns(df)
    df = _normalize_import_platforms(df)
    if "date" not in df.columns and "scheduled_at" in df.columns:
        df["date"] = pd.to_datetime(df["scheduled_at"], errors="coerce").dt.date
    missing = [col for col in ("shop_id", "date") if col not in df.columns]
    if missing:
        raise HTTPException(400, f"直播排期缺少必填列：{', '.join(missing)}")
    if "event_type" not in df.columns:
        df["event_type"] = "live"
    cfg = _get_cfg(db)
    df, shop_converted = resolve_shop_id(df, cfg)
    preview = df.head(20).fillna("").to_dict(orient="records")
    return {
        "rows": len(df), "shop_converted": shop_converted,
        "preview": preview, "_df_json": df.to_json(orient="records", date_format="iso"),
    }


@router.post("/save/schedule")
async def save_live_schedules(payload: dict, db: Session = Depends(get_db)):
    records = payload.get("records", [])
    if not records:
        raise HTTPException(400, "没有记录")
    batch_id = str(uuid.uuid4())[:8]
    objs = []

    def optional_text(value):
        if value is None or pd.isna(value):
            return None
        cleaned = str(value).strip()
        return cleaned or None

    def optional_number(value, cast=float):
        if value is None or (isinstance(value, str) and not value.strip()) or pd.isna(value):
            return None
        return cast(float(str(value).replace(",", "")))

    def optional_datetime(value):
        if value is None or (isinstance(value, str) and not value.strip()) or pd.isna(value):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.to_pydatetime()

    def optional_date(value):
        parsed = optional_datetime(value)
        return parsed.date() if parsed else None

    for r in records:
        try:
            scheduled_at = optional_datetime(r.get("scheduled_at"))
            schedule_date = optional_date(r.get("date"))
            if not schedule_date and scheduled_at:
                schedule_date = scheduled_at.date()
            shop_id = optional_text(r.get("shop_id"))
            if not shop_id or not schedule_date:
                continue
            objs.append(models.LiveSchedule(
                shop_id=shop_id,
                date=schedule_date,
                event_type=str(r.get("event_type", "live") or "live"),
                has_live=bool(r.get("has_live", 1)),
                anchor_tier=optional_text(r.get("anchor_tier")),
                anchor_name=optional_text(r.get("anchor_name")),
                expected_viewers=optional_number(r.get("expected_viewers"), int),
                expected_orders=optional_number(r.get("expected_orders")),
                expected_revenue=optional_number(r.get("expected_revenue")),
                session_type=optional_text(r.get("session_type")),
                platform=_normalize_import_platform(r.get("platform")) or None,
                channel=optional_text(r.get("channel")),
                mcn=optional_text(r.get("mcn")),
                scheduled_at=scheduled_at,
                source_type="manual",
                broadcast_date=optional_date(r.get("broadcast_date")),
                upload_batch=batch_id,
            ))
        except Exception:
            continue
    if not objs:
        raise HTTPException(400, "没有可保存的有效直播排期")

    # 新导入的完整排期覆盖同一逻辑场次的旧记录，尤其是早期只包含
    # 店铺/日期/达人、没有渠道和目标单量的空占位，避免跨批次重复展示。
    affected_batches = set()
    for new_row in objs:
        candidates = db.query(models.LiveSchedule).filter(
            models.LiveSchedule.shop_id == new_row.shop_id,
            models.LiveSchedule.date == new_row.date,
            models.LiveSchedule.event_type == new_row.event_type,
            models.LiveSchedule.anchor_name == new_row.anchor_name,
        ).all()
        for existing in candidates:
            distinct_source_records = (
                existing.source_record_id
                and new_row.source_record_id
                and existing.source_record_id != new_row.source_record_id
            )
            if distinct_source_records:
                continue
            existing_is_shadow = (
                existing.scheduled_at is None
                and not existing.channel
                and not existing.expected_orders
                and not existing.expected_revenue
            )
            same_event_slot = existing.scheduled_at == new_row.scheduled_at
            if not (existing_is_shadow or same_event_slot):
                continue
            if existing.upload_batch:
                affected_batches.add(existing.upload_batch)
            db.delete(existing)

    db.bulk_save_objects(objs)
    db.flush()
    if affected_batches:
        from sqlalchemy import func
        for affected_batch in affected_batches:
            count, data_start, data_end = db.query(
                func.count(models.LiveSchedule.id),
                func.min(models.LiveSchedule.date),
                func.max(models.LiveSchedule.date),
            ).filter(models.LiveSchedule.upload_batch == affected_batch).first()
            log = db.query(models.UploadBatchLog).filter(
                models.UploadBatchLog.data_type == "schedule",
                models.UploadBatchLog.batch_id == affected_batch,
            ).first()
            if log is None:
                continue
            if not count:
                db.delete(log)
            else:
                log.row_count = int(count)
                log.data_start, log.data_end = data_start, data_end
    db.commit()
    _record_upload_batch(
        db, data_type="schedule", batch_id=batch_id, row_count=len(objs),
        date_values=[item.date for item in objs], source_filename=payload.get("source_filename"),
    )
    _invalidate_data_summary_cache()
    try:
        from api.services.live_prediction_overlay import refresh_latest_live_overlay
        overlay_refresh = refresh_latest_live_overlay()
    except Exception as exc:
        # 排期已经可靠落库；增量刷新失败不回滚用户数据，返回持久化提示。
        overlay_refresh = {"skipped": True, "reason": f"直播增量刷新失败：{exc}"}
    return {
        "message": f"已保存 {len(objs)} 条直播排期",
        "saved": len(objs), "skipped": len(records) - len(objs), "batch_id": batch_id,
        "overlay_refresh": overlay_refresh,
    }


@router.get("/feishu-schedule/status")
def get_feishu_schedule_status():
    from api.services.feishu_schedule_sync import get_sync_status
    return get_sync_status()


@router.post("/feishu-schedule/sync")
async def sync_feishu_schedule_now():
    import asyncio
    from api.services.feishu_schedule_sync import sync_feishu_schedule

    try:
        result = await asyncio.to_thread(sync_feishu_schedule, "manual")
    except Exception as exc:
        raise HTTPException(502, f"飞书排期同步失败：{exc}") from exc
    _invalidate_data_summary_cache()
    return result


# ──────────────────────────────────────────────
# 联盟佣金上传
# ──────────────────────────────────────────────

@router.post("/upload/commission")
async def upload_commissions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        df = _read_upload(file)
    except Exception as e:
        raise HTTPException(400, f"文件读取失败：{e}")
    df = auto_map_columns(df)
    cfg = _get_cfg(db)
    df, shop_converted = resolve_shop_id(df, cfg)
    preview = df.head(20).fillna("").to_dict(orient="records")
    return {
        "rows": len(df), "shop_converted": shop_converted,
        "preview": preview, "_df_json": df.to_json(orient="records", date_format="iso"),
    }


@router.post("/save/commission")
async def save_commissions(payload: dict, db: Session = Depends(get_db)):
    records = payload.get("records", [])
    if not records:
        raise HTTPException(400, "没有记录")
    batch_id = str(uuid.uuid4())[:8]
    objs = [models.Commission(
        shop_id=str(r.get("shop_id", "")),
        month=str(r.get("month", ""))[:7],
        commission_rate=float(r.get("commission_rate", 0)) if r.get("commission_rate") else None,
        active_affiliates=int(r.get("active_affiliates", 0)) if r.get("active_affiliates") else None,
        upload_batch=batch_id,
    ) for r in records]
    db.bulk_save_objects(objs)
    db.commit()
    _record_upload_batch(
        db, data_type="commission", batch_id=batch_id, row_count=len(objs),
        date_values=[item.month for item in objs], source_filename=payload.get("source_filename"),
    )
    _invalidate_data_summary_cache()
    return {
        "message": f"已保存 {len(objs)} 条佣金记录",
        "saved": len(objs), "skipped": 0, "batch_id": batch_id,
    }


# ──────────────────────────────────────────────
# 预售历史上传
# ──────────────────────────────────────────────

def _infer_campaign_type_from_date(payment_date) -> str:
    """根据尾款日推断大促类型（1111/618/38/default）"""
    try:
        from datetime import date as date_type
        import datetime as dt_mod
        if isinstance(payment_date, str):
            d = dt_mod.date.fromisoformat(payment_date[:10])
        elif hasattr(payment_date, "date"):
            d = payment_date.date()
        else:
            d = payment_date
        m, day = d.month, d.day
        # 双11：10月下旬~11月中旬
        if (m == 10 and day >= 20) or (m == 11 and day <= 15):
            return "1111"
        # 618：5月下旬~6月下旬
        if (m == 5 and day >= 20) or (m == 6 and day <= 25):
            return "618"
        # 38大促：2月下旬~3月上旬
        if (m == 2 and day >= 20) or (m == 3 and day <= 15):
            return "38"
        # 双12：12月上中旬
        if m == 12 and day <= 15:
            return "1212"
        return "default"
    except Exception:
        return "default"


@router.post("/upload/presale")
async def upload_presale_history(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    解析预售历史数据 Excel，返回预览（不保存）。
    列名：直播时间 / 尾款时间 / 店铺 / 是否预售单 / 单量
    """
    try:
        df = _read_upload(file)
    except Exception as e:
        raise HTTPException(400, f"文件读取失败：{e}")

    df = auto_map_columns(df)

    # 必填列检查
    _check_required(df, "presale")

    # 解析日期
    df["broadcast_date"] = pd.to_datetime(df["broadcast_date"], errors="coerce")
    df["payment_date"] = pd.to_datetime(df["payment_date"], errors="coerce")
    df["order_count"] = pd.to_numeric(df["order_count"], errors="coerce").fillna(0).astype(int)

    # 店铺映射
    cfg = _get_cfg(db)
    shop_converted = 0
    if "shop_id" in df.columns:
        df, shop_converted = resolve_shop_id(df, cfg)

    # NaN 店铺标记
    if "shop_id" not in df.columns:
        df["shop_id"] = None
    df["_shop_missing"] = df["shop_id"].isna() | (df["shop_id"].astype(str).str.strip() == "") | \
                           (df["shop_id"].astype(str).str.lower() == "nan")

    # 自动推断活动类型
    df["campaign_type"] = df["payment_date"].apply(_infer_campaign_type_from_date)

    # 自动推断渠道（默认直播，从DB查询该店铺的event渠道）
    # 查询各店铺的 event 渠道
    from api import models as _models
    event_channels = db.query(_models.Channel).filter(_models.Channel.channel_type == "event").all()
    shop_event_ch: dict = {}  # {shop_id: [channel_name]}
    for ec in event_channels:
        # channel_orders 中用的是 channel.name
        for s in cfg.get("shops", []):
            # 所有店铺都可能用此渠道（按平台匹配更精确，但先用通用默认）
            sid = s["shop_id"]
            shop_event_ch.setdefault(sid, [])
            if ec.name and ec.name not in shop_event_ch[sid]:
                shop_event_ch[sid].append(ec.name)

    def _resolve_channel(shop_id):
        chs = shop_event_ch.get(str(shop_id), [])
        if len(chs) == 1:
            return chs[0]
        return "直播"  # 多个或无→默认"直播"，前端可手动修正

    df["channel"] = df["shop_id"].apply(
        lambda sid: _resolve_channel(sid) if pd.notna(sid) and str(sid).strip() not in ("", "nan") else "直播"
    )

    # 统计摘要
    campaign_dist = df.groupby("campaign_type")["order_count"].agg(["count", "sum"]).reset_index()
    campaign_summary = campaign_dist.to_dict(orient="records")
    nan_shop_count = int(df["_shop_missing"].sum())

    # 构建预览 records（前20行）
    preview_df = df.head(20).copy()
    for col in ["broadcast_date", "payment_date"]:
        preview_df[col] = preview_df[col].astype(str)
    preview_df["shop_id"] = preview_df["shop_id"].astype(str)
    preview = preview_df[[
        "broadcast_date", "payment_date", "shop_id", "channel",
        "campaign_type", "order_count", "_shop_missing"
    ]].fillna("").to_dict(orient="records")

    # 完整 records 供前端确认后 POST /save/presale
    full_df = df.copy()
    for col in ["broadcast_date", "payment_date"]:
        full_df[col] = full_df[col].astype(str)
    full_df["shop_id"] = full_df["shop_id"].astype(str)
    full_df["_shop_missing"] = full_df["_shop_missing"].astype(bool)
    records_json = full_df[[
        "broadcast_date", "payment_date", "shop_id", "channel",
        "campaign_type", "order_count", "_shop_missing"
    ]].to_dict(orient="records")

    # 获取可用店铺列表（供前端下拉）
    shops_list = [{"shop_id": s["shop_id"], "name": s.get("name", "")} for s in cfg.get("shops", [])]

    return {
        "rows": len(df),
        "shop_converted": shop_converted,
        "nan_shop_count": nan_shop_count,
        "campaign_summary": campaign_summary,
        "preview": preview,
        "shops": shops_list,
        "_records_json": records_json,
    }


@router.post("/save/presale")
async def save_presale_history(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    将前端确认后的预售历史记录持久化到 presale_history 表。
    使用 INSERT … ON DUPLICATE KEY UPDATE 去重。
    """
    records = payload.get("records", [])
    if not records:
        raise HTTPException(400, "没有记录")

    # 校验：不允许保存 shop_id 为空的记录
    bad = [r for r in records if not r.get("shop_id") or str(r.get("shop_id", "")).strip() in ("", "nan", "None")]
    if bad:
        raise HTTPException(400, f"有 {len(bad)} 条记录缺少店铺信息，请补全后再保存")

    batch_id = str(uuid.uuid4())[:8]

    from sqlalchemy.dialects.mysql import insert as mysql_insert

    objs_data = []
    for r in records:
        try:
            bd = r.get("broadcast_date")
            pd_date = r.get("payment_date")
            if not bd or not pd_date:
                continue
            objs_data.append({
                "shop_id": str(r["shop_id"]).strip(),
                "channel": str(r.get("channel") or "直播").strip(),
                "broadcast_date": str(bd)[:10],
                "payment_date": str(pd_date)[:10],
                "order_count": int(r.get("order_count", 0) or 0),
                "campaign_type": str(r.get("campaign_type") or "default"),
                "upload_batch": batch_id,
            })
        except Exception:
            continue

    if not objs_data:
        raise HTTPException(400, "没有有效记录")

    # INSERT … ON DUPLICATE KEY UPDATE（按 UniqueConstraint 去重）
    stmt = mysql_insert(models.PresaleHistory).values(objs_data)
    stmt = stmt.on_duplicate_key_update(
        order_count=stmt.inserted.order_count,
        campaign_type=stmt.inserted.campaign_type,
        upload_batch=stmt.inserted.upload_batch,
    )
    db.execute(stmt)
    db.commit()
    _record_upload_batch(
        db, data_type="presale", batch_id=batch_id, row_count=len(objs_data),
        date_values=[item.get("payment_date") for item in objs_data],
        source_filename=payload.get("source_filename"),
    )
    _reconcile_batch_logs(
        db, data_type="presale", model=models.PresaleHistory,
        date_column=models.PresaleHistory.payment_date,
    )
    _invalidate_data_summary_cache()

    return {"saved": len(objs_data), "batch_id": batch_id}


# ──────────────────────────────────────────────
# 达人直播单量上传
# ──────────────────────────────────────────────

@router.post("/upload/anchor")
async def upload_anchor_live_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.post("/save/anchor")
async def save_anchor_live_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.delete("/anchor")
def delete_anchor_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


# ──────────────────────────────────────────────
# 数据概览
# ──────────────────────────────────────────────

@router.get("/overview")
def data_overview(db: Session = Depends(get_db)):
    result = query_source.source_overview()
    result["live_schedules"] = db.query(models.LiveSchedule).count()
    result["commissions"] = db.query(models.Commission).count()
    return result


# ──────────────────────────────────────────────
# 历史看板：分仓每日单量
# ──────────────────────────────────────────────

@router.get("/warehouse-daily")
def warehouse_daily(
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    recent_days: Optional[int] = Query(None, ge=1, le=366),
    db: Session = Depends(get_db),
):
    return query_source.warehouse_daily(date_start, date_end, recent_days)


# ──────────────────────────────────────────────
# 首页看板：每日客单价趋势
# ──────────────────────────────────────────────

@router.get("/aov-daily")
def aov_daily(
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    recent_days: int = Query(7, ge=1, le=366),
    shop_id: Optional[str] = None,
    channel: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return query_source.aov_daily(date_start, date_end, recent_days, shop_id, channel)


# ──────────────────────────────────────────────
# 批次列表（供前端展示）
# ──────────────────────────────────────────────

@router.get("/batches")
def list_batches(
    data_type: Optional[str] = Query(None, description="只读取指定类型；为空时返回全部类型"),
    db: Session = Depends(get_db),
):
    """从物化批次日志列出批次；不再对每张业务表做全量 GROUP BY。"""
    if data_type is not None and data_type not in _BATCH_TYPES:
        raise HTTPException(400, "不支持的数据类型")
    cached = _get_data_summary_cache("batches")
    if cached is not None:
        return {data_type: cached.get(data_type, [])} if data_type else cached
    from sqlalchemy import func

    def _batches(model, label, kind, date_column=None):
        logs = db.query(models.UploadBatchLog).filter(
            models.UploadBatchLog.data_type == kind,
            models.UploadBatchLog.row_count > 0,
        ).order_by(models.UploadBatchLog.uploaded_at.desc()).all()
        items = [
            {
                "type": label,
                "batch_id": row.batch_id,
                "count": row.row_count,
                "created_at": str(row.uploaded_at)[:19] if row.uploaded_at else None,
                "time_source": row.time_source,
                "source_filename": row.source_filename,
                "data_start": str(row.data_start) if row.data_start else None,
                "data_end": str(row.data_end) if row.data_end else None,
            }
            for row in logs
        ]

        # 极早期未带批次的数据没有日志，只对 NULL 索引范围做一次兼容查询。
        selected = [
            func.count(model.id).label("count"),
            func.max(model.created_at).label("created_at"),
        ]
        if date_column is not None:
            selected += [func.min(date_column).label("data_start"), func.max(date_column).label("data_end")]
        unbatched = db.query(*selected).filter(model.upload_batch.is_(None)).first()
        if unbatched and unbatched.count:
            items.append({
                "type": label,
                "batch_id": "（无批次）",
                "count": unbatched.count,
                "created_at": str(unbatched.created_at)[:19] if unbatched.created_at else None,
                "time_source": "inferred",
                "source_filename": None,
                "data_start": str(getattr(unbatched, "data_start", None)) if getattr(unbatched, "data_start", None) else None,
                "data_end": str(getattr(unbatched, "data_end", None)) if getattr(unbatched, "data_end", None) else None,
            })
        return sorted(items, key=lambda item: item["created_at"] or "", reverse=True)

    requested = [data_type] if data_type else list(_BATCH_TYPES)
    requested = [kind for kind in requested if kind not in query_source.KINDS]
    result = {kind: [] for kind in query_source.KINDS} if not data_type else {data_type: []}
    for kind in requested:
        model, label, date_column = _BATCH_TYPES[kind]
        result[kind] = _batches(model, label, kind, date_column)
    if data_type is None:
        _set_data_summary_cache("batches", result)
    return result


@router.get("/records")
def list_detail_records(
    data_type: str = Query(..., description="channel/warehouse/schedule/commission/anchor/presale"),
    batch_id: Optional[str] = Query(None, max_length=64),
    shop_id: Optional[str] = Query(None, max_length=64),
    channel: Optional[str] = Query(None, max_length=64),
    anchor_name: Optional[str] = Query(None, max_length=255),
    date_start: Optional[date] = None,
    date_end: Optional[date] = None,
    after_id: Optional[str] = Query(None, max_length=1024),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """按批次游标分页读取已入库原始明细。

    默认按主键倒序；翻页使用 ``id < after_id``，不会随页码变深而退化为大 OFFSET。
    ``query_ms`` 只统计数据库执行和取回行的耗时，不包含 HTTP/JSON 序列化。
    """
    if data_type in query_source.KINDS:
        return query_source.detail_records(data_type, after_id, page_size, shop_id=shop_id,
            channel=channel, date_start=date_start, date_end=date_end, anchor_name=anchor_name)
    if after_id is not None:
        if not str(after_id).isdigit():
            raise HTTPException(400, "明细游标无效")
        after_id = int(after_id)
    config = _DETAIL_CONFIG.get(data_type)
    if config is None:
        raise HTTPException(400, "不支持的数据类型")
    model, field_names = config

    columns = [getattr(model, name) for name in field_names]
    statement = select(*columns)
    mysql_conditions = []
    mysql_params = []
    if batch_id:
        if batch_id == "（无批次）":
            statement = statement.where(model.upload_batch.is_(None))
            mysql_conditions.append("`upload_batch` IS NULL")
        else:
            statement = statement.where(model.upload_batch == batch_id)
            mysql_conditions.append("`upload_batch` = %s")
            mysql_params.append(batch_id)
    if after_id is not None:
        statement = statement.where(model.id < after_id)
        mysql_conditions.append("`id` < %s")
        mysql_params.append(after_id)
    if shop_id and hasattr(model, "shop_id"):
        statement = statement.where(model.shop_id == shop_id)
        mysql_conditions.append("`shop_id` = %s")
        mysql_params.append(shop_id)
    if channel and hasattr(model, "channel"):
        statement = statement.where(model.channel == channel)
        mysql_conditions.append("`channel` = %s")
        mysql_params.append(channel)

    date_column = None
    date_column_name = None
    for candidate in ("date", "payment_date", "broadcast_date"):
        if hasattr(model, candidate):
            date_column = getattr(model, candidate)
            date_column_name = candidate
            break
    if date_start and date_column is not None:
        statement = statement.where(date_column >= date_start)
        mysql_conditions.append(f"`{date_column_name}` >= %s")
        mysql_params.append(date_start)
    if date_end and date_column is not None:
        statement = statement.where(date_column <= date_end)
        mysql_conditions.append(f"`{date_column_name}` <= %s")
        mysql_params.append(date_end)

    statement = statement.order_by(model.id.desc()).limit(page_size + 1)
    if db.get_bind().dialect.name == "mysql":
        # 明细是只读热路径：直接走 DBAPI，避免 ORM 对每一列做对象适配。
        # 表名、列名、排序均来自上方固定白名单；所有用户值仍使用参数绑定。
        driver_connection = db.connection().connection.driver_connection
        cursor = driver_connection.cursor()
        where_sql = f" WHERE {' AND '.join(mysql_conditions)}" if mysql_conditions else ""
        column_sql = ", ".join(f"`{name}`" for name in field_names)
        # MySQL 对大批次的基数估计偶尔会误选 PRIMARY 反向扫描；按批次查看时
        # 强制命中 (upload_batch, id)，旧批次也能稳定只读 page_size+1 行。
        force_index = ""
        index_strategy = "primary id keyset pagination"
        if batch_id and shop_id and hasattr(model, "shop_id"):
            force_index = f" FORCE INDEX (`ix_{model.__tablename__}_batch_shop_id`)"
            index_strategy = "upload_batch + shop_id + id keyset pagination"
        elif batch_id:
            force_index = f" FORCE INDEX (`ix_{model.__tablename__}_batch_id`)"
            index_strategy = "upload_batch + id keyset pagination"
        sql = (
            f"SELECT {column_sql} FROM `{model.__tablename__}`{force_index}{where_sql} "
            f"ORDER BY `id` DESC LIMIT {page_size + 1}"
        )
        try:
            started_ns = time.perf_counter_ns()
            cursor.execute(sql, tuple(mysql_params))
            rows = cursor.fetchall()
            query_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        finally:
            cursor.close()
    else:
        started_ns = time.perf_counter_ns()
        rows = db.execute(statement).all()
        query_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    records = [
        {name: _json_value(value) for name, value in zip(field_names, row)}
        for row in rows
    ]
    return {
        "data_type": data_type,
        "columns": field_names,
        "records": records,
        "page_size": page_size,
        "has_more": has_more,
        "next_cursor": records[-1]["id"] if has_more and records else None,
        "query_ms": round(query_ms, 3),
        "performance_target_ms": 1.0,
        "index_strategy": index_strategy if db.get_bind().dialect.name == "mysql" else "SQLAlchemy keyset pagination",
    }


@router.get("/record-options")
def get_detail_record_options(
    data_type: str = Query(..., description="channel/warehouse/schedule/commission/anchor/presale"),
    batch_id: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(get_db),
):
    if data_type in query_source.KINDS:
        return query_source.record_options(data_type)
    """返回当前批次实际存在的店铺，供明细查询搜索下拉使用。"""
    cache_key = f"record-options:{data_type}:{batch_id}"
    cached = _get_data_summary_cache(cache_key)
    if cached is not None:
        return cached
    config = _DETAIL_CONFIG.get(data_type)
    if config is None:
        raise HTTPException(400, "不支持的数据类型")
    model, _ = config
    if not hasattr(model, "shop_id"):
        return {"shops": []}

    query = db.query(model.shop_id).filter(model.shop_id.isnot(None), model.shop_id != "")
    if batch_id == "（无批次）":
        query = query.filter(model.upload_batch.is_(None))
    else:
        query = query.filter(model.upload_batch == batch_id)
    shop_ids = [row[0] for row in query.distinct().order_by(model.shop_id).limit(5000).all()]
    names = {
        row.shop_id: row.name
        for row in db.query(models.Shop).filter(models.Shop.shop_id.in_(shop_ids)).all()
    } if shop_ids else {}
    result = {
        "shops": [
            {"shop_id": shop_id, "name": names.get(shop_id) or ""}
            for shop_id in shop_ids
        ]
    }
    _set_data_summary_cache(cache_key, result)
    return result


# ──────────────────────────────────────────────
# 删除接口
# ──────────────────────────────────────────────

def _delete_by(
    db, model, batch_id=None, date_start=None, date_end=None, delete_all=False,
    *, data_type: str,
):
    from sqlalchemy import func

    q = db.query(model)
    if batch_id:
        q = q.filter(
            model.upload_batch.is_(None) if batch_id == "（无批次）"
            else model.upload_batch == batch_id
        )
    if date_start and hasattr(model, "date"):
        q = q.filter(model.date >= date_start)
    if date_end and hasattr(model, "date"):
        q = q.filter(model.date <= date_end)
    if not batch_id and not date_start and not date_end and not delete_all:
        raise HTTPException(400, "请至少指定 batch_id、日期范围或 delete_all=true")

    # 日期范围删除可能横跨多个批次，先按批次汇总本次删掉的数量，随后只更新
    # 受影响的小型日志表。普通按批次删除/清空不会执行这一步。
    removed_by_batch = []
    if (date_start or date_end) and not delete_all:
        removed_by_batch = q.with_entities(
            model.upload_batch, func.count(model.id)
        ).group_by(model.upload_batch).all()

    # Query.delete() 本身会返回受影响行数，避免删除前再 COUNT 扫描一次。
    count = q.delete(synchronize_session=False)
    if delete_all:
        db.query(models.UploadBatchLog).filter(
            models.UploadBatchLog.data_type == data_type
        ).delete(synchronize_session=False)
    elif batch_id and not date_start and not date_end and batch_id != "（无批次）":
        db.query(models.UploadBatchLog).filter(
            models.UploadBatchLog.data_type == data_type,
            models.UploadBatchLog.batch_id == batch_id,
        ).delete(synchronize_session=False)
    elif removed_by_batch:
        range_start = _as_date(date_start)
        range_end = _as_date(date_end)
        for affected_batch, removed_count in removed_by_batch:
            if affected_batch is None:
                continue
            log = db.query(models.UploadBatchLog).filter(
                models.UploadBatchLog.data_type == data_type,
                models.UploadBatchLog.batch_id == affected_batch,
            ).first()
            if log is None:
                continue
            log.row_count = max(0, int(log.row_count or 0) - int(removed_count or 0))
            if log.row_count == 0:
                db.delete(log)
                continue
            # 只有删到了已知日期边界时才重算范围；删除中间区间不改变首尾日期。
            touches_start = range_start is None or (log.data_start and range_start <= log.data_start)
            touches_end = range_end is None or (log.data_end and range_end >= log.data_end)
            if (touches_start or touches_end) and hasattr(model, "date"):
                new_start, new_end = db.query(
                    func.min(model.date), func.max(model.date)
                ).filter(model.upload_batch == affected_batch).first()
                log.data_start, log.data_end = new_start, new_end
    db.commit()
    _invalidate_data_summary_cache()
    return count


@router.delete("/channel")
def delete_channel_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.delete("/warehouse")
def delete_warehouse_orders():
    raise HTTPException(410, "此类数据已切换为只读数据库直连，上传、保存和删除功能已关闭。")


@router.delete("/schedule")
def delete_schedules(
    batch_id: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    delete_all: bool = False,
    db: Session = Depends(get_db),
):
    count = _delete_by(db, models.LiveSchedule, batch_id, date_start, date_end, delete_all, data_type="schedule")
    return {"message": f"已删除 {count} 条直播排期", "deleted": count}


@router.delete("/commission")
def delete_commissions(
    batch_id: Optional[str] = None,
    delete_all: bool = False,
    db: Session = Depends(get_db),
):
    count = _delete_by(db, models.Commission, batch_id, delete_all=delete_all, data_type="commission")
    return {"message": f"已删除 {count} 条佣金记录", "deleted": count}


@router.delete("/presale")
def delete_presale_history(
    batch_id: Optional[str] = None,
    delete_all: bool = False,
    db: Session = Depends(get_db),
):
    count = _delete_by(db, models.PresaleHistory, batch_id, delete_all=delete_all, data_type="presale")
    return {"message": f"已删除 {count} 条预售历史记录", "deleted": count}
