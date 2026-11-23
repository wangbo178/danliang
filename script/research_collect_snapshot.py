"""只读提取排除作废单的日汇总，并冻结可复算的研究快照。"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ecom_forecast"))
from api.database import SessionLocal
from api.routers.config import export_config
from api import models
from api.services import query_source as source

OUT = ROOT / "output" / "research_20261029"
OUT.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    with source.source_connection() as connection:
        frame = source._frame(source.CHANNEL_TRAIN_SQL, connection)
        statuses = source.rows("SELECT order_status,COUNT(*) records,SUM(order_qty) orders FROM dwh_db_order_summary GROUP BY order_status", connection=connection)
        live = source.rows("""SELECT COUNT(*) records,MIN(live_date) first_date,MAX(live_date) last_date,
            COUNT(DISTINCT author_aweme_id) authors,
            SUM(author_nick_name IS NULL OR TRIM(author_nick_name)='') missing_author,
            SUM(live_begin_time IS NULL OR TRIM(live_begin_time)='') missing_begin,
            COUNT(DISTINCT shop_name) source_shop_names
            FROM ads_dy_shop_live_features""", connection=connection)
        warehouse = source.rows("SELECT COUNT(*) records,MIN(pay_date) first_date,MAX(pay_date) last_date,SUM(order_count) orders FROM ads_ofs_facility_order_daily_summary", connection=connection)
    frame["date"] = pd.to_datetime(frame["date"])
    keys = frame[["shop_id", "platform", "channel"]].drop_duplicates().sort_values(["platform", "channel", "shop_id"]).reset_index(drop=True)
    keys["series_id"] = np.arange(len(keys))
    indexed = frame.merge(keys, on=["shop_id", "platform", "channel"], validate="many_to_one")
    dates = pd.date_range(frame["date"].min(), frame["date"].max())
    y = np.zeros((len(dates), len(keys)), dtype=np.float64)
    observed = np.zeros_like(y, dtype=bool)
    day_idx = (indexed["date"] - dates[0]).dt.days.to_numpy()
    series_idx = indexed["series_id"].to_numpy()
    y[day_idx, series_idx] = indexed["order_count"].to_numpy(float)
    observed[day_idx, series_idx] = True
    path = OUT / "snapshot.npz"
    np.savez_compressed(path, y=y, observed=observed,
                        dates=dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
                        keys=keys[["shop_id", "platform", "channel"]].to_numpy(dtype="U255"))
    with SessionLocal() as db:
        config = export_config(db)
        local = {
            "schedules": db.query(models.LiveSchedule).count(),
            "presale_rows": db.query(models.PresaleHistory).count(),
            "commission_rows": db.query(models.Commission).count(),
            "calendar": config.get("campaign_calendar", []),
            "recent_jobs": [{"job_id": row.job_id, "status": row.status,
                             "finished_at": row.finished_at,
                             "data_source": (row.config_snapshot or {}).get("training_data_source")}
                            for row in db.query(models.TrainingJob).order_by(models.TrainingJob.id.desc()).limit(3)],
        }
    multi = frame.groupby("shop_id")["platform"].nunique()
    daily_coverage = frame.groupby("date").size().reindex(dates, fill_value=0)
    meta = {
        "read_at": datetime.now().isoformat(timespec="seconds"), "excluded_statuses": ["999"],
        "date_start": str(dates[0].date()), "date_end": str(dates[-1].date()),
        "days": len(dates), "rows": len(frame), "series": len(keys),
        "total_orders": float(y.sum()), "global_missing_dates": dates[daily_coverage.eq(0)].strftime("%Y-%m-%d").tolist(),
        "series_explicit_zero_rows": int((frame["order_count"] == 0).sum()),
        "multi_platform_shops": multi[multi > 1].to_dict(),
        "statuses": statuses, "live": live, "warehouse": warehouse, "local": local,
        "config": config, "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "zero_fill_assumption": "在全表有数据的日期、系列首次出现后，汇总表未返回的组合按无单处理；另报告仅源表有记录行的评分敏感性。",
    }
    (OUT / "snapshot_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: meta[key] for key in ("read_at", "date_start", "date_end", "days", "rows", "series", "total_orders", "global_missing_dates", "multi_platform_shops", "live", "warehouse")}, ensure_ascii=False, default=str), flush=True)
