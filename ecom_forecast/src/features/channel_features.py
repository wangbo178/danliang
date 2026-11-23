"""
渠道特有特征
- 直播类：排期特征
- 联盟类：佣金率特征
- 货架类：无额外特征（依赖时序）
- 预售单量特征：从 is_presale_order 标记提取规模信号
"""

import pandas as pd
import numpy as np
from typing import Optional, List


def prepare_anchor_history_stats(
    anchor_df: Optional[pd.DataFrame],
    date_col: str = "date",
    shop_col: str = "shop_id",
) -> Optional[pd.DataFrame]:
    """预聚合达人历史统计，供训练、正式预测和 holdout 复用。

    统计值包含当日实绩；特征关联时使用 allow_exact_matches=False，
    因此模型在任意日期只能看到之前的统计，不会泄漏当日目标。
    """
    if anchor_df is None or len(anchor_df) == 0:
        return anchor_df
    required = {date_col, "anchor_name", "order_count"}
    if not required.issubset(anchor_df.columns):
        return anchor_df

    group_cols = ["anchor_name"]
    if shop_col in anchor_df.columns:
        group_cols.insert(0, shop_col)
    prepared = anchor_df[[*group_cols, date_col, "order_count"]].copy()
    prepared[date_col] = pd.to_datetime(prepared[date_col]).astype("datetime64[ns]")
    prepared["anchor_name"] = prepared["anchor_name"].astype("string").str.strip()
    prepared["order_count"] = pd.to_numeric(
        prepared["order_count"], errors="coerce"
    )
    prepared = prepared.dropna(subset=["anchor_name", "order_count"])
    prepared = (
        prepared.groupby([*group_cols, date_col], as_index=False)["order_count"]
        .sum()
        .sort_values([*group_cols, date_col])
    )
    prepared["anchor_hist_mean_precomputed"] = (
        prepared.groupby(group_cols, sort=False)["order_count"]
        .expanding().mean()
        .reset_index(level=group_cols, drop=True)
    )
    prepared["anchor_hist_recent_precomputed"] = (
        prepared.groupby(group_cols, sort=False)["order_count"]
        .rolling(3, min_periods=1).mean()
        .reset_index(level=group_cols, drop=True)
    )
    return prepared.reset_index(drop=True)


