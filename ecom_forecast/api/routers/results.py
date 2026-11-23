"""
结果路由：预测结果查询、评估指标、Excel 导出
"""
import io
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import ORJSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from api.database import get_db
from api import models, schemas

router = APIRouter()

_FORECAST_SHEET_CACHE_LOCK = threading.Lock()
_FORECAST_SHEET_CACHE: dict[str, object] = {"job_id": None, "data": None}


def _get_cached_forecast_sheet(job_id: str):
    with _FORECAST_SHEET_CACHE_LOCK:
        if _FORECAST_SHEET_CACHE["job_id"] == job_id:
            return _FORECAST_SHEET_CACHE["data"]
    return None


def _set_cached_forecast_sheet(job_id: str, data: dict) -> None:
    # 只保留一份最近读取的完整表，避免历史任务占用过多内存。
    with _FORECAST_SHEET_CACHE_LOCK:
        _FORECAST_SHEET_CACHE["job_id"] = job_id
        _FORECAST_SHEET_CACHE["data"] = data


def _invalidate_forecast_sheet_cache(job_id: str) -> None:
    with _FORECAST_SHEET_CACHE_LOCK:
        if _FORECAST_SHEET_CACHE["job_id"] == job_id:
            _FORECAST_SHEET_CACHE["job_id"] = None
            _FORECAST_SHEET_CACHE["data"] = None


def _effective_orders_expression():
    """人工值优先；未调整时继续使用模型校准值。"""
    return func.coalesce(
        models.Prediction.manual_predicted_orders,
        models.Prediction.predicted_orders,
        0,
    )


def _latest_completed_job(db: Session):
    """只取结果接口真正需要的字段，避免反复加载较大的配置快照。"""
    return (
        db.query(
            models.TrainingJob.job_id,
            models.TrainingJob.finished_at,
        )
        .filter(models.TrainingJob.status == "done")
        .order_by(models.TrainingJob.finished_at.desc())
        .first()
    )


