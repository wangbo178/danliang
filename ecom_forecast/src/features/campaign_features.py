"""
大促特征工程
将大促日历转换为相对距离特征，处理浮动日期问题

店铺级大促敏感度（两种方式二选一或结合）：
  - 数据驱动：build_shop_campaign_sensitivity() 从历史数据自动计算
  - 手动覆盖：通过 shop_override_map {"shop_id": "on"/"off"/"auto"} 强制指定
优先级：手动覆盖 > 数据驱动 > 默认值(0.5)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from config.campaign_calendar import CAMPAIGN_CALENDAR, POST_CAMPAIGN_DAYS, PREHEAT_DAYS


def _build_campaign_events(calendar: Dict) -> List[Dict]:
    """将嵌套的日历字典展平为事件列表"""
    events = []
    for year, campaigns in calendar.items():
        for name, dates in campaigns.items():
            start = pd.Timestamp(dates["start"])
            end = pd.Timestamp(dates["end"])
            presale_start = pd.Timestamp(dates["presale_start"]) if dates["presale_start"] else None
            events.append({
                "name": name,
                "year": year,
                "presale_start": presale_start,
                "start": start,
                "end": end,
                "post_end": end + timedelta(days=POST_CAMPAIGN_DAYS),
            })
    return sorted(events, key=lambda x: x["start"])


_CAMPAIGN_EVENTS = _build_campaign_events(CAMPAIGN_CALENDAR)


def _find_nearest_campaign(date: pd.Timestamp) -> Tuple[Optional[Dict], int]:
    """
    找到距给定日期最近的大促事件，返回 (event, days_to_start)
    days_to_start < 0 表示大促未开始（大促前）
    days_to_start >= 0 表示大促已开始或结束（大促后）
    """
    best_event = None
    best_distance = float("inf")
    best_days = 0

    for event in _CAMPAIGN_EVENTS:
        # 相对于大促正式开始日的天数差
        days = (date - event["start"]).days
        distance = abs(days)

        if distance < best_distance:
            best_distance = distance
            best_event = event
            best_days = days

    return best_event, best_days


def build_shop_campaign_sensitivity(
    history_df: pd.DataFrame,
    date_col: str = "date",
    shop_col: str = "shop_id",
    order_col: str = "order_count",
    min_campaign_days: int = 5,
) -> Dict[str, float]:
    """
    从历史数据自动计算每个店铺的大促敏感度，返回 {shop_id: score}。

    score 含义（归一化到 [0, 1]）：
        1.0  大促日单量是基准的 3 倍以上（高度敏感）
        0.5  大促日单量约为基准的 2 倍（中等敏感，默认值）
        0.0  大促日单量与基准持平或更低（不参与/不受影响）

    计算公式：
        raw_lift = campaign_avg / baseline_avg
        sensitivity = clip((raw_lift - 1.0) / 2.0, 0, 1)

    参数：
        min_campaign_days: 店铺至少需要这么多大促历史样本，否则返回默认值 0.5
    """
    df = history_df[[date_col, shop_col, order_col]].dropna(subset=[order_col]).copy()
    # 先打大促日标签（不带敏感度，纯日历）
    df = build_campaign_features(df, date_col=date_col, shop_col=shop_col)

    results: Dict[str, float] = {}
    for shop_id, grp in df.groupby(shop_col):
        campaign_rows = grp[grp["is_campaign_day"] == 1]
        baseline_rows = grp[
            (grp["is_campaign_day"] == 0) &
            (grp["is_presale"] == 0) &
            (grp["is_preheat"] == 0) &
            (grp["is_post_campaign"] == 0)
        ]

        if len(campaign_rows) < min_campaign_days or len(baseline_rows) == 0:
            results[shop_id] = 0.5  # 数据不足，保守默认
            continue

        campaign_avg = campaign_rows[order_col].mean()
        baseline_avg = baseline_rows[order_col].mean()

        if baseline_avg <= 0:
            results[shop_id] = 0.5
            continue

        raw_lift = campaign_avg / baseline_avg
        # lift=1.0 → 无变化 → sensitivity=0
        # lift=3.0 → 3倍增长 → sensitivity=1.0
        sensitivity = float(np.clip((raw_lift - 1.0) / 2.0, 0.0, 1.0))
        results[shop_id] = round(sensitivity, 4)

    return results


def build_campaign_features(
    df: pd.DataFrame,
    date_col: str = "date",
    shop_col: str = "shop_id",
    shop_sensitivity_map: Optional[Dict[str, float]] = None,
    shop_override_map: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    为 DataFrame 添加大促相关特征

    输入：
        df: 包含日期列的 DataFrame
        date_col: 日期列名
        shop_col: 店铺 ID 列名（用于查店铺敏感度）
        shop_sensitivity_map: {shop_id: score(0~1)}，数据驱动敏感度
        shop_override_map: {shop_id: "on"/"off"/"auto"}，手动覆盖
            "on"   → 强制 sensitivity=1.0（确认参与大促）
            "off"  → 强制 sensitivity=0.0（不参与大促）
            "auto" → 使用数据驱动结果（或默认值）

    输出：
        df with 新增列：
        - days_to_nearest_campaign: 距最近大促正式日的天数（负=大促前，正=大促后）
        - nearest_campaign_name: 最近大促名称
        - is_preheat: 是否在预热期（大促前 7 天内）
        - is_presale: 是否在预售期
        - presale_day_index: 预售期第几天（1=第一天，0=非预售期）
        - is_campaign_day: 是否在大促正式期
        - is_final_payment: 是否在尾款爆发期（仅含预售大促的正式开始后 0~2 天）
        - final_payment_day_offset: 尾款期第几天（1/2/3，0=非尾款爆发期或无预售大促）
        - is_post_campaign: 是否在大促售后期（结束后 7 天内）
        - campaign_intensity: 大促强度分（0~1，纯日历，不含店铺敏感度）
        - shop_campaign_sensitivity: 店铺大促敏感度（0~1）
        - effective_campaign_intensity: campaign_intensity × shop_campaign_sensitivity
    """
    df = df.copy()
    dates = pd.to_datetime(df[date_col])

    results = {
        "days_to_nearest_campaign": [],
        "nearest_campaign_name": [],
        "is_preheat": [],
        "is_presale": [],
        "presale_day_index": [],
        "is_campaign_day": [],
        "is_final_payment": [],
        "final_payment_day_offset": [],
        "is_post_campaign": [],
        "campaign_intensity": [],
    }

    for date in dates:
        event, days = _find_nearest_campaign(date)

        if event is None:
            results["days_to_nearest_campaign"].append(999)
            results["nearest_campaign_name"].append("none")
            results["is_preheat"].append(0)
            results["is_presale"].append(0)
            results["presale_day_index"].append(0)
            results["is_campaign_day"].append(0)
            results["is_final_payment"].append(0)
            results["final_payment_day_offset"].append(0)
            results["is_post_campaign"].append(0)
            results["campaign_intensity"].append(0.0)
            continue

        results["days_to_nearest_campaign"].append(days)
        results["nearest_campaign_name"].append(event["name"])

        # 是否大促正式期
        in_campaign = event["start"] <= date <= event["end"]
        results["is_campaign_day"].append(int(in_campaign))

        # 是否预热期（大促前 PREHEAT_DAYS 天，排除预售期）
        in_preheat = (-PREHEAT_DAYS <= days < 0) and not in_campaign
        results["is_preheat"].append(int(in_preheat))

        # 是否预售期 + 预售期第几天
        in_presale = False
        presale_day_idx = 0
        if event["presale_start"] is not None:
            in_presale = event["presale_start"] <= date < event["start"]
            if in_presale:
                presale_day_idx = (date - event["presale_start"]).days + 1
        results["is_presale"].append(int(in_presale))
        results["presale_day_index"].append(presale_day_idx)

        # 尾款爆发期：仅有预售期的大促才存在尾款机制（付定金→付尾款）
        # 无预售大促（38、1212等）直接开门红，不存在尾款，不标记
        # 有预售大促中，>95% 的尾款集中在正式开始后前3天
        has_presale = event["presale_start"] is not None
        in_final_payment = in_campaign and has_presale and 0 <= days <= 2
        final_payment_offset = (days + 1) if in_final_payment else 0
        results["is_final_payment"].append(int(in_final_payment))
        results["final_payment_day_offset"].append(final_payment_offset)

        # 是否售后期
        in_post = event["end"] < date <= event["post_end"]
        results["is_post_campaign"].append(int(in_post))

        # 大促强度分：大促日=1.0，越远越低，最远=0
        if in_campaign:
            intensity = 1.0
        elif in_presale:
            presale_len = (event["start"] - event["presale_start"]).days
            days_in_presale = (date - event["presale_start"]).days
            intensity = 0.3 + 0.7 * (days_in_presale / max(presale_len, 1))
        elif -PREHEAT_DAYS <= days < 0:
            intensity = 0.2 + 0.1 * (days + PREHEAT_DAYS) / PREHEAT_DAYS
        elif 0 < days <= POST_CAMPAIGN_DAYS:
            intensity = 0.3 * (1 - days / POST_CAMPAIGN_DAYS)
        else:
            intensity = max(0.0, 1.0 - abs(days) / 60)  # 60天外衰减到0

        results["campaign_intensity"].append(round(intensity, 4))

    for col, values in results.items():
        df[col] = values

    # 大促名称编码
    campaign_names = sorted(set(results["nearest_campaign_name"]))
    campaign_code_map = {name: i for i, name in enumerate(campaign_names)}
    df["nearest_campaign_code"] = df["nearest_campaign_name"].map(campaign_code_map)

    # ── 店铺大促敏感度 ────────────────────────────────────────
    # 优先级：手动覆盖(on/off) > 数据驱动 > 默认值(0.5)
    _sm = shop_sensitivity_map or {}
    _om = shop_override_map or {}

    def _get_sensitivity(shop_id) -> float:
        mode = _om.get(shop_id, "auto")
        if mode == "off":
            return 0.0
        if mode == "on":
            return 1.0
        # auto：用数据驱动结果，无则默认 0.5
        return _sm.get(shop_id, 0.5)

    if shop_col in df.columns:
        df["shop_campaign_sensitivity"] = df[shop_col].map(_get_sensitivity)
    else:
        df["shop_campaign_sensitivity"] = 0.5

    df["effective_campaign_intensity"] = (
        df["campaign_intensity"] * df["shop_campaign_sensitivity"]
    ).round(4)

    return df