def build_live_features(
    df: pd.DataFrame,
    schedule_df: Optional[pd.DataFrame] = None,
    date_col: str = "date",
    shop_col: str = "shop_id",
    channel_df: Optional[pd.DataFrame] = None,
    anchor_encoder: Optional[dict] = None,
    anchor_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    为直播类渠道添加排期特征。

    schedule_df 格式（新版）：
        shop_id | date           | event_type       | anchor_tier | broadcast_date
        CS01    | 2024-06-01     | live             | A           |
        CS01    | 2024-06-18     | presale_payment  | A           | 2024-06-01

        event_type:
          - live             : 普通直播/定金日直播，当天产生订单
          - presale_payment  : 预售尾款日，date=尾款日，broadcast_date=定金场播出日

    新增特征：
      has_live              - 当天是否有普通直播 (event_type='live')
      anchor_tier_code      - 当天直播主播档级 (A=3, B=2, C=1)
      live_lag1/7/14        - 1/7/14天前是否有直播
      anchor_tier_lag1/7/14 - 对应档级
      is_presale_payment    - 当天是否是预售尾款日
      presale_pay_lag       - 尾款日距定金场播出日的实际天数（精确，非固定7天）
      presale_pay_tier      - 该预售场的主播档级
    """
    df = df.copy()
    # MySQL DATE 、pd.date_range 和 Excel 可能产生 s/us/ns 不同精度的
    # datetime64；merge_asof 要求两侧 dtype 完全一致。
    df[date_col] = pd.to_datetime(df[date_col]).astype("datetime64[ns]")

    _new_cols = [
        "has_live", "anchor_tier_code",
        "live_lag1", "live_lag7", "live_lag14",
        "anchor_tier_lag1", "anchor_tier_lag7", "anchor_tier_lag14",
        "is_presale_payment", "presale_pay_lag", "presale_pay_tier",
        "anchor_name_code", "anchor_hist_mean", "anchor_hist_recent",
    ]
    if schedule_df is None or len(schedule_df) == 0:
        for col in _new_cols:
            df[col] = 0
        return df

    schedule_df = schedule_df.copy()
    schedule_df[date_col] = pd.to_datetime(schedule_df[date_col]).astype("datetime64[ns]")

    # 兼容旧格式（只有 has_live=1/0，无 event_type）
    if "event_type" not in schedule_df.columns:
        schedule_df["event_type"] = np.where(
            schedule_df.get("has_live", pd.Series(1, index=schedule_df.index)).astype(int) == 1,
            "live", "none"
        )
    schedule_df["event_type"] = schedule_df["event_type"].fillna("live")

    # anchor_tier 编码：A=3（头部）, B=2（腰部）, C=1（尾部）
    tier_map = {"A": 3, "B": 2, "C": 1}
    schedule_df["anchor_tier_code"] = (
        schedule_df.get("anchor_tier", pd.Series("", index=schedule_df.index))
        .map(tier_map).fillna(0).astype(int)
    )

    # ── 普通直播行（event_type='live'）────────────────────────
    live_source = schedule_df[schedule_df["event_type"] == "live"]
    live_merge_keys = [shop_col, date_col]
    if (
        "channel" in live_source.columns
        and "channel" in df.columns
        and len(live_source) > 0
        and live_source["channel"].notna().all()
    ):
        live_merge_keys.append("channel")
    live_df = live_source[[*live_merge_keys, "anchor_tier_code"]].copy()
    live_df["has_live"] = 1
    live_df = (
        live_df.groupby(live_merge_keys, as_index=False)
        .agg(has_live=("has_live", "max"), anchor_tier_code=("anchor_tier_code", "max"))
    )

    # 1. 当天直播特征
    df = df.merge(
        live_df[[*live_merge_keys, "has_live", "anchor_tier_code"]],
        on=live_merge_keys, how="left", validate="many_to_one"
    )
    df["has_live"]         = df["has_live"].fillna(0).astype(int)
    df["anchor_tier_code"] = df["anchor_tier_code"].fillna(0).astype(int)

    # 2. 直播滞后特征：lag1 / lag7 / lag14
    for lag in [1, 7, 14]:
        lagged = live_df.copy()
        lagged[date_col] = lagged[date_col] + pd.Timedelta(days=lag)
        lagged = lagged.rename(columns={
            "has_live":         f"live_lag{lag}",
            "anchor_tier_code": f"anchor_tier_lag{lag}",
        })
        df = df.merge(
            lagged[[*live_merge_keys, f"live_lag{lag}", f"anchor_tier_lag{lag}"]],
            on=live_merge_keys, how="left", validate="many_to_one"
        )
        df[f"live_lag{lag}"]         = df[f"live_lag{lag}"].fillna(0).astype(int)
        df[f"anchor_tier_lag{lag}"]  = df[f"anchor_tier_lag{lag}"].fillna(0).astype(int)

    # ── 预售尾款行（event_type='presale_payment'）─────────────
    pay_source = schedule_df[schedule_df["event_type"] == "presale_payment"]
    if "broadcast_date" not in pay_source.columns:
        pay_source = pay_source.assign(broadcast_date=pd.NaT)
    pay_merge_keys = [shop_col, date_col]
    if (
        "channel" in pay_source.columns
        and "channel" in df.columns
        and len(pay_source) > 0
        and pay_source["channel"].notna().all()
    ):
        pay_merge_keys.append("channel")
    pay_df = pay_source[[*pay_merge_keys, "anchor_tier_code", "broadcast_date"]].copy()
    pay_df["is_presale_payment"] = 1

    # 精确 lag = 尾款日 - 定金播出日（实际天数，非固定7/14）
    if "broadcast_date" in pay_df.columns and pay_df["broadcast_date"].notna().any():
        pay_df["broadcast_date"] = pd.to_datetime(pay_df["broadcast_date"])
        pay_df["presale_pay_lag"] = (
            (pay_df[date_col] - pay_df["broadcast_date"]).dt.days.clip(lower=0)
        )
    else:
        pay_df["presale_pay_lag"] = 0

    pay_df = pay_df.rename(columns={"anchor_tier_code": "presale_pay_tier"})

    # 同一天多场尾款取最高档级、最大lag
    pay_agg = (
        pay_df.groupby(pay_merge_keys)
        .agg(
            is_presale_payment=("is_presale_payment", "max"),
            presale_pay_lag=("presale_pay_lag", "max"),
            presale_pay_tier=("presale_pay_tier", "max"),
        )
        .reset_index()
    )
    df = df.merge(pay_agg, on=pay_merge_keys, how="left", validate="many_to_one")
    df["is_presale_payment"] = df["is_presale_payment"].fillna(0).astype(int)
    df["presale_pay_lag"]    = df["presale_pay_lag"].fillna(0).astype(int)
    df["presale_pay_tier"]   = df["presale_pay_tier"].fillna(0).astype(int)

    # ── 达人名称特征 ─────────────────────────────────────────
    # Step A：将排期表中的 anchor_name 关联到 df 的每一行
    _enc = anchor_encoder or {}
    if "anchor_name" in schedule_df.columns:
        anchor_name_day = schedule_df[schedule_df["event_type"] == "live"][[
            *live_merge_keys, "anchor_name"
        ]].copy()
        anchor_name_day[date_col] = pd.to_datetime(anchor_name_day[date_col])
        anchor_name_day = anchor_name_day.dropna(subset=["anchor_name"])
        # 同一天同店铺多场：取排名第一的达人（按字典序，保持确定性）
        anchor_name_day = anchor_name_day.sort_values("anchor_name").drop_duplicates(
            subset=live_merge_keys, keep="first"
        )
        df = df.merge(
            anchor_name_day[[*live_merge_keys, "anchor_name"]],
            on=live_merge_keys, how="left", validate="many_to_one"
        )
        # left join 后全 NaN 时 pandas 会把列推断为 float64，强制保持 object 类型
        # 否则后续 merge(_anchor_stats, on="anchor_name") 会因 float64 vs object 报错
        df["anchor_name"] = df["anchor_name"].astype(object)
        df["anchor_name_code"] = df["anchor_name"].map(_enc).fillna(0).astype(int)
    else:
        df["anchor_name"] = pd.array([None] * len(df), dtype=object)  # object 而非 float64
        df["anchor_name_code"] = 0

    # Step B：按日期做 as-of 历史统计。旧实现把达人全量均值（含当前和未来场次）
    # 填到每个训练日，形成目标泄漏。
    anchor_history = None
    if anchor_df is not None and len(anchor_df) > 0:
        anchor_cols = [date_col, "anchor_name", "order_count"]
        if shop_col in anchor_df.columns:
            anchor_cols.insert(1, shop_col)
        for stat_col in (
            "anchor_hist_mean_precomputed", "anchor_hist_recent_precomputed"
        ):
            if stat_col in anchor_df.columns:
                anchor_cols.append(stat_col)
        anchor_history = anchor_df[anchor_cols].copy()
    # 不再从 channel_df 的目标值反推达人历史；那会在训练/验证阶段引入
    # 当前或未来订单，并且预测期无法获得同口径数据。没有达人实绩时统一填 0。

    df["anchor_hist_mean"] = np.nan
    df["anchor_hist_recent"] = np.nan
    if anchor_history is not None and len(anchor_history) > 0:
        history = anchor_history.dropna(subset=["anchor_name", "order_count"]).copy()
        history[date_col] = pd.to_datetime(history[date_col]).astype("datetime64[ns]")
        history["anchor_name"] = history["anchor_name"].astype(str).str.strip()
        if "anchor_name" in df.columns:
            df["anchor_name"] = df["anchor_name"].apply(
                lambda value: value.strip() if isinstance(value, str) else value
            )
        history["_anchor_key"] = history["anchor_name"].astype(str)
        df["_anchor_key"] = df["anchor_name"].fillna("").astype(str)
        anchor_group_cols = ["_anchor_key"]
        if shop_col in history.columns and shop_col in df.columns:
            history["_shop_key"] = history[shop_col].astype(str)
            df["_shop_key"] = df[shop_col].astype(str)
            anchor_group_cols.insert(0, "_shop_key")
        precomputed = {
            "anchor_hist_mean_precomputed", "anchor_hist_recent_precomputed"
        }.issubset(history.columns)
        if precomputed:
            history = history.rename(columns={
                "anchor_hist_mean_precomputed": "anchor_hist_mean",
                "anchor_hist_recent_precomputed": "anchor_hist_recent",
            }).sort_values([*anchor_group_cols, date_col])
        else:
            history = (
                history.groupby([*anchor_group_cols, date_col], as_index=False)["order_count"]
                .sum()
                .sort_values([*anchor_group_cols, date_col])
            )
            history["anchor_hist_mean"] = (
                history.groupby(anchor_group_cols, sort=False)["order_count"]
                .expanding().mean()
                .reset_index(level=anchor_group_cols, drop=True)
            )
            history["anchor_hist_recent"] = (
                history.groupby(anchor_group_cols, sort=False)["order_count"]
                .rolling(3, min_periods=1).mean()
                .reset_index(level=anchor_group_cols, drop=True)
            )
        df["_anchor_row_id"] = np.arange(len(df))
        left = df[df["_anchor_key"].ne("")][
            ["_anchor_row_id", date_col, *anchor_group_cols]
        ].sort_values([date_col, *anchor_group_cols])
        right = history[
            [date_col, *anchor_group_cols, "anchor_hist_mean", "anchor_hist_recent"]
        ].sort_values([date_col, *anchor_group_cols])
        if len(left) > 0 and len(right) > 0:
            stats_all = pd.merge_asof(
                left,
                right,
                on=date_col,
                by=anchor_group_cols,
                direction="backward",
                allow_exact_matches=False,
            ).set_index("_anchor_row_id")
            df["anchor_hist_mean"] = df["_anchor_row_id"].map(stats_all["anchor_hist_mean"])
            df["anchor_hist_recent"] = df["_anchor_row_id"].map(stats_all["anchor_hist_recent"])
        df = df.drop(columns=["_anchor_row_id", "_anchor_key", "_shop_key"], errors="ignore")

    df["anchor_hist_mean"]   = df["anchor_hist_mean"].fillna(0.0)
    df["anchor_hist_recent"] = df["anchor_hist_recent"].fillna(0.0)

    return df


def build_affiliate_features(
    df: pd.DataFrame,
    commission_df: Optional[pd.DataFrame] = None,
    date_col: str = "date",
    shop_col: str = "shop_id",
) -> pd.DataFrame:
    """
    为联盟/CPS渠道添加佣金特征

    commission_df 格式：
        shop_id | channel | month | commission_rate | active_affiliates
    """
    df = df.copy()

    if commission_df is None:
        df["commission_rate"] = 0.0
        df["active_affiliates"] = 0
        return df

    commission_df = commission_df.copy()
    df["_month"] = pd.to_datetime(df[date_col]).dt.to_period("M").astype(str)
    commission_df["_month"] = pd.to_datetime(commission_df["month"]).dt.to_period("M").astype(str)

    df = df.merge(
        commission_df[[shop_col, "_month", "commission_rate", "active_affiliates"]],
        on=[shop_col, "_month"],
        how="left",
    )
    df["commission_rate"] = df["commission_rate"].fillna(0.0)
    df["active_affiliates"] = df["active_affiliates"].fillna(0)
    df = df.drop(columns=["_month"])

    return df


def build_presale_order_features(
    df: pd.DataFrame,
    group_cols: List[str] = None,
    date_col: str = "date",
    order_col: str = "order_count",
    presale_flag_col: str = "is_presale_order",
) -> pd.DataFrame:
    """
    从历史订单数据中的 is_presale_order 标记，提取预售单量规模特征。

    核心思路
    --------
    预售单量和普通单量的时间节奏完全不同：
      - 普通直播：当天有直播就有单量，lag1/7/14 有效
      - 预售尾款：单量集中在大促前后3天，相邻日几乎为0，lag365（去年同期）才有意义

    因此单独提取 presale_orders 序列，供模型学习预售规模与上次大促的关系。

    新增特征
    --------
    presale_orders          : 当日预售单量（= order_count × is_presale_order）
    presale_orders_lag7     : 7天前预售单量（一般=0，除非7天前也是大促）
    presale_orders_lag14    : 14天前预售单量
    presale_orders_lag28    : 28天前预售单量
    presale_orders_roll_max30 : 近30天最大日预售单量（≈上次大促峰值）
    presale_orders_roll_sum90 : 近90天累计预售单量（≈近期一个季度预售规模）
    presale_camp_ratio      : 近30天中预售单占总单量的比例（渠道预售依赖度）

    参数
    ----
    group_cols : 分组键，默认 ['shop_id', 'channel']
    presale_flag_col : 是否预售单的列名（列名兼容 is_presale_order / 是否预售单）
    """
    if group_cols is None:
        group_cols = ["shop_id", "channel"]

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values([*group_cols, date_col])

    # ── 兼容中文列名 ─────────────────────────────────────────
    _flag_col = None
    for _c in [presale_flag_col, "是否预售单", "is_presale_order"]:
        if _c in df.columns:
            _flag_col = _c
            break
    if _flag_col is None:
        # 列不存在时全填0（兼容未上传该列的情况）
        for col in ["presale_orders", "presale_orders_lag7", "presale_orders_lag14",
                    "presale_orders_lag28", "presale_orders_roll_max30",
                    "presale_orders_roll_sum90", "presale_camp_ratio"]:
            df[col] = 0.0
        return df

    flag = df[_flag_col].fillna(0).astype(int)

    # ── 预售单量序列 ─────────────────────────────────────────
    df["presale_orders"] = (df[order_col].fillna(0) * flag).clip(lower=0)

    g = df.groupby(group_cols)["presale_orders"]

    # 点滞后（同 order_count lags，但只看预售单）
    for lag in [7, 14, 28]:
        df[f"presale_orders_lag{lag}"] = g.shift(lag).fillna(0)

    # 滚动最大值：近30天预售峰值（≈上次大促最高日单量）
    df["presale_orders_roll_max30"] = (
        g.transform(lambda x: x.shift(1).rolling(30, min_periods=1).max()).fillna(0)
    )

    # 滚动求和：近90天累计预售量（跨大促周期的规模信号）
    df["presale_orders_roll_sum90"] = (
        g.transform(lambda x: x.shift(1).rolling(90, min_periods=1).sum()).fillna(0)
    )

    # 渠道预售依赖度：近30天预售单 / 总单（越高 = 该渠道越依赖预售）
    total_roll30 = (
        df.groupby(group_cols)[order_col]
        .transform(lambda x: x.shift(1).rolling(30, min_periods=1).sum())
        .fillna(1).clip(lower=1)
    )
    presale_roll30 = (
        df.groupby(group_cols)["presale_orders"]
        .transform(lambda x: x.shift(1).rolling(30, min_periods=1).sum())
        .fillna(0)
    )
    df["presale_camp_ratio"] = (presale_roll30 / total_roll30).clip(upper=1.0).round(4)

    return df
