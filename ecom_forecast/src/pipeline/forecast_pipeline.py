"""
端到端训练/预测流水线
整合三层架构：全局模型 → 渠道模型 → 分仓路由
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Tuple, Callable, Iterable
import joblib

from src.models.base_model import GlobalBaseModel
from src.models.channel_models import ChannelEnsemble
from src.routing.warehouse_router import WarehouseRouter
from evaluation.metrics import evaluate, evaluate_warehouse, print_evaluation_report


def apply_channel_calibration_guardrail(
    merged: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict]]:
    """独立 holdout 上逐渠道比较，校准变差时回退到稳定候选值。

    新版候选值去掉渠道倍数校准，但保留 smearing 与稳定滚动基线，避免
    安全阀的一次二元切换把未来基础量直接压回 log 模型中位数。旧模型或
    旧测试数据没有该列时，仍兼容使用 raw_predicted_orders。
    """
    guarded = merged.copy()
    required = {
        "channel", "order_count", "predicted_orders", "raw_predicted_orders"
    }
    if guarded.empty or not required.issubset(guarded.columns):
        return guarded, []

    details = []
    for channel, group in guarded.groupby("channel", dropna=False):
        actual = pd.to_numeric(group["order_count"], errors="coerce")
        calibrated = pd.to_numeric(group["predicted_orders"], errors="coerce")
        fallback_col = (
            "guardrail_predicted_orders"
            if "guardrail_predicted_orders" in group.columns
            else "raw_predicted_orders"
        )
        fallback = pd.to_numeric(group[fallback_col], errors="coerce")
        valid = actual.notna() & calibrated.notna() & fallback.notna()
        if not valid.any():
            continue
        actual_valid = actual[valid].clip(lower=0)
        calibrated_error = float(
            (actual_valid - calibrated[valid].clip(lower=0)).abs().sum()
        )
        fallback_error = float(
            (actual_valid - fallback[valid].clip(lower=0)).abs().sum()
        )
        if calibrated_error <= fallback_error + 1e-9:
            continue
        actual_total = float(actual_valid.sum())
        details.append({
            "channel": str(channel),
            "n_samples": int(valid.sum()),
            "actual_total": actual_total,
            # 保留 raw_wape 键兼容训练日志；其含义现在是安全回退候选 WAPE。
            "raw_wape": fallback_error / actual_total * 100 if actual_total > 0 else None,
            "fallback_wape": (
                fallback_error / actual_total * 100 if actual_total > 0 else None
            ),
            "calibrated_wape": (
                calibrated_error / actual_total * 100 if actual_total > 0 else None
            ),
        })
        guarded.loc[group.index[valid], "predicted_orders"] = fallback[valid].to_numpy()

    return guarded, details


class ForecastPipeline:
    """
    三层预测流水线

    Phase 1 (MVP):  全局模型，验证精度
    Phase 2:        渠道子模型，精度提升
    Phase 3:        分仓路由，输出仓级预测
    """

    def __init__(self, model_dir: str = "models", config_dict: Optional[Dict] = None):
        self.model_dir = Path(model_dir)
        self.config_dict = config_dict or {}

        # 从注入的配置覆盖全局配置模块变量
        if self.config_dict:
            from config.routing_config import load_from_dict
            load_from_dict(self.config_dict)

        # 从 shops 配置自动构建 shop_override_map（优先使用显式传入的）
        # shops 列表中每个 shop 可有 campaign_mode: "auto"/"on"/"off"
        if "shop_override_map" not in self.config_dict:
            shops = self.config_dict.get("shops", [])
            self.config_dict["shop_override_map"] = {
                s["shop_id"]: s["campaign_mode"]
                for s in shops
                if s.get("campaign_mode", "auto") != "auto"
            }

        # 全局模型支持外部参数注入
        global_params = self.config_dict.get("global_model_params")
        self.global_model = GlobalBaseModel(params=global_params) if global_params else GlobalBaseModel()
        # 手动覆盖大促参与模式注入全局模型
        self.global_model.shop_override_map = self.config_dict.get("shop_override_map", {})

        # 渠道模型：支持外部参数注入
        channel_params = self.config_dict.get("channel_model_params")
        training_config = self.config_dict.get("training_config")
        self.prediction_history_days = int(
            (training_config or {}).get("prediction_history_days", 400)
        )
        self.channel_ensemble = ChannelEnsemble(
            channel_params=channel_params,
            training_config=training_config,
            shop_override_map=self.config_dict.get("shop_override_map", {}),
            channel_type_map=self.config_dict.get("channel_type_map"),
        )

        # 路由引擎：注入仓库配置
        router_kwargs = {}
        if "routing_rule_change_date" in self.config_dict:
            router_kwargs["rule_change_date"] = self.config_dict["routing_rule_change_date"]
        if "routing_transition_days" in self.config_dict:
            router_kwargs["transition_days"] = self.config_dict["routing_transition_days"]
        if "temp_routing_overrides" in self.config_dict:
            router_kwargs["temp_overrides"] = self.config_dict["temp_routing_overrides"]
        # warehouse_config 优先使用显式传入的；否则从 DB 的 warehouses 列表转换
        if "warehouse_config" in self.config_dict:
            router_kwargs["warehouse_config"] = self.config_dict["warehouse_config"]
        elif "warehouses" in self.config_dict and self.config_dict["warehouses"]:
            # DB 的 warehouses 列表转为 WAREHOUSE_CONFIG 格式（is_active 默认 True）
            router_kwargs["warehouse_config"] = [
                {
                    "warehouse_id": w["warehouse_id"],
                    "name": w.get("name", ""),
                    "is_active": w.get("is_active", True),
                }
                for w in self.config_dict["warehouses"]
            ]
        if "shop_warehouse_binding" in self.config_dict:
            router_kwargs["shop_warehouse_binding"] = self.config_dict["shop_warehouse_binding"]

        self.warehouse_router = WarehouseRouter(**router_kwargs)
        self.is_trained = False

    # ----------------------------------------------------------
    # Phase 1: 全局模型
    # ----------------------------------------------------------

    def train_global(self, order_df: pd.DataFrame) -> "ForecastPipeline":
        """
        Phase 1：训练全局基础模型

        order_df: 渠道粒度日单量，必须有 date, shop_id, platform, order_count
        以店铺为粒度合并渠道数据训练
        """
        print("[Phase 1] 训练全局基础模型...")
        shop_daily = (
            order_df
            .groupby(["date", "shop_id", "platform"])["order_count"]
            .sum()
            .reset_index()
        )
        self.global_model.fit(shop_daily)
        print("[Phase 1] 完成。")
        return self

    def predict_global(
        self,
        future_dates: pd.DatetimeIndex,
        shop_platform_df: pd.DataFrame,
        history_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Phase 1 预测

        future_dates: 待预测日期
        shop_platform_df: shop_id × platform 的唯一组合
        history_df: 历史数据（用于滞后/滚动特征）
        """
        rows = []
        for _, sp_row in shop_platform_df.iterrows():
            for dt in future_dates:
                rows.append({
                    "date": dt,
                    "shop_id": sp_row["shop_id"],
                    "platform": sp_row["platform"],
                    "order_count": np.nan,  # 待预测
                })

        pred_df = pd.DataFrame(rows)
        # 拼接历史数据以计算滞后特征
        history_part = history_df[["date", "shop_id", "platform", "order_count"]].copy()
        history_part["_is_future"] = False
        pred_df["_is_future"] = True
        combined = pd.concat([history_part, pred_df], ignore_index=True)
        combined = combined.sort_values(["shop_id", "platform", "date"])

        preds = self.global_model.predict(combined)
        combined["predicted_shop_orders"] = preds
        # 不能按最后 N 行取值：多店铺排序后未来行会夹在其他店铺历史行之前。
        return combined[combined["_is_future"]].drop(columns=["_is_future"])

    # ----------------------------------------------------------
    # Phase 2: 渠道子模型
    # ----------------------------------------------------------

    def train_channels(
        self,
        order_df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> "ForecastPipeline":
        """Phase 2：训练渠道类型子模型"""
        print("[Phase 2] 训练渠道子模型...")
        # 保存辅助数据供 evaluate_on_holdout 使用
        self._schedule_df = schedule_df
        self._commission_df = commission_df
        self._anchor_df = anchor_df
        self.channel_ensemble.fit(
            order_df, schedule_df=schedule_df, commission_df=commission_df, anchor_df=anchor_df
        )
        print("[Phase 2] 完成。")
        return self

    def predict_channels(
        self,
        future_df: pd.DataFrame,
        history_df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
        anchor_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """Phase 2 预测（渠道粒度）"""
        if len(future_df) == 0:
            return future_df.assign(predicted_orders=np.nan)

        # 逐日递归：当天预测写回伪历史后再预测下一天，使 lag1/rolling
        # 真正使用上一日预测，而不是用一段历史均值填满整个预测窗口。
        rolling_hist = history_df.copy()
        outputs = []
        for pred_date in sorted(pd.to_datetime(future_df["date"]).unique()):
            day_future = future_df[pd.to_datetime(future_df["date"]) == pred_date].copy()
            # 特征中最长依赖是去年同期±3天。仅保留最近400天可与
            # 全量历史生成相同的当日 lag/rolling/yoy，但显著降低逐日重算成本。
            history_cutoff = pd.Timestamp(pred_date) - pd.Timedelta(
                days=self.prediction_history_days
            )
            hist_part = rolling_hist[
                pd.to_datetime(rolling_hist["date"]) >= history_cutoff
            ].copy()
            hist_part["_is_future"] = False
            day_future["_is_future"] = True
            combined = pd.concat([hist_part, day_future], ignore_index=True).sort_values(
                ["shop_id", "channel", "date"]
            )
            result = self.channel_ensemble.predict(
                combined,
                schedule_df=schedule_df,
                commission_df=commission_df,
                anchor_df=anchor_df,
            )
            # 新增排期渠道可能由 overlay 动态补行；对旧模型或异常补行也统一
            # 把空值视为历史行，避免 object 类型的 0/1/NaN 被 Pandas 当成列名。
            future_mask = result["_is_future"].fillna(False).astype(bool)
            day_result = result.loc[future_mask].drop(columns=["_is_future"])
            outputs.append(day_result)

            pseudo_hist = day_future.drop(columns=["_is_future"]).copy()
            merge_keys = ["date", "shop_id", "channel"]
            if "platform" in pseudo_hist.columns and "platform" in day_result.columns:
                merge_keys.append("platform")
            # 达人直播属于独立增量，不能写回下一天的基础模型 lag/rolling。
            # 优先写回 base；兼容没有拆分字段的旧调用方。
            recursive_col = (
                "base_predicted_orders"
                if "base_predicted_orders" in day_result.columns
                else "predicted_orders"
            )
            pred_values = day_result[merge_keys + [recursive_col]].rename(
                columns={recursive_col: "_recursive_orders"}
            )
            pseudo_hist = pseudo_hist.drop(columns=["order_count"], errors="ignore").merge(
                pred_values,
                on=merge_keys,
                how="left",
                validate="one_to_one",
            )
            pseudo_hist["order_count"] = pseudo_hist["_recursive_orders"].fillna(0)
            pseudo_hist = pseudo_hist.drop(columns=["_recursive_orders"])
            rolling_hist = pd.concat([rolling_hist, pseudo_hist], ignore_index=True)

        return pd.concat(outputs, ignore_index=True)

    # ----------------------------------------------------------
    # Phase 3: 分仓路由
    # ----------------------------------------------------------

    def train_routing(self, warehouse_order_df: pd.DataFrame) -> "ForecastPipeline":
        """Phase 3：学习分仓比例"""
        print("[Phase 3] 学习分仓路由比例...")
        self.warehouse_router.fit(warehouse_order_df)
        summary = self.warehouse_router.get_routing_summary()
        print("  路由比例摘要：")
        print(summary.to_string(index=False))
        print("[Phase 3] 完成。")
        return self

    def predict_warehouse(
        self,
        channel_pred_df: pd.DataFrame,
        sample_ratio_map: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        Phase 3：将渠道预测分配到各仓库

        sample_ratio_map 参数仅为兼容旧调用保留；当前分仓不区分派样。
        """
        forecast = channel_pred_df.copy()

        routed = self.warehouse_router.route(
            forecast,
            qty_col="predicted_orders",
        )
        return routed

    # ----------------------------------------------------------
    # 全流程
    # ----------------------------------------------------------

    def train_all(
        self,
        order_df: pd.DataFrame,
        warehouse_order_df: pd.DataFrame,
        schedule_df: Optional[pd.DataFrame] = None,
        commission_df: Optional[pd.DataFrame] = None,
    ) -> "ForecastPipeline":
        """训练全部三层模型"""
        self.train_global(order_df)
        self.train_channels(order_df, schedule_df, commission_df)
        self.train_routing(warehouse_order_df)
        self.is_trained = True
        return self

    def evaluate_on_holdout(
        self,
        order_df: pd.DataFrame,
        warehouse_order_df: Optional[pd.DataFrame] = None,
        holdout_days: int = 30,
        actual_order_df: Optional[pd.DataFrame] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        用最近 holdout_days 天评估渠道级和仓级精度

        warehouse_order_df: 可选。为 None 或空时跳过仓级评估，返回空 DataFrame。
        返回：(channel_eval_df, warehouse_eval_df, holdout_merged_df)
          holdout_merged_df: holdout 期的 actual + predicted，供诊断图表使用。
        """
        actual_source = actual_order_df if actual_order_df is not None else order_df
        cutoff = actual_source["date"].max() - pd.Timedelta(days=holdout_days)
        history = order_df[order_df["date"] <= cutoff]
        # order_df 可为剥离预售后的模型输入；真实评估标签必须来自未剥离数据。
        holdout_keys = actual_source[actual_source["date"] > cutoff]
        holdout = actual_source[actual_source["date"] > cutoff]

        # 取训练时保存的辅助数据（若有）
        _schedule_df = getattr(self, "_schedule_df", None)
        _commission_df = getattr(self, "_commission_df", None)
        _anchor_df = getattr(self, "_anchor_df", None)

        # 滚动逐周预测：每次用到目前为止的真实值填充 lag 特征
        holdout_dates = sorted(holdout["date"].unique())
        chunk_size = 1
        all_pred_chunks = []
        rolling_hist = history.copy()

        for i in range(0, len(holdout_dates), chunk_size):
            chunk_dates = holdout_dates[i : i + chunk_size]
            chunk = holdout_keys[holdout_keys["date"].isin(chunk_dates)].copy()
            future_chunk = chunk[["date", "shop_id", "channel", "platform"]].copy()
            future_chunk["order_count"] = np.nan
            pred_chunk = self.predict_channels(
                future_chunk, rolling_hist,
                schedule_df=_schedule_df,
                commission_df=_commission_df,
                anchor_df=_anchor_df,
            )
            all_pred_chunks.append(pred_chunk)
            # 将本周实际值追加到滚动历史，供下一周使用
            # 回测模拟每日结束后拿到真实“模型目标”历史。若模型训练目标剥离了
            # 预售，则从 order_df 取对应日期，而不是把总订单写回普通单历史。
            observed_chunk = order_df[order_df["date"].isin(chunk_dates)].copy()
            rolling_hist = pd.concat([rolling_hist, observed_chunk], ignore_index=True)
            if progress_callback is not None:
                progress_callback(min(i + len(chunk_dates), len(holdout_dates)), len(holdout_dates))

        pred_df = pd.concat(all_pred_chunks, ignore_index=True) if all_pred_chunks else pd.DataFrame()

        merge_keys = ["date", "shop_id", "channel"]
        if "platform" in holdout.columns and "platform" in pred_df.columns:
            merge_keys.append("platform")
        prediction_cols = merge_keys + ["predicted_orders"]
        for component in (
            "raw_predicted_orders", "guardrail_predicted_orders",
            "base_predicted_orders", "anchor_predicted_orders"
        ):
            if component in pred_df.columns:
                prediction_cols.append(component)
        merged = holdout.merge(
            pred_df[prediction_cols],
            on=merge_keys,
            how="left",
        )

        from src.features.campaign_features import build_campaign_features
        # 复用渠道模型中已训练的敏感度 map（stable 模型覆盖面最广）
        _sensitivity = self.channel_ensemble.models["stable"].shop_sensitivity_map
        _override = self.channel_ensemble.models["stable"].shop_override_map
        merged = build_campaign_features(
            merged,
            shop_sensitivity_map=_sensitivity,
            shop_override_map=_override,
        )

        merged, guardrail_details = apply_channel_calibration_guardrail(merged)
        self.calibration_guardrail_details = guardrail_details
        self.calibration_fallback_channels = [
            item["channel"] for item in guardrail_details
        ]

        channel_eval = evaluate(
            merged,
            actual_col="order_count",
            pred_col="predicted_orders",
            group_col="channel",
        )
        if "raw_predicted_orders" in merged.columns:
            raw_eval = evaluate(
                merged,
                actual_col="order_count",
                pred_col="raw_predicted_orders",
                group_col="channel",
            )
            raw_eval = raw_eval[[
                "segment", "wape", "mape", "bias_pct", "rmse", "total_predicted"
            ]].rename(columns={
                "wape": "raw_wape",
                "mape": "raw_mape",
                "bias_pct": "raw_bias_pct",
                "rmse": "raw_rmse",
                "total_predicted": "raw_total_predicted",
            })
            channel_eval = channel_eval.merge(raw_eval, on="segment", how="left")

        # 仓级评估：仅在路由层已训练且提供了仓库数据时执行
        wh_eval_df = pd.DataFrame()
        self.warehouse_holdout_merged = pd.DataFrame()
        has_wh_data = (
            warehouse_order_df is not None
            and len(warehouse_order_df) > 0
            and "warehouse_id" in warehouse_order_df.columns
        )
        if has_wh_data and warehouse_order_df.attrs.get("routing_scope") == "global" and self.config_dict.get("excluded_channels"):
            # 源仓表没有渠道维度，无法剔除被排除渠道的实际量，不能与部分渠道预测计算同口径 WAPE。
            has_wh_data = False
            print("  [提示] 仓库源表无渠道维度且存在排除渠道，跳过仓级回测；整体分仓比例仍可使用。")
        router_trained = bool(self.warehouse_router.routing_ratios) or bool(
            self.warehouse_router.global_default_ratio.get(0)
        )

        if has_wh_data and router_trained:
            try:
                wh_pred = self.predict_warehouse(pred_df)
                wh_actual = warehouse_order_df[warehouse_order_df["date"] > cutoff]
                # 当前不区分派样，仓级回测按仓库总单量比较。
                global_source = warehouse_order_df.attrs.get("routing_scope") == "global"
                join_cols = ["date", "warehouse_id"] if global_source else ["date", "shop_id", "channel", "warehouse_id"]
                actual_agg = (
                    wh_actual.groupby(join_cols, as_index=False)["order_count"].sum()
                )
                pred_agg = (
                    wh_pred.groupby(join_cols, as_index=False)["warehouse_orders"].sum()
                    .rename(columns={"warehouse_orders": "warehouse_orders_pred"})
                )
                wh_merged = actual_agg.merge(pred_agg, on=join_cols, how="outer")
                wh_merged[["order_count", "warehouse_orders_pred"]] = wh_merged[
                    ["order_count", "warehouse_orders_pred"]
                ].fillna(0)
                self.warehouse_holdout_merged = wh_merged.copy()
                wh_eval_df = evaluate_warehouse(wh_merged, actual_col="order_count")
            except Exception as e:
                print(f"  [警告] 仓级评估失败，跳过：{e}")

        return channel_eval, wh_eval_df, merged

    def restore_untrained_components(self, trained_phases: Iterable[str]) -> list[str]:
        """加载本次未训练的已有模型，供后续预测继续使用。"""
        phases = set(trained_phases)
        restored = []

        global_path = self.model_dir / "global_model.joblib"
        if "global" not in phases and global_path.exists():
            self.global_model = GlobalBaseModel.load(str(global_path))
            restored.append("global")

        channel_dir = self.model_dir / "channel_models"
        if "channel" not in phases and channel_dir.exists():
            self.channel_ensemble.load(str(channel_dir))
            restored.append("channel")

        router_path = self.model_dir / "warehouse_router.joblib"
        if "routing" not in phases and router_path.exists():
            self.warehouse_router = WarehouseRouter.load(str(router_path))
            restored.append("routing")

        return restored

    def save(self, phases: Optional[Iterable[str]] = None) -> None:
        """保存本次训练的模型组件；未指定阶段时保持全量保存。"""
        self.model_dir.mkdir(parents=True, exist_ok=True)
        selected = set(phases) if phases is not None else {"global", "channel", "routing"}
        if "global" in selected:
            self.global_model.save(str(self.model_dir / "global_model.joblib"))
        if "channel" in selected:
            self.channel_ensemble.save(str(self.model_dir / "channel_models"))
        if "routing" in selected:
            self.warehouse_router.save(str(self.model_dir / "warehouse_router.joblib"))
        print(f"模型已保存至 {self.model_dir}")

    def load(self) -> "ForecastPipeline":
        self.global_model = GlobalBaseModel.load(str(self.model_dir / "global_model.joblib"))
        self.channel_ensemble.load(str(self.model_dir / "channel_models"))
        self.warehouse_router = WarehouseRouter.load(str(self.model_dir / "warehouse_router.joblib"))
        self.is_trained = True
        return self
