"""
分仓路由引擎
- 处理三类约束：仓库生命周期、单仓店铺、寻源规则变更/临时调仓
- 学习"稳定期"历史仓分布比例
- 预测时按比例分配渠道总单量到各活跃仓

设计决策（根据实际数据情况）：
- 历史路由规则已缺失，无法还原历史"首选路由仓"
- 历史上规则正式变更过一次，只用变更日期后的数据训练
- 大促期间存在临时调仓，这些时段排除出训练数据
- 预测目标：基于稳定期学习的实际仓分布比例（含正常库存瀑布，不含临时分流）
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import timedelta

from config.routing_config import (
    ROUTING_RULE_CHANGE_DATE,
    ROUTING_TRANSITION_DAYS,
    TEMP_ROUTING_OVERRIDES,
    WAREHOUSE_CONFIG,
    SHOP_WAREHOUSE_BINDING,
)


class WarehouseRouter:
    """
    分仓路由引擎

    核心职责：
    1. 数据清洗：过滤规则变更前数据、过渡期、临时调仓期
    2. 学习稳定期仓分布比例（按 shop_id → 各仓比例）
    3. 预测时分配：单仓店铺直接归仓，多仓店铺按比例分配
    """

    def __init__(
        self,
        rule_change_date: Optional[str] = None,
        transition_days: int = ROUTING_TRANSITION_DAYS,
        temp_overrides: Optional[List[Dict]] = None,
        warehouse_config: Optional[List[Dict]] = None,
        shop_warehouse_binding: Optional[List[Dict]] = None,
    ):
        self.rule_change_date = pd.Timestamp(rule_change_date or ROUTING_RULE_CHANGE_DATE)
        self.transition_days = transition_days
        self.temp_overrides = temp_overrides or TEMP_ROUTING_OVERRIDES

        # 仓库状态（支持外部注入）
        wh_cfg = warehouse_config if warehouse_config is not None else WAREHOUSE_CONFIG
        self.warehouse_df = pd.DataFrame(wh_cfg)
        self.active_warehouses = self.warehouse_df[self.warehouse_df["is_active"]]["warehouse_id"].tolist()

        # 店铺绑定关系（支持外部注入）
        binding_cfg = shop_warehouse_binding if shop_warehouse_binding is not None else SHOP_WAREHOUSE_BINDING
        self.binding_df = pd.DataFrame(binding_cfg)

        # 学习到的路由比例。保留两层字典是为了兼容旧模型，但当前只使用
        # key=0，表示“不区分派样”的整体仓库比例。
        # 结构：{shop_id: {0: {warehouse_id: ratio}}}
        self.routing_ratios: Dict[str, Dict[int, Dict[str, float]]] = {}

        # 全局默认比例（无历史数据时的 fallback）
        self.global_default_ratio: Dict[int, Dict[str, float]] = {}

        # 仅为兼容旧 joblib 保留；当前路由不再计算或使用派样占比。
        self.sample_ratio_by_shop_channel: Dict[Tuple[str, str], float] = {}
        self.sample_ratio_by_channel: Dict[str, float] = {}
        self.global_sample_ratio: float = 0.0

    # ----------------------------------------------------------
    # 数据清洗
    # ----------------------------------------------------------

    def _get_excluded_dates(self) -> pd.DatetimeIndex:
        """
        获取应排除的日期集合：
        1. 规则变更前所有日期
        2. 规则变更点 ±transition_days 天
        3. 临时调仓时段
        """
        excluded = set()

        # 规则变更前 + 过渡期
        transition_start = self.rule_change_date - timedelta(days=self.transition_days)
        transition_end = self.rule_change_date + timedelta(days=self.transition_days)

        # 通过 bool 索引实现"变更前全排除 + 过渡期排除"
        # 实际在 filter_stable_data 中统一处理

        # 临时调仓时段
        for override in self.temp_overrides:
            start = pd.Timestamp(override["start"])
            end = pd.Timestamp(override["end"])
            date_range = pd.date_range(start, end)
            excluded.update(date_range)

        return excluded, transition_start, transition_end

    def filter_stable_data(self, df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
        """
        过滤出"稳定期"数据，用于路由比例学习

        排除：
        - 规则变更日期之前的所有数据（规则已变，历史不可用）
        - 规则变更前后 transition_days 天（过渡混乱期）
        - 临时调仓时段（大促应急期）
        """
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col])

        excluded_temp, transition_start, transition_end = self._get_excluded_dates()

        # 排除变更日期前数据（含过渡期）
        df = df[df[date_col] > transition_end]

        # 排除临时调仓时段
        if excluded_temp:
            excluded_series = pd.DatetimeIndex(list(excluded_temp))
            df = df[~df[date_col].isin(excluded_series)]

        return df

    # ----------------------------------------------------------
    # 路由比例学习
    # ----------------------------------------------------------

    def fit(
        self,
        order_df: pd.DataFrame,
        date_col: str = "date",
        shop_col: str = "shop_id",
        sample_col: str = "is_sample",
        warehouse_col: str = "warehouse_id",
        qty_col: str = "order_count",
    ) -> "WarehouseRouter":
        """
        从历史订单数据学习各仓分配比例

        order_df 必须包含：date, shop_id, warehouse_id, order_count。
        is_sample 即使存在也会被忽略；派样与普通订单共同学习一套比例。
        """
        stable_df = self.filter_stable_data(order_df, date_col)

        print(f"  [路由] 稳定期数据: {len(stable_df)} 条 "
              f"（原始 {len(order_df)} 条，"
              f"截断日期: {self.rule_change_date.date()} + {self.transition_days}天过渡）")

        if len(stable_df) == 0:
            print("  [警告] 无稳定期数据，将使用均匀分配作为默认比例")
            self._set_uniform_default()
            return self

        self.sample_ratio_by_shop_channel = {}
        self.sample_ratio_by_channel = {}
        self.global_sample_ratio = 0.0

        # 只统计活跃仓的比例，关闭仓归零后重新归一化
        stable_df = stable_df[stable_df[warehouse_col].isin(self.active_warehouses)]

        # 派样目前所有仓都可发，因此只按 shop_id × warehouse_id 汇总。
        grouped = (
            stable_df
            .groupby([shop_col, warehouse_col])[qty_col]
            .sum()
            .reset_index()
        )

        # 计算各仓比例
        total = grouped.groupby(shop_col)[qty_col].transform("sum")
        grouped["ratio"] = grouped[qty_col] / total.clip(lower=1)

        # key=0 代表“不区分派样”的整体分仓比例。
        self.routing_ratios = {}
        for shop_id, sub in grouped.groupby(shop_col):
            if order_df.attrs.get("routing_scope") == "global":
                continue
            self.routing_ratios[shop_id] = {0: dict(
                zip(sub[warehouse_col], sub["ratio"])
            )}

        # 全局默认比例（用于新店铺无历史数据的 fallback）
        global_grouped = (
            stable_df
            .groupby(warehouse_col)[qty_col]
            .sum()
            .reset_index()
        )
        global_total = float(global_grouped[qty_col].sum())
        global_grouped["ratio"] = global_grouped[qty_col] / max(global_total, 1.0)
        self.global_default_ratio = {0: dict(
            zip(global_grouped[warehouse_col], global_grouped["ratio"])
        )}

        return self

    def _set_uniform_default(self) -> None:
        """均匀分配默认比例"""
        n = len(self.active_warehouses)
        if n == 0:
            self.global_default_ratio = {0: {}}
            return
        uniform = {wh: 1.0 / n for wh in self.active_warehouses}
        self.global_default_ratio = {0: uniform}

    def _get_ratio(self, shop_id: str) -> Dict[str, float]:
        """获取路由比例，按 shop_id → 全局 fallback 逐级查找"""
        if shop_id in self.routing_ratios:
            ratio = self.routing_ratios[shop_id].get(0)
            if ratio:
                return ratio

        # 全局 fallback
        if self.global_default_ratio.get(0):
            return self.global_default_ratio[0]

        return self._get_uniform()

    def _get_uniform(self) -> Dict[str, float]:
        n = len(self.active_warehouses)
        if n == 0:
            return {}
        return {wh: 1.0 / n for wh in self.active_warehouses}

    # ----------------------------------------------------------
    # 路由预测
    # ----------------------------------------------------------

    def _get_shop_binding(self, shop_id: str) -> Optional[Dict]:
        """获取店铺绑定配置"""
        if self.binding_df.empty or "shop_id" not in self.binding_df.columns:
            return None
        matches = self.binding_df[self.binding_df["shop_id"] == shop_id]
        if len(matches) == 0:
            return None
        return matches.iloc[0].to_dict()

    def route(
        self,
        forecast_df: pd.DataFrame,
        shop_col: str = "shop_id",
        qty_col: str = "predicted_orders",
        sample_ratio_col: str = "sample_ratio",
    ) -> pd.DataFrame:
        """
        将渠道级预测单量分配到各仓库

        forecast_df 必须包含：
            shop_id, predicted_orders
            sample_ratio 字段会被忽略；当前不区分派样。

        返回：以 (shop_id, channel, date, warehouse_id) 为粒度的 DataFrame
        """
        results = []

        for _, row in forecast_df.iterrows():
            shop_id = row[shop_col]
            total_orders = row[qty_col]

            if total_orders <= 0:
                continue

            binding = self._get_shop_binding(shop_id)

            # 单仓店铺：直接归仓（用户显式绑定，无需通过 active_warehouses 过滤）
            if binding is not None and binding.get("binding_type") == "single":
                wh_id = binding.get("warehouse_id")
                if wh_id:
                    result_row = row.to_dict()
                    result_row["warehouse_id"] = wh_id
                    result_row["is_sample"] = 0
                    result_row["warehouse_orders"] = total_orders
                    result_row["routing_type"] = "single"
                    results.append(result_row)
                continue

            # 多仓路由：不区分派样，全部订单使用同一套店铺历史比例。
            ratios = self._get_ratio(shop_id)
            for wh_id, ratio in ratios.items():
                if wh_id not in self.active_warehouses:
                    continue
                result_row = row.to_dict()
                result_row["warehouse_id"] = wh_id
                result_row["is_sample"] = 0
                result_row["warehouse_orders"] = total_orders * ratio
                result_row["routing_type"] = "multi"
                results.append(result_row)

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)
        # 数据库存 Float，展示层再取整；这里保留精度，避免逐仓四舍五入后
        # 大批量预测累计产生“渠道合计 != 分仓合计”的误差。
        result_df["warehouse_orders"] = result_df["warehouse_orders"].clip(lower=0)
        return result_df

    def get_routing_summary(self) -> pd.DataFrame:
        """返回各店铺的路由比例汇总，用于检查和审计"""
        rows = []
        for shop_id, sample_map in self.routing_ratios.items():
            for is_sample, ratios in sample_map.items():
                for wh_id, ratio in ratios.items():
                    rows.append({
                        "shop_id": shop_id,
                        "is_sample": is_sample,
                        "warehouse_id": wh_id,
                        "ratio": round(ratio, 4),
                    })
        if not rows:
            rows = [{"shop_id": "全局默认", "is_sample": 0, "warehouse_id": warehouse_id,
                     "ratio": round(ratio, 4)}
                    for warehouse_id, ratio in self.global_default_ratio.get(0, {}).items()]
        if not rows:
            return pd.DataFrame(columns=["shop_id", "is_sample", "warehouse_id", "ratio"])
        return pd.DataFrame(rows).sort_values(["shop_id", "is_sample", "warehouse_id"])

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "routing_ratios": self.routing_ratios,
            "global_default_ratio": self.global_default_ratio,
            "active_warehouses": self.active_warehouses,
            "rule_change_date": self.rule_change_date,
            "transition_days": self.transition_days,
            "binding_records": self.binding_df.to_dict("records"),  # 持久化绑定关系
            "sample_ratio_by_shop_channel": self.sample_ratio_by_shop_channel,
            "sample_ratio_by_channel": self.sample_ratio_by_channel,
            "global_sample_ratio": self.global_sample_ratio,
            "routing_mode": "all_orders",
        }, path)

    @classmethod
    def load(cls, path: str) -> "WarehouseRouter":
        data = joblib.load(path)
        obj = cls(
            rule_change_date=str(data["rule_change_date"].date()),
            transition_days=data["transition_days"],
        )
        obj.routing_ratios = data["routing_ratios"]
        obj.global_default_ratio = data["global_default_ratio"]
        obj.active_warehouses = data["active_warehouses"]
        obj.sample_ratio_by_shop_channel = data.get("sample_ratio_by_shop_channel", {})
        obj.sample_ratio_by_channel = data.get("sample_ratio_by_channel", {})
        obj.global_sample_ratio = data.get("global_sample_ratio", 0.05)
        # 恢复 binding_df（兼容旧版本 joblib 文件没有此 key 的情况）
        if "binding_records" in data and data["binding_records"]:
            obj.binding_df = pd.DataFrame(data["binding_records"])
        # 旧模型按派样/非派样保存两套路由。加载时折叠成一套整体比例，
        # 确保升级后即使尚未重训，也不会继续按派样拆单。
        if data.get("routing_mode") != "all_orders":
            sample_weight = float(np.clip(data.get("global_sample_ratio", 0.0), 0, 1))
            obj.routing_ratios = {
                shop_id: {0: cls._collapse_ratio_map(sample_map, sample_weight)}
                for shop_id, sample_map in obj.routing_ratios.items()
            }
            obj.global_default_ratio = {0: cls._collapse_ratio_map(
                obj.global_default_ratio, sample_weight
            )}
        obj.sample_ratio_by_shop_channel = {}
        obj.sample_ratio_by_channel = {}
        obj.global_sample_ratio = 0.0
        return obj

    @staticmethod
    def _collapse_ratio_map(
        sample_map: Dict[int, Dict[str, float]], sample_weight: float
    ) -> Dict[str, float]:
        """把旧模型的派样/非派样比例折叠为一套整体比例。"""
        non_sample = sample_map.get(0) or sample_map.get("0") or {}
        sample = sample_map.get(1) or sample_map.get("1") or {}
        if not non_sample:
            return dict(sample)
        if not sample:
            return dict(non_sample)
        warehouses = set(non_sample) | set(sample)
        blended = {
            warehouse_id: (
                (1 - sample_weight) * float(non_sample.get(warehouse_id, 0))
                + sample_weight * float(sample.get(warehouse_id, 0))
            )
            for warehouse_id in warehouses
        }
        total = sum(blended.values())
        if total <= 0:
            return {}
        return {
            warehouse_id: value / total
            for warehouse_id, value in blended.items()
        }
