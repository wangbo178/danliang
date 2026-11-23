"""
第二层：渠道类型子模型
按流量特性分三类：stable / event / affiliate
每类训练独立模型，最终汇总为渠道粒度预测
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import joblib
from pathlib import Path
from typing import Dict, List, Optional

from config.model_config import CHANNEL_MODEL_PARAMS, TRAINING_CONFIG
from config.routing_config import CHANNEL_TYPE_MAP
from src.features.campaign_features import build_campaign_features, build_shop_campaign_sensitivity
from src.features.time_features import build_all_time_features, fill_future_lag_features
from src.features.channel_features import (
    build_live_features,
    build_affiliate_features,
    build_presale_order_features,
)
from src.models.presale_model import PresaleDistributionModel
from src.models.anchor_live_model import AnchorLiveOrderModel


# 各渠道类型的特征列
STABLE_FEATURE_COLS = [
    "day_of_week", "day_of_month", "month", "quarter",
    "is_weekend", "is_month_start", "is_month_end",
    "dow_sin", "dow_cos", "month_sin", "month_cos",
    "days_to_nearest_campaign", "nearest_campaign_code",
    "is_preheat", "is_presale", "presale_day_index",
    "is_campaign_day", "is_final_payment", "final_payment_day_offset",
    "is_post_campaign", "campaign_intensity",
    "shop_campaign_sensitivity", "effective_campaign_intensity",
    "order_count_lag7", "order_count_lag14", "order_count_lag28",
    "order_count_roll_mean7", "order_count_roll_mean14", "order_count_roll_mean30",
    "order_count_roll_std7",
    "order_count_yoy",
    # ── 预售单量规模特征（所有渠道通用）─────────────────────
    "presale_orders_roll_max30",   # 近30天预售峰值（≈上次大促最高日单量）
    "presale_orders_roll_sum90",   # 近90天累计预售量
    "presale_camp_ratio",          # 近30天预售单占比（渠道预售依赖度）
    "shop_id_code", "channel_code",
    # ── 中国法定节假日特征 ────────────────────────────────────────────────
    "is_spring_festival", "days_to_spring_festival",
    "is_national_holiday", "is_labor_day",
    "is_qingming_holiday", "is_duanwu_holiday", "is_zhongqiu_holiday",
]

EVENT_FEATURE_COLS = STABLE_FEATURE_COLS + [
    "order_count_lag1",                    # 直播类需要昨天数据
    # ── 当天普通直播信号 ──────────────────────────────────────
    "has_live",                            # 今天是否有普通直播（event_type='live'）
    "anchor_tier_code",                    # 今天主播档级（A=3/B=2/C=1/无=0）
    # ── 直播短期滞后（连播效应）─────────────────────────────
    "live_lag1",                           # 昨天是否有直播
    "live_lag7",  "live_lag14",            # 7/14天前是否有直播
    "anchor_tier_lag1",
    "anchor_tier_lag7", "anchor_tier_lag14",
    # ── 预售尾款信号（精确到天，非固定窗口）─────────────────
    "is_presale_payment",                  # 今天是否是预售尾款日
    "presale_pay_lag",                     # 尾款日距定金场播出的实际天数
    "presale_pay_tier",                    # 该预售场主播档级
    # ── 预售单量专项特征 ─────────────────────────────────────
    "presale_orders_lag7",
    "presale_orders_lag14",
    # ── 达人名称特征（新增）──────────────────────────────────
    "anchor_name_code",     # 达人整数编码（0=未知）
    "anchor_hist_mean",     # 该达人历史直播日均单量
    "anchor_hist_recent",   # 该达人最近3场均量
]

AFFILIATE_FEATURE_COLS = STABLE_FEATURE_COLS + [
    "commission_rate",
    "active_affiliates",
]

FEATURE_COLS_MAP = {
    "stable": STABLE_FEATURE_COLS,
    "event": EVENT_FEATURE_COLS,
    "affiliate": AFFILIATE_FEATURE_COLS,
}


def _schedule_for_base_model(
    schedule_df: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """Return schedule signals that belong to the non-anchor base model.

    Ordinary ``live`` rows are owned exclusively by ``AnchorLiveOrderModel``.
    Passing them into the event LightGBM as well would make the base prediction
    change when a live schedule is uploaded, and the same live event would then
    be represented twice.  Presale-payment rows remain available to the event
    base model because they are a separate, rule-driven component.
    """
    if schedule_df is None or len(schedule_df) == 0:
        return schedule_df
    schedule = schedule_df.copy()
    if "event_type" in schedule.columns:
        event_type = (
            schedule["event_type"].fillna("live").astype(str).str.strip().str.lower()
        )
        return schedule.loc[~event_type.eq("live")].copy()
    # Legacy schedules without event_type represented ordinary live rows only.
    return schedule.iloc[0:0].copy()

# 当前 holdout 缺口最大的主力渠道。仅放宽其“从验证集学习”的校准上限，
# 不设置固定乘数，也不使用最终 holdout 标签。
PRIORITY_BIAS_CHANNEL_CAPS = {
    "tm_zb": 2.50,
    "tm_hj": 1.75,
    "dy_hj": 1.75,
    "pdd_shelf": 1.60,
}


def _joint_wape_bias_score(actual: np.ndarray, predicted: np.ndarray) -> float:
    """稳定渠道内层模型选择分数：主优化 WAPE，同时约束系统性偏差。"""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    valid = np.isfinite(actual) & np.isfinite(predicted)
    if not valid.any():
        return float("inf")
    actual = np.clip(actual[valid], 0, None)
    predicted = np.clip(predicted[valid], 0, None)
    total = float(actual.sum())
    if total <= 0:
        return float("inf")
    wape_value = float(np.abs(actual - predicted).sum() / total * 100)
    bias_value = float((predicted.sum() - total) / total * 100)
    return wape_value + 0.25 * abs(bias_value)


def _select_stable_baseline(
    actual: np.ndarray,
    model_pred: np.ndarray,
    rolling_mean7: np.ndarray,
) -> tuple[float, float]:
    """在内层验证集上选择近7日基线校准系数和融合权重。"""
    actual = np.asarray(actual, dtype=float)
    model_pred = np.clip(np.asarray(model_pred, dtype=float), 0, None)
    rolling_mean7 = np.asarray(rolling_mean7, dtype=float)
    rolling_mean7 = np.where(np.isfinite(rolling_mean7), rolling_mean7, model_pred)
    rolling_mean7 = np.clip(rolling_mean7, 0, None)

    best_factor = 1.0
    best_factor_score = float("inf")
    for factor in np.linspace(0.75, 1.30, 111):
        score = _joint_wape_bias_score(actual, rolling_mean7 * factor)
        if score < best_factor_score:
            best_factor_score = score
            best_factor = float(factor)

    baseline_pred = rolling_mean7 * best_factor
    best_weight = 0.0
    best_blend_score = _joint_wape_bias_score(actual, model_pred)
    for weight in (0.25, 0.50, 0.75, 1.0):
        candidate = (1.0 - weight) * model_pred + weight * baseline_pred
        score = _joint_wape_bias_score(actual, candidate)
        if score + 1e-9 < best_blend_score:
            best_blend_score = score
            best_weight = float(weight)
    return best_factor, best_weight


class ChannelTypeModel:
    """
    单个渠道类型的预测模型
    """

    def __init__(
        self,
        channel_type: str,
        params: Optional[Dict] = None,
        config: Optional[Dict] = None,
    ):
        assert channel_type in ("stable", "event", "affiliate"), \
            f"未知渠道类型: {channel_type}"
        self.channel_type = channel_type
        self.params = params or CHANNEL_MODEL_PARAMS[channel_type]
        self.config = config or TRAINING_CONFIG
        self.model: Optional[lgb.Booster] = None
        self.feature_cols: List[str] = []
        self.encoders: Dict = {}
        self.shop_sensitivity_map: Dict[str, float] = {}
        self.shop_override_map: Dict[str, str] = {}
        self.smearing_factor: float = 1.0
        self.channel_calibration_factors: Dict[str, float] = {}
        self.live_peak_factors: Dict[str, float] = {}
        # stable 渠道在无外部信息时，用内层验证自动选择
        # LightGBM 与近7日水平基线的融合比例。
        self.stable_baseline_factor: float = 1.0
        self.stable_baseline_weight: float = 0.0
        self.stable_low_cardinality_channels: set[str] = set()
        # 独立外层 holdout 发现校准恶化时，正式预测回退到“未做渠道倍数
        # 校准、但保留 smearing 与稳定滚动基线”的候选值。属性名保留用于
        # 兼容既有模型文件。
        self.raw_fallback_channels: set[str] = set()
        # 预售分布模型（仅 event 类型使用）
        self.presale_model: Optional[PresaleDistributionModel] = (
            PresaleDistributionModel() if channel_type == "event" else None
        )

    def _encode_categoricals(self, df: pd.DataFrame, fit: bool = False) -> pd.DataFrame:
        df = df.copy()
        for col in ["shop_id", "channel"]:
            out_col = f"{col}_code"
            if fit:
                unique_vals = sorted(df[col].dropna().unique())
                self.encoders[col] = {v: i for i, v in enumerate(unique_vals)}
            df[out_col] = df[col].map(self.encoders.get(col, {})).fillna(-1).astype(int)
        return df

    def _build_sample_weights(self, df: pd.DataFrame) -> np.ndarray:
        weights = np.ones(len(df))

        # 时间衰减加权：近期数据比早期数据更重要，避免模型被旧时期低量拉偏。
        # 半衰期 180 天，并设置 0.1 下限，兼顾近期分布与少量长期季节性。
        if "date" in df.columns:
            max_date = df["date"].max()
            days_back = (max_date - df["date"]).dt.days.clip(lower=0)
            decay = np.power(0.5, days_back / 180.0)
            weights = weights * np.clip(decay, 0.1, 1.0)

        # 大促期 / 预售期基础加权（现有）
        campaign_mask = (
            df["is_campaign_day"].fillna(0).astype(bool) |
            df["is_presale"].fillna(0).astype(bool)
        )
        weights[campaign_mask] = weights[campaign_mask] * self.config["campaign_sample_weight"]

        # 预售单额外加权：is_presale_order=1 的行是尾款爆发日的真实标注
        # 这些行是最稀有、最难预测、最重要的样本，给予最高权重
        _presale_order_col = None
        for _c in ["is_presale_order", "是否预售单"]:
            if _c in df.columns:
                _presale_order_col = _c
                break
        if _presale_order_col is not None:
            presale_mask = df[_presale_order_col].fillna(0).astype(bool)
            # 在大促权重基础上再乘 1.5（兼顾已有大促权重）
            weights[presale_mask] = weights[presale_mask] * 1.5

        # 尾款爆发期额外加权（来自大促日历，不依赖 is_presale_order 标注）
        if "is_final_payment" in df.columns:
            fp_mask = df["is_final_payment"].fillna(0).astype(bool)
            weights[fp_mask] = np.maximum(
                weights[fp_mask],
                self.config["campaign_sample_weight"] * 1.5
            )

        return weights

    def _prepare_features(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        fit: bool = False,
        channel_df_for_anchor: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        if fit:
            self.shop_sensitivity_map = build_shop_campaign_sensitivity(
                df, date_col="date", shop_col="shop_id", order_col="order_count"
            )

        df = build_campaign_features(
            df,
            date_col="date",
            shop_col="shop_id",
            shop_sensitivity_map=self.shop_sensitivity_map,
            shop_override_map=self.shop_override_map,
        )
        df = build_all_time_features(
            df,
            target_col="order_count",
            group_cols=["shop_id", "channel"],
            date_col="date",
        )
        # 预售单量规模特征（所有渠道通用，列不存在时自动填0）
        df = build_presale_order_features(
            df, group_cols=["shop_id", "channel"], date_col="date"
        )

        if self.channel_type == "event":
            df = build_live_features(
                df,
                schedule_df=schedule_df,
                date_col="date",
                shop_col="shop_id",
                channel_df=channel_df_for_anchor,
                anchor_encoder=self.encoders.get("anchor", {}),
                anchor_df=anchor_df,
            )
        elif self.channel_type == "affiliate":
            df = build_affiliate_features(df, commission_df=commission_df, date_col="date", shop_col="shop_id")

        df = self._encode_categoricals(df, fit=fit)

        # 预测模式：前向填充 future 行的 lag/rolling 特征（修复多步预测中的 NaN 连锁）
        if not fit:
            df = fill_future_lag_features(df, "order_count", ["shop_id", "channel"])

        return df

    def _get_available_features(self, df: pd.DataFrame) -> List[str]:
        desired = FEATURE_COLS_MAP[self.channel_type]
        return [c for c in desired if c in df.columns]

    def fit(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> "ChannelTypeModel":
        """
        训练渠道子模型

        df 必须包含：date, shop_id, channel, order_count
        channel 值应已按 CHANNEL_TYPE_MAP 筛选为本类型的渠道
        """
        df = df.sort_values("date").reset_index(drop=True)

        # 构建 anchor encoder（event 类型专用，从 schedule_df 中提取达人名称）
        if self.channel_type == "event" and schedule_df is not None and "anchor_name" in schedule_df.columns:
            anchor_names = schedule_df["anchor_name"].dropna().unique()
            self.encoders["anchor"] = {name: i + 1 for i, name in enumerate(sorted(anchor_names))}

        # 先切分 train/val，再分别构建特征，避免验证集 lag 特征泄露
        val_cutoff = df["date"].max() - pd.Timedelta(days=self.config["validation_days"])
        train_raw = df[df["date"] <= val_cutoff].copy()
        val_raw   = df[df["date"] > val_cutoff].copy()
        if len(train_raw) == 0 or len(val_raw) == 0:
            raise ValueError(
                f"{self.channel_type} 训练或验证数据为空，请缩短 validation_days 或补充历史数据"
            )
        # 在 train_raw 上 fit：建立 encoder / sensitivity map
        train_df = self._prepare_features(
            train_raw, schedule_df, commission_df, fit=True,
            channel_df_for_anchor=df, anchor_df=anchor_df,
        )

        # 内层验证按“每日结束后获得实际值”的滚动口径构建。
        # lag/rolling 都已 shift/closed-left，使用前日实际值不会泄漏当日标签，
        # 且与外层 holdout 的逐日回测口径一致。
        full_df = self._prepare_features(
            df, schedule_df, commission_df, fit=False,
            channel_df_for_anchor=df, anchor_df=anchor_df,
        )
        val_df  = full_df[full_df["date"] > val_cutoff].copy()

        self.feature_cols = self._get_available_features(train_df)

        X_train = train_df[self.feature_cols]
        # log1p 变换：压缩右偏分布，使 L2 损失不被大促尖刺主导，
        # 同时让 lag/rolling 特征的残差贡献在对数尺度上均匀分布。
        y_train = np.log1p(train_df["order_count"].clip(lower=0))
        w_train = self._build_sample_weights(train_df)

        X_val = val_df[self.feature_cols]
        y_val = np.log1p(val_df["order_count"].clip(lower=0))

        dtrain = lgb.Dataset(X_train, label=y_train, weight=w_train, feature_name=self.feature_cols)
        dval = lgb.Dataset(X_val, label=y_val, reference=dtrain, feature_name=self.feature_cols)

        callbacks = [
            lgb.early_stopping(stopping_rounds=self.config["early_stopping_rounds"], verbose=False),
            lgb.log_evaluation(period=50),
        ]

        train_params = {k: v for k, v in self.params.items() if k != "n_estimators"}
        self.model = lgb.train(
            train_params,
            dtrain,
            num_boost_round=self.params.get("n_estimators", 300),
            valid_sets=[dval],
            callbacks=callbacks,
        )

        # log1p 模型直接 expm1 反变换会低估条件均值。使用 Duan smearing
        # 从训练残差学习类型级修正，再从内层验证集学习分渠道缩减校准。
        train_log_pred = self.model.predict(X_train)
        log_residual = np.asarray(y_train) - np.asarray(train_log_pred)
        residual_multiplier = np.exp(np.clip(log_residual, -2.0, 2.0))
        self.smearing_factor = float(np.clip(
            np.average(residual_multiplier, weights=w_train), 0.85, 1.60
        ))

        val_log_pred = self.model.predict(X_val)
        val_smear_pred = np.clip((
            np.exp(np.clip(val_log_pred, 0, None)) * self.smearing_factor - 1.0
        ), 0, None)
        calibration_frame = val_df[["channel", "order_count"]].copy()
        calibration_frame["_base_pred"] = val_smear_pred
        calibration_frame["_raw_pred"] = np.clip(np.expm1(np.clip(val_log_pred, 0, None)), 0, None)
        if "is_presale_payment" in val_df.columns:
            calibration_frame["_is_presale"] = (
                val_df["is_presale_payment"].fillna(0).astype(bool).to_numpy()
            )
        else:
            calibration_frame["_is_presale"] = False
        if "has_live" in val_df.columns:
            calibration_frame["_has_live"] = val_df["has_live"].fillna(0).astype(bool).to_numpy()
        else:
            calibration_frame["_has_live"] = False

        self.channel_calibration_factors = {}
        self.live_peak_factors = {}
        ordinary = calibration_frame[~calibration_frame["_is_presale"]]
        for channel, group in ordinary.groupby("channel"):
            actual_total = float(group["order_count"].clip(lower=0).sum())
            pred_total = float(group["_base_pred"].clip(lower=0).sum())
            if len(group) < 3 or actual_total <= 0 or pred_total <= 0:
                continue
            valid_ratio = group[group["_base_pred"] > 1e-6].copy()
            ratios = (
                valid_ratio["order_count"].clip(lower=0)
                / valid_ratio["_base_pred"].clip(lower=1e-6)
            ).to_numpy(dtype=float)
            ratio_weights = valid_ratio["_base_pred"].clip(lower=1e-6).to_numpy(dtype=float)
            order = np.argsort(ratios)
            sorted_ratios = ratios[order]
            sorted_weights = ratio_weights[order]
            median_index = int(np.searchsorted(
                np.cumsum(sorted_weights), sorted_weights.sum() / 2.0, side="left"
            ))
            mae_optimal_ratio = float(sorted_ratios[min(median_index, len(sorted_ratios) - 1)])
            volume_ratio = actual_total / pred_total
            # 主力低估渠道兼顾总量 Bias；其他渠道优先最小化 WAPE 分子。
            if str(channel) in PRIORITY_BIAS_CHANNEL_CAPS:
                desired_ratio = 0.70 * mae_optimal_ratio + 0.30 * volume_ratio
            else:
                desired_ratio = mae_optimal_ratio
            reliability = min(1.0, len(group) / 21.0) * min(1.0, actual_total / 5000.0)
            shrunk = 1.0 + reliability * (desired_ratio - 1.0)
            total_upper = PRIORITY_BIAS_CHANNEL_CAPS.get(str(channel), 1.35)
            channel_factor = float(np.clip(
                shrunk, 0.60, total_upper / max(self.smearing_factor, 1e-6)
            ))

            # 直播峰值仅对 tm_zb 使用。其他 event 渠道样本过少时，双重倍数会
            # 把偶发尖峰放大（历史上 dy_zb 就出现过），交给 has_live 特征本身处理。
            live_factor = 1.0
            live_group = group[group["_has_live"]]
            if self.channel_type == "event" and str(channel) == "tm_zb" and len(live_group) >= 4:
                live_base = live_group["_base_pred"].clip(lower=1e-6) * channel_factor
                live_ratios = (
                    live_group["order_count"].clip(lower=0) / live_base
                ).to_numpy(dtype=float)
                live_weights = live_base.to_numpy(dtype=float)
                live_order = np.argsort(live_ratios)
                live_sorted_ratios = live_ratios[live_order]
                live_sorted_weights = live_weights[live_order]
                if live_sorted_weights.sum() > 0:
                    live_index = int(np.searchsorted(
                        np.cumsum(live_sorted_weights), live_sorted_weights.sum() / 2.0,
                        side="left",
                    ))
                    live_ratio = float(live_sorted_ratios[
                        min(live_index, len(live_sorted_ratios) - 1)
                    ])
                    live_reliability = min(1.0, len(live_group) / 8.0)
                    live_shrunk = 1.0 + live_reliability * (live_ratio - 1.0)
                    live_upper = min(
                        1.50,
                        2.50 / max(self.smearing_factor * channel_factor, 1e-6),
                    )
                    live_factor = float(np.clip(live_shrunk, 0.80, live_upper))

            candidate = group["_base_pred"].to_numpy(dtype=float) * channel_factor
            if live_factor != 1.0:
                candidate *= np.where(group["_has_live"].to_numpy(dtype=bool), live_factor, 1.0)
            actual = group["order_count"].clip(lower=0).to_numpy(dtype=float)
            raw = group["_raw_pred"].clip(lower=0).to_numpy(dtype=float)
            # WAPE 护栏：在内层验证上不允许修正后的绝对误差超过原始预测。
            if np.abs(actual - candidate).sum() > np.abs(actual - raw).sum():
                channel_factor = 1.0 / max(self.smearing_factor, 1e-6)
                live_factor = 1.0

            self.channel_calibration_factors[str(channel)] = channel_factor
            if live_factor != 1.0:
                self.live_peak_factors[str(channel)] = live_factor

        self.stable_baseline_factor = 1.0
        self.stable_baseline_weight = 0.0
        self.stable_low_cardinality_channels = set()
        if self.channel_type == "stable" and "order_count_roll_mean7" in val_df.columns:
            calibrated_val_pred = val_smear_pred * (
                val_df["channel"].astype(str)
                .map(self.channel_calibration_factors)
                .fillna(1.0)
                .to_numpy(dtype=float)
            )
            rolling_mean7 = pd.to_numeric(
                val_df["order_count_roll_mean7"], errors="coerce"
            ).to_numpy(dtype=float)
            self.stable_baseline_factor, self.stable_baseline_weight = (
                _select_stable_baseline(
                    val_df["order_count"].to_numpy(dtype=float),
                    calibrated_val_pred,
                    rolling_mean7,
                )
            )
            # 同一渠道覆盖店铺很多时（典型如“其他”），店铺差异较大，
            # 保留 LightGBM 拟合横截差异。低店铺数的明确稳定渠道更适合
            # 短期水平基线，使其能及时跟随近期变化。
            shop_counts = df.groupby("channel")["shop_id"].nunique()
            self.stable_low_cardinality_channels = {
                str(channel)
                for channel, count in shop_counts.items()
                if int(count) <= 20
            }
            print(
                "  [稳定基线] "
                f"mean7系数={self.stable_baseline_factor:.3f}, "
                f"高基数融合权重={self.stable_baseline_weight:.2f}, "
                f"低基数渠道={len(self.stable_low_cardinality_channels)}个"
            )


        print(
            f"  [Bias校准] {self.channel_type}: smearing={self.smearing_factor:.3f}, "
            f"渠道={len(self.channel_calibration_factors)}个, "
            f"直播峰值={len(self.live_peak_factors)}个"
        )

        # 训练预售分布模型（event 类型专用）
        if self.channel_type == "event" and self.presale_model is not None and schedule_df is not None:
            try:
                events = PresaleDistributionModel.build_payment_events_from_df(
                    schedule_df=schedule_df,
                    channel_df=df,
                )
                if events:
                    self.presale_model.fit(events)
                    n_events = sum(len(v) for v in self.presale_model.volume_history.values())
                    print(f"  [预售模型] fit 完成，覆盖 {n_events} 个历史预售事件")
                else:
                    print("  [预售模型] schedule_df 无 presale_payment 行，跳过 fit")
            except Exception as e:
                print(f"  [预售模型] fit 失败（不影响 LightGBM）: {e}")

        return self

    def predict(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> np.ndarray:
        calibrated, _ = self.predict_with_raw(
            df, schedule_df=schedule_df,
            commission_df=commission_df, anchor_df=anchor_df,
        )
        return calibrated

    def predict_with_raw(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
        return_guardrail: bool = False,
    ):
        """Return calibrated and raw predictions, optionally guardrail candidate.

        The guardrail candidate deliberately keeps smearing and the stable
        rolling-level blend.  It only removes channel-specific calibration and
        live-peak multipliers, so a tiny holdout difference cannot switch a
        stable channel all the way back to the much lower log-model median.
        """
        assert self.model is not None, f"{self.channel_type} 模型未训练"
        df = self._prepare_features(
            df, schedule_df, commission_df, fit=False,
            channel_df_for_anchor=None, anchor_df=anchor_df,
        )
        # pipeline 会把历史行与当日待预测行拼接来计算时序特征。
        # 历史行只是特征上下文，无需再调用 LightGBM 预测数万次。
        if "_is_future" in df.columns and df["_is_future"].fillna(False).any():
            predict_mask = df["_is_future"].fillna(False).astype(bool)
        else:
            predict_mask = pd.Series(True, index=df.index)
        predict_df = df.loc[predict_mask].copy()
        X = predict_df[self.feature_cols] if all(c in predict_df.columns for c in self.feature_cols) \
            else predict_df.reindex(columns=self.feature_cols, fill_value=0)
        # 逆变换 log1p → expm1，还原到原始单量尺度
        log_preds = self.model.predict(X)
        raw_target_preds = np.expm1(np.clip(log_preds, 0, None))
        uncalibrated_preds = np.clip((
            np.exp(np.clip(log_preds, 0, None)) * self.smearing_factor - 1.0
        ), 0, None)

        # 正常流水线始终带 channel；保留无 channel 的兼容分支，便于单模型调用和回归测试。
        prediction_channels = (
            predict_df["channel"].astype(str)
            if "channel" in predict_df.columns
            else pd.Series("", index=predict_df.index, dtype=str)
        )
        channel_factor = prediction_channels.map(
            self.channel_calibration_factors
        ).fillna(1.0).to_numpy(dtype=float)
        target_preds = uncalibrated_preds * channel_factor
        guardrail_preds = uncalibrated_preds.copy()
        if self.channel_type == "stable" and (
            self.stable_baseline_weight > 0 or self.stable_low_cardinality_channels
        ):
            baseline = pd.to_numeric(
                predict_df.get("order_count_roll_mean7"), errors="coerce"
            )
            if baseline is not None:
                for fallback_col in (
                    "order_count_roll_mean14", "order_count_roll_mean30"
                ):
                    if fallback_col in predict_df.columns:
                        baseline = baseline.fillna(pd.to_numeric(
                            predict_df[fallback_col], errors="coerce"
                        ))
                baseline_values = baseline.to_numpy(dtype=float)
                baseline_values = np.where(
                    np.isfinite(baseline_values),
                    np.clip(baseline_values, 0, None) * self.stable_baseline_factor,
                    target_preds,
                )
                baseline_weights = np.where(
                    prediction_channels.isin(
                        self.stable_low_cardinality_channels
                    ).to_numpy(dtype=bool),
                    max(0.40, self.stable_baseline_weight),
                    self.stable_baseline_weight,
                )
                target_preds = (
                    (1.0 - baseline_weights) * target_preds
                    + baseline_weights * baseline_values
                )
                guardrail_preds = (
                    (1.0 - baseline_weights) * guardrail_preds
                    + baseline_weights * baseline_values
                )
        if self.channel_type == "event" and "has_live" in predict_df.columns:
            live_factor = prediction_channels.map(
                self.live_peak_factors
            ).fillna(1.0).to_numpy(dtype=float)
            is_live = predict_df["has_live"].fillna(0).astype(bool).to_numpy()
            target_preds = target_preds * np.where(is_live, live_factor, 1.0)

        if self.raw_fallback_channels:
            fallback_mask = prediction_channels.isin(
                self.raw_fallback_channels
            ).to_numpy(dtype=bool)
            target_preds = np.where(fallback_mask, guardrail_preds, target_preds)

        # 预售覆盖：对 is_presale_payment=1 的行用规则预测替换 LightGBM 预测
        if self.channel_type == "event" and self.presale_model is not None:
            target_preds = self._apply_presale_override(
                predict_df, target_preds, schedule_df
            )
            raw_target_preds = self._apply_presale_override(
                predict_df, raw_target_preds, schedule_df
            )
            guardrail_preds = self._apply_presale_override(
                predict_df, guardrail_preds, schedule_df
            )

        preds = np.full(len(df), np.nan, dtype=float)
        raw_preds = np.full(len(df), np.nan, dtype=float)
        fallback_preds = np.full(len(df), np.nan, dtype=float)
        preds[np.flatnonzero(predict_mask.to_numpy())] = target_preds
        raw_preds[np.flatnonzero(predict_mask.to_numpy())] = raw_target_preds
        fallback_preds[np.flatnonzero(predict_mask.to_numpy())] = guardrail_preds
        if return_guardrail:
            return preds, raw_preds, fallback_preds
        return preds, raw_preds

    def _apply_presale_override(
        self,
        df: pd.DataFrame,
        preds: np.ndarray,
        schedule_df: Optional[pd.DataFrame],
    ) -> np.ndarray:
        """
        加法模型：对 is_presale_payment=1 的行，将 PresaleDistributionModel 预测值
        叠加到 LightGBM 预测上（LightGBM 已学习普通单+流量溢出效应）。
        非预售日保持 LightGBM 预测不变。
        """
        if "is_presale_payment" not in df.columns:
            return preds

        presale_mask = df["is_presale_payment"].fillna(0).astype(bool)
        if presale_mask.sum() == 0:
            return preds

        preds = preds.copy()

        # 按 (shop_id, broadcast_date / 预估事件) 分组计算
        # 从 schedule_df 中找到各预售事件的 broadcast_date 信息
        pay_event_map: Dict = {}  # {(shop_id, channel|None, payment_date): broadcast_date}
        pay_rows = pd.DataFrame()
        if schedule_df is not None and len(schedule_df) > 0:
            pay_rows = schedule_df[
                schedule_df.get("event_type", pd.Series("live", index=schedule_df.index)) == "presale_payment"
            ] if "event_type" in schedule_df.columns else pd.DataFrame()
            for _, r in pay_rows.iterrows():
                ch = (
                    str(r["channel"])
                    if "channel" in pay_rows.columns and pd.notna(r.get("channel"))
                    else None
                )
                pay_event_map[(str(r["shop_id"]), ch, pd.Timestamp(r["date"]).date())] = r.get(
                    "broadcast_date"
                )

        # 按 (shop_id, channel, 推断 broadcast_date) 分组处理每个预售事件
        presale_df = df[presale_mask].copy()
        presale_df["_date"] = pd.to_datetime(presale_df["date"]).dt.date

        # 反查 broadcast_date 确定事件分组键
        def _get_bcast(row):
            shop = str(row.get("shop_id", ""))
            channel = str(row.get("channel", ""))
            bd = pay_event_map.get((shop, channel, row["_date"]))
            if bd is None:
                bd = pay_event_map.get((shop, None, row["_date"]))
            return pd.Timestamp(bd).date() if pd.notna(bd) and bd is not None else None

        presale_df["_bcast"] = presale_df.apply(_get_bcast, axis=1)

        # 对每个事件（shop_id × channel × bcast_date）做预售覆盖
        event_groups = presale_df.groupby(
            ["shop_id", "channel", "_bcast"], dropna=False
        )
        for (shop_id, channel, bcast_date), grp in event_groups:
            payment_dates = sorted(grp["_date"].unique())
            # 逐日递归预测时，df 里只有当前未来日；使用排期中同一事件的
            # 完整尾款日期，否则单日分布会被归一化为 100%。
            if bcast_date is not None and len(pay_rows) > 0:
                event_rows = pay_rows[
                    (pay_rows["shop_id"].astype(str) == str(shop_id))
                    & (pd.to_datetime(pay_rows["broadcast_date"]).dt.date == bcast_date)
                ]
                if "channel" in event_rows.columns:
                    event_rows = event_rows[
                        event_rows["channel"].isna()
                        | (event_rows["channel"].astype(str) == str(channel))
                    ]
                scheduled_dates = sorted(pd.to_datetime(event_rows["date"]).dt.date.unique())
                if scheduled_dates:
                    payment_dates = scheduled_dates
            try:
                vol_map = self.presale_model.predict_daily_volumes(
                    payment_dates=payment_dates,
                    shop_id=str(shop_id),
                    channel=str(channel),
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"预售覆盖失败 (shop={shop_id}, ch={channel}): {e}，保留 LightGBM 预测"
                )
                continue

            # 加法：LightGBM 预测的是普通单（已剥离预售），加上预售分布预测
            for orig_idx, row in grp.iterrows():
                predicted_vol = vol_map.get(row["_date"])
                if predicted_vol is not None and predicted_vol > 0:
                    pos = df.index.get_loc(orig_idx)
                    preds[pos] = preds[pos] + predicted_vol   # 相加，不替换

        return preds

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "model": self.model,
            "feature_cols": self.feature_cols,
            "encoders": self.encoders,
            "channel_type": self.channel_type,
            "shop_sensitivity_map": self.shop_sensitivity_map,
            "shop_override_map": self.shop_override_map,
            "presale_model": self.presale_model,
            "smearing_factor": self.smearing_factor,
            "channel_calibration_factors": self.channel_calibration_factors,
            "live_peak_factors": self.live_peak_factors,
            "stable_baseline_factor": self.stable_baseline_factor,
            "stable_baseline_weight": self.stable_baseline_weight,
            "stable_low_cardinality_channels": sorted(
                self.stable_low_cardinality_channels
            ),
            "raw_fallback_channels": sorted(self.raw_fallback_channels),
        }, path)

    @classmethod
    def load(cls, path: str) -> "ChannelTypeModel":
        data = joblib.load(path)
        obj = cls(channel_type=data["channel_type"])
        obj.model = data["model"]
        obj.feature_cols = data["feature_cols"]
        obj.encoders = data["encoders"]
        obj.shop_sensitivity_map = data.get("shop_sensitivity_map", {})
        obj.shop_override_map = data.get("shop_override_map", {})
        obj.presale_model = data.get("presale_model") or obj.presale_model
        obj.smearing_factor = data.get("smearing_factor", 1.0)
        obj.channel_calibration_factors = data.get("channel_calibration_factors", {})
        obj.live_peak_factors = data.get("live_peak_factors", {})
        obj.stable_baseline_factor = data.get("stable_baseline_factor", 1.0)
        obj.stable_baseline_weight = data.get("stable_baseline_weight", 0.0)
        obj.stable_low_cardinality_channels = set(
            data.get("stable_low_cardinality_channels", [])
        )
        obj.raw_fallback_channels = set(data.get("raw_fallback_channels", []))
        return obj


class ChannelEnsemble:
    """
    管理三类渠道子模型，统一训练/预测接口
    """

    def __init__(self, channel_params: Optional[Dict] = None, training_config: Optional[Dict] = None,
                 shop_override_map: Optional[Dict[str, str]] = None,
                 channel_type_map: Optional[Dict[str, str]] = None):
        """
        channel_params: {channel_type: {param_name: value}}，为 None 时使用 CHANNEL_MODEL_PARAMS
        training_config: 覆盖 TRAINING_CONFIG（campaign_sample_weight / early_stopping_rounds 等）
        shop_override_map: {shop_id: "on"/"off"/"auto"}，手动覆盖大促参与模式
        channel_type_map: {channel_id: "stable"/"event"/"affiliate"}，用户配置优先；
                          为 None 时回退到 routing_config 中的硬编码默认值
        """
        _override = shop_override_map or {}
        self.models: Dict[str, ChannelTypeModel] = {
            ctype: ChannelTypeModel(
                ctype,
                params=channel_params.get(ctype) if channel_params else None,
                config=training_config,
            )
            for ctype in ("stable", "event", "affiliate")
        }
        # 将手动覆盖注入每个子模型
        for m in self.models.values():
            m.shop_override_map = _override
        # 用户配置优先；未传入时回退到硬编码默认值（兼容非 UI 调用场景）
        self.channel_type_map = channel_type_map if channel_type_map else CHANNEL_TYPE_MAP
        self.anchor_live_model = AnchorLiveOrderModel()

    def _split_by_type(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """按渠道类型拆分数据"""
        df = df.copy()
        df["_channel_type"] = df["channel"].map(self.channel_type_map)

        # 未知渠道归入 stable
        df["_channel_type"] = df["_channel_type"].fillna("stable")

        return {
            ctype: df[df["_channel_type"] == ctype].drop(columns=["_channel_type"])
            for ctype in ("stable", "event", "affiliate")
        }

    def fit(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> "ChannelEnsemble":
        """训练所有子模型"""
        splits = self._split_by_type(df)

        event_history = splits.get("event", pd.DataFrame())
        self.anchor_live_model.fit(
            anchor_df=anchor_df,
            schedule_df=schedule_df,
            event_order_df=event_history,
        )
        if self.anchor_live_model.is_fitted and len(event_history) > 0:
            splits["event"] = self.anchor_live_model.remove_actuals_from_event_history(
                event_history, anchor_df
            )

        base_schedule_df = _schedule_for_base_model(schedule_df)

        for ctype, sub_df in splits.items():
            if len(sub_df) == 0:
                print(f"  [跳过] {ctype} 类无数据")
                continue
            print(f"  [训练] {ctype} 模型，样本数: {len(sub_df)}")
            kwargs = {}
            if ctype == "event":
                kwargs["schedule_df"] = base_schedule_df
                # 达人名称/历史量级只属于独立达人增量模型，不进入基础模型。
                kwargs["anchor_df"] = None
            elif ctype == "affiliate":
                kwargs["commission_df"] = commission_df
            self.models[ctype].fit(sub_df, **kwargs)

        return self

    def predict(
        self,
        df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        预测各渠道单量

        返回：原始 df 附加 predicted_orders 列
        """
        df = df.copy()
        df["predicted_orders"] = np.nan
        df["raw_predicted_orders"] = np.nan
        df["guardrail_predicted_orders"] = np.nan
        df["_channel_type"] = df["channel"].map(self.channel_type_map).fillna("stable")

        base_schedule_df = _schedule_for_base_model(schedule_df)

        for ctype in ("stable", "event", "affiliate"):
            mask = df["_channel_type"] == ctype
            if mask.sum() == 0:
                continue
            sub_df = df[mask].copy()
            kwargs = {}
            if ctype == "event":
                kwargs["schedule_df"] = base_schedule_df
                kwargs["anchor_df"] = None
                sub_df = self.anchor_live_model.remove_actuals_from_event_history(
                    sub_df, anchor_df
                )
            elif ctype == "affiliate":
                kwargs["commission_df"] = commission_df
            preds, raw_preds, fallback_preds = self.models[ctype].predict_with_raw(
                sub_df, return_guardrail=True, **kwargs
            )
            df.loc[mask, "predicted_orders"] = preds
            df.loc[mask, "raw_predicted_orders"] = raw_preds
            df.loc[mask, "guardrail_predicted_orders"] = fallback_preds

        # event 模型的训练标签已剔除达人实绩；在这里把排期达人单量加回。
        df["base_predicted_orders"] = df["predicted_orders"]
        df["anchor_predicted_orders"] = 0.0
        if self.anchor_live_model.is_fitted and schedule_df is not None:
            additions = self.anchor_live_model.predict_schedule(schedule_df)
            if len(additions) > 0:
                date_key = pd.to_datetime(df["date"]).dt.normalize()
                addition_map = additions.set_index(["date", "shop_id", "channel"])[
                    "anchor_predicted_orders"
                ]
                keys = list(zip(date_key, df["shop_id"].astype(str), df["channel"].astype(str)))
                added = np.array([float(addition_map.get(key, 0.0)) for key in keys])

                # 用户在排期中明确填写渠道时，该值优先于模型按店铺推断的渠道。
                # 这样新渠道或同店多直播渠道也能准确落到预测表；未填写渠道的
                # 旧排期仍只对 event 类渠道生效，避免误把直播增量叠到货架渠道。
                explicit_keys = set()
                platform_map = {}
                if "channel" in schedule_df.columns:
                    explicit = schedule_df.copy()
                    explicit["date"] = pd.to_datetime(explicit["date"], errors="coerce").dt.normalize()
                    if "shop_id" not in explicit.columns:
                        explicit["shop_id"] = ""
                    explicit["shop_id"] = explicit["shop_id"].fillna("").astype(str).str.strip()
                    explicit["channel"] = explicit["channel"].astype("string").str.strip().replace("", pd.NA)
                    explicit = explicit.dropna(subset=["date", "channel"])
                    if "event_type" in explicit.columns:
                        explicit = explicit[explicit["event_type"].fillna("live").eq("live")]
                    explicit_keys = set(zip(
                        explicit["date"], explicit["shop_id"], explicit["channel"].astype(str)
                    ))
                    if "platform" in explicit.columns:
                        platform_map = {
                            (row.date, str(row.shop_id), str(row.channel)): row.platform
                            for row in explicit.itertuples()
                        }

                event_mask = df["_channel_type"].eq("event").to_numpy()
                explicit_mask = np.array([key in explicit_keys for key in keys])
                added = np.where(event_mask | explicit_mask, added, 0.0)
                df["anchor_predicted_orders"] = added
                df["predicted_orders"] = df["predicted_orders"] + added
                df["raw_predicted_orders"] = df["raw_predicted_orders"] + added
                df["guardrail_predicted_orders"] = (
                    df["guardrail_predicted_orders"] + added
                )

                # 若手工填写的是一个历史预测组合中不存在的新渠道，补一条纯直播
                # 增量记录，避免排期已保存但预测表和分仓结果完全看不到该渠道。
                existing_keys = set(keys)
                # 滚动预测会把近 N 天历史和“当前待预测日”拼在一起，而达人
                # 排期中还包含更早的历史场次。补行只能针对本轮真正的预测日，
                # 否则窗口外历史场次会被误补回来，并产生 _is_future=NaN。
                if "_is_future" in df.columns:
                    future_mask = df["_is_future"].fillna(False).astype(bool)
                    prediction_dates = set(date_key[future_mask])
                else:
                    prediction_dates = set(date_key)
                extra_rows = []
                for row in additions.itertuples():
                    key = (pd.Timestamp(row.date).normalize(), str(row.shop_id), str(row.channel))
                    if (
                        key[0] not in prediction_dates
                        or key not in explicit_keys
                        or key in existing_keys
                    ):
                        continue
                    extra = {column: np.nan for column in df.columns}
                    extra.update({
                        "date": key[0],
                        "shop_id": key[1],
                        "channel": key[2],
                        "platform": platform_map.get(key),
                        "predicted_orders": float(row.anchor_predicted_orders),
                        "raw_predicted_orders": float(row.anchor_predicted_orders),
                        "guardrail_predicted_orders": float(row.anchor_predicted_orders),
                        "base_predicted_orders": 0.0,
                        "anchor_predicted_orders": float(row.anchor_predicted_orders),
                        "_channel_type": "event",
                    })
                    if "_is_future" in df.columns:
                        extra["_is_future"] = True
                    extra_rows.append(extra)
                    existing_keys.add(key)
                if extra_rows:
                    df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)

        df = df.drop(columns=["_channel_type"])
        return df

    def set_raw_fallback_channels(self, channels) -> None:
        """回退渠道倍数校准，同时保留 smearing 和稳定滚动基线。"""
        selected = {str(channel) for channel in channels}
        for model in self.models.values():
            model.raw_fallback_channels.clear()
        for channel in selected:
            channel_type = self.channel_type_map.get(channel, "stable")
            self.models[channel_type].raw_fallback_channels.add(channel)

    def save(self, dir_path: str) -> None:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        for ctype, model in self.models.items():
            model.save(f"{dir_path}/{ctype}_model.joblib")
        joblib.dump(self.anchor_live_model, f"{dir_path}/anchor_live_model.joblib")
        # 持久化 channel_type_map，保证 load() 后预测路由与训练时一致
        joblib.dump({"channel_type_map": self.channel_type_map},
                    f"{dir_path}/channel_type_map.joblib")

    def load(self, dir_path: str) -> "ChannelEnsemble":
        for ctype in ("stable", "event", "affiliate"):
            path = f"{dir_path}/{ctype}_model.joblib"
            if Path(path).exists():
                self.models[ctype] = ChannelTypeModel.load(path)
        # 恢复 channel_type_map（若存在持久化文件，否则保留构造时的默认值）
        map_path = f"{dir_path}/channel_type_map.joblib"
        if Path(map_path).exists():
            self.channel_type_map = joblib.load(map_path)["channel_type_map"]
        anchor_path = f"{dir_path}/anchor_live_model.joblib"
        if Path(anchor_path).exists():
            self.anchor_live_model = joblib.load(anchor_path)
        return self