@router.get("/predictions", response_model=List[schemas.PredictionOut])
def get_predictions(
    job_id: Optional[str] = Query(None),
    shop_id: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """查询渠道预测结果"""
    q = db.query(models.Prediction)
    if job_id:
        q = q.filter(models.Prediction.job_id == job_id)
    if shop_id:
        q = q.filter(models.Prediction.shop_id == shop_id)
    if channel:
        q = q.filter(models.Prediction.channel == channel)
    return q.order_by(models.Prediction.date).limit(5000).all()


@router.get("/predictions/latest")
def get_latest_predictions(
    shop_id: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    view: Literal["trend", "detail"] = Query("trend"),
    keyword: Optional[str] = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """获取最新结果。趋势由数据库聚合，明细使用服务端分页。"""
    latest_job = _latest_completed_job(db)
    if not latest_job:
        return {
            "job_id": None, "predictions": [], "channels": [],
            "total": 0, "page": page, "page_size": page_size,
        }

    filters = [models.Prediction.job_id == latest_job.job_id]
    if shop_id:
        filters.append(models.Prediction.shop_id == shop_id)
    if channel:
        filters.append(models.Prediction.channel == channel)

    if view == "trend":
        channels = [
            row[0]
            for row in (
                db.query(models.Prediction.channel)
                .filter(models.Prediction.job_id == latest_job.job_id)
                .distinct()
                .order_by(models.Prediction.channel)
                .all()
            )
        ]
        rows = (
            db.query(
                models.Prediction.date,
                models.Prediction.channel,
                func.sum(_effective_orders_expression()).label("predicted_orders"),
                func.sum(models.Prediction.raw_predicted_orders).label("raw_predicted_orders"),
            )
            .filter(*filters)
            .group_by(models.Prediction.date, models.Prediction.channel)
            .order_by(models.Prediction.date, models.Prediction.channel)
            .all()
        )
        predictions = [
            {
                "date": str(row.date),
                "shop_id": None,
                "channel": row.channel,
                "platform": None,
                "predicted_orders": float(row.predicted_orders or 0),
                "raw_predicted_orders": (
                    float(row.raw_predicted_orders)
                    if row.raw_predicted_orders is not None else None
                ),
            }
            for row in rows
        ]
        return {
            "job_id": latest_job.job_id,
            "finished_at": latest_job.finished_at,
            "predictions": predictions,
            "channels": channels,
            "total": len(predictions),
            "page": 1,
            "page_size": len(predictions),
        }

    if keyword:
        escaped = keyword.replace("%", r"\%").replace("_", r"\_")
        like_value = f"%{escaped}%"
        filters.append(or_(
            models.Prediction.shop_id.like(like_value, escape="\\"),
            models.Prediction.channel.like(like_value, escape="\\"),
            models.Prediction.platform.like(like_value, escape="\\"),
        ))

    base = db.query(models.Prediction).filter(*filters)
    total = base.count()
    preds = (
        base.order_by(
            models.Prediction.date,
            models.Prediction.shop_id,
            models.Prediction.channel,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "job_id": latest_job.job_id,
        "finished_at": latest_job.finished_at,
        "total": total,
        "page": page,
        "page_size": page_size,
        "predictions": [
            {
                "date": str(p.date),
                "shop_id": p.shop_id,
                "channel": p.channel,
                "platform": p.platform,
                "predicted_orders": (
                    p.manual_predicted_orders
                    if p.manual_predicted_orders is not None else p.predicted_orders
                ),
                "model_predicted_orders": p.predicted_orders,
                "raw_predicted_orders": p.raw_predicted_orders,
                "base_predicted_orders": p.base_predicted_orders,
                "anchor_predicted_orders": p.anchor_predicted_orders,
                "manual_predicted_orders": p.manual_predicted_orders,
                "adjustment_note": p.adjustment_note,
                "adjusted_at": p.adjusted_at,
            }
            for p in preds
        ],
    }


def get_forecast_sheet(
    job_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, max_length=64),
    platform: Optional[str] = Query(None, max_length=64),
    channel: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(get_db),
):
    """返回可编辑预测排期表的扁平单元格数据，由前端按日期透视。"""
    if not job_id:
        latest = _latest_completed_job(db)
        if not latest:
            return {
                "job_id": None, "finished_at": None, "dates": [], "records": [],
                "platforms": [], "channels": [], "adjusted_count": 0,
            }
        job_id = latest.job_id
        finished_at = latest.finished_at
    else:
        job = db.query(
            models.TrainingJob.job_id,
            models.TrainingJob.finished_at,
        ).filter(
            models.TrainingJob.job_id == job_id,
            models.TrainingJob.status == "done",
        ).first()
        if not job:
            raise HTTPException(404, "预测任务不存在或尚未完成")
        finished_at = job.finished_at

    cacheable = not keyword and not platform and not channel
    if cacheable:
        cached = _get_cached_forecast_sheet(job_id)
        if cached is not None:
            return cached

    filters = [models.Prediction.job_id == job_id]
    if platform:
        filters.append(models.Prediction.platform == platform)
    if channel:
        filters.append(models.Prediction.channel == channel)
    if keyword:
        escaped = keyword.replace("%", r"\%").replace("_", r"\_")
        like_value = f"%{escaped}%"
        filters.append(or_(
            models.Prediction.shop_id.like(like_value, escape="\\"),
            models.Prediction.channel.like(like_value, escape="\\"),
            models.Prediction.platform.like(like_value, escape="\\"),
        ))

    predictions = (
        db.query(
            models.Prediction.id,
            models.Prediction.date,
            models.Prediction.shop_id,
            models.Prediction.channel,
            models.Prediction.platform,
            models.Prediction.predicted_orders,
            models.Prediction.raw_predicted_orders,
            models.Prediction.base_predicted_orders,
            models.Prediction.anchor_predicted_orders,
            models.Prediction.manual_predicted_orders,
            models.Prediction.adjustment_note,
            models.Prediction.adjusted_at,
        )
        .filter(*filters)
        .order_by(
            models.Prediction.platform,
            models.Prediction.shop_id,
            models.Prediction.channel,
            models.Prediction.date,
        )
        .all()
    )
    config_snapshot = db.query(models.TrainingJob.config_snapshot).filter(
        models.TrainingJob.job_id == job_id
    ).scalar() or {}
    platform_names = {}
    channel_names = {}
    for platform_item in config_snapshot.get("platforms", []):
        platform_id = str(platform_item.get("platform_id") or "").strip()
        platform_key = platform_id.casefold()
        if platform_key:
            platform_names[platform_key] = str(
                platform_item.get("name") or platform_id
            ).strip()
        for channel_item in platform_item.get("channels", []):
            channel_id = str(channel_item.get("channel_id") or "").strip()
            channel_name = str(channel_item.get("name") or channel_id).strip()
            for alias in (channel_id, channel_name):
                alias_key = alias.casefold()
                if alias_key:
                    channel_names[(platform_key, alias_key)] = channel_name
    shop_names = {
        str(item.get("shop_id") or "").strip(): str(
            item.get("name") or item.get("shop_id") or ""
        ).strip()
        for item in config_snapshot.get("shops", [])
        if item.get("shop_id")
    }
    excluded_keys = set()
    for item in config_snapshot.get("excluded_channels", []):
        platform_key = str(item.get("platform_id") or "").strip().casefold()
        for value in (item.get("channel_id"), item.get("channel_name")):
            channel_key = str(value or "").strip().casefold()
            if channel_key:
                excluded_keys.add((platform_key, channel_key))
    dates = sorted({str(row.date) for row in predictions})
    records = []
    adjusted_count = 0
    for row in predictions:
        is_adjusted = row.manual_predicted_orders is not None
        if is_adjusted:
            adjusted_count += 1
        platform_key = str(row.platform or "").strip().casefold()
        channel_key = str(row.channel or "").strip().casefold()
        records.append({
            "id": row.id,
            "date": str(row.date),
            "shop_id": row.shop_id,
            "shop_name": shop_names.get(row.shop_id, row.shop_id),
            "channel": row.channel,
            "channel_name": channel_names.get(
                (platform_key, channel_key), row.channel
            ),
            "platform": row.platform,
            "platform_name": platform_names.get(platform_key, row.platform),
            "include_in_forecast": (
                (platform_key, channel_key) not in excluded_keys
            ),
            "model_predicted_orders": row.predicted_orders,
            "raw_predicted_orders": row.raw_predicted_orders,
            "base_predicted_orders": row.base_predicted_orders,
            "anchor_predicted_orders": row.anchor_predicted_orders,
            "manual_predicted_orders": row.manual_predicted_orders,
            "effective_predicted_orders": (
                row.manual_predicted_orders
                if is_adjusted else row.predicted_orders
            ),
            "adjustment_note": row.adjustment_note,
            "adjusted_at": row.adjusted_at,
        })

    result = {
        "job_id": job_id,
        "finished_at": finished_at,
        "dates": dates,
        "records": records,
        "platforms": sorted({row.platform for row in predictions if row.platform}),
        "channels": sorted({row.channel for row in predictions if row.channel}),
        "adjusted_count": adjusted_count,
    }
    if cacheable:
        _set_cached_forecast_sheet(job_id, result)
    return result


@router.get("/forecast-sheet", response_class=ORJSONResponse)
def get_forecast_sheet_response(
    job_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, max_length=64),
    platform: Optional[str] = Query(None, max_length=64),
    channel: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(get_db),
):
    """使用预序列化响应返回大型排期表，避免 FastAPI 再递归遍历整份结果。"""
    data = get_forecast_sheet(
        job_id=job_id,
        keyword=keyword,
        platform=platform,
        channel=channel,
        db=db,
    )
    return ORJSONResponse(data)


@router.patch("/forecast-sheet")
def update_forecast_sheet(
    payload: schemas.ForecastSheetUpdate,
    db: Session = Depends(get_db),
):
    """批量保存人工预测值；传 null 可恢复对应单元格的模型值。"""
    if not payload.edits:
        return {"updated": 0, "adjusted_count": 0}
    if len(payload.edits) > 20_000:
        raise HTTPException(400, "单次最多保存 20000 个单元格")

    requested_ids = [edit.prediction_id for edit in payload.edits]
    if len(requested_ids) != len(set(requested_ids)):
        raise HTTPException(400, "同一个预测单元格不能重复提交")

    predictions = db.query(models.Prediction).filter(
        models.Prediction.job_id == payload.job_id,
        models.Prediction.id.in_(requested_ids),
    ).all()
    prediction_map = {row.id: row for row in predictions}
    missing_ids = sorted(set(requested_ids) - set(prediction_map))
    if missing_ids:
        raise HTTPException(404, f"有 {len(missing_ids)} 个预测单元格不存在或不属于该任务")

    # 同步缩放已有分仓结果，保证人工调整后“渠道合计”和“分仓合计”仍一致。
    warehouse_rows = db.query(models.WarehousePrediction).filter(
        models.WarehousePrediction.job_id == payload.job_id,
    ).all()
    warehouse_map = {}
    for warehouse_row in warehouse_rows:
        key = (
            warehouse_row.date,
            warehouse_row.shop_id,
            warehouse_row.channel or "",
        )
        warehouse_map.setdefault(key, []).append(warehouse_row)

    shops = {
        shop.shop_id: shop
        for shop in db.query(models.Shop).filter(
            models.Shop.shop_id.in_({row.shop_id for row in predictions})
        ).all()
    }
    active_warehouses = [
        warehouse.warehouse_id
        for warehouse in db.query(models.Warehouse).filter(
            models.Warehouse.is_active.is_(True)
        ).all()
    ]

    now = datetime.now()
    for edit in payload.edits:
        row = prediction_map[edit.prediction_id]
        if edit.manual_predicted_orders is None:
            row.manual_predicted_orders = None
            row.adjustment_note = None
            row.adjusted_at = None
        else:
            row.manual_predicted_orders = round(edit.manual_predicted_orders)
            row.adjustment_note = edit.adjustment_note
            row.adjusted_at = now

        target_orders = (
            row.manual_predicted_orders
            if row.manual_predicted_orders is not None else (row.predicted_orders or 0)
        )
        matching_warehouses = warehouse_map.get(
            (row.date, row.shop_id, row.channel or ""),
            [],
        )
        if not matching_warehouses and target_orders > 0:
            shop = shops.get(row.shop_id)
            allocations = {}
            if shop is not None and shop.binding_type == "single" and shop.warehouse_id:
                allocations = {shop.warehouse_id: 1.0}
            else:
                shop_history = [item for item in warehouse_rows if item.shop_id == row.shop_id]
                totals = {}
                for item in shop_history:
                    totals[item.warehouse_id] = totals.get(item.warehouse_id, 0) + float(
                        item.warehouse_orders or 0
                    )
                total = sum(totals.values())
                if total > 0:
                    allocations = {warehouse_id: value / total for warehouse_id, value in totals.items()}
                elif active_warehouses:
                    allocations = {
                        warehouse_id: 1 / len(active_warehouses)
                        for warehouse_id in active_warehouses
                    }
            for warehouse_id, ratio in allocations.items():
                warehouse_row = models.WarehousePrediction(
                    job_id=payload.job_id,
                    date=row.date,
                    shop_id=row.shop_id,
                    channel=row.channel,
                    warehouse_id=warehouse_id,
                    warehouse_orders=target_orders * ratio,
                    sample_orders=0,
                )
                db.add(warehouse_row)
                warehouse_rows.append(warehouse_row)
                matching_warehouses.append(warehouse_row)
            if matching_warehouses:
                warehouse_map[(row.date, row.shop_id, row.channel or "")] = matching_warehouses
        if matching_warehouses:
            current_total = sum(item.warehouse_orders or 0 for item in matching_warehouses)
            if current_total > 0:
                scale = target_orders / current_total
                for item in matching_warehouses:
                    item.warehouse_orders = (item.warehouse_orders or 0) * scale
                    item.sample_orders = 0
            else:
                per_warehouse = target_orders / len(matching_warehouses)
                for item in matching_warehouses:
                    item.warehouse_orders = per_warehouse
                    item.sample_orders = 0
    db.commit()
    _invalidate_forecast_sheet_cache(payload.job_id)

    adjusted_count = db.query(func.count(models.Prediction.id)).filter(
        models.Prediction.job_id == payload.job_id,
        models.Prediction.manual_predicted_orders.isnot(None),
    ).scalar() or 0
    return {"updated": len(payload.edits), "adjusted_count": adjusted_count}


@router.get("/warehouse-predictions", response_model=List[schemas.WarehousePredictionOut])
def get_warehouse_predictions(
    job_id: Optional[str] = Query(None),
    warehouse_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.WarehousePrediction)
    if job_id:
        q = q.filter(models.WarehousePrediction.job_id == job_id)
    if warehouse_id:
        q = q.filter(models.WarehousePrediction.warehouse_id == warehouse_id)
    return q.order_by(models.WarehousePrediction.date).limit(5000).all()


@router.get("/metrics", response_model=List[schemas.EvaluationMetricOut])
def get_metrics(
    job_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """查询评估指标"""
    if not job_id:
        # 默认取最新完成任务
        latest = _latest_completed_job(db)
        if not latest:
            return []
        job_id = latest.job_id

    q = db.query(models.EvaluationMetric).filter(models.EvaluationMetric.job_id == job_id)
    if level:
        q = q.filter(models.EvaluationMetric.level == level)
    return q.all()


@router.get("/summary")
def get_results_summary(db: Session = Depends(get_db)):
    """结果页 KPI 摘要"""
    latest = _latest_completed_job(db)
    if not latest:
        return {"has_results": False}

    # 总预测单量
    total_pred = db.query(func.sum(_effective_orders_expression())).filter(
        models.Prediction.job_id == latest.job_id
    ).scalar() or 0
    total_base = db.query(func.sum(models.Prediction.base_predicted_orders)).filter(
        models.Prediction.job_id == latest.job_id
    ).scalar() or 0
    total_anchor = db.query(func.sum(models.Prediction.anchor_predicted_orders)).filter(
        models.Prediction.job_id == latest.job_id
    ).scalar() or 0

    # 预测天数
    pred_days = db.query(func.count(func.distinct(models.Prediction.date))).filter(
        models.Prediction.job_id == latest.job_id
    ).scalar() or 0

    # 优先使用全部 holdout 样本一次计算的整体指标。
    # 不再算“渠道 WAPE 算术平均”，否则一个小渠道会和主力渠道拥有相同权重。
    overall = db.query(models.EvaluationMetric).filter(
        models.EvaluationMetric.job_id == latest.job_id,
        models.EvaluationMetric.level == "overall",
        models.EvaluationMetric.group_key == "all",
    ).first()
    channel_rows = db.query(models.EvaluationMetric).filter(
        models.EvaluationMetric.job_id == latest.job_id,
        models.EvaluationMetric.level == "channel",
    ).all()
    anchor_metric = db.query(models.EvaluationMetric).filter(
        models.EvaluationMetric.job_id == latest.job_id,
        models.EvaluationMetric.level == "anchor",
        models.EvaluationMetric.group_key == "live",
    ).first()

    if overall is not None:
        overall_wape = overall.wape
        overall_bias = overall.bias
        overall_mape = overall.mape
        eval_samples = overall.n_samples
        eval_total_actual = overall.total_actual
        raw_wape = overall.raw_wape
        raw_bias = overall.raw_bias
        raw_total_predicted = overall.raw_total_predicted
        wape_method = "overall"
    else:
        # 兼容旧任务：旧版将 WAPE 存在 mae 字段，且没有总量权重。
        legacy_wapes = [row.mae for row in channel_rows if row.mae is not None]
        legacy_bias = [row.bias for row in channel_rows if row.bias is not None]
        legacy_mape = [row.mape for row in channel_rows if row.mape is not None]
        overall_wape = sum(legacy_wapes) / len(legacy_wapes) if legacy_wapes else None
        overall_bias = sum(legacy_bias) / len(legacy_bias) if legacy_bias else None
        overall_mape = sum(legacy_mape) / len(legacy_mape) if legacy_mape else None
        eval_samples = None
        eval_total_actual = None
        raw_wape = None
        raw_bias = None
        raw_total_predicted = None
        wape_method = "legacy_channel_average"

    return {
        "has_results": True,
        "job_id": latest.job_id,
        "finished_at": latest.finished_at,
        "total_predicted_orders": round(total_pred),
        "total_base_predicted_orders": round(total_base),
        "total_anchor_predicted_orders": round(total_anchor),
        "anchor_share": round(total_anchor / total_pred * 100, 1) if total_pred else 0.0,
        "anchor_wape": round(anchor_metric.wape, 1) if anchor_metric and anchor_metric.wape is not None else None,
        "anchor_bias": round(anchor_metric.bias * 100, 1) if anchor_metric and anchor_metric.bias is not None else None,
        "anchor_target_wape": round(anchor_metric.raw_wape, 1) if anchor_metric and anchor_metric.raw_wape is not None else None,
        "forecast_days": pred_days,
        # 保留 avg_wape 键以兼容已发布前端，其语义已修正为整体 WAPE。
        "avg_wape": round(overall_wape, 1) if overall_wape is not None else None,
        "overall_wape": (
            round(overall_wape, 1)
            if overall_wape is not None and wape_method == "overall" else None
        ),
        "wape_method": wape_method,
        "avg_mape": round(overall_mape * 100, 1) if overall_mape is not None else None,
        "avg_bias": round(overall_bias * 100, 1) if overall_bias is not None else None,
        "channel_count": len({row.group_key for row in channel_rows}),
        "evaluation_samples": int(eval_samples) if eval_samples is not None else None,
        "evaluation_total_actual": round(eval_total_actual) if eval_total_actual is not None else None,
        "raw_wape": round(raw_wape, 1) if raw_wape is not None else None,
        "raw_bias": round(raw_bias * 100, 1) if raw_bias is not None else None,
        "raw_total_predicted": (
            round(raw_total_predicted) if raw_total_predicted is not None else None
        ),
        "wape_improvement": (
            round(raw_wape - overall_wape, 1)
            if raw_wape is not None and overall_wape is not None else None
        ),
    }


@router.get("/warehouse-stats")
def get_warehouse_stats(
    job_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """按仓库汇总预测总单量"""
    if not job_id:
        latest = _latest_completed_job(db)
        if not latest:
            return []
        job_id = latest.job_id

    rows = (
        db.query(
            models.WarehousePrediction.warehouse_id,
            func.sum(models.WarehousePrediction.warehouse_orders).label("total_orders"),
        )
        # 两个 job_id 前缀索引成本接近时 MySQL 偶尔选择日期索引；
        # 排名查询固定使用 warehouse_id 覆盖索引，避免额外排序读取。
        .with_hint(
            models.WarehousePrediction,
            "FORCE INDEX (ix_wh_predictions_job_wh_orders)",
            dialect_name="mysql",
        )
        .filter(models.WarehousePrediction.job_id == job_id)
        .group_by(models.WarehousePrediction.warehouse_id)
        .order_by(func.sum(models.WarehousePrediction.warehouse_orders).desc())
        .all()
    )

    return [
        {"warehouse_id": r.warehouse_id, "total_orders": round(r.total_orders) if r.total_orders else 0}
        for r in rows
    ]


@router.get("/backtest-daily")
def get_backtest_daily(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """返回最新训练任务的逐日历史回测：总体及分仓预测与实发。"""
    latest_job = _latest_completed_job(db)
    if not latest_job:
        return {
            "job_id": None, "dates": [], "total": {}, "warehouses": {},
            "warehouse_ids": [], "requires_retrain": True,
        }

    latest_date = db.query(func.max(models.BacktestDailyResult.date)).filter(
        models.BacktestDailyResult.job_id == latest_job.job_id
    ).scalar()
    if latest_date is None:
        return {
            "job_id": latest_job.job_id, "dates": [], "total": {}, "warehouses": {},
            "warehouse_ids": [], "requires_retrain": True,
        }

    start_date = latest_date - timedelta(days=days - 1)
    rows = db.query(models.BacktestDailyResult).filter(
        models.BacktestDailyResult.job_id == latest_job.job_id,
        models.BacktestDailyResult.date >= start_date,
        models.BacktestDailyResult.date <= latest_date,
    ).order_by(
        models.BacktestDailyResult.date,
        models.BacktestDailyResult.level,
        models.BacktestDailyResult.group_key,
    ).all()

    dates = [str(start_date + timedelta(days=offset)) for offset in range(days)]

    def build_series(items):
        lookup = {str(item.date): item for item in items}
        actual = [round(float(lookup[d].actual_orders or 0)) if d in lookup else 0 for d in dates]
        predicted = [round(float(lookup[d].predicted_orders or 0)) if d in lookup else 0 for d in dates]
        gap = [predicted[index] - actual[index] for index in range(len(dates))]
        actual_sum = sum(actual)
        predicted_sum = sum(predicted)
        absolute_error = sum(abs(value) for value in gap)
        return {
            "actual": actual,
            "predicted": predicted,
            "gap": gap,
            "summary": {
                "actual": actual_sum,
                "predicted": predicted_sum,
                "gap": predicted_sum - actual_sum,
                "wape": round(absolute_error / actual_sum * 100, 1) if actual_sum else None,
            },
        }

    total_rows = [row for row in rows if row.level == "total"]
    warehouse_ids = sorted({row.group_key for row in rows if row.level == "warehouse"})
    warehouses = {
        warehouse_id: build_series([
            row for row in rows
            if row.level == "warehouse" and row.group_key == warehouse_id
        ])
        for warehouse_id in warehouse_ids
    }
    warehouse_ids.sort(
        key=lambda warehouse_id: warehouses[warehouse_id]["summary"]["actual"],
        reverse=True,
    )

    return {
        "job_id": latest_job.job_id,
        "finished_at": latest_job.finished_at.isoformat() if latest_job.finished_at else None,
        "dates": dates,
        "total": build_series(total_rows),
        "warehouses": warehouses,
        "warehouse_ids": warehouse_ids,
        "requires_retrain": False,
    }


@router.get("/warehouse-daily")
def get_warehouse_daily(
    job_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """按日期 × 仓库返回分仓预测单量，供趋势图和明细表使用"""
    if not job_id:
        latest = _latest_completed_job(db)
        if not latest:
            return {"dates": [], "warehouses": [], "series": {}, "totals": []}
        job_id = latest.job_id

    rows = (
        db.query(
            models.WarehousePrediction.date,
            models.WarehousePrediction.warehouse_id,
            func.sum(models.WarehousePrediction.warehouse_orders).label("orders"),
        )
        .filter(models.WarehousePrediction.job_id == job_id)
        .group_by(models.WarehousePrediction.date, models.WarehousePrediction.warehouse_id)
        .order_by(models.WarehousePrediction.date)
        .all()
    )

    if not rows:
        return {"dates": [], "warehouses": [], "series": {}, "totals": []}

    dates = sorted(set(str(r.date) for r in rows))
    warehouses = sorted(set(r.warehouse_id for r in rows))

    # 构建 {warehouse_id: {date: orders}} 查找表
    lookup: dict = {}
    for r in rows:
        lookup.setdefault(r.warehouse_id, {})[str(r.date)] = round(r.orders or 0)

    series = {wh: [lookup[wh].get(d, 0) for d in dates] for wh in warehouses}
    totals = [sum(series[wh][i] for wh in warehouses) for i in range(len(dates))]

    return {"dates": dates, "warehouses": warehouses, "series": series, "totals": totals}


@router.get("/export/excel")
def export_excel(
    job_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """导出预测结果为 Excel"""
    if not job_id:
        latest = _latest_completed_job(db)
        if not latest:
            raise HTTPException(404, "没有可导出的结果")
        job_id = latest.job_id

    preds = db.query(models.Prediction).filter(models.Prediction.job_id == job_id).order_by(
        models.Prediction.date, models.Prediction.shop_id, models.Prediction.channel
    ).all()
    metrics = db.query(models.EvaluationMetric).filter(
        models.EvaluationMetric.job_id == job_id
    ).all()

    pred_df = pd.DataFrame([{
        "日期": str(p.date), "店铺": p.shop_id, "渠道": p.channel,
        "平台": p.platform,
        "原始预测单量": round(p.raw_predicted_orders) if p.raw_predicted_orders else 0,
        "非达人基线单量": round(p.base_predicted_orders) if p.base_predicted_orders else 0,
        "达人直播加单量": round(p.anchor_predicted_orders) if p.anchor_predicted_orders else 0,
        "模型校准预测": round(p.predicted_orders) if p.predicted_orders else 0,
        "人工调整值": (
            round(p.manual_predicted_orders)
            if p.manual_predicted_orders is not None else None
        ),
        "最终预测单量": round(
            p.manual_predicted_orders
            if p.manual_predicted_orders is not None else (p.predicted_orders or 0)
        ),
        "调整说明": p.adjustment_note,
        "调整时间": p.adjusted_at,
    } for p in preds])

    metrics_df = pd.DataFrame([{
        "层级": m.level, "分组": m.group_key,
        "样本数": m.n_samples, "实际总量": m.total_actual,
        "预测总量": m.total_predicted,
        "WAPE(%)": m.wape if m.wape is not None else (m.mae if m.level == "channel" else None),
        "原始WAPE(%)": m.raw_wape,
        "MAE": m.mae, "RMSE": m.rmse,
        "MAPE(%)": round(m.mape * 100, 1) if m.mape is not None else None,
        "Bias(%)": round(m.bias * 100, 1) if m.bias is not None else None,
        "原始Bias(%)": round(m.raw_bias * 100, 1) if m.raw_bias is not None else None,
    } for m in metrics])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pred_df.to_excel(writer, sheet_name="渠道预测", index=False)
        metrics_df.to_excel(writer, sheet_name="评估指标", index=False)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=forecast_{job_id[:8]}.xlsx"},
    )
