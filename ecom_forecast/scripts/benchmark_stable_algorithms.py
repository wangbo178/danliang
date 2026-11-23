"""稳定渠道无外部信息算法基线。

默认使用最新渠道订单批次，排除“不加入测算”、短视频和非 stable 渠道，
用最后 30 天做外层 holdout。所有基线只使用预测日之前的真实数据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.database import engine  # noqa: E402
from api.routers.config import export_config  # noqa: E402
from api.database import SessionLocal  # noqa: E402
from evaluation.metrics import bias_pct, wape  # noqa: E402
from src.pipeline.forecast_pipeline import ForecastPipeline  # noqa: E402


GROUP_COLS = ["shop_id", "channel", "platform"]


def _latest_batch() -> str:
    with engine.connect() as conn:
        return str(conn.execute(text(
            "SELECT upload_batch FROM channel_orders "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        )).scalar_one())


def _load_data(batch_id: str) -> tuple[pd.DataFrame, dict]:
    query = text("""
        SELECT date, shop_id, platform, channel, SUM(order_count) AS order_count
        FROM channel_orders
        WHERE upload_batch = :batch_id
        GROUP BY date, shop_id, platform, channel
    """)
    with engine.connect() as conn:
        frame = pd.read_sql(query, conn, params={"batch_id": batch_id})
    frame["date"] = pd.to_datetime(frame["date"])

    db = SessionLocal()
    try:
        config = export_config(db)
    finally:
        db.close()
    return frame, config


def _filter_stable(frame: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.copy()
    channel_text = work["channel"].fillna("").astype(str).str.strip()
    excluded = pd.Series(False, index=work.index)
    platform_text = work["platform"].fillna("").astype(str).str.strip().str.casefold()
    for item in config.get("excluded_channels", []):
        aliases = {
            str(item.get("channel_id") or "").strip().casefold(),
            str(item.get("channel_name") or "").strip().casefold(),
        } - {""}
        item_mask = channel_text.str.casefold().isin(aliases)
        platform_id = str(item.get("platform_id") or "").strip().casefold()
        if platform_id:
            item_mask &= platform_text.eq(platform_id)
        excluded |= item_mask

    short_video = channel_text.str.contains("短视频", regex=False)
    channel_type = channel_text.map(config.get("channel_type_map", {})).fillna("stable")
    stable = channel_type.eq("stable")
    kept = work.loc[~excluded & ~short_video & stable].copy()
    removed = work.loc[excluded | short_video | ~stable].copy()
    return kept, removed


def _history_values(history: pd.DataFrame, pred_date: pd.Timestamp) -> pd.DataFrame:
    hist = history[history["date"] < pred_date].copy()
    keys = hist[GROUP_COLS].drop_duplicates()
    result = keys.copy()

    for lag in (7, 14, 21, 28):
        lookup = hist[hist["date"] == pred_date - pd.Timedelta(days=lag)][
            GROUP_COLS + ["order_count"]
        ].rename(columns={"order_count": f"lag{lag}"})
        result = result.merge(lookup, on=GROUP_COLS, how="left", validate="one_to_one")

    recent = hist[hist["date"] >= pred_date - pd.Timedelta(days=28)].copy()
    recent["days_back"] = (pred_date - recent["date"]).dt.days
    for days in (7, 14, 28):
        stats = (
            recent[recent["days_back"] <= days]
            .groupby(GROUP_COLS, as_index=False)["order_count"]
            .mean()
            .rename(columns={"order_count": f"mean{days}"})
        )
        result = result.merge(stats, on=GROUP_COLS, how="left", validate="one_to_one")
    return result


def _make_predictions(frame: pd.DataFrame, dates: list[pd.Timestamp]) -> pd.DataFrame:
    outputs = []
    for pred_date in dates:
        actual = frame[frame["date"] == pred_date].copy()
        features = _history_values(frame, pred_date)
        day = actual.merge(features, on=GROUP_COLS, how="left", validate="one_to_one")

        lag_cols = ["lag7", "lag14", "lag21", "lag28"]
        day["seasonal4"] = day[lag_cols].mean(axis=1, skipna=True)
        day["seasonal_recent"] = (
            0.55 * day["lag7"] + 0.25 * day["lag14"]
            + 0.12 * day["lag21"] + 0.08 * day["lag28"]
        )
        day["level_blend"] = (
            0.55 * day["seasonal4"] + 0.30 * day["mean7"] + 0.15 * day["mean28"]
        )
        day["trend_blend"] = (
            day["level_blend"]
            * (day["mean7"] / day["mean28"].clip(lower=1)).clip(0.80, 1.20)
        )
        fallback = day["mean28"].fillna(day["seasonal4"]).fillna(0)
        for col in [
            *lag_cols, "mean7", "mean14", "mean28", "seasonal4",
            "seasonal_recent", "level_blend", "trend_blend",
        ]:
            day[col] = day[col].fillna(fallback).clip(lower=0)
        outputs.append(day)
    return pd.concat(outputs, ignore_index=True)


def _metric_row(frame: pd.DataFrame, pred_col: str) -> dict:
    actual = frame["order_count"].to_numpy(dtype=float)
    pred = frame[pred_col].to_numpy(dtype=float)
    return {
        "algorithm": pred_col,
        "wape": round(wape(actual, pred), 3),
        "bias_pct": round(bias_pct(actual, pred), 3),
        "actual": int(actual.sum()),
        "predicted": int(np.rint(pred.sum())),
    }


def _best_scale(frame: pd.DataFrame, pred_col: str) -> float:
    """在内层验证集上联合压低 WAPE 和 |Bias|。"""
    actual = frame["order_count"].to_numpy(dtype=float)
    pred = frame[pred_col].to_numpy(dtype=float)
    best = (float("inf"), 1.0)
    for factor in np.linspace(0.75, 1.30, 111):
        candidate = pred * factor
        score = wape(actual, candidate) + 0.25 * abs(bias_pct(actual, candidate))
        if score < best[0]:
            best = (score, float(factor))
    return best[1]


def _add_calibrated_mean7(
    tuning: pd.DataFrame,
    holdout: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    result = holdout.copy()
    global_factor = _best_scale(tuning, "mean7")
    result["mean7_global_calibrated"] = result["mean7"] * global_factor

    channel_factors: dict[str, float] = {}
    for channel, group in tuning.groupby("channel"):
        if len(group) < 14 or float(group["order_count"].sum()) < 5000:
            continue
        local_factor = _best_scale(group, "mean7")
        reliability = min(1.0, len(group) / 90.0) * min(
            1.0, float(group["order_count"].sum()) / 50_000.0
        )
        channel_factors[str(channel)] = (
            global_factor + reliability * (local_factor - global_factor)
        )
    result["mean7_channel_calibrated"] = result["mean7"] * (
        result["channel"].astype(str).map(channel_factors).fillna(global_factor)
    )
    return result, {
        "global_factor": round(global_factor, 4),
        "channel_factors": {k: round(v, 4) for k, v in channel_factors.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", default=None)
    parser.add_argument("--as-of", default=None, help="回测数据截止日，YYYY-MM-DD")
    parser.add_argument("--holdout-days", type=int, default=30)
    parser.add_argument("--lightgbm", action="store_true")
    parser.add_argument("--by-channel", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    batch_id = args.batch or _latest_batch()
    frame, config = _load_data(batch_id)
    stable, removed = _filter_stable(frame, config)
    if args.as_of:
        stable = stable[stable["date"] <= pd.Timestamp(args.as_of)].copy()
    max_date = stable["date"].max()
    cutoff = max_date - pd.Timedelta(days=args.holdout_days)
    dates = sorted(stable.loc[stable["date"] > cutoff, "date"].unique())
    predicted = _make_predictions(stable, [pd.Timestamp(value) for value in dates])

    tuning_start = cutoff - pd.Timedelta(days=args.holdout_days)
    tuning_dates = sorted(stable.loc[
        (stable["date"] > tuning_start) & (stable["date"] <= cutoff), "date"
    ].unique())
    tuning = _make_predictions(stable, [pd.Timestamp(value) for value in tuning_dates])
    predicted, mean7_calibration = _add_calibrated_mean7(tuning, predicted)

    candidates = [
        "lag7", "lag14", "lag28", "seasonal4", "seasonal_recent",
        "mean7", "mean14", "mean28", "level_blend", "trend_blend",
        "mean7_global_calibrated", "mean7_channel_calibrated",
    ]
    metric_rows = [_metric_row(predicted, col) for col in candidates]
    if args.lightgbm:
        train_only = stable[stable["date"] <= cutoff].copy()
        pipeline = ForecastPipeline(config_dict=config)
        pipeline.train_channels(train_only)
        channel_eval, _, merged = pipeline.evaluate_on_holdout(
            stable,
            holdout_days=args.holdout_days,
            actual_order_df=stable,
        )
        overall = channel_eval[channel_eval["segment"] == "全局"].iloc[0]
        metric_rows.extend([
            {
                "algorithm": "lightgbm_calibrated",
                "wape": float(overall["wape"]),
                "bias_pct": float(overall["bias_pct"]),
                "actual": int(round(float(overall["total_actual"]))),
                "predicted": int(round(float(overall["total_predicted"]))),
            },
            _metric_row(merged, "raw_predicted_orders") | {"algorithm": "lightgbm_raw"},
        ])
        comparison = predicted.merge(
            merged[GROUP_COLS + ["date", "predicted_orders"]],
            on=GROUP_COLS + ["date"],
            how="inner",
            validate="one_to_one",
            suffixes=("", "_lgb"),
        )
        for baseline_weight in (0.25, 0.50, 0.75):
            name = f"blend_mean7_{baseline_weight:.2f}"
            comparison[name] = (
                baseline_weight * comparison["mean7_global_calibrated"]
                + (1.0 - baseline_weight) * comparison["predicted_orders"]
            )
            metric_rows.append(_metric_row(comparison, name))

    result = {
        "batch": batch_id,
        "date_range": [str(stable["date"].min().date()), str(max_date.date())],
        "holdout": [str((cutoff + pd.Timedelta(days=1)).date()), str(max_date.date())],
        "kept_rows": int(len(stable)),
        "kept_channels": sorted(stable["channel"].astype(str).unique().tolist()),
        "removed_channels": sorted(removed["channel"].astype(str).unique().tolist()),
        "mean7_calibration": mean7_calibration,
        "metrics": metric_rows,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"批次: {batch_id}  holdout: {result['holdout'][0]} ~ {result['holdout'][1]}")
    print(f"稳定渠道: {', '.join(result['kept_channels'])}")
    print(pd.DataFrame(result["metrics"]).sort_values("wape").to_string(index=False))
    if args.by_channel:
        rows = []
        for channel, group in predicted.groupby("channel"):
            row = _metric_row(group, "mean7_global_calibrated")
            row["channel"] = str(channel)
            rows.append(row)
        print("\n近7日校准基线分渠道：")
        print(pd.DataFrame(rows).sort_values("actual", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
