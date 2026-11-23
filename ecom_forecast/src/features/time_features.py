"""
时序特征工程
基础时间特征 + 滚动统计特征 + 同比特征 + 中国节假日特征
"""

import pandas as pd
import numpy as np
from typing import List, Optional


def build_time_features(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """
    添加基础时间特征

    新增列：
    - day_of_week: 星期几（0=周一）
    - day_of_month: 月中第几天
    - month: 月份
    - quarter: 季度
    - week_of_year: 年中第几周
    - is_weekend: 是否周末
    - is_month_start: 是否月初（1~3号）
    - is_month_end: 是否月末（最后3天）
    """
    df = df.copy()
    dates = pd.to_datetime(df[date_col])

    df["day_of_week"] = dates.dt.dayofweek
    df["day_of_month"] = dates.dt.day
    df["month"] = dates.dt.month
    df["quarter"] = dates.dt.quarter
    df["week_of_year"] = dates.dt.isocalendar().week.astype(int)
    df["is_weekend"] = (dates.dt.dayofweek >= 5).astype(int)
    df["is_month_start"] = (dates.dt.day <= 3).astype(int)
    df["is_month_end"] = (dates.dt.day >= dates.dt.days_in_month - 2).astype(int)

    # 正弦/余弦编码（保留周期连续性）
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    return df


def build_lag_features(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    lags: List[int] = [1, 7, 14, 28],
    date_col: str = "date",
) -> pd.DataFrame:
    """
    添加滞后特征（lag features）

    参数：
        group_cols: 分组键（如 ['shop_id', 'channel']）
        lags: 滞后天数列表
    """
    df = df.copy().sort_values([*group_cols, date_col])
    df[date_col] = pd.to_datetime(df[date_col])
    # shift(N) 是“前 N 行”，数据缺日时并不等于 N 天前。这里按日期键关联，
    # 同组同日若有多行先汇总，避免 merge 产生笛卡尔积。
    daily = (
        df[[*group_cols, date_col, target_col]]
        .groupby([*group_cols, date_col], as_index=False)[target_col]
        .sum(min_count=1)
    )
    for lag in lags:
        col_name = f"{target_col}_lag{lag}"
        lookup = daily.rename(columns={target_col: col_name}).copy()
        lookup[date_col] = lookup[date_col] + pd.Timedelta(days=lag)
        df = df.merge(
            lookup,
            on=[*group_cols, date_col],
            how="left",
            validate="many_to_one",
        )
    return df


def build_rolling_features(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    windows: List[int] = [7, 14, 30],
    date_col: str = "date",
) -> pd.DataFrame:
    """
    添加滚动统计特征（rolling mean / std）
    使用 shift(1) 避免数据泄露（只用过去数据）
    """
    df = df.copy().sort_values([*group_cols, date_col])
    for window in windows:
        shifted = df.groupby(group_cols)[target_col].shift(1)
        df[f"{target_col}_roll_mean{window}"] = (
            shifted.groupby(df[group_cols].apply(tuple, axis=1))
            .transform(lambda x: x.rolling(window, min_periods=1).mean())
        )
        df[f"{target_col}_roll_std{window}"] = (
            shifted.groupby(df[group_cols].apply(tuple, axis=1))
            .transform(lambda x: x.rolling(window, min_periods=1).std().fillna(0))
        )
    return df


def build_rolling_features_fast(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    windows: List[int] = [7, 14, 30],
    date_col: str = "date",
) -> pd.DataFrame:
    """
    更高效的滚动特征实现（使用 groupby + rolling）
    """
    df = df.copy().sort_values([*group_cols, date_col])
    df[date_col] = pd.to_datetime(df[date_col])
    daily = (
        df[[*group_cols, date_col, target_col]]
        .groupby([*group_cols, date_col], as_index=False)[target_col]
        .sum(min_count=1)
        .sort_values([*group_cols, date_col])
    )

    for window in windows:
        mean_col = f"{target_col}_roll_mean{window}"
        std_col = f"{target_col}_roll_std{window}"

        feature_parts = []
        for _, grp in daily.groupby(group_cols, sort=False):
            grp = grp.sort_values(date_col).copy()
            indexed = grp.set_index(date_col)[target_col]
            grp[mean_col] = indexed.rolling(
                f"{window}D", closed="left", min_periods=1
            ).mean().to_numpy()
            grp[std_col] = indexed.rolling(
                f"{window}D", closed="left", min_periods=1
            ).std().fillna(0).to_numpy()
            feature_parts.append(grp[[*group_cols, date_col, mean_col, std_col]])

        feature_df = pd.concat(feature_parts, ignore_index=True) if feature_parts else pd.DataFrame()
        df = df.merge(
            feature_df,
            on=[*group_cols, date_col],
            how="left",
            validate="many_to_one",
        )

    return df


def build_yoy_features(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    date_col: str = "date",
) -> pd.DataFrame:
    """
    添加同比特征（Year-over-Year）
    用去年同一周（±3天范围取均值）对应值，避免大促日期浮动问题
    """
    df = df.copy()
    dates = pd.to_datetime(df[date_col])

    # 创建年份 + 年中天数 索引
    df["_year"] = dates.dt.year
    df["_dayofyear"] = dates.dt.dayofyear

    # 构建去年同期查找表
    df_ref = df[[*group_cols, "_year", "_dayofyear", target_col]].copy()
    df_ref["_year"] = df_ref["_year"] + 1  # 作为"今年"的参照
    df_ref = df_ref.rename(columns={target_col: f"{target_col}_yoy"})

    # ±3天窗口取均值（用 dayofyear 近似匹配，处理闰年偏移）
    merged_parts = []
    for offset in range(-3, 4):
        ref = df_ref.copy()
        ref["_dayofyear"] = ref["_dayofyear"] + offset
        merged_parts.append(ref)

    df_ref_expanded = pd.concat(merged_parts)
    yoy_avg = (
        df_ref_expanded
        .groupby([*group_cols, "_year", "_dayofyear"])[f"{target_col}_yoy"]
        .mean()
        .reset_index()
    )

    df = df.merge(yoy_avg, on=[*group_cols, "_year", "_dayofyear"], how="left")
    df = df.drop(columns=["_year", "_dayofyear"])

    return df


def fill_future_lag_features(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    date_col: str = "date",
    history_window: int = 14,
) -> pd.DataFrame:
    """
    对预测期 lag/rolling/yoy 特征用历史滚动均值填充。

    当 history + future 拼接后，future 行的 target_col = NaN 导致 lag 列为 NaN。
    本函数对每个分组，取历史最后 history_window 天该特征列的均值填入 future 行的 NaN，
    比 ffill 更稳定（避免单个异常日影响整个预测期）。

    注意：调用前应保证 df 已按 [*group_cols, date_col] 升序排列，或在本函数内重新排序。
    """
    lag_cols = [c for c in df.columns if c.startswith(f"{target_col}_lag")]
    roll_cols = [c for c in df.columns if c.startswith(f"{target_col}_roll")]
    yoy_cols = [c for c in df.columns if f"{target_col}_yoy" in c]
    fill_cols = lag_cols + roll_cols + yoy_cols
    if not fill_cols:
        return df

    df = df.copy().sort_values([*group_cols, date_col])

    def _fill_group(grp: pd.DataFrame) -> pd.DataFrame:
        is_hist = grp[target_col].notna()
        is_fut = ~is_hist
        if is_fut.sum() == 0:
            return grp
        for col in fill_cols:
            if col not in grp.columns:
                continue
            # 取历史区间最后 history_window 行的均值作为填充值
            hist_vals = grp.loc[is_hist, col].dropna().tail(history_window)
            fill_val = hist_vals.mean() if len(hist_vals) > 0 else np.nan
            if pd.isna(fill_val):
                # 回退：直接前向填充
                grp[col] = grp[col].ffill()
                continue
            nan_mask = is_fut & grp[col].isna()
            grp.loc[nan_mask, col] = fill_val
        return grp

    # pandas 3 的 GroupBy.apply 默认不再把分组列传入子 DataFrame，导致
    # shop_id/channel 等关键列丢失；显式循环可跨 pandas 版本保持结构稳定。
    filled_groups = [
        _fill_group(grp.copy())
        for _, grp in df.groupby(group_cols, sort=False, dropna=False)
    ]
    if not filled_groups:
        return df
    return pd.concat(filled_groups, ignore_index=True).sort_values(
        [*group_cols, date_col]
    ).reset_index(drop=True)


# ── 中国法定节假日日期表（公历，硬编码至 2026 年）────────────────────────────
_SPRING_FESTIVAL = {  # 正月初一
    2019: "2019-02-05", 2020: "2020-01-25", 2021: "2021-02-12",
    2022: "2022-02-01", 2023: "2023-01-22", 2024: "2024-02-10",
    2025: "2025-01-29", 2026: "2026-02-17",
}
_QINGMING = {  # 清明节
    2019: "2019-04-05", 2020: "2020-04-04", 2021: "2021-04-05",
    2022: "2022-04-05", 2023: "2023-04-05", 2024: "2024-04-04",
    2025: "2025-04-04", 2026: "2026-04-05",
}
_DUANWU = {  # 端午节
    2019: "2019-06-07", 2020: "2020-06-25", 2021: "2021-06-14",
    2022: "2022-06-03", 2023: "2023-06-22", 2024: "2024-06-10",
    2025: "2025-05-31", 2026: "2026-06-19",
}
_ZHONGQIU = {  # 中秋节
    2019: "2019-09-13", 2020: "2020-10-01", 2021: "2021-09-21",
    2022: "2022-09-10", 2023: "2023-09-29", 2024: "2024-09-17",
    2025: "2025-10-06", 2026: "2026-09-25",
}


def build_cn_holiday_features(
    df: pd.DataFrame,
    date_col: str = "date",
) -> pd.DataFrame:
    """
    添加中国法定节假日特征（支持 2019-2026 年）

    新增列：
    - is_spring_festival    : 春节期（除夕到正月十五）
    - days_to_spring_festival: 距最近春节天数（节前负，节后正）
    - is_national_holiday   : 国庆黄金周（10/1~10/7）
    - is_labor_day          : 五一小长假（5/1~5/5）
    - is_qingming_holiday   : 清明节（±2 天）
    - is_duanwu_holiday     : 端午节（±2 天）
    - is_zhongqiu_holiday   : 中秋节（±2 天）
    """
    df = df.copy()
    dates = pd.to_datetime(df[date_col])

    # ── 距最近春节天数（向量化）─────────────────────────────────────────────
    sf_ts = np.array(
        [np.datetime64(v) for v in _SPRING_FESTIVAL.values()],
        dtype="datetime64[D]",
    )
    dates_d = dates.values.astype("datetime64[D]")
    # dist_matrix shape: (n_dates, n_sf)
    dist_matrix = (dates_d[:, np.newaxis] - sf_ts[np.newaxis, :]).astype(np.int64)
    nearest_idx = np.abs(dist_matrix).argmin(axis=1)
    days_to_sf = dist_matrix[np.arange(len(dates_d)), nearest_idx]

    df["days_to_spring_festival"] = days_to_sf
    # 春节期：除夕(-1) 到正月十五(+14)
    df["is_spring_festival"] = (
        (df["days_to_spring_festival"] >= -1) &
        (df["days_to_spring_festival"] <= 14)
    ).astype(int)

    # ── 国庆黄金周（10/1~10/7）��───────────────────────────────────────────
    df["is_national_holiday"] = (
        (dates.dt.month == 10) & (dates.dt.day >= 1) & (dates.dt.day <= 7)
    ).astype(int)

    # ── 五一小长假（5/1~5/5）──────────────────────────────────────────────
    df["is_labor_day"] = (
        (dates.dt.month == 5) & (dates.dt.day >= 1) & (dates.dt.day <= 5)
    ).astype(int)

    # ── 清明 / 端午 / 中秋（各 ±2 天窗口，按年查表）────────────────────
    def _holiday_mask(date_series: pd.Series, year_date_map: dict, window: int = 2) -> pd.Series:
        result = pd.Series(0, index=date_series.index, dtype=int)
        for year, date_str in year_date_map.items():
            center = pd.Timestamp(date_str)
            mask = (date_series - center).dt.days.abs() <= window
            result = result | mask.astype(int)
        return result

    df["is_qingming_holiday"] = _holiday_mask(dates, _QINGMING)
    df["is_duanwu_holiday"] = _holiday_mask(dates, _DUANWU)
    df["is_zhongqiu_holiday"] = _holiday_mask(dates, _ZHONGQIU)

    return df


def build_all_time_features(
    df: pd.DataFrame,
    target_col: str,
    group_cols: List[str],
    date_col: str = "date",
    lag_days: List[int] = [1, 7, 14, 28],
    roll_windows: List[int] = [7, 14, 30],
    include_yoy: bool = True,
) -> pd.DataFrame:
    """
    统一入口：依次构建所有时序特征
    """
    df = build_time_features(df, date_col)
    df = build_lag_features(df, target_col, group_cols, lag_days, date_col)
    df = build_rolling_features_fast(df, target_col, group_cols, roll_windows, date_col)
    if include_yoy:
        df = build_yoy_features(df, target_col, group_cols, date_col)
    df = build_cn_holiday_features(df, date_col)
    return df
