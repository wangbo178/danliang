"""达人直播单量的独立叠加模型。

模型只使用预测时已知的信息：达人、店铺、历史实绩与排期目标。
它先从渠道模型的历史标签中剔除已知达人实绩，使渠道模型学习
“非达人基线”；预测时再把本模型的排期单量加回，避免重复计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd


def _clean_name(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip().str.replace(r"\s+", "", regex=True)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    idx = int(np.searchsorted(np.cumsum(weights), weights.sum() / 2.0, side="left"))
    return float(values[min(idx, len(values) - 1)])


def _score(actual: np.ndarray, predicted: np.ndarray) -> float:
    actual = np.clip(np.asarray(actual, dtype=float), 0, None)
    predicted = np.clip(np.asarray(predicted, dtype=float), 0, None)
    total = float(actual.sum())
    if total <= 0:
        return float("inf")
    wape = np.abs(actual - predicted).sum() / total
    bias = (predicted.sum() - total) / total
    return float(wape + 0.25 * abs(bias))


@dataclass
class AnchorLiveMetrics:
    n: int
    actual_total: float
    predicted_total: float
    wape: float
    bias: float


class AnchorLiveOrderModel:
    """小样本稳健模型：近期中位数 + 分层收缩 + 排期目标校准。"""

    def __init__(self, shrinkage: float = 4.0):
        self.shrinkage = float(shrinkage)
        self.recent_weight = 0.55
        self.global_level = 0.0
        self.anchor_stats: Dict[str, Tuple[int, float, float]] = {}
        self.anchor_shop_stats: Dict[Tuple[str, str], Tuple[int, float, float]] = {}
        self.anchor_shop_map: Dict[str, str] = {}
        self.shop_channel_map: Dict[str, str] = {}
        self.target_factor = 1.0
        self.target_weight = 0.0
        self.is_fitted = False

    @staticmethod
    def _prepare_actuals(anchor_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        cols = ["date", "shop_id", "anchor_name", "order_count"]
        if anchor_df is None or len(anchor_df) == 0 or not {
            "date", "anchor_name", "order_count"
        }.issubset(anchor_df.columns):
            return pd.DataFrame(columns=cols)
        data = anchor_df.copy()
        if "shop_id" not in data.columns:
            data["shop_id"] = ""
        data["date"] = pd.to_datetime(data["date"], errors="coerce").dt.normalize()
        data["shop_id"] = data["shop_id"].fillna("").astype(str).str.strip()
        data["anchor_name"] = _clean_name(data["anchor_name"])
        data["order_count"] = pd.to_numeric(data["order_count"], errors="coerce")
        data = data.dropna(subset=["date", "anchor_name", "order_count"])
        data = data[data["order_count"] >= 0]
        return data.groupby(["date", "shop_id", "anchor_name"], as_index=False)[
            "order_count"
        ].sum()

    @staticmethod
    def _prepare_schedule(schedule_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        if schedule_df is None or len(schedule_df) == 0 or not {
            "date", "anchor_name"
        }.issubset(schedule_df.columns):
            return pd.DataFrame()
        schedule = schedule_df.copy()
        schedule["date"] = pd.to_datetime(schedule["date"], errors="coerce").dt.normalize()
        schedule["anchor_name"] = _clean_name(schedule["anchor_name"])
        if "shop_id" not in schedule.columns:
            schedule["shop_id"] = ""
        schedule["shop_id"] = schedule["shop_id"].fillna("").astype(str).str.strip()
        if "event_type" in schedule.columns:
            schedule = schedule[schedule["event_type"].fillna("live").eq("live")]
        target_col = next(
            (c for c in ("expected_orders", "estimated_orders", "target_orders", "预估单量") if c in schedule.columns),
            None,
        )
        schedule["expected_orders"] = (
            pd.to_numeric(schedule[target_col], errors="coerce") if target_col else np.nan
        )
        schedule = schedule.dropna(subset=["date", "anchor_name"]).copy()

        # 同一场排期可能同时存在旧批次占位行和后续补充了目标单量的完整行。
        # 无场次 ID/开播时间时按店铺×日期×达人视为同一场，并优先保留人工目标。
        source_record = (
            schedule["source_record_id"].fillna("").astype(str).str.strip()
            if "source_record_id" in schedule.columns
            else pd.Series("", index=schedule.index)
        )
        scheduled_at = (
            pd.to_datetime(schedule["scheduled_at"], errors="coerce")
            if "scheduled_at" in schedule.columns
            else pd.Series(pd.NaT, index=schedule.index)
        )
        schedule["_event_key"] = np.where(
            source_record.ne(""),
            "record:" + source_record,
            np.where(
                scheduled_at.notna(),
                "time:" + scheduled_at.astype(str),
                "daily",
            ),
        )
        schedule["_target_priority"] = schedule["expected_orders"].gt(0).astype(int)
        schedule["_channel_priority"] = (
            schedule.get("channel", pd.Series("", index=schedule.index))
            .fillna("").astype(str).str.strip().ne("").astype(int)
        )
        schedule = (
            schedule.sort_values(["_target_priority", "_channel_priority"])
            .drop_duplicates(
                subset=["date", "shop_id", "anchor_name", "_event_key"],
                keep="last",
            )
            .drop(columns=["_event_key", "_target_priority", "_channel_priority"])
        )
        return schedule

    @staticmethod
    def _stat_map(data: pd.DataFrame, keys) -> Dict:
        result = {}
        for key, group in data.sort_values("date").groupby(keys, dropna=False):
            values = group["order_count"].to_numpy(dtype=float)
            recent = values[-3:]
            map_key = key if isinstance(key, tuple) else str(key)
            result[map_key] = (
                int(len(values)),
                float(np.median(values)),
                float(np.median(recent)),
            )
        return result

    def _history_prediction(self, anchor: str, shop: str) -> float:
        anchor_stat = self.anchor_stats.get(anchor)
        pair_stat = self.anchor_shop_stats.get((shop, anchor)) if shop else None
        if pair_stat:
            n, median, recent = pair_stat
            local = self.recent_weight * recent + (1.0 - self.recent_weight) * median
            parent = (
                self.recent_weight * anchor_stat[2]
                + (1.0 - self.recent_weight) * anchor_stat[1]
                if anchor_stat else self.global_level
            )
            reliability = n / (n + self.shrinkage)
            return float(reliability * local + (1.0 - reliability) * parent)
        if anchor_stat:
            n, median, recent = anchor_stat
            local = self.recent_weight * recent + (1.0 - self.recent_weight) * median
            reliability = n / (n + self.shrinkage)
            return float(reliability * local + (1.0 - reliability) * self.global_level)
        return float(self.global_level)

    def _fit_history_stats(
        self,
        actual: pd.DataFrame,
        event_order_df: Optional[pd.DataFrame] = None,
    ) -> None:
        """只根据传入时点及之前的实绩刷新分层统计。"""
        self.global_level = float(np.median(actual["order_count"]))
        self.anchor_stats = self._stat_map(actual, "anchor_name")
        self.anchor_shop_stats = self._stat_map(actual, ["shop_id", "anchor_name"])
        dominant_shop = (
            actual[actual["shop_id"].ne("")]
            .groupby(["anchor_name", "shop_id"], as_index=False)["order_count"].sum()
            .sort_values(["anchor_name", "order_count", "shop_id"], ascending=[True, False, True])
            .drop_duplicates("anchor_name")
        )
        self.anchor_shop_map = dict(zip(dominant_shop["anchor_name"], dominant_shop["shop_id"]))

        self.shop_channel_map = {}
        if event_order_df is not None and len(event_order_df) > 0:
            totals = (
                event_order_df.groupby(["shop_id", "channel"], as_index=False)["order_count"]
                .sum()
                .sort_values(["shop_id", "order_count", "channel"], ascending=[True, False, True])
                .drop_duplicates("shop_id")
            )
            self.shop_channel_map = dict(zip(totals["shop_id"].astype(str), totals["channel"].astype(str)))

    def fit(
        self,
        anchor_df: Optional[pd.DataFrame],
        schedule_df: Optional[pd.DataFrame] = None,
        event_order_df: Optional[pd.DataFrame] = None,
    ) -> "AnchorLiveOrderModel":
        actual = self._prepare_actuals(anchor_df)
        if len(actual) == 0:
            self.is_fitted = False
            return self

        self._fit_history_stats(actual, event_order_df)

        schedule = self._prepare_schedule(schedule_df)
        if len(schedule) > 0 and self.anchor_shop_map:
            missing_shop = schedule["shop_id"].eq("")
            schedule.loc[missing_shop, "shop_id"] = schedule.loc[
                missing_shop, "anchor_name"
            ].map(self.anchor_shop_map).fillna("")
        self.target_factor = 1.0
        self.target_weight = 0.0
        if len(schedule) > 0 and schedule["expected_orders"].gt(0).any():
            matched = schedule.merge(
                actual, on=["date", "shop_id", "anchor_name"], how="inner"
            )
            matched = matched[matched["expected_orders"].gt(0)]
            if len(matched) >= 8:
                split_date = matched["date"].quantile(0.75)
                train = matched[matched["date"] <= split_date]
                valid = matched[matched["date"] > split_date]
                if len(valid) >= 2 and len(train) >= 4:
                    ratio = (
                        train["order_count"].to_numpy(dtype=float)
                        / train["expected_orders"].clip(lower=1).to_numpy(dtype=float)
                    )
                    self.target_factor = float(np.clip(
                        _weighted_median(ratio, train["expected_orders"].to_numpy(dtype=float)),
                        0.35, 2.50,
                    ))
                    # 内层验证的历史水平严格只使用切分日之前实绩，
                    # 避免用验证期真实单量选融合权重。
                    full_stats = (
                        self.global_level, self.anchor_stats, self.anchor_shop_stats,
                        self.anchor_shop_map, self.shop_channel_map,
                    )
                    train_actual = actual[actual["date"] <= split_date]
                    train_event = event_order_df
                    if train_event is not None and len(train_event) > 0 and "date" in train_event.columns:
                        train_event = train_event[pd.to_datetime(train_event["date"]) <= split_date]
                    self._fit_history_stats(train_actual, train_event)
                    target = valid["expected_orders"].to_numpy(dtype=float) * self.target_factor
                    actual_valid = valid["order_count"].to_numpy(dtype=float)
                    best = (float("inf"), self.shrinkage, self.recent_weight, 0.0)
                    for shrinkage in (1.0, 2.0, 4.0, 8.0, 12.0):
                        for recent_weight in (0.0, 0.25, 0.50, 0.75, 1.0):
                            self.shrinkage = shrinkage
                            self.recent_weight = recent_weight
                            hist = np.array([
                                self._history_prediction(str(r.anchor_name), str(r.shop_id))
                                for r in valid.itertuples()
                            ])
                            for weight in (0.0, 0.25, 0.50, 0.75, 1.0):
                                candidate = (1.0 - weight) * hist + weight * target
                                current = _score(actual_valid, candidate)
                                if current < best[0]:
                                    best = (current, shrinkage, recent_weight, weight)
                    self.shrinkage = float(best[1])
                    self.recent_weight = float(best[2])
                    self.target_weight = float(best[3])
                    (
                        self.global_level, self.anchor_stats, self.anchor_shop_stats,
                        self.anchor_shop_map, self.shop_channel_map,
                    ) = full_stats

        self.is_fitted = True
        return self

    def evaluate_holdout(
        self,
        anchor_df: Optional[pd.DataFrame],
        schedule_df: Optional[pd.DataFrame],
        start_date,
        end_date=None,
    ) -> dict:
        """评估已训练模型在未参与训练的排期场次上的 WAPE/Bias。"""
        actual = self._prepare_actuals(anchor_df)
        schedule = self.predict_events(schedule_df)
        if len(actual) == 0 or len(schedule) == 0:
            return {}
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize() if end_date is not None else actual["date"].max()
        schedule = schedule[schedule["date"].between(start, end)]
        if "schedule_source" in schedule.columns:
            uploaded = schedule[schedule["schedule_source"].fillna("uploaded").eq("uploaded")]
            if len(uploaded) > 0:
                schedule = uploaded
        actual = actual[actual["date"].between(start, end)]
        detail = schedule.merge(
            actual, on=["date", "shop_id", "anchor_name"], how="inner"
        )
        if len(detail) == 0:
            return {}
        model_metric = self.metrics(detail["order_count"], detail["anchor_predicted_orders"])
        target_detail = detail[detail["expected_orders"].gt(0)]
        target_metric = (
            self.metrics(target_detail["order_count"], target_detail["expected_orders"])
            if len(target_detail) else None
        )
        return {
            "n": model_metric.n,
            "actual_total": model_metric.actual_total,
            "predicted_total": model_metric.predicted_total,
            "wape": model_metric.wape,
            "bias": model_metric.bias,
            "target_n": target_metric.n if target_metric else 0,
            "target_predicted_total": target_metric.predicted_total if target_metric else None,
            "target_wape": target_metric.wape if target_metric else None,
            "target_bias": target_metric.bias if target_metric else None,
        }

    def remove_actuals_from_event_history(self, event_df: pd.DataFrame, anchor_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """从主直播渠道的历史标签中减去达人实绩。"""
        if not self.is_fitted or event_df is None or len(event_df) == 0:
            return event_df
        actual = self._prepare_actuals(anchor_df)
        actual = actual[actual["shop_id"].ne("")]
        if len(actual) == 0:
            return event_df
        deduction = actual.groupby(["date", "shop_id"], as_index=False)["order_count"].sum()
        deduction["channel"] = deduction["shop_id"].map(self.shop_channel_map)
        deduction = deduction.dropna(subset=["channel"]).rename(columns={"order_count": "_anchor_orders"})
        result = event_df.copy()
        result["date"] = pd.to_datetime(result["date"]).dt.normalize()
        result["shop_id"] = result["shop_id"].astype(str)
        result["channel"] = result["channel"].astype(str)
        result = result.merge(deduction, on=["date", "shop_id", "channel"], how="left")
        original_orders = pd.to_numeric(result["order_count"], errors="coerce")
        result["order_count"] = (
            original_orders.fillna(0)
            - result["_anchor_orders"].fillna(0)
        ).clip(lower=0)
        result.loc[original_orders.isna(), "order_count"] = np.nan
        return result.drop(columns=["_anchor_orders"])

    def predict_events(self, schedule_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """返回每条达人排期的预测，用于可解释明细与回测。"""
        schedule = self._prepare_schedule(schedule_df)
        if not self.is_fitted or len(schedule) == 0:
            return pd.DataFrame(columns=[
                "date", "shop_id", "channel", "anchor_name", "anchor_predicted_orders"
            ])
        missing_shop = schedule["shop_id"].eq("")
        schedule.loc[missing_shop, "shop_id"] = schedule.loc[
            missing_shop, "anchor_name"
        ].map(self.anchor_shop_map).fillna("")
        if "channel" not in schedule.columns:
            schedule["channel"] = schedule["shop_id"].map(self.shop_channel_map)
        else:
            schedule["channel"] = schedule["channel"].astype("string").str.strip().replace("", pd.NA)
            schedule["channel"] = schedule["channel"].fillna(
                schedule["shop_id"].map(self.shop_channel_map)
            )
        schedule = schedule.dropna(subset=["channel"])
        history_pred = np.array([
            self._history_prediction(str(r.anchor_name), str(r.shop_id))
            for r in schedule.itertuples()
        ])
        target = schedule["expected_orders"].to_numpy(dtype=float)
        has_target = np.isfinite(target) & (target > 0)
        # 人工填写的目标单量是明确的业务输入，应直接生效；历史模型只负责
        # 为未填写目标的排期补量，避免校准权重为 0 时悄然忽略人工排期。
        schedule["anchor_predicted_orders"] = np.where(
            has_target,
            target,
            history_pred,
        ).clip(min=0)
        return schedule

    def predict_schedule(self, schedule_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """返回 date/shop_id/channel 粒度的可加单量。"""
        schedule = self.predict_events(schedule_df)
        if len(schedule) == 0:
            return pd.DataFrame(columns=["date", "shop_id", "channel", "anchor_predicted_orders"])
        return schedule.groupby(["date", "shop_id", "channel"], as_index=False)[
            "anchor_predicted_orders"
        ].sum()

    @staticmethod
    def metrics(actual, predicted) -> AnchorLiveMetrics:
        actual = np.asarray(actual, dtype=float)
        predicted = np.asarray(predicted, dtype=float)
        total = float(actual.sum())
        return AnchorLiveMetrics(
            n=int(len(actual)),
            actual_total=total,
            predicted_total=float(predicted.sum()),
            wape=float(np.abs(actual - predicted).sum() / total) if total > 0 else np.nan,
            bias=float((predicted.sum() - total) / total) if total > 0 else np.nan,
        )
