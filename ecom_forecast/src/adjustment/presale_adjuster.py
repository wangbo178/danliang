"""
预售订单校准器
在模型预测基础上，用预售期末已知的定金订单数做最终修正。

核心公式：
    仓库订单(第d天) = 定金订单数
                    × 尾款转化率
                    × (1 - 退货率)
                    × 尾款支付分布[d]

使用时机：
    大促预售期结束前1~2天，定金订单数已基本确定，
    此时用真实数量替换模型的估算值，精度大幅提升。
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional


# 默认尾款支付分布（可通过历史数据学习）
# key = 尾款开启后第几天（从1开始），value = 当天支付比例
DEFAULT_PAYMENT_SPREAD = {
    1: 0.65,   # 第1天：大部分用户会在开启后立即支付
    2: 0.20,   # 第2天：补款尾部
    3: 0.10,   # 第3天：长尾
    4: 0.05,   # 第4天及以后归到第4天
}


class PresaleAdjuster:
    """
    预售订单校准器

    参数
    ----
    final_payment_rate : float
        尾款转化率，即付了定金最终完成尾款的比例（默认 0.88）
        = 1 - 尾款放弃率（用户付完定金不付尾款）

    return_rate : float
        退货率，尾款支付后最终退货的比例（默认 0.05）

    payment_spread : dict
        尾款支付分布，{第N天: 比例}，所有比例之和应为1.0
        不同活动/渠道可设置不同分布
    """

    def __init__(
        self,
        final_payment_rate: float = 0.88,
        return_rate: float = 0.05,
        payment_spread: Optional[Dict[int, float]] = None,
    ):
        self.final_payment_rate = final_payment_rate
        self.return_rate = return_rate
        self.payment_spread = payment_spread or DEFAULT_PAYMENT_SPREAD.copy()

        # 归一化（防止分布之和不为1）
        total = sum(self.payment_spread.values())
        if abs(total - 1.0) > 0.01:
            self.payment_spread = {k: v / total for k, v in self.payment_spread.items()}

    # ----------------------------------------------------------
    # 核心计算
    # ----------------------------------------------------------

    def net_rate(self) -> float:
        """净入仓率 = 尾款转化率 × (1 - 退货率)"""
        return self.final_payment_rate * (1 - self.return_rate)

    def compute_daily_orders(
        self,
        deposit_orders: int,
        final_payment_start_date: str,
    ) -> pd.DataFrame:
        """
        根据已知定金订单数，计算尾款期各天的入仓订单预测。

        参数
        ----
        deposit_orders : int
            预售期末已确认的定金订单数

        final_payment_start_date : str
            尾款开启日期，格式 YYYY-MM-DD

        返回
        ----
        DataFrame：date | adjusted_orders | cumulative_orders | payment_day
        """
        start = pd.Timestamp(final_payment_start_date)
        net_orders = deposit_orders * self.net_rate()

        rows = []
        for day_offset, spread_ratio in sorted(self.payment_spread.items()):
            rows.append({
                "date": start + pd.Timedelta(days=day_offset - 1),
                "payment_day": day_offset,
                "spread_ratio": round(spread_ratio, 4),
                "adjusted_orders": round(net_orders * spread_ratio),
            })

        df = pd.DataFrame(rows)
        df["cumulative_orders"] = df["adjusted_orders"].cumsum()
        return df

    def blend_with_model(
        self,
        model_pred_df: pd.DataFrame,
        deposit_orders: int,
        final_payment_start_date: str,
        shop_id: str,
        channel: str,
        pred_col: str = "predicted_orders",
        blend_weight: float = 1.0,
    ) -> pd.DataFrame:
        """
        将预售校准值与模型预测融合，返回最终建议预测。

        blend_weight : float
            校准值权重（1.0 = 完全用校准值；0.5 = 各取一半；0.0 = 纯模型）
        """
        adj_df = self.compute_daily_orders(deposit_orders, final_payment_start_date)
        adj_df["shop_id"] = shop_id
        adj_df["channel"] = channel

        # 只替换尾款期的预测
        payment_dates = set(adj_df["date"])
        result = model_pred_df.copy()

        for _, adj_row in adj_df.iterrows():
            if all(c in result.columns for c in ["date", "shop_id", "channel"]):
                mask = (
                    (result["date"] == adj_row["date"]) &
                    (result["shop_id"] == shop_id) &
                    (result["channel"] == channel)
                )
            else:
                mask = (result["date"] == adj_row["date"])

            if mask.any():
                model_val = result.loc[mask, pred_col].values[0]
                adj_val = adj_row["adjusted_orders"]
                blended = blend_weight * adj_val + (1 - blend_weight) * model_val
                result.loc[mask, pred_col] = round(blended)
                result.loc[mask, "adjustment_source"] = "presale"
                result.loc[mask, "model_original"] = round(model_val)
                result.loc[mask, "presale_adjusted"] = round(adj_val)

        return result

    # ----------------------------------------------------------
    # 历史学习：从真实数据估算支付分布
    # ----------------------------------------------------------

    @classmethod
    def learn_spread_from_history(
        cls,
        warehouse_df: pd.DataFrame,
        campaigns: List[Dict],
        shop_id: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Dict[int, float]:
        """
        从历史仓库订单数据中学习尾款支付分布。

        campaigns 格式：
            [{"final_payment_start": "2024-10-21", "deposit_orders": 50000}, ...]

        返回：{day_offset: ratio}
        """
        all_spreads = []

        for camp in campaigns:
            start = pd.Timestamp(camp["final_payment_start"])
            deposit = camp.get("deposit_orders", 1)
            if deposit <= 0:
                continue

            window = warehouse_df.copy()
            window["date"] = pd.to_datetime(window["date"])

            if shop_id and "shop_id" in window.columns:
                window = window[window["shop_id"] == shop_id]
            if channel and "channel" in window.columns:
                window = window[window["channel"] == channel]

            # 取尾款开启后 7 天的数据
            end = start + pd.Timedelta(days=6)
            window = window[(window["date"] >= start) & (window["date"] <= end)]

            if len(window) == 0:
                continue

            daily = window.groupby("date")["order_count"].sum().reset_index()
            total = daily["order_count"].sum()
            if total <= 0:
                continue

            spread = {}
            for _, row in daily.iterrows():
                day_n = (row["date"] - start).days + 1
                spread[day_n] = row["order_count"] / total

            all_spreads.append(spread)

        if not all_spreads:
            return DEFAULT_PAYMENT_SPREAD.copy()

        # 取各活动的平均分布
        all_days = set()
        for s in all_spreads:
            all_days.update(s.keys())

        avg_spread = {}
        for day in sorted(all_days):
            vals = [s.get(day, 0) for s in all_spreads]
            avg_spread[day] = round(np.mean(vals), 4)

        # 归一化
        total = sum(avg_spread.values())
        return {k: round(v / total, 4) for k, v in avg_spread.items()}

    @classmethod
    def learn_rates_from_history(
        cls,
        deposit_records: pd.DataFrame,
        final_payment_records: pd.DataFrame,
        return_records: Optional[pd.DataFrame] = None,
    ) -> Dict[str, float]:
        """
        从历史记录学习尾款转化率和退货率。

        deposit_records : DataFrame，含 campaign / deposit_orders
        final_payment_records : DataFrame，含 campaign / paid_orders
        return_records : DataFrame，含 campaign / returned_orders（可选）

        返回：{"final_payment_rate": x, "return_rate": y}
        """
        merged = deposit_records.merge(final_payment_records, on="campaign", how="inner")
        merged["payment_rate"] = (
            merged["paid_orders"] / merged["deposit_orders"].clip(lower=1)
        ).clip(upper=1.0)
        avg_payment_rate = float(merged["payment_rate"].mean())

        avg_return_rate = 0.05  # 默认值
        if return_records is not None and len(return_records) > 0:
            merged2 = merged.merge(return_records, on="campaign", how="left")
            merged2["return_rate"] = (
                merged2["returned_orders"].fillna(0) /
                merged2["paid_orders"].clip(lower=1)
            ).clip(upper=1.0)
            avg_return_rate = float(merged2["return_rate"].mean())

        return {
            "final_payment_rate": round(avg_payment_rate, 4),
            "return_rate": round(avg_return_rate, 4),
        }
