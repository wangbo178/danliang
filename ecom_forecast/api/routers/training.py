"""
训练路由：启动训练、WebSocket 进度、任务历史
"""
import sys
import os
import uuid
import threading
import json
import asyncio
from datetime import datetime
from typing import List, Optional

import pandas as pd
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from api.database import get_db, SessionLocal
from api import models, schemas
from api.services import query_source

router = APIRouter()

# 全局 WebSocket 连接管理
_ws_connections: dict[str, list] = {}
_ws_lock = asyncio.Lock()


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _db_log(job_id: str, text: str, level: str = "info"):
    """在独立线程中写日志到数据库"""
    db = SessionLocal()
    try:
        db.add(models.JobLog(job_id=job_id, log_text=text, log_level=level))
        db.commit()
    finally:
        db.close()


def _update_job(job_id: str, **kwargs):
    db = SessionLocal()
    try:
        job = db.query(models.TrainingJob).filter(models.TrainingJob.job_id == job_id).first()
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            db.commit()
    finally:
        db.close()


def _broadcast(job_id: str, msg: dict):
    """向该 job 的所有 WS 连接广播消息（线程安全）"""
    connections = _ws_connections.get(job_id, [])
    for ws in connections[:]:
        try:
            # 在事件循环中发送
            loop = ws._loop if hasattr(ws, "_loop") else None
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    ws.send_text(json.dumps(msg, ensure_ascii=False)), loop
                )
        except Exception:
            pass


def _load_data_from_db(
    db: Session,
) -> dict:
    """训练启动时从远端读取当前快照；本地只保存配置、排期和结果。"""
    live_data = query_source.load_training_frames()
    channel_df = live_data["channel_df"]
    warehouse_df = live_data["warehouse_df"]

    # 直播排期（补充 event_type / broadcast_date 以支持预售分布模型）
    sc_rows = db.query(models.LiveSchedule).order_by(
        models.LiveSchedule.created_at.asc(),
        models.LiveSchedule.id.asc(),
    ).all()
    schedule_df = pd.DataFrame([{
        "shop_id": r.shop_id,
        "date": r.date,
        "event_type": r.event_type or "live",       # live | presale_payment
        "anchor_tier": r.anchor_tier,
        "anchor_name": r.anchor_name,               # 达人名称（可为空）
        "broadcast_date": r.broadcast_date,         # 仅 presale_payment 行有值
        "has_live": int(r.has_live) if r.has_live is not None else 1,
        "expected_viewers": r.expected_viewers,
        "expected_orders": r.expected_orders,
        "expected_revenue": r.expected_revenue,
        "session_type": r.session_type,
        "platform": r.platform,
        "channel": r.channel,
        "mcn": r.mcn,
        "scheduled_at": r.scheduled_at,
        "source_type": r.source_type,
        "source_record_id": r.source_record_id,
        "schedule_source": "uploaded",
        "upload_batch": r.upload_batch,
        "created_at": r.created_at,
    } for r in sc_rows]) if sc_rows else None
    if schedule_df is not None and len(schedule_df):
        schedule_df["date"] = pd.to_datetime(schedule_df["date"])
        if "broadcast_date" in schedule_df.columns:
            schedule_df["broadcast_date"] = pd.to_datetime(schedule_df["broadcast_date"])
        if "anchor_name" in schedule_df.columns:
            schedule_df["anchor_name"] = (
                schedule_df["anchor_name"].astype("string").str.strip().replace("", pd.NA)
            )
        # 直播排期按增量批次长期累积；同一场排期被后续批次修订时，明确保留
        # created_at / id 顺序中的最新版本，避免依赖数据库未定义的返回顺序。
        schedule_key = [
            column for column in (
                "shop_id", "date", "event_type", "channel", "broadcast_date",
                "anchor_name", "scheduled_at", "session_type", "mcn", "source_record_id",
            ) if column in schedule_df.columns
        ]
        if schedule_key:
            schedule_df = schedule_df.drop_duplicates(subset=schedule_key, keep="last")
        schedule_df = schedule_df.drop(
            columns=["upload_batch", "created_at"], errors="ignore"
        ).reset_index(drop=True)

    anchor_df = live_data["anchor_df"]

    # 联盟佣金
    cm_rows = db.query(models.Commission).all()
    commission_df = pd.DataFrame([{
        "shop_id": r.shop_id, "month": r.month,
        "commission_rate": r.commission_rate, "active_affiliates": r.active_affiliates,
    } for r in cm_rows]) if cm_rows else None

    return {
        "channel_df": channel_df,
        "warehouse_df": warehouse_df if len(warehouse_df) > 0 else pd.DataFrame(),
        "schedule_df": schedule_df,
        "commission_df": commission_df,
        "anchor_df": anchor_df,
        "source_read_at": live_data["source_read_at"],
    }


def _excluded_channel_mask(frame: pd.DataFrame, excluded_channels: list[dict]) -> pd.Series:
    """同时兼容渠道 ID 与渠道名称，按平台精确排除配置为“不加入测算”的渠道。"""
    mask = pd.Series(False, index=frame.index)
    if frame.empty or not excluded_channels or "channel" not in frame.columns:
        return mask
    channel_values = frame["channel"].fillna("").astype(str).str.strip().str.casefold()
    platform_values = (
        frame["platform"].fillna("").astype(str).str.strip().str.casefold()
        if "platform" in frame.columns else pd.Series("", index=frame.index)
    )
    for item in excluded_channels:
        platform_id = str(item.get("platform_id") or "").strip().casefold()
        aliases = {
            str(item.get("channel_id") or "").strip().casefold(),
            str(item.get("channel_name") or "").strip().casefold(),
        } - {""}
        if not aliases:
            continue
        item_mask = channel_values.isin(aliases)
        if platform_id:
            item_mask &= platform_values.eq(platform_id)
        mask |= item_mask
    return mask


