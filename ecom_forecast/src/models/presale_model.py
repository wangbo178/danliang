"""
预售尾款预测模块 — PresaleDistributionModel

解决问题：天猫直播渠道每年仅 2-3 次预售，样本极度稀疏（8-18 个尾款爆发日），
         LightGBM 无法可靠学习，改用规则先验 + 数据校正的混合策略。

核心思路
--------
1. 规则先验：用已知的尾款分布曲线（历史归一化比例）分配总量到各付款日
2. 数据校正：随数据积累，用实际历史比例替换极端兜底先验
3. LightGBM 主导非预售日：只对 is_presale_payment=1 行做覆盖，不影响其他日预测

使用方式
--------
# 训练阶段：fit() 从 live_schedules 历史行计算分布
model = PresaleDistributionModel()
model.fit(payment_events)

# 预测阶段：predict_daily_volumes() 返回各尾款日预测量
volumes = model.predict_daily_volumes(payment_dates, campaign_type, estimated_total)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.campaign_calendar import CAMPAIGN_CALENDAR
from config.campaign_config import (
    PRESALE_MAX_PAYMENT_DAYS,
    PRESALE_MIN_EVENTS_TO_FIT,
    PRESALE_RECENT_EVENT_WEIGHT,
    PRESALE_TYPE_RATIO,
)

logger = logging.getLogger(__name__)

# 极端兜底先验（仅当历史数据完全为空时使用）
_FALLBACK_DISTRIBUTION = [0.60, 0.20, 0.10, 0.06, 0.04]


def _infer_campaign_type(target_date: date) -> Optional[str]:
    """
    根据日期在大促日历中反查活动类型。
    target_date 为预售尾款日，向前最多 30 天查找对应的大促窗口。
    """
    year = target_date.year
    calendars_to_check = [year, year - 1]  # 跨年兜底

    for y in calendars_to_check:
        cal = CAMPAIGN_CALENDAR.get(y, {})
        for campaign_type, cfg in cal.items():
            start_str = cfg.get("start")
            end_str = cfg.get("end")
            presale_str = cfg.get("presale_start")
            if not start_str:
                continue
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
            # 尾款爆发日通常在 presale_start ~ end 之间（含提前几天缓冲）
            window_start = (
                date.fromisoformat(presale_str) if presale_str else start - timedelta(days=7)
            )
            window_end = end + timedelta(days=3)
            if window_start <= target_date <= window_end:
                return campaign_type
    return None


def _normalize_distribution(values: List[float]) -> List[float]:
    """归一化为概率分布，处理全零情况"""
    total = sum(v for v in values if v >= 0)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [v / total for v in values]


class PresaleDistributionModel:
    """
    基于经验分布的预售尾款预测模块（解决稀疏样本问题）

    核心逻辑：
    1. 从 live_schedules 中识别 presale_payment 行
    2. 估算本次预售总量（基于历史峰值 + 同比增长）
    3. 将总量按尾款分布曲线分配到各付款日
    4. 用该值覆盖 LightGBM 对预售尾款日的预测
    """

    def __init__(self):
        # {(shop_id, channel, campaign_type): [[day0_ratio, day1_ratio, ...], ...]}
        # 每个元素是一次大促的归一化分布
        self._dist_history: Dict[Tuple, List[List[float]]] = defaultdict(list)

        # {(shop_id, channel, campaign_type): [总量1, 总量2, ...]}
        # 按时间顺序，最新在末尾
        self.volume_history: Dict[Tuple, List[float]] = defaultdict(list)

        # {(shop_id, channel, campaign_type): [broadcast_date1, ...]}（辅助排序）
        self._event_dates: Dict[Tuple, List[date]] = defaultdict(list)

    # ──────────────────────────────────────────────────────────────
    # fit
    # ──────────────────────────────────────────────────────────────

    def fit(self, payment_events: List[Dict]) -> "PresaleDistributionModel":
        """
        从历史预售付款事件计算实际分布曲线，按大促类型分别保存。

        payment_events 格式（每条记录对应一个店铺×渠道×大促×尾款日的观测值）：
        [
          {
            "shop_id": "CS07",
            "channel": "直播",
            "campaign_type": "1111",      # 可选；为 None 时自动推断
            "broadcast_date": date(...),  # 定金场播出日
            "payment_date": date(...),    # 付款日
            "day_index": 0,               # 0=尾款第一天，1=第二天...；为 None 时自动按 payment_date 排序计算
            "daily_volume": 3500,         # 当日付款单量
          },
          ...
        ]

        每个 (shop_id, channel, campaign_type, broadcast_date) 为一个"事件"，
        同一事件内各天归一化后加权平均，得到该店铺×渠道×活动类型的分布。
        """
        if not payment_events:
            return self

        df = pd.DataFrame(payment_events)
        required = {"shop_id", "channel", "payment_date", "daily_volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"payment_events 缺少字段: {missing}")

        # day_index 为 None 时自动补全（按同一事件内 payment_date 升序排列）
        if "day_index" not in df.columns:
            df["day_index"] = None

        df["payment_date"] = pd.to_datetime(df["payment_date"]).dt.date

        # 自动推断 campaign_type
        if "campaign_type" not in df.columns or df["campaign_type"].isna().any():
            df["campaign_type"] = df["campaign_type"] if "campaign_type" in df.columns else None
            mask = df["campaign_type"].isna() if "campaign_type" in df.columns else pd.Series(True, index=df.index)
            df.loc[mask, "campaign_type"] = df.loc[mask, "payment_date"].apply(_infer_campaign_type)
        df["campaign_type"] = df["campaign_type"].fillna("default")

        if "broadcast_date" not in df.columns:
            df["broadcast_date"] = None
        df["broadcast_date"] = pd.to_datetime(df["broadcast_date"]).dt.date

        # 当 day_index 为 None 时，按同一 (shop_id, broadcast_date) 事件内 payment_date 升序自动计算
        need_auto_idx = df["day_index"].isna()
        if need_auto_idx.any():
            # 按组计算 day_index，逐行写回
            auto_df = df[need_auto_idx].copy()
            idx_result = pd.Series(0, index=auto_df.index, dtype=int)
            for (shop_id, bcast_date), grp in auto_df.groupby(
                ["shop_id", "broadcast_date"], dropna=False
            ):
                sorted_dates = sorted(grp["payment_date"].unique())
                date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
                idx_result.loc[grp.index] = grp["payment_date"].map(
                    lambda d: date_to_idx.get(d, 0)
                )
            df.loc[need_auto_idx, "day_index"] = idx_result

        df["day_index"] = df["day_index"].fillna(0).astype(int)

        # 截断超过最大付款天数的行
        df = df[df["day_index"] < PRESALE_MAX_PAYMENT_DAYS].copy()

        # 按事件分组：(shop_id, channel, campaign_type, broadcast_date)
        group_keys = ["shop_id", "channel", "campaign_type", "broadcast_date"]
        for event_key, event_df in df.groupby(group_keys, dropna=False):
            shop_id, channel, campaign_type, broadcast_date = event_key
            key = (str(shop_id), str(channel), str(campaign_type))

            # 构建该事件的分布向量（按 day_index 对齐）
            max_day = int(event_df["day_index"].max())
            dist_vec = [0.0] * (max_day + 1)
            for _, row in event_df.iterrows():
                idx = int(row["day_index"])
                dist_vec[idx] += float(row["daily_volume"])

            total = sum(dist_vec)
            if total <= 0:
                continue

            normalized = _normalize_distribution(dist_vec)
            self._dist_history[key].append(normalized)
            self.volume_history[key].append(total)

            bd = broadcast_date if isinstance(broadcast_date, date) else None
            self._event_dates[key].append(bd or event_df["payment_date"].min())

        return self

    # ──────────────────────────────────────────────────────────────
    # predict
    # ──────────────────────────────────────────────────────────────

    def predict_daily_volumes(
        self,
        payment_dates: List[date],
        shop_id: str,
        channel: str,
        campaign_type: Optional[str] = None,
        estimated_total: Optional[float] = None,
    ) -> Dict[date, float]:
        """
        返回 {date: predicted_volume} 字典。

        参数
        ----
        payment_dates  : 本次预售的尾款日列表（已排序）
        shop_id        : 店铺 ID
        channel        : 渠道名称
        campaign_type  : 大促类型（None 则从 payment_dates[0] 自动推断）
        estimated_total: 本次预售估算总量（None 则自动估算）
        """
        if not payment_dates:
            return {}

        payment_dates = sorted(payment_dates)
        n_days = min(len(payment_dates), PRESALE_MAX_PAYMENT_DAYS)
        payment_dates = payment_dates[:n_days]

        # 推断 campaign_type
        if campaign_type is None:
            campaign_type = _infer_campaign_type(payment_dates[0]) or "default"

        key = (str(shop_id), str(channel), str(campaign_type))

        # 获取分布曲线
        dist = self._get_distribution(key, n_days)

        # 估算总量
        if estimated_total is None:
            estimated_total = self._estimate_total(key, campaign_type, shop_id, channel)

        # 分配到各天
        result: Dict[date, float] = {}
        for i, d in enumerate(payment_dates):
            ratio = dist[i] if i < len(dist) else 0.0
            result[d] = max(0.0, estimated_total * ratio)

        return result

    # ──────────────────────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────────────────────

    def _get_distribution(self, key: Tuple, n_days: int) -> List[float]:
        """
        获取分布曲线：
        - 有足够历史数据 → 加权均值（近期权重更高）
        - 历史不足 → 兜底先验
        """
        history = self._dist_history.get(key, [])
        n_events = len(history)

        if n_events < PRESALE_MIN_EVENTS_TO_FIT:
            # 兜底先验
            fallback = _FALLBACK_DISTRIBUTION[:n_days]
            return _normalize_distribution(fallback + [0.0] * max(0, n_days - len(fallback)))

        # 加权均值：最近一次权重 PRESALE_RECENT_EVENT_WEIGHT，其余权重 1.0
        weights = [1.0] * n_events
        weights[-1] = PRESALE_RECENT_EVENT_WEIGHT
        total_w = sum(weights)

        # 对齐到 n_days 长度
        aligned = []
        for dist_vec in history:
            padded = list(dist_vec) + [0.0] * max(0, n_days - len(dist_vec))
            aligned.append(padded[:n_days])

        avg = [
            sum(aligned[j][i] * weights[j] for j in range(n_events)) / total_w
            for i in range(n_days)
        ]
        return _normalize_distribution(avg)

    def _estimate_total(
        self,
        key: Tuple,
        campaign_type: str,
        shop_id: str,
        channel: str,
    ) -> float:
        """
        按优先级估算本次预售总量：
        1. 同类型最近一次历史总量（± 增长趋势）
        2. 同类型历史均值
        3. 跨类型降级（其他活动历史 × 类型比例系数）
        4. 终极兜底（返回 0，由 LightGBM 预测保留）
        """
        vol_list = self.volume_history.get(key, [])

        if len(vol_list) >= 2:
            # 有多次记录：用线性趋势外推
            trend = (vol_list[-1] - vol_list[0]) / len(vol_list)
            estimate = vol_list[-1] + trend
            return max(estimate, vol_list[-1] * 0.5)  # 不低于最近值 50%

        if len(vol_list) == 1:
            return vol_list[0]

        # 跨类型降级
        base_ratio = PRESALE_TYPE_RATIO.get(campaign_type, PRESALE_TYPE_RATIO["default"])
        for other_type, other_ratio in PRESALE_TYPE_RATIO.items():
            if other_type in ("default",) or other_type == campaign_type:
                continue
            other_key = (str(shop_id), str(channel), str(other_type))
            other_vols = self.volume_history.get(other_key, [])
            if other_vols:
                ref_total = np.mean(other_vols)
                return ref_total * (base_ratio / other_ratio)

        # 终极兜底：返回 None，让调用方保留 LightGBM 预测
        logger.warning(
            f"PresaleDistributionModel: 无历史数据可估算总量 "
            f"(shop={shop_id}, channel={channel}, type={campaign_type})，"
            f"预售覆盖不触发"
        )
        return None  # type: ignore

    # ──────────────────────────────────────────────────────────────
    # 从数据库直接 fit
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def fit_from_db(cls, db) -> "PresaleDistributionModel":
        """
        从 presale_history 表直接加载数据并 fit。
        日期内 day_index 自动按 payment_date 升序计算。
        """
        # 延迟导入避免循环依赖
        from api.models import PresaleHistory

        rows = db.query(PresaleHistory).all()
        if not rows:
            logger.warning("presale_history 表为空，PresaleDistributionModel 未训练任何数据")
            return cls()

        payment_events = [
            {
                "shop_id": r.shop_id,
                "channel": r.channel or "直播",
                "campaign_type": r.campaign_type,
                "broadcast_date": r.broadcast_date,
                "payment_date": r.payment_date,
                "day_index": None,   # 由 fit() 内部按 payment_date 顺序自动计算
                "daily_volume": float(r.order_count or 0),
            }
            for r in rows
        ]

        m = cls()
        m.fit(payment_events)
        logger.info(
            f"PresaleDistributionModel.fit_from_db: 加载 {len(rows)} 条预售历史，"
            f"覆盖 {len(m.volume_history)} 个(shop,channel,campaign)组合"
        )
        return m

    # ──────────────────────────────────────────────────────────────
    # 从 schedule_df 快速构建事件列表
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def build_payment_events_from_df(
        cls,
        schedule_df: pd.DataFrame,
        channel_df: pd.DataFrame,
        shop_col: str = "shop_id",
        date_col: str = "date",
        order_col: str = "order_count",
        channel_col: str = "channel",
    ) -> List[Dict]:
        """
        从 schedule_df (presale_payment 行) + channel_df (实际订单) 自动构建
        payment_events 列表，供 fit() 使用。

        schedule_df 需含字段：shop_id, date, event_type, anchor_tier, broadcast_date
        channel_df  需含字段：shop_id, date, channel, order_count

        对应逻辑：
          1. 筛选 event_type='presale_payment' 的行 → 确定尾款日和定金场
          2. 对每个 (shop_id, broadcast_date) 事件，找出所有尾款日
          3. 从 channel_df 中读取这些日期的实际订单量
          4. 计算 day_index（第几个尾款日，0-based）
        """
        if schedule_df is None or len(schedule_df) == 0:
            return []

        pay_rows = schedule_df[
            schedule_df.get(
                "event_type", pd.Series("live", index=schedule_df.index)
            ) == "presale_payment"
        ].copy()
        if len(pay_rows) == 0:
            return []

        pay_rows[date_col] = pd.to_datetime(pay_rows[date_col]).dt.date
        if "broadcast_date" in pay_rows.columns:
            pay_rows["broadcast_date"] = pd.to_datetime(pay_rows["broadcast_date"]).dt.date
        else:
            pay_rows["broadcast_date"] = None

        channel_df = channel_df.copy()
        channel_df[date_col] = pd.to_datetime(channel_df[date_col]).dt.date

        events = []
        for _, row in pay_rows.iterrows():
            s_id = row[shop_col]
            pay_date = row[date_col]
            bcast_date = row.get("broadcast_date")
            campaign_type = _infer_campaign_type(pay_date)

            # 找对应渠道当天订单量（直播渠道可能有多个 channel 名，取 event 渠道）
            day_orders = channel_df[
                (channel_df[shop_col] == s_id) &
                (channel_df[date_col] == pay_date)
            ]

            for _, ord_row in day_orders.iterrows():
                # day_index 从同一 broadcast_date 事件的第一个尾款日算起
                same_event = pay_rows[
                    (pay_rows[shop_col] == s_id) &
                    (pay_rows.get("broadcast_date", pd.Series(None)) == bcast_date)
                ]
                sorted_dates = sorted(same_event[date_col].unique())
                day_idx = sorted_dates.index(pay_date) if pay_date in sorted_dates else 0

                events.append({
                    "shop_id": s_id,
                    "channel": ord_row[channel_col],
                    "campaign_type": campaign_type,
                    "broadcast_date": bcast_date,
                    "payment_date": pay_date,
                    "day_index": day_idx,
                    "daily_volume": float(ord_row.get(order_col, 0) or 0),
                })

        return events
