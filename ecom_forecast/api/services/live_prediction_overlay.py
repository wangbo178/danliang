"""Refresh live-schedule increments without retraining the base forecast."""

from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

from api import models
from api.database import SessionLocal
from api.services.query_source import TRAINING_EXCLUDED_ORDER_STATUSES
from src.pipeline.forecast_pipeline import ForecastPipeline


_refresh_lock = threading.Lock()


def _schedule_frame(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([{
        "shop_id": row.shop_id,
        "date": row.date,
        "event_type": row.event_type or "live",
        "anchor_tier": row.anchor_tier,
        "anchor_name": row.anchor_name,
        "broadcast_date": row.broadcast_date,
        "has_live": int(row.has_live) if row.has_live is not None else 1,
        "expected_viewers": row.expected_viewers,
        "expected_orders": row.expected_orders,
        "expected_revenue": row.expected_revenue,
        "session_type": row.session_type,
        "platform": row.platform,
        "channel": row.channel,
        "mcn": row.mcn,
    } for row in rows])
    frame["date"] = pd.to_datetime(frame["date"])
    if "broadcast_date" in frame.columns:
        frame["broadcast_date"] = pd.to_datetime(frame["broadcast_date"])
    return frame


def _warehouse_rows(pipeline: ForecastPipeline, prediction_df: pd.DataFrame) -> list[dict]:
    routed = pipeline.predict_warehouse(prediction_df)
    if routed is None or len(routed) == 0:
        return []
    routed = routed.copy()
    routed["date_key"] = routed["date"].apply(
        lambda value: value.date() if hasattr(value, "date") else value
    )
    aggregate = (
        routed.groupby(["date_key", "shop_id", "channel", "warehouse_id"])
        .apply(lambda group: pd.Series({
            "warehouse_orders": group["warehouse_orders"].sum(),
            "sample_orders": 0.0,
        }))
        .reset_index()
    )
    return aggregate.to_dict(orient="records")


def refresh_latest_live_overlay() -> dict:
    """Update only anchor increments on the latest completed forecast.

    The stored ``base_predicted_orders`` is treated as immutable.  This makes a
    schedule upload a cheap overlay refresh instead of a stable-model retrain.
    Warehouse predictions are regenerated from the updated channel totals in
    the same transaction.
    """
    if not _refresh_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "已有直播增量刷新正在运行"}

    db = SessionLocal()
    try:
        latest = (
            db.query(models.TrainingJob)
            .filter(models.TrainingJob.status == "done")
            .order_by(models.TrainingJob.created_at.desc(), models.TrainingJob.id.desc())
            .first()
        )
        if latest is None:
            return {"skipped": True, "reason": "尚无已完成预测任务"}
        source_policy = ((latest.config_snapshot or {}).get("training_data_source") or {})
        if source_policy.get("mode") != "direct" or source_policy.get("excluded_order_statuses") != TRAINING_EXCLUDED_ORDER_STATUSES:
            return {"skipped": True, "reason": "数据源或订单状态口径已更新，请先完整训练一次，再刷新直播增量。"}

        newer_active = (
            db.query(models.TrainingJob.id)
            .filter(
                models.TrainingJob.status.in_(["pending", "running"]),
                models.TrainingJob.created_at > latest.created_at,
            )
            .first()
        )
        if newer_active is not None:
            return {"skipped": True, "reason": "新训练任务正在运行，完成后会自动带入排期"}

        predictions = (
            db.query(models.Prediction)
            .filter(models.Prediction.job_id == latest.job_id)
            .all()
        )
        if not predictions:
            return {"skipped": True, "reason": "最新任务没有预测结果"}

        schedule_df = _schedule_frame(db.query(models.LiveSchedule).all())
        model_dir = Path(__file__).resolve().parents[2] / "models"
        pipeline = ForecastPipeline(
            model_dir=str(model_dir),
            config_dict=latest.config_snapshot or {},
        ).load()
        additions = pipeline.channel_ensemble.anchor_live_model.predict_schedule(schedule_df)
        addition_map = {}
        if len(additions) > 0:
            additions = additions.copy()
            additions["date"] = pd.to_datetime(additions["date"]).dt.date
            addition_map = additions.set_index(["date", "shop_id", "channel"])[
                "anchor_predicted_orders"
            ].to_dict()

            # 手工排期可指定当前预测中尚不存在的渠道。为这些键补一条
            # base=0 的预测记录，保证直播增量能出现在渠道表并参与分仓。
            existing_keys = {
                (row.date, str(row.shop_id), str(row.channel)) for row in predictions
            }
            forecast_start = min(row.date for row in predictions)
            forecast_end = max(row.date for row in predictions)
            platform_map = {}
            if len(schedule_df) > 0 and "channel" in schedule_df.columns:
                explicit = schedule_df.copy()
                explicit["date"] = pd.to_datetime(explicit["date"], errors="coerce").dt.date
                explicit["channel"] = explicit["channel"].astype("string").str.strip().replace("", pd.NA)
                explicit = explicit.dropna(subset=["date", "channel"])
                platform_map = {
                    (row.date, str(row.shop_id), str(row.channel)): row.platform
                    for row in explicit.itertuples()
                }
            for row in additions.itertuples():
                key = (row.date, str(row.shop_id), str(row.channel))
                if key in existing_keys or not (forecast_start <= row.date <= forecast_end):
                    continue
                prediction = models.Prediction(
                    job_id=latest.job_id,
                    date=row.date,
                    shop_id=key[1],
                    channel=key[2],
                    platform=platform_map.get(key),
                    predicted_orders=0.0,
                    raw_predicted_orders=0.0,
                    base_predicted_orders=0.0,
                    anchor_predicted_orders=0.0,
                )
                db.add(prediction)
                predictions.append(prediction)
                existing_keys.add(key)

        old_anchor_total = 0.0
        new_anchor_total = 0.0
        prediction_records = []
        for row in predictions:
            old_anchor = float(row.anchor_predicted_orders or 0.0)
            base = (
                float(row.base_predicted_orders)
                if row.base_predicted_orders is not None
                else max(0.0, float(row.predicted_orders or 0.0) - old_anchor)
            )
            raw_base = max(0.0, float(row.raw_predicted_orders or 0.0) - old_anchor)
            new_anchor = float(addition_map.get(
                (row.date, str(row.shop_id), str(row.channel)), 0.0
            ))
            row.base_predicted_orders = base
            row.anchor_predicted_orders = new_anchor
            row.predicted_orders = base + new_anchor
            row.raw_predicted_orders = raw_base + new_anchor
            old_anchor_total += old_anchor
            new_anchor_total += new_anchor
            prediction_records.append({
                "date": pd.Timestamp(row.date),
                "shop_id": str(row.shop_id),
                "channel": str(row.channel),
                "platform": row.platform,
                "predicted_orders": float(row.predicted_orders),
            })

        warehouse_records = _warehouse_rows(
            pipeline, pd.DataFrame(prediction_records)
        )
        db.query(models.WarehousePrediction).filter(
            models.WarehousePrediction.job_id == latest.job_id
        ).delete(synchronize_session=False)
        if warehouse_records:
            db.bulk_save_objects([
                models.WarehousePrediction(
                    job_id=latest.job_id,
                    date=item["date_key"],
                    shop_id=str(item["shop_id"]),
                    channel=str(item["channel"]) if item["channel"] else None,
                    warehouse_id=str(item["warehouse_id"]),
                    warehouse_orders=float(item["warehouse_orders"] or 0.0),
                    sample_orders=float(item["sample_orders"] or 0.0),
                )
                for item in warehouse_records
            ])
        db.commit()
        return {
            "skipped": False,
            "job_id": latest.job_id,
            "prediction_rows": len(predictions),
            "warehouse_rows": len(warehouse_records),
            "old_anchor_orders": round(old_anchor_total),
            "new_anchor_orders": round(new_anchor_total),
            "anchor_delta": round(new_anchor_total - old_anchor_total),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        _refresh_lock.release()