def _resolve_presale_allocations(
    channel_df: pd.DataFrame,
    presale_records: list[dict],
    channel_type_map: dict,
) -> tuple[pd.DataFrame, float]:
    """把预售历史精确分配到实际 event 渠道，避免按店铺重复扣减。"""
    columns = [
        "shop_id", "channel", "broadcast_date", "payment_date",
        "order_count", "campaign_type",
    ]
    if not presale_records:
        return pd.DataFrame(columns=columns), 0.0

    actual = channel_df.copy()
    actual["date"] = pd.to_datetime(actual["date"])
    actual["_date"] = actual["date"].dt.date
    allocations = []
    unresolved = 0.0

    for record in presale_records:
        shop_id = str(record["shop_id"])
        payment_date = pd.Timestamp(record["payment_date"]).date()
        source_channel = str(record.get("channel") or "")
        requested = max(float(record.get("order_count") or 0), 0.0)
        day_rows = actual[
            (actual["shop_id"].astype(str) == shop_id)
            & (actual["_date"] == payment_date)
        ]

        exact = day_rows[day_rows["channel"].astype(str) == source_channel]
        if len(exact) > 0:
            candidates = exact
        else:
            event_mask = day_rows["channel"].map(channel_type_map).eq("event")
            candidates = day_rows[event_mask]

        if len(candidates) == 0 or requested <= 0:
            unresolved += requested
            continue

        candidate_qty = candidates["order_count"].clip(lower=0).astype(float)
        available = float(candidate_qty.sum())
        allocated_total = min(requested, available)
        if allocated_total <= 0:
            unresolved += requested
            continue
        unresolved += requested - allocated_total

        weights = candidate_qty / available
        for (_, candidate), weight in zip(candidates.iterrows(), weights):
            allocations.append({
                "shop_id": shop_id,
                "channel": str(candidate["channel"]),
                "broadcast_date": pd.Timestamp(record["broadcast_date"]),
                "payment_date": pd.Timestamp(payment_date),
                "order_count": allocated_total * float(weight),
                "campaign_type": record.get("campaign_type"),
            })

    if not allocations:
        return pd.DataFrame(columns=columns), unresolved

    allocation_df = (
        pd.DataFrame(allocations)
        .groupby(
            ["shop_id", "channel", "broadcast_date", "payment_date", "campaign_type"],
            dropna=False,
            as_index=False,
        )["order_count"]
        .sum()
    )
    return allocation_df, unresolved


def _merge_schedule_sources(
    uploaded_schedule: Optional[pd.DataFrame],
    historical_presale_schedule: Optional[pd.DataFrame],
    history_end: pd.Timestamp,
) -> Optional[pd.DataFrame]:
    """合并用户排期与从实绩推导的历史预售排期。

    历史推导数据只替换已发生日期的同店预售记录，绝不能删除
    用户上传的未来 presale_payment。
    """
    uploaded = uploaded_schedule.copy() if uploaded_schedule is not None else pd.DataFrame()
    generated = (
        historical_presale_schedule.copy()
        if historical_presale_schedule is not None else pd.DataFrame()
    )
    if len(uploaded) == 0 and len(generated) == 0:
        return None

    if len(uploaded) > 0:
        uploaded["date"] = pd.to_datetime(uploaded["date"])
        if "event_type" not in uploaded.columns:
            uploaded["event_type"] = "live"
        uploaded["event_type"] = uploaded["event_type"].fillna("live").astype(str).str.strip()

    if len(generated) > 0:
        generated["date"] = pd.to_datetime(generated["date"])
        generated["event_type"] = "presale_payment"

    # 实绩推导行包含真实渠道，在历史时段优先于手工行。
    # 但未来行是预测的业务输入，必须无条件保留。
    if len(uploaded) > 0 and len(generated) > 0:
        generated_keys = set(
            zip(generated["shop_id"].astype(str), generated["date"].dt.normalize())
        )
        is_historical_ps = (
            uploaded["event_type"].eq("presale_payment")
            & uploaded["date"].le(pd.Timestamp(history_end))
        )
        overlaps_generated = pd.Series(
            [
                (str(shop), pd.Timestamp(day).normalize()) in generated_keys
                for shop, day in zip(uploaded["shop_id"], uploaded["date"])
            ],
            index=uploaded.index,
        )
        uploaded = uploaded[~(is_historical_ps & overlaps_generated)].copy()

    merged = pd.concat([uploaded, generated], ignore_index=True, sort=False)
    merged["date"] = pd.to_datetime(merged["date"])
    if "broadcast_date" in merged.columns:
        merged["broadcast_date"] = pd.to_datetime(merged["broadcast_date"], errors="coerce")

    dedupe_cols = [
        col for col in (
            "shop_id", "date", "event_type", "channel", "broadcast_date",
            "anchor_name", "scheduled_at", "session_type", "mcn", "source_record_id",
        ) if col in merged.columns
    ]
    if dedupe_cols:
        merged = merged.drop_duplicates(subset=dedupe_cols, keep="last")
    return merged.reset_index(drop=True)


def _audit_future_schedule(
    schedule_df: Optional[pd.DataFrame],
    anchor_df: Optional[pd.DataFrame],
    forecast_start: pd.Timestamp,
    forecast_end: pd.Timestamp,
) -> dict:
    """统计预测窗口内真正可生效的排期及达人匹配情况。"""
    result = {
        "total": 0, "in_window": 0, "live": 0, "presale": 0,
        "after_window": 0, "live_with_anchor": 0, "matched_anchor": 0,
        "unmatched_anchor_names": [],
    }
    if schedule_df is None or len(schedule_df) == 0:
        return result

    schedule = schedule_df.copy()
    schedule["date"] = pd.to_datetime(schedule["date"])
    if "event_type" not in schedule.columns:
        schedule["event_type"] = "live"
    schedule["event_type"] = schedule["event_type"].fillna("live").astype(str).str.strip()
    in_window = schedule[
        schedule["date"].between(pd.Timestamp(forecast_start), pd.Timestamp(forecast_end))
    ].copy()
    live = in_window[in_window["event_type"].eq("live")].copy()
    result.update({
        "total": int(len(schedule)),
        "in_window": int(len(in_window)),
        "live": int(len(live)),
        "presale": int(in_window["event_type"].eq("presale_payment").sum()),
        "after_window": int((schedule["date"] > pd.Timestamp(forecast_end)).sum()),
    })

    if "anchor_name" not in live.columns:
        return result
    live["_anchor"] = live["anchor_name"].fillna("").astype(str).str.strip()
    named = live[live["_anchor"].ne("")].copy()
    result["live_with_anchor"] = int(len(named))
    if len(named) == 0 or anchor_df is None or len(anchor_df) == 0:
        return result

    history = anchor_df.copy()
    history["_anchor"] = history["anchor_name"].fillna("").astype(str).str.strip()
    if "shop_id" in history.columns and "shop_id" in named.columns:
        known = set(zip(history["shop_id"].astype(str), history["_anchor"]))
        matched = [
            (str(shop), anchor) in known
            for shop, anchor in zip(named["shop_id"], named["_anchor"])
        ]
    else:
        known_names = set(history["_anchor"])
        matched = [anchor in known_names for anchor in named["_anchor"]]
    result["matched_anchor"] = int(sum(matched))
    result["unmatched_anchor_names"] = sorted(
        set(named.loc[[not flag for flag in matched], "_anchor"].tolist())
    )[:5]
    return result


