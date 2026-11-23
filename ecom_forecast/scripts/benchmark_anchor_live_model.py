"""用飞书排期与达人实绩做滚动时间回测。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.anchor_live_model import AnchorLiveOrderModel


FIELDS = ["平台", "主播", "场次", "日期", "完成状态", "预估目标", "预估单量", "主推", "MCN"]


def read_feishu(cli: str, base_token: str, table_id: str) -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        command = [
            cli, "base", "+record-list", "--base-token", base_token,
            "--table-id", table_id, "--as", "user", "--limit", "200",
            "--offset", str(offset), "--format", "json",
        ]
        for field in FIELDS:
            command += ["--field-id", field]
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True,
        )
        payload = json.loads(completed.stdout)["data"]
        rows.extend(payload["data"])
        if not payload.get("has_more"):
            break
        offset += len(payload["data"])
    return pd.DataFrame(rows, columns=FIELDS)


def prepare(schedule: pd.DataFrame, actual: pd.DataFrame):
    schedule = schedule.copy()
    schedule["date"] = pd.to_datetime(schedule["日期"], errors="coerce").dt.tz_localize(None).dt.normalize()
    schedule["anchor_name"] = schedule["主播"].astype("string").str.strip().str.replace(r"\s+", "", regex=True)
    schedule["expected_orders"] = pd.to_numeric(schedule["预估单量"], errors="coerce")
    schedule["shop_id"] = ""
    schedule["channel"] = "douyin_anchor"
    schedule["event_type"] = "live"
    schedule = schedule.dropna(subset=["date", "anchor_name"])
    schedule = schedule.groupby(["date", "shop_id", "channel", "event_type", "anchor_name"], as_index=False)["expected_orders"].sum(min_count=1)

    actual = actual.copy()
    actual["date"] = pd.to_datetime(actual["订单汇总时间"], errors="coerce").dt.normalize()
    actual["anchor_name"] = actual["达人名称"].astype("string").str.strip().str.replace(r"\s+", "", regex=True)
    actual["order_count"] = pd.to_numeric(actual["订单数量"], errors="coerce")
    actual["shop_id"] = ""
    actual = actual.dropna(subset=["date", "anchor_name", "order_count"])
    actual = actual.groupby(["date", "shop_id", "anchor_name"], as_index=False)["order_count"].sum()
    return schedule, actual


def metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    total = actual.sum()
    return {
        "n": int(len(actual)),
        "actual": float(total),
        "predicted": float(predicted.sum()),
        "wape": float(np.abs(actual - predicted).sum() / total),
        "bias": float((predicted.sum() - total) / total),
    }


def rolling_benchmark(schedule: pd.DataFrame, actual: pd.DataFrame):
    matched_dates = sorted(set(schedule["date"]) & set(actual["date"]))
    boundaries = [0.60, 0.72, 0.84, 1.00]
    cuts = [pd.Timestamp(matched_dates[min(int((len(matched_dates) - 1) * q), len(matched_dates) - 1)]) for q in boundaries]
    predictions = []
    for start, end in zip(cuts[:-1], cuts[1:]):
        train_actual = actual[actual["date"] <= start]
        train_schedule = schedule[schedule["date"] <= start]
        test_schedule = schedule[(schedule["date"] > start) & (schedule["date"] <= end)]
        model = AnchorLiveOrderModel().fit(train_actual, train_schedule)
        forecast = model.predict_events(test_schedule)
        detail = forecast.merge(
            actual, on=["date", "shop_id", "anchor_name"], how="inner"
        )
        if len(detail):
            daily = detail.groupby("date", as_index=False).agg(
                order_count=("order_count", "sum"),
                expected_orders=("expected_orders", "sum"),
                anchor_predicted_orders=("anchor_predicted_orders", "sum"),
            )
            predictions.append(daily)
    result = pd.concat(predictions, ignore_index=True)
    return {
        "model": metrics(result["order_count"], result["anchor_predicted_orders"]),
        "feishu_target": metrics(result["order_count"], result["expected_orders"]),
        "date_min": str(result["date"].min().date()),
        "date_max": str(result["date"].max().date()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", required=True)
    parser.add_argument("--base-token", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--cli", default="lark-cli")
    args = parser.parse_args()
    schedule_raw = read_feishu(args.cli, args.base_token, args.table_id)
    actual_raw = pd.read_excel(Path(args.actual), sheet_name="达人直播数据")
    schedule, actual = prepare(schedule_raw, actual_raw)
    exact = schedule.merge(actual, on=["date", "shop_id", "anchor_name"], how="inner")
    report = {
        "schedule_rows": int(len(schedule_raw)),
        "actual_rows": int(len(actual_raw)),
        "exact_matched_events": int(len(exact)),
        "rolling_origin": rolling_benchmark(schedule, actual),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
