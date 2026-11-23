"""
第一层：全局基础模型
- 以店铺为粒度，全渠道合并训练
- 捕捉平台级季节性、大促效应
- 使用 LightGBM，大促期间样本加权
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import joblib
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from config.model_config import GLOBAL_MODEL_PARAMS, TRAINING_CONFIG
from src.features.campaign_features import build_campaign_features, build_shop_campaign_sensitivity
from src.features.time_features import build_all_time_features, fill_future_lag_features


GLOBAL_FEATURE_COLS = [
    # 时间基础特征
    "day_of_week", "day_of_month", "month", "quarter", "week_of_year",
    "is_weekend", "is_month_start", "is_month_end",
    "dow_sin", "dow_cos", "month_sin", "month_cos",
    # 大促特征
    "days_to_nearest_campaign", "nearest_campaign_code",
    "is_preheat", "is_presale", "is_campaign_day", "is_post_campaign",
    "campaign_intensity",
    "shop_campaign_sensitivity", "effective_campaign_intensity",
    # 滞后特征
    "order_count_lag1", "order_count_lag7", "order_count_lag14", "order_count_lag28",
    # 滚动特征
    "order_count_roll_mean7", "order_count_roll_mean14", "order_count_roll_mean30",
    "order_count_roll_std7", "order_count_roll_std14", "order_count_roll_std30",
    # 同比
    "order_count_yoy",
    # 店铺/平台编码
    "shop_id_code", "platform_code",
]


class GlobalBaseModel:
    """
    第一层全局基础模型
    全渠道合并，输出店铺级日粒度总单量预测
    """

    def __init__(self, params: Optional[Dict] = None, config: Optional[Dict] = None):
        self.params = params or GLOBAL_MODEL_PARAMS
        self.config = config or TRAINING_CONFIG
        self.model: Optional[lgb.Booster] = None
        self.feature_cols: List[str] = []
        self.encoders: Dict = {}  # 类别编码映射
        self.shop_sensitivity_map: Dict[str, float] = {}  # 店铺大促敏感度
        self.shop_override_map: Dict[str, str] = {}       # 手动覆盖 on/off/auto

    def _encode_categoricals(self, df: pd.DataFrame, fit: bool = False) -> pd.DataFrame:
        """对 shop_id、platform 做 label encoding"""
        df = df.copy()
        for col in ["shop_id", "platform"]:
            out_col = f"{col}_code"
            if fit:
                unique_vals = sorted(df[col].dropna().unique())
                self.encoders[col] = {v: i for i, v in enumerate(unique_vals)}
            df[out_col] = df[col].map(self.encoders.get(col, {})).fillna(-1).astype(int)
        return df

    def _build_sample_weights(self, df: pd.DataFrame) -> np.ndarray:
        """时间衰减 + 大促期间样本加权"""
        weights = np.ones(len(df))
        # 时间衰减：半衰期 180 天，近期数据权重更高
        if "date" in df.columns:
            max_date = df["date"].max()
            days_back = (max_date - df["date"]).dt.days.clip(lower=0)
            decay = np.power(0.5, days_back / 180.0)
            weights = weights * np.clip(decay, 0.1, 1.0)
        campaign_mask = (
            df["is_campaign_day"].fillna(0).astype(bool) |
            df["is_presale"].fillna(0).astype(bool)
        )
        weights[campaign_mask] = weights[campaign_mask] * self.config["campaign_sample_weight"]
        return weights

    def _prepare_features(
        self,
        df: pd.DataFrame,
        target_col: str = "order_count",
        group_cols: List[str] = None,
        fit: bool = False,
    ) -> pd.DataFrame:
        """特征工程流水线"""
        if group_cols is None:
            group_cols = ["shop_id", "platform"]

        # 训练时从数据自动计算敏感度；预测时复用已存储的 map
        if fit:
            self.shop_sensitivity_map = build_shop_campaign_sensitivity(
                df, date_col="date", shop_col="shop_id", order_col=target_col
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
            target_col=target_col,
            group_cols=group_cols,
            date_col="date",
        )
        df = self._encode_categoricals(df, fit=fit)
        return df

    def _get_available_features(self, df: pd.DataFrame) -> List[str]:
        """从全局特征列表中取实际存在的列"""
        return [c for c in GLOBAL_FEATURE_COLS if c in df.columns]

    def fit(self, df: pd.DataFrame, target_col: str = "order_count") -> "GlobalBaseModel":
        """
        训练全局模型

        df 必须包含：date, shop_id, platform, order_count
        """
        df = df.sort_values("date").reset_index(drop=True)

        # 先切分原始数据再构建特征，验证期目标置空，避免 lag/rolling 和
        # 店铺大促敏感度偷看验证期真实订单。
        val_cutoff = df["date"].max() - pd.Timedelta(days=self.config["validation_days"])
        train_raw = df[df["date"] <= val_cutoff].copy()
        val_raw = df[df["date"] > val_cutoff].copy()
        if len(train_raw) == 0 or len(val_raw) == 0:
            raise ValueError("训练或验证数据为空，请缩短 validation_days 或补充历史数据")

        train_df = self._prepare_features(train_raw, target_col=target_col, fit=True)
        val_labels = val_raw[["shop_id", "platform", "date", target_col]].copy()
        val_raw[target_col] = np.nan
        combined_raw = pd.concat([train_raw, val_raw], ignore_index=True)
        full_df = self._prepare_features(combined_raw, target_col=target_col, fit=False)
        full_df = fill_future_lag_features(
            full_df, target_col, ["shop_id", "platform"]
        )
        val_df = full_df[full_df["date"] > val_cutoff].drop(
            columns=[target_col], errors="ignore"
        )
        val_df = val_df.merge(
            val_labels,
            on=["shop_id", "platform", "date"],
            how="left",
            validate="one_to_one",
        )

        self.feature_cols = self._get_available_features(train_df)

        X_train = train_df[self.feature_cols]
        # log1p 变换：压缩右偏分布，L2 损失在对数尺度上均匀响应 lag 特征。
        y_train = np.log1p(train_df[target_col].clip(lower=0))
        w_train = self._build_sample_weights(train_df)

        X_val = val_df[self.feature_cols]
        y_val = np.log1p(val_df[target_col].clip(lower=0))

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
            num_boost_round=self.params.get("n_estimators", 500),
            valid_sets=[dval],
            callbacks=callbacks,
        )

        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """
        预测

        df 必须包含：date, shop_id, platform
        已有历史数据（用于滞后/滚动特征计算）
        """
        assert self.model is not None, "模型未训练，请先调用 fit()"
        df = self._prepare_features(df, target_col="order_count", fit=False)
        feature_cols = self._get_available_features(df)
        X = df[feature_cols].reindex(columns=self.feature_cols, fill_value=0)
        # 逆变换 log1p → expm1，还原到原始单量尺度
        preds = self.model.predict(X)
        return np.expm1(np.clip(preds, 0, None))

    def feature_importance(self) -> pd.DataFrame:
        """返回特征重要性 DataFrame"""
        assert self.model is not None
        importance = self.model.feature_importance(importance_type="gain")
        return pd.DataFrame({
            "feature": self.feature_cols,
            "importance": importance,
        }).sort_values("importance", ascending=False)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "model": self.model,
            "feature_cols": self.feature_cols,
            "encoders": self.encoders,
            "shop_sensitivity_map": self.shop_sensitivity_map,
            "shop_override_map": self.shop_override_map,
        }, path)

    @classmethod
    def load(cls, path: str) -> "GlobalBaseModel":
        obj = cls()
        data = joblib.load(path)
        obj.model = data["model"]
        obj.feature_cols = data["feature_cols"]
        obj.encoders = data["encoders"]
        obj.shop_sensitivity_map = data.get("shop_sensitivity_map", {})
        obj.shop_override_map = data.get("shop_override_map", {})
        return obj