def _build_manual_channel_placeholders(
    pred_df: pd.DataFrame,
    manual_channel_meta: pd.DataFrame,
    future_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """仅为预测结果中尚不存在的人工维护渠道补 0 单占位。"""
    columns = [
        "date", "shop_id", "channel", "platform", "predicted_orders",
        "raw_predicted_orders", "guardrail_predicted_orders",
        "base_predicted_orders", "anchor_predicted_orders",
    ]
    if manual_channel_meta.empty:
        return pd.DataFrame(columns=columns)

    existing_keys = set()
    if pred_df is not None and len(pred_df) > 0:
        existing_dates = pd.to_datetime(pred_df["date"], errors="coerce").dt.normalize()
        existing_keys = set(zip(
            existing_dates,
            pred_df["shop_id"].astype(str),
            pred_df["channel"].astype(str),
        ))

    rows = []
    for forecast_date in future_dates:
        normalized_date = pd.Timestamp(forecast_date).normalize()
        for _, meta in manual_channel_meta.iterrows():
            key = (normalized_date, str(meta["shop_id"]), str(meta["channel"]))
            if key in existing_keys:
                continue
            rows.append({
                "date": normalized_date,
                "shop_id": key[1],
                "channel": key[2],
                "platform": meta["platform"],
                "predicted_orders": 0.0,
                "raw_predicted_orders": 0.0,
                "guardrail_predicted_orders": 0.0,
                "base_predicted_orders": 0.0,
                "anchor_predicted_orders": 0.0,
            })
            existing_keys.add(key)
    return pd.DataFrame(rows, columns=columns)


def _evaluate_holdout_before_forecast(
    *,
    model_dir: str,
    full_config: dict,
    holdout_days: int,
    channel_df: pd.DataFrame,
    channel_actual_df: pd.DataFrame,
    schedule_df: Optional[pd.DataFrame],
    commission_df: Optional[pd.DataFrame],
    anchor_df: Optional[pd.DataFrame],
    resolved_presale_df: pd.DataFrame,
    warehouse_df: pd.DataFrame,
    config_dict: dict,
    progress_callback=None,
):
    """在生成未来预测前完成独立回测，返回可用于正式模型的安全阀渠道。"""
    from src.pipeline.forecast_pipeline import ForecastPipeline

    eval_cutoff = channel_actual_df["date"].max() - pd.Timedelta(days=holdout_days)
    train_only = channel_df[channel_df["date"] <= eval_cutoff].copy()
    if len(train_only) == 0:
        raise ValueError("holdout 过长，切分后无训练数据")

    eval_pipeline = ForecastPipeline(model_dir=model_dir, config_dict=full_config)
    schedule_train = (
        schedule_df[pd.to_datetime(schedule_df["date"]) <= eval_cutoff].copy()
        if schedule_df is not None and len(schedule_df) > 0 else None
    )
    anchor_train = (
        anchor_df[pd.to_datetime(anchor_df["date"]) <= eval_cutoff].copy()
        if anchor_df is not None and len(anchor_df) > 0 else None
    )
    eval_pipeline.train_channels(
        train_only,
        schedule_df=schedule_train,
        commission_df=commission_df,
        anchor_df=anchor_train,
    )
    # 回测预测可以使用当时已知的完整直播排期，但达人实绩只允许用 cutoff 前数据。
    eval_pipeline._schedule_df = schedule_df
    eval_pipeline._commission_df = commission_df
    eval_pipeline._anchor_df = anchor_train

    if len(resolved_presale_df) > 0:
        ps_train = resolved_presale_df[
            pd.to_datetime(resolved_presale_df["payment_date"]) <= eval_cutoff
        ]
        if len(ps_train) > 0:
            from src.models.presale_model import PresaleDistributionModel
            ps_events = [{
                "shop_id": row["shop_id"], "channel": row["channel"],
                "campaign_type": row["campaign_type"],
                "broadcast_date": row["broadcast_date"],
                "payment_date": row["payment_date"], "day_index": None,
                "daily_volume": float(row["order_count"]),
            } for _, row in ps_train.iterrows()]
            eval_pipeline.channel_ensemble.models["event"].presale_model = (
                PresaleDistributionModel().fit(ps_events)
            )

    wh_for_eval = None
    warehouse_eval_df = warehouse_df.loc[
        ~_excluded_channel_mask(
            warehouse_df, config_dict.get("excluded_channels", [])
        )
    ].copy() if len(warehouse_df) > 0 else warehouse_df
    if len(warehouse_eval_df) > 0:
        wh_train = warehouse_eval_df[warehouse_eval_df["date"] <= eval_cutoff]
        if len(wh_train) > 0:
            eval_pipeline.train_routing(wh_train)
            wh_for_eval = warehouse_eval_df

    ch_eval, wh_eval, merged = eval_pipeline.evaluate_on_holdout(
        channel_df,
        wh_for_eval,
        holdout_days=holdout_days,
        actual_order_df=channel_actual_df,
        progress_callback=progress_callback,
    )
    eval_pipeline.anchor_holdout_metrics = (
        eval_pipeline.channel_ensemble.anchor_live_model.evaluate_holdout(
            anchor_df=anchor_df,
            schedule_df=schedule_df,
            start_date=eval_cutoff + pd.Timedelta(days=1),
            end_date=channel_actual_df["date"].max(),
        )
    )
    return eval_pipeline, ch_eval, wh_eval, merged


def _save_evaluation_metrics(
    job_id: str,
    channel_eval: pd.DataFrame,
    anchor_metrics: Optional[dict] = None,
) -> None:
    def metric_value(value, scale: float = 1.0):
        return float(value) * scale if pd.notna(value) else None

    db = SessionLocal()
    try:
        for _, row in channel_eval.iterrows():
            segment = str(row.get("segment", ""))
            if segment.startswith("channel="):
                level = "channel"
                group_key = segment[len("channel="):]
            elif segment == "全局":
                level = "overall"
                group_key = "all"
            else:
                level = "segment"
                group_key = segment
            db.add(models.EvaluationMetric(
                job_id=job_id, level=level, group_key=group_key,
                mae=metric_value(row.get("mae")),
                wape=metric_value(row.get("wape")),
                rmse=metric_value(row.get("rmse")),
                mape=metric_value(row.get("mape"), 0.01),
                bias=metric_value(row.get("bias_pct"), 0.01),
                n_samples=int(row.get("n_samples") or 0),
                total_actual=metric_value(row.get("total_actual")),
                total_predicted=metric_value(row.get("total_predicted")),
                raw_wape=metric_value(row.get("raw_wape")),
                raw_rmse=metric_value(row.get("raw_rmse")),
                raw_mape=metric_value(row.get("raw_mape"), 0.01),
                raw_bias=metric_value(row.get("raw_bias_pct"), 0.01),
                raw_total_predicted=metric_value(row.get("raw_total_predicted")),
            ))
        if anchor_metrics:
            db.add(models.EvaluationMetric(
                job_id=job_id,
                level="anchor",
                group_key="live",
                mae=metric_value(anchor_metrics.get("wape"), 100.0),
                wape=metric_value(anchor_metrics.get("wape"), 100.0),
                bias=metric_value(anchor_metrics.get("bias")),
                n_samples=int(anchor_metrics.get("n") or 0),
                total_actual=metric_value(anchor_metrics.get("actual_total")),
                total_predicted=metric_value(anchor_metrics.get("predicted_total")),
                raw_wape=metric_value(anchor_metrics.get("target_wape"), 100.0),
                raw_bias=metric_value(anchor_metrics.get("target_bias")),
                raw_total_predicted=metric_value(
                    anchor_metrics.get("target_predicted_total")
                ),
            ))
        db.commit()
    finally:
        db.close()


def _save_backtest_daily_results(
    job_id: str,
    channel_merged: pd.DataFrame,
    warehouse_merged: Optional[pd.DataFrame] = None,
) -> int:
    """持久化回测逐日结果，供看板比较预测与实发。"""
    rows = []
    if channel_merged is not None and len(channel_merged) > 0:
        channel_daily = channel_merged.copy()
        channel_daily["order_count"] = pd.to_numeric(
            channel_daily["order_count"], errors="coerce"
        ).fillna(0)
        channel_daily["predicted_orders"] = pd.to_numeric(
            channel_daily["predicted_orders"], errors="coerce"
        ).fillna(0)
        channel_daily = channel_daily.groupby("date", as_index=False).agg(
            actual_orders=("order_count", "sum"),
            predicted_orders=("predicted_orders", "sum"),
        )
        for record in channel_daily.itertuples():
            rows.append(models.BacktestDailyResult(
                job_id=job_id,
                date=record.date.date() if hasattr(record.date, "date") else record.date,
                level="total",
                group_key="all",
                actual_orders=float(record.actual_orders),
                predicted_orders=float(record.predicted_orders),
            ))

    if warehouse_merged is not None and len(warehouse_merged) > 0:
        warehouse_daily = warehouse_merged.copy()
        warehouse_daily["order_count"] = pd.to_numeric(
            warehouse_daily["order_count"], errors="coerce"
        ).fillna(0)
        warehouse_daily["warehouse_orders_pred"] = pd.to_numeric(
            warehouse_daily["warehouse_orders_pred"], errors="coerce"
        ).fillna(0)
        warehouse_daily = warehouse_daily.groupby(
            ["date", "warehouse_id"], as_index=False
        ).agg(
            actual_orders=("order_count", "sum"),
            predicted_orders=("warehouse_orders_pred", "sum"),
        )
        for record in warehouse_daily.itertuples():
            rows.append(models.BacktestDailyResult(
                job_id=job_id,
                date=record.date.date() if hasattr(record.date, "date") else record.date,
                level="warehouse",
                group_key=str(record.warehouse_id),
                actual_orders=float(record.actual_orders),
                predicted_orders=float(record.predicted_orders),
            ))

    db = SessionLocal()
    try:
        db.query(models.BacktestDailyResult).filter(
            models.BacktestDailyResult.job_id == job_id
        ).delete(synchronize_session=False)
        if rows:
            db.bulk_save_objects(rows)
        db.commit()
    finally:
        db.close()
    return len(rows)


def _enrich_schedule_with_anchor_history(
    schedule_df: Optional[pd.DataFrame],
    anchor_df: Optional[pd.DataFrame],
    channel_df: pd.DataFrame,
    channel_type_map: dict,
    history_end: pd.Timestamp,
) -> tuple[Optional[pd.DataFrame], int]:
    """
    把达人实绩转为历史 live 特征，使模型能学到达人量级与渠道单量的关系。

    只对“店铺可明确映射到一个主直播渠道”的历史生效；不推断未来直播，
    也不覆盖用户已上传的历史排期。
    """
    if anchor_df is None or len(anchor_df) == 0 or len(channel_df) == 0:
        return schedule_df, 0
    required = {"date", "shop_id", "anchor_name", "order_count"}
    if not required.issubset(anchor_df.columns):
        return schedule_df, 0

    orders = channel_df.copy()
    orders["_channel_type"] = orders["channel"].map(channel_type_map).fillna("stable")
    event_orders = orders[orders["_channel_type"].eq("event")]
    if len(event_orders) == 0:
        return schedule_df, 0

    # 若一店有多个直播渠道，取历史单量最大的主渠道，避免重复施加。
    primary_channel = (
        event_orders.groupby(["shop_id", "channel"], as_index=False)["order_count"]
        .sum()
        .sort_values(["shop_id", "order_count", "channel"], ascending=[True, False, True])
        .drop_duplicates("shop_id", keep="first")
        .set_index("shop_id")["channel"]
        .to_dict()
    )

    history = anchor_df.copy()
    history["date"] = pd.to_datetime(history["date"])
    history["anchor_name"] = history["anchor_name"].astype("string").str.strip()
    history["order_count"] = pd.to_numeric(history["order_count"], errors="coerce").fillna(0)
    history = history[
        history["date"].le(pd.Timestamp(history_end))
        & history["order_count"].gt(0)
        & history["anchor_name"].notna()
        & history["shop_id"].isin(primary_channel)
    ]
    if len(history) == 0:
        return schedule_df, 0

    # 同店同日可能有多位达人；现有特征是日粒度，使用贡献单量最大的主达人。
    dominant = (
        history.groupby(["shop_id", "date", "anchor_name"], as_index=False)["order_count"]
        .sum()
        .sort_values(
            ["shop_id", "date", "order_count", "anchor_name"],
            ascending=[True, True, False, True],
        )
        .drop_duplicates(["shop_id", "date"], keep="first")
    )
    dominant["channel"] = dominant["shop_id"].map(primary_channel)
    # 档位按各主渠道的历史分位数生成，避免不同平台绝对量级不可比。
    rank_pct = dominant.groupby("channel")["order_count"].rank(pct=True, method="average")
    dominant["anchor_tier"] = np.select(
        [rank_pct.ge(0.90), rank_pct.ge(0.50)], ["A", "B"], default="C"
    )
    inferred = dominant[["shop_id", "date", "channel", "anchor_name", "anchor_tier"]].copy()
    inferred["event_type"] = "live"
    inferred["has_live"] = 1
    inferred["broadcast_date"] = pd.NaT
    inferred["expected_viewers"] = None
    inferred["schedule_source"] = "anchor_history"

    existing = schedule_df.copy() if schedule_df is not None else pd.DataFrame()
    if len(existing) > 0:
        existing["date"] = pd.to_datetime(existing["date"])
        event_type = (
            existing["event_type"].fillna("live").astype(str)
            if "event_type" in existing.columns else pd.Series("live", index=existing.index)
        )
        explicit_live = existing[event_type.eq("live")]
        explicit_keys = set(zip(explicit_live["shop_id"].astype(str), explicit_live["date"].dt.normalize()))
        inferred = inferred[
            [
                (str(shop), pd.Timestamp(day).normalize()) not in explicit_keys
                for shop, day in zip(inferred["shop_id"], inferred["date"])
            ]
        ]

    if len(inferred) == 0:
        return schedule_df, 0
    merged = pd.concat([existing, inferred], ignore_index=True, sort=False)
    return merged.reset_index(drop=True), int(len(inferred))


def _run_training(
    job_id: str,
    request: schemas.TrainingStartRequest,
    config_dict: dict,
):
    """在子线程中执行训练流水线"""

    def log(text: str, level: str = "info"):
        _db_log(job_id, text, level)
        _broadcast(job_id, {"type": "log", "data": text})

    def progress(pct: int):
        _update_job(job_id, progress=pct)
        _broadcast(job_id, {"type": "progress", "data": pct})

    try:
        log(f"[{datetime.now():%H:%M:%S}] 训练任务启动，job_id={job_id}")
        _update_job(job_id, status="running", started_at=datetime.now())

        # 加载数据
        log("正在直连只读数据库，渠道训练与回测排除 999（作废）订单；其余状态保留。")
        db = SessionLocal()
        try:
            data = _load_data_from_db(db)
        finally:
            db.close()

        if not data or len(data.get("channel_df", [])) == 0:
            raise ValueError("只读数据源中没有渠道订单历史，请检查源表。")

        channel_df = data["channel_df"]
        warehouse_df = data["warehouse_df"]
        schedule_df = data.get("schedule_df")
        commission_df = data.get("commission_df")
        anchor_df = data.get("anchor_df")
        config_dict.setdefault("training_data_source", {"mode": "direct", "tables": query_source.TABLES}).update({
            "read_at": data["source_read_at"],
            "channel_rows": len(channel_df), "warehouse_rows": len(warehouse_df),
            "anchor_rows": len(anchor_df) if anchor_df is not None else 0,
            "channel_end": str(channel_df["date"].max().date()),
        })
        _update_job(job_id, config_snapshot=config_dict)

        log("仓库源表无店铺维度，分仓学习使用整体逻辑仓占比；直播实绩仅按主播匹配。")
        log(f"数据加载完成：渠道订单 {len(channel_df)} 条，仓库订单 {len(warehouse_df)} 条")
        if anchor_df is not None and len(anchor_df) > 0:
            log(f"达人直播数据：{len(anchor_df)} 条，覆盖 {anchor_df['anchor_name'].nunique()} 位达人")

        # ── 加载预售历史 ──────────────────────────────────────────────────────
        db_presale = SessionLocal()
        try:
            presale_rows = db_presale.query(models.PresaleHistory).all()
        finally:
            db_presale.close()

        presale_model_obj = None
        resolved_presale_df = pd.DataFrame()

        # 保持模型输入为日期×店铺×渠道×平台；源查询已聚合当前事实，不含上传批次。
        raw_rows = len(channel_df)
        channel_df = (
            channel_df
            .groupby(["date", "shop_id", "channel", "platform"], as_index=False)["order_count"]
            .sum()
        )
        channel_df["date"] = pd.to_datetime(channel_df["date"])
        dedup_rows = len(channel_df)
        if raw_rows != dedup_rows:
            log(f"数据去重聚合：{raw_rows} 条 → {dedup_rows} 条（合并了 {raw_rows - dedup_rows} 条重复日期记录）")

        data_end_date = channel_df["date"].max()
        excluded_mask = _excluded_channel_mask(
            channel_df, config_dict.get("excluded_channels", [])
        )
        manual_channel_meta = channel_df.loc[
            excluded_mask, ["shop_id", "channel", "platform"]
        ].drop_duplicates()
        excluded_rows = int(excluded_mask.sum())
        channel_df = channel_df.loc[~excluded_mask].copy()
        channel_actual_df = channel_df.copy()
        if excluded_rows:
            log(
                f"已排除 {len(manual_channel_meta)} 个店铺渠道组合、{excluded_rows} 条历史记录；"
                "这些渠道不参与训练与 WAPE，对未来生成 0 单占位供人工排期。"
            )
        if channel_df.empty:
            raise ValueError("所有渠道都已设置为不加入测算，至少需要保留一个训练渠道")

        # ── 将预售量解析到实际渠道后再剥离，保证总扣减不超过预售历史总量 ──
        if presale_rows:
            presale_records = [{
                "shop_id": r.shop_id,
                "channel": r.channel,
                "broadcast_date": r.broadcast_date,
                "payment_date": r.payment_date,
                "order_count": r.order_count,
                "campaign_type": r.campaign_type,
            } for r in presale_rows]
            allocation_df, unresolved_qty = _resolve_presale_allocations(
                channel_df,
                presale_records,
                config_dict.get("channel_type_map", {}),
            )
            if len(allocation_df) > 0:
                resolved_presale_df = allocation_df.copy()
                deduct = (
                    allocation_df.groupby(["shop_id", "channel", "payment_date"], as_index=False)["order_count"]
                    .sum()
                    .rename(columns={"payment_date": "date", "order_count": "_presale_adj"})
                )
                deduct["date"] = pd.to_datetime(deduct["date"])
                channel_df = channel_df.merge(
                    deduct, on=["shop_id", "channel", "date"], how="left", validate="one_to_one"
                )
                channel_df["_presale_adj"] = channel_df["_presale_adj"].fillna(0)
                channel_df["order_count"] = (
                    channel_df["order_count"] - channel_df["_presale_adj"]
                ).clip(lower=0)
                channel_df = channel_df.drop(columns=["_presale_adj"])

                presale_sched_df = allocation_df[[
                    "shop_id", "channel", "payment_date", "broadcast_date"
                ]].drop_duplicates().rename(columns={"payment_date": "date"})
                presale_sched_df["event_type"] = "presale_payment"
                presale_sched_df["anchor_tier"] = "A"
                presale_sched_df["has_live"] = 1
                presale_sched_df["expected_viewers"] = None
                schedule_df = _merge_schedule_sources(
                    schedule_df,
                    presale_sched_df,
                    history_end=channel_actual_df["date"].max(),
                )

                from src.models.presale_model import PresaleDistributionModel
                events = [{
                    "shop_id": r["shop_id"],
                    "channel": r["channel"],
                    "campaign_type": r["campaign_type"],
                    "broadcast_date": r["broadcast_date"],
                    "payment_date": r["payment_date"],
                    "day_index": None,
                    "daily_volume": float(r["order_count"]),
                } for _, r in allocation_df.iterrows()]
                presale_model_obj = PresaleDistributionModel().fit(events)
                log(
                    f"预售量已按实际 event 渠道剥离：{allocation_df['order_count'].sum():.0f} 单；"
                    f"未匹配/超出渠道实绩：{unresolved_qty:.0f} 单"
                )
            else:
                log("预售历史无法匹配到实际 event 渠道，未从训练目标扣减", "warning")
        else:
            log("presale_history 表为空，跳过预售分布模型训练", "warning")

        schedule_df, inferred_live_days = _enrich_schedule_with_anchor_history(
            schedule_df=schedule_df,
            anchor_df=anchor_df,
            channel_df=channel_actual_df,
            channel_type_map=config_dict.get("channel_type_map", {}),
            history_end=channel_actual_df["date"].max(),
        )
        if inferred_live_days > 0:
            log(
                f"达人历史已关联到主直播渠道：补充 {inferred_live_days} 个历史直播日，"
                "用于学习达人量级特征。"
            )
        if anchor_df is not None and len(anchor_df) > 0:
            from src.features.channel_features import prepare_anchor_history_stats
            anchor_df = prepare_anchor_history_stats(anchor_df)
            log(f"达人历史统计已预计算：{len(anchor_df)} 条，后续预测与回测直接复用。")

        progress(10)

        # 构建完整配置
        from config.routing_config import load_from_dict
        full_config = {**config_dict}
        if request.global_model_params:
            full_config["global_model_params"] = request.global_model_params
        if request.channel_model_params:
            full_config["channel_model_params"] = request.channel_model_params
        if request.training_config:
            full_config["training_config"] = request.training_config

        # 初始化 pipeline
        sys_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        model_dir = os.path.join(sys_root, "ecom_forecast", "models") if os.path.basename(sys_root) != "ecom_forecast" \
            else os.path.join(sys_root, "models")

        from src.pipeline.forecast_pipeline import ForecastPipeline
        pipeline = ForecastPipeline(model_dir=model_dir, config_dict=full_config)

        phases = request.phases
        restored_phases = pipeline.restore_untrained_components(phases)
        if restored_phases:
            log(
                "已加载本次未训练的历史模型组件："
                + "、".join(restored_phases)
                + "；本次训练不会覆盖这些组件。"
            )

        # Phase 1
        if "global" in phases:
            log("[Phase 1] 开始训练全局基础模型...")
            pipeline.train_global(channel_df)
            log("[Phase 1] 全局模型训练完成。")
            progress(30)

        # Phase 2
        if "channel" in phases:
            log("[Phase 2] 开始训练渠道子模型...")
            pipeline.train_channels(
                channel_df, schedule_df=schedule_df,
                commission_df=commission_df, anchor_df=anchor_df,
            )
            log("[Phase 2] 渠道模型训练完成。")
            anchor_model = pipeline.channel_ensemble.anchor_live_model
            if anchor_model.is_fitted:
                log(
                    "[Phase 2A] 达人直播模型训练完成："
                    f"达人={len(anchor_model.anchor_stats)}，"
                    f"达人×店铺={len(anchor_model.anchor_shop_stats)}，"
                    f"排期目标校准系数={anchor_model.target_factor:.3f}，"
                    f"目标融合权重={anchor_model.target_weight:.2f}，"
                    f"近期权重={anchor_model.recent_weight:.2f}，"
                    f"分层收缩={anchor_model.shrinkage:.1f}。"
                )
            else:
                log("[Phase 2A] 跳过达人直播模型（无有效达人实绩）。", "warning")

            # 注入预售分布模型到 event 渠道子模型
            if presale_model_obj is not None:
                try:
                    pipeline.channel_ensemble.models["event"].presale_model = presale_model_obj
                    log("预售分布模型已注入 event 渠道子模型（加法模式：PS_pred + LGB_pred）")
                except Exception as _e:
                    log(f"注入预售模型失败（非阻断性）: {_e}", "warning")

            progress(60)

        # Phase 3
        if "routing" in phases and len(warehouse_df) > 0:
            log("[Phase 3] 开始学习分仓路由比例...")
            pipeline.train_routing(warehouse_df)
            log("[Phase 3] 路由训练完成。")
        elif "routing" in phases:
            log("[Phase 3] 跳过路由训练（无仓库数据）", "warning")
        progress(75)

        # 先用完全独立的最近 N 天做外层回测，再决定正式未来预测是否采用校准。
        # 逐渠道回退保证校准后的整体 WAPE 不会比原始预测更差。
        if "channel" in phases:
            log(f"正在进行 holdout {request.holdout_days} 天评估并检查校准安全阀...")
            try:
                eval_pipeline, ch_eval, wh_eval, merged = _evaluate_holdout_before_forecast(
                    model_dir=model_dir,
                    full_config=full_config,
                    holdout_days=request.holdout_days,
                    channel_df=channel_df,
                    channel_actual_df=channel_actual_df,
                    schedule_df=schedule_df,
                    commission_df=commission_df,
                    anchor_df=anchor_df,
                    resolved_presale_df=resolved_presale_df,
                    warehouse_df=warehouse_df,
                    config_dict=config_dict,
                    progress_callback=lambda done, total: progress(
                        75 + int(7 * done / max(total, 1))
                    ),
                )
                fallback_channels = getattr(
                    eval_pipeline, "calibration_fallback_channels", []
                )
                fallback_details = getattr(
                    eval_pipeline, "calibration_guardrail_details", []
                )
                pipeline.channel_ensemble.set_raw_fallback_channels(fallback_channels)
                if fallback_details:
                    detail_text = "、".join(
                        f"{item['channel']}（{item['calibrated_wape']:.1f}%→"
                        f"{item['fallback_wape']:.1f}%）"
                        for item in fallback_details
                        if item.get("fallback_wape") is not None
                        and item.get("calibrated_wape") is not None
                    )
                    log(
                        f"校准安全阀已回退 {len(fallback_channels)} 个渠道到未校准稳定基线："
                        f"{detail_text}",
                        "warning",
                    )
                else:
                    log("校准安全阀检查通过：所有渠道均未劣于未校准稳定基线。")

                anchor_metrics = getattr(eval_pipeline, "anchor_holdout_metrics", {})
                _save_evaluation_metrics(job_id, ch_eval, anchor_metrics)
                backtest_count = _save_backtest_daily_results(
                    job_id,
                    merged,
                    getattr(eval_pipeline, "warehouse_holdout_merged", None),
                )
                log(f"历史回测明细已保存，共 {backtest_count} 条日级结果。")
                if anchor_metrics:
                    target_wape = anchor_metrics.get("target_wape")
                    target_bias = anchor_metrics.get("target_bias")
                    baseline_text = (
                        f"飞书目标 WAPE={target_wape * 100:.1f}% / "
                        f"Bias={target_bias * 100:.1f}% → "
                        if target_wape is not None and target_bias is not None else ""
                    )
                    log(
                        "达人直播子模型回测："
                        f"{baseline_text}模型 WAPE={anchor_metrics['wape'] * 100:.1f}% / "
                        f"Bias={anchor_metrics['bias'] * 100:.1f}%，"
                        f"场次={int(anchor_metrics['n'])}"
                    )
                overall = ch_eval[ch_eval["segment"].eq("全局")]
                if len(overall) > 0:
                    overall_row = overall.iloc[0]
                    log(
                        f"评估完成：原始 WAPE="
                        f"{overall_row.get('raw_wape', np.nan):.1f}% → "
                        f"安全校准后={overall_row['wape']:.1f}%；"
                        f"原始 Bias={overall_row.get('raw_bias_pct', np.nan):.1f}% → "
                        f"校准后={overall_row['bias_pct']:.1f}%  "
                        f"样本={int(overall_row['n_samples'])}  "
                        f"实际总量={overall_row['total_actual']:.0f}"
                    )
            except Exception as e:
                # 回测失败时保守禁用渠道倍数校准，但保留稳定滚动基线。
                all_channels = channel_df["channel"].dropna().astype(str).unique()
                pipeline.channel_ensemble.set_raw_fallback_channels(all_channels)
                log(
                    f"评估阶段警告：{e}；为避免负向优化，本次未来预测已全部回退未校准稳定基线。",
                    "warning",
                )
        progress(82)

        # 保存模型
        log("正在保存模型...")
        pipeline.save(phases=phases)
        progress(83)

        # 生成预测（滚动自回归 + 偏差修正，防止预测值级联衰减）
        log(f"正在生成未来 {request.forecast_days} 天预测（滚动自回归+偏差修正模式）...")
        future_dates = pd.date_range(data_end_date + pd.Timedelta(days=1), periods=request.forecast_days)
        sp_df = channel_df[["shop_id", "channel", "platform"]].drop_duplicates()

        # 禁用原先基于“模型已经见过的最近14天”计算的 0.3~3.0 倍修正。
        # 该做法属于样本内校准，会掩盖泛化误差；递归 lag 已改为逐日写回。
        correction_scale: dict = {}
        log("  已启用逐日递归预测；禁用样本内偏差倍数修正")

        schedule_audit = _audit_future_schedule(
            schedule_df, anchor_df, future_dates.min(), future_dates.max()
        )
        log(
            f"排期生效检查：预测窗口 {future_dates.min():%Y-%m-%d} ~ "
            f"{future_dates.max():%Y-%m-%d}，命中 {schedule_audit['in_window']} 条"
            f"（直播 {schedule_audit['live']} / 预售尾款 {schedule_audit['presale']}）"
        )
        if schedule_audit["total"] > 0 and schedule_audit["in_window"] == 0:
            log("当前预测窗口内没有可生效的直播/预售排期，本次预测不会因排期变化。", "warning")
        if schedule_audit["after_window"] > 0:
            log(
                f"有 {schedule_audit['after_window']} 条排期晚于预测截止日，"
                "本轮不生效；请增大预测天数。",
                "warning",
            )
        if anchor_df is not None and len(anchor_df) > 0 and schedule_audit["live"] == 0:
            log(
                "已加载达人历史，但预测窗口内没有 event_type=live 的达人排期；"
                "达人历史不会自动生成未来直播。",
                "warning",
            )
        if schedule_audit["live_with_anchor"] > schedule_audit["matched_anchor"]:
            names = "、".join(schedule_audit["unmatched_anchor_names"]) or "未填写"
            log(
                f"有 {schedule_audit['live_with_anchor'] - schedule_audit['matched_anchor']} 条未来直播"
                f"未匹配到同店铺的达人历史（示例：{names}），将仅使用档位特征。",
                "warning",
            )

        CHUNK_SIZE = 1          # 逐日预测并立即写回，保证 lag1 使用上一日预测
        rolling_hist = channel_df.copy()
        all_pred_chunks = []
        last_forecast_progress = 83

        for i in range(0, len(future_dates), CHUNK_SIZE):
            chunk_dates = future_dates[i: i + CHUNK_SIZE]
            chunk_rows = []
            for dt in chunk_dates:
                for _, row in sp_df.iterrows():
                    chunk_rows.append({"date": dt, "shop_id": row["shop_id"],
                                       "channel": row["channel"], "platform": row["platform"],
                                       "order_count": np.nan})
            chunk_future = pd.DataFrame(chunk_rows)

            pred_chunk = pipeline.predict_channels(
                chunk_future, rolling_hist,
                schedule_df=schedule_df, commission_df=commission_df,
                anchor_df=anchor_df,
            )

            # ── 应用偏差修正到输出预测 ─────────────────────────────────────
            if correction_scale:
                pred_chunk["_scale"] = pred_chunk.apply(
                    lambda r: correction_scale.get(
                        (r["shop_id"], r["channel"]),
                        correction_scale.get(("", r["channel"]), 1.0),
                    ),
                    axis=1,
                )
                pred_chunk["predicted_orders"] = (
                    pred_chunk["predicted_orders"] * pred_chunk["_scale"]
                ).clip(lower=0)
                for component in ("base_predicted_orders", "anchor_predicted_orders"):
                    if component in pred_chunk.columns:
                        pred_chunk[component] = (
                            pred_chunk[component] * pred_chunk["_scale"]
                        ).clip(lower=0)
                pred_chunk = pred_chunk.drop(columns=["_scale"])

            all_pred_chunks.append(pred_chunk)

            # ── 将修正后预测值写入伪历史（锚定到真实水平，防止级联衰减）──────
            merge_keys = ["date", "shop_id", "channel"]
            if "platform" in chunk_future.columns and "platform" in pred_chunk.columns:
                merge_keys.append("platform")
            # 达人直播是独立增量，只把不含达人加单的基础预测写回递归历史。
            # 否则一场直播会通过 lag/rolling 继续抬高后续多天的基础量。
            recursive_col = (
                "base_predicted_orders"
                if "base_predicted_orders" in pred_chunk.columns
                else "predicted_orders"
            )
            recursive_values = pred_chunk[merge_keys + [recursive_col]].rename(
                columns={recursive_col: "_recursive_orders"}
            )
            pseudo_hist = chunk_future.merge(
                recursive_values,
                on=merge_keys, how="left", validate="one_to_one",
            )
            pseudo_hist["order_count"] = pseudo_hist["_recursive_orders"].fillna(0)
            pseudo_hist = pseudo_hist.drop(columns=["_recursive_orders"])
            rolling_hist = pd.concat([rolling_hist, pseudo_hist], ignore_index=True)

            forecast_progress = 83 + int(
                8 * min(i + len(chunk_dates), len(future_dates)) / len(future_dates)
            )
            if forecast_progress > last_forecast_progress:
                progress(forecast_progress)
                last_forecast_progress = forecast_progress

        pred_df = pd.concat(all_pred_chunks, ignore_index=True) if all_pred_chunks else pd.DataFrame()
        if "anchor_predicted_orders" in pred_df.columns:
            anchor_total = float(
                pd.to_numeric(pred_df["anchor_predicted_orders"], errors="coerce")
                .fillna(0).sum()
            )
            anchor_days = int(
                pred_df.loc[
                    pd.to_numeric(pred_df["anchor_predicted_orders"], errors="coerce") > 0,
                    "date",
                ].nunique()
            )
            log(
                f"达人直播模型已加入未来预测："
                f"共 {anchor_total:.0f} 单，覆盖 {anchor_days} 个直播日。"
            )
        if not manual_channel_meta.empty:
            manual_rows = _build_manual_channel_placeholders(
                pred_df, manual_channel_meta, future_dates
            )
            if len(manual_rows) > 0:
                pred_df = pd.concat([pred_df, manual_rows], ignore_index=True)
            log(f"已生成 {len(manual_rows)} 条人工排期占位，模型值均为 0。")
        progress(91)

        # 保存预测结果
        db = SessionLocal()
        try:
            pred_objs = []
            for _, row in pred_df.iterrows():
                pred_objs.append(models.Prediction(
                    job_id=job_id,
                    date=row["date"].date() if hasattr(row["date"], "date") else row["date"],
                    shop_id=str(row.get("shop_id", "")),
                    channel=str(row.get("channel", "")),
                    platform=str(row.get("platform", "")) or None,
                    predicted_orders=float(row.get("predicted_orders", 0) or 0),
                    raw_predicted_orders=float(row.get("raw_predicted_orders", 0) or 0),
                    base_predicted_orders=float(row.get("base_predicted_orders", 0) or 0),
                    anchor_predicted_orders=float(row.get("anchor_predicted_orders", 0) or 0),
                ))
            db.bulk_save_objects(pred_objs)
            db.commit()
        finally:
            db.close()
        log(f"预测结果已保存，共 {len(pred_df)} 条。")
        progress(94)

        # 生成并保存分仓预测
        try:
            log("正在生成分仓预测...")
            wh_pred_df = pipeline.predict_warehouse(pred_df)
            if wh_pred_df is not None and len(wh_pred_df) > 0:
                # 按 (date, shop_id, channel, warehouse_id) 聚合；当前不区分派样。
                wh_pred_df["date_key"] = wh_pred_df["date"].apply(
                    lambda d: d.date() if hasattr(d, "date") else d
                )
                agg_rows = (
                    wh_pred_df
                    .groupby(["date_key", "shop_id", "channel", "warehouse_id"])
                    .apply(lambda g: pd.Series({
                        "warehouse_orders": g["warehouse_orders"].sum(),
                        "sample_orders": 0.0,
                    }))
                    .reset_index()
                )
                db = SessionLocal()
                try:
                    wh_objs = []
                    for _, row in agg_rows.iterrows():
                        wh_objs.append(models.WarehousePrediction(
                            job_id=job_id,
                            date=row["date_key"],
                            shop_id=str(row["shop_id"]),
                            channel=str(row["channel"]) if row["channel"] else None,
                            warehouse_id=str(row["warehouse_id"]),
                            warehouse_orders=float(row["warehouse_orders"] or 0),
                            sample_orders=float(row["sample_orders"] or 0),
                        ))
                    db.bulk_save_objects(wh_objs)
                    db.commit()
                finally:
                    db.close()
                log(f"分仓预测已保存，共 {len(agg_rows)} 条。")
            else:
                log("分仓预测为空（路由器无可用仓库配置），跳过保存。", "warning")
        except Exception as e:
            log(f"分仓预测生成警告：{e}", "warning")

        # Holdout 已在正式预测前完成，使校准安全阀直接作用于本次未来结果。

        progress(100)
        _update_job(job_id, status="done", finished_at=datetime.now(), progress=100)
        log(f"[{datetime.now():%H:%M:%S}] 训练任务完成！")
        _broadcast(job_id, {"type": "done", "data": {"job_id": job_id}})

    except Exception as e:
        import traceback
        err_msg = f"训练失败：{e}\n{traceback.format_exc()}"
        _db_log(job_id, err_msg, "error")
        _update_job(job_id, status="error", error_msg=str(e), finished_at=datetime.now())
        _broadcast(job_id, {"type": "error", "data": str(e)})


# ──────────────────────────────────────────────
# REST 端点
# ──────────────────────────────────────────────

@router.post("/start", response_model=dict, status_code=201)
def start_training(request: schemas.TrainingStartRequest, db: Session = Depends(get_db)):
    """启动训练任务，返回 job_id"""
    latest = db.query(models.TrainingJob).filter(models.TrainingJob.status == "done").order_by(
        models.TrainingJob.finished_at.desc(), models.TrainingJob.id.desc()
    ).first()
    previous_source = ((latest.config_snapshot or {}).get("training_data_source") or {}) if latest else {}
    compatible_source = previous_source.get("mode") == "direct" and previous_source.get("excluded_order_statuses") == query_source.TRAINING_EXCLUDED_ORDER_STATUSES
    if not compatible_source and not {"global", "channel", "routing"}.issubset(request.phases):
        raise HTTPException(400, "数据源或订单状态口径已更新，首次请完整训练全局、渠道和分仓三个阶段，再复用已有模型。")
    # 新增来源维度只写本地配置库；远端账号始终只读。
    query_source.ensure_dimensions(db)
    from api.routers.config import export_config
    config_dict = export_config(db)
    if hasattr(config_dict, "model_dump"):
        config_dict = config_dict.model_dump()
    config_dict["training_data_source"] = {
        "mode": "direct", "tables": query_source.TABLES,
        "excluded_order_statuses": list(query_source.TRAINING_EXCLUDED_ORDER_STATUSES),
        "requested_at": datetime.now().isoformat(timespec="seconds"),
        "anchor_scope": "anchor_only", "routing_scope": "global",
    }

    job_id = str(uuid.uuid4())
    job = models.TrainingJob(
        job_id=job_id,
        status="pending",
        phases=request.phases,
        config_snapshot=config_dict,
        progress=0,
    )
    db.add(job)
    db.commit()

    # 在后台线程执行训练
    t = threading.Thread(
        target=_run_training,
        args=(job_id, request, config_dict),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "status": "pending"}


@router.get("/jobs", response_model=List[schemas.TrainingJobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(models.TrainingJob).order_by(models.TrainingJob.created_at.desc()).limit(20).all()


@router.get("/jobs/{job_id}", response_model=schemas.TrainingJobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(models.TrainingJob).filter(models.TrainingJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/jobs/{job_id}/logs", response_model=List[schemas.JobLogOut])
def get_job_logs(job_id: str, db: Session = Depends(get_db)):
    return db.query(models.JobLog).filter(models.JobLog.job_id == job_id).order_by(models.JobLog.created_at).all()


# ──────────────────────────────────────────────
# WebSocket 实时进度
# ──────────────────────────────────────────────

@router.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    # 注册连接
    if job_id not in _ws_connections:
        _ws_connections[job_id] = []
    # 存储 loop 供线程回调使用
    websocket._loop = asyncio.get_event_loop()
    _ws_connections[job_id].append(websocket)

    try:
        # 先发送当前任务状态（历史日志）
        db = SessionLocal()
        try:
            logs = db.query(models.JobLog).filter(
                models.JobLog.job_id == job_id
            ).order_by(models.JobLog.created_at).all()
            for lg in logs:
                await websocket.send_text(json.dumps(
                    {"type": "log", "data": lg.log_text}, ensure_ascii=False
                ))
            job = db.query(models.TrainingJob).filter(models.TrainingJob.job_id == job_id).first()
            if job:
                await websocket.send_text(json.dumps(
                    {"type": "progress", "data": job.progress}, ensure_ascii=False
                ))
        finally:
            db.close()

        # 保持连接直到断开
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}, ensure_ascii=False))

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if job_id in _ws_connections:
            try:
                _ws_connections[job_id].remove(websocket)
            except ValueError:
                pass
