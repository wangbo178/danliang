"""
将 config/user_config.json 中的基础配置导入到 MySQL ecom_forecast 数据库。
支持重复运行（已存在则跳过）。

用法：
    cd ecom_forecast
    python import_config.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from pathlib import Path

# ── 先确保建库建表 ────────────────────────────────────────────────────────
from api.database import init_db, SessionLocal
from api import models

print("正在初始化数据库...")
init_db()

# ── 读取 JSON ─────────────────────────────────────────────────────────────
cfg_path = Path(__file__).parent / "config" / "user_config.json"
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

db = SessionLocal()

def upsert(model, unique_field, unique_val, **kwargs):
    """存在则跳过，不存在则插入"""
    existing = db.query(model).filter(
        getattr(model, unique_field) == unique_val
    ).first()
    if existing:
        return existing, False
    obj = model(**{unique_field: unique_val}, **kwargs)
    db.add(obj)
    return obj, True

stats = {"platforms": 0, "channels": 0, "shops": 0, "warehouses": 0, "calendar": 0}

try:
    # ── 平台 & 渠道 ──────────────────────────────────────────────────────
    print("\n导入平台与渠道...")
    for p in cfg.get("platforms", []):
        pid = p["platform_id"]
        _, created = upsert(models.Platform, "platform_id", pid, name=p.get("name", pid))
        if created:
            stats["platforms"] += 1
        # 必须先 flush Platform，才能插入引用它的 Channel（外键约束）
        db.flush()

        for ch in p.get("channels", []):
            cid = ch["channel_id"]
            existing_ch = db.query(models.Channel).filter(
                models.Channel.platform_id == pid,
                models.Channel.channel_id == cid,
            ).first()
            if not existing_ch:
                db.add(models.Channel(
                    platform_id=pid,
                    channel_id=cid,
                    name=ch.get("name", cid),
                ))
                stats["channels"] += 1
        db.flush()

    # ── 店铺 ─────────────────────────────────────────────────────────────
    print("导入店铺...")
    for s in cfg.get("shops", []):
        sid = str(s["shop_id"])
        _, created = upsert(
            models.Shop, "shop_id", sid,
            name=s.get("name"),
            platform_id=s.get("platform_id"),
            campaign_mode=s.get("campaign_mode", "auto"),
        )
        if created:
            stats["shops"] += 1

    db.flush()

    # ── 仓库 ─────────────────────────────────────────────────────────────
    print("导入仓库...")
    for w in cfg.get("warehouses", []):
        wid = w["warehouse_id"]
        _, created = upsert(
            models.Warehouse, "warehouse_id", wid,
            name=w.get("name"),
            region=w.get("region"),
            is_active=w.get("is_active", True),
        )
        if created:
            stats["warehouses"] += 1

    db.flush()

    # ── 大促日历 ─────────────────────────────────────────────────────────
    # 格式：{年份: {大促名: {presale_start, start, end}}}
    print("导入大促日历...")
    raw_cal = cfg.get("campaign_calendar", {})
    for year, campaigns in raw_cal.items():
        for campaign_name, dates in campaigns.items():
            start = dates.get("start")
            end = dates.get("end")
            if not start or not end:          # 日期为 None 的跳过
                continue
            full_name = f"{year} {campaign_name}"
            existing_cal = db.query(models.CampaignCalendar).filter(
                models.CampaignCalendar.name == full_name,
                models.CampaignCalendar.start_date == start,
            ).first()
            if not existing_cal:
                db.add(models.CampaignCalendar(
                    name=full_name,
                    start_date=start,
                    end_date=end,
                    campaign_type=campaign_name,
                ))
                stats["calendar"] += 1

    db.commit()

    print("\n✅ 导入完成！")
    print(f"   平台新增：{stats['platforms']} 条")
    print(f"   渠道新增：{stats['channels']} 条")
    print(f"   店铺新增：{stats['shops']} 条")
    print(f"   仓库新增：{stats['warehouses']} 条")
    print(f"   大促日历新增：{stats['calendar']} 条")

except Exception as e:
    db.rollback()
    print(f"\n❌ 导入失败：{e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    db.close()
