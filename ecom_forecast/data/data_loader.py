"""
真实数据适配器
将业务系统导出的数据列名映射为模型期望的标准列名
根据实际数据字段修改下方的列名映射即可，不需要改模型代码
"""

import pandas as pd
from pathlib import Path


# ============================================================
# 列名映射：左边是你的真实数据列名，右边是模型期望的列名
# 根据实际情况修改左边的值
# ============================================================

CHANNEL_ORDER_COLUMN_MAP = {
    "日期":     "date",
    "平台":     "platform",
    "店铺ID":   "shop_id",
    "渠道":     "channel",
    "订单量":   "order_count",
    "是否派样": "is_sample",   # 值：0 或 1（或 True/False，会自动转换）
}

WAREHOUSE_ORDER_COLUMN_MAP = {
    "日期":     "date",
    "店铺ID":   "shop_id",
    "平台":     "platform",
    "渠道":     "channel",
    "是否派样": "is_sample",
    "仓库ID":   "warehouse_id",
    "订单量":   "order_count",
}

LIVE_SCHEDULE_COLUMN_MAP = {
    "店铺ID":     "shop_id",
    "日期":       "date",
    "是否直播":   "has_live",      # 0/1
    "主播档级":   "anchor_tier",   # A/B/C，可选
    "预计观看人数": "expected_viewers",  # 可选
}

COMMISSION_COLUMN_MAP = {
    "店铺ID":    "shop_id",
    "月份":      "month",          # 格式：YYYY-MM 或 YYYY-MM-DD
    "佣金率":    "commission_rate", # 小数，如 0.15 表示 15%
    "活跃达人数": "active_affiliates",
}

# ============================================================
# 渠道名称映射
# 左边是业务系统中的渠道名称，右边是模型内部 key
# 需要与 config/routing_config.py 中的 CHANNEL_TYPE_MAP 保持一致
# ============================================================

CHANNEL_NAME_MAP = {
    # 淘宝
    "淘宝货架":  "taobao_shelf",
    "淘宝直播":  "taobao_live",
    "CPS":       "taobao_cps",
    # 抖音
    "抖音货架":  "douyin_shelf",
    "自播":      "douyin_live",
    "达播":      "douyin_anchor",
    "精选联盟":  "douyin_union",
}

PLATFORM_NAME_MAP = {
    "淘宝": "taobao",
    "抖音": "douyin",
}


# ============================================================
# 加载函数
# ============================================================

def _apply_column_map(df: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    """只重命名存在的列，不存在的列跳过"""
    existing_map = {k: v for k, v in col_map.items() if k in df.columns}
    return df.rename(columns=existing_map)


def load_channel_orders(path: str) -> pd.DataFrame:
    """
    加载渠道订单数据

    支持格式：.csv / .xlsx / .parquet
    加载后自动完成列名映射、类型转换、渠道名标准化
    """
    df = _read_file(path)
    df = _apply_column_map(df, CHANNEL_ORDER_COLUMN_MAP)

    # 渠道名标准化
    if "channel" in df.columns and df["channel"].dtype == object:
        df["channel"] = df["channel"].map(CHANNEL_NAME_MAP).fillna(df["channel"])

    # 平台名标准化
    if "platform" in df.columns and df["platform"].dtype == object:
        df["platform"] = df["platform"].map(PLATFORM_NAME_MAP).fillna(df["platform"])

    # 类型转换
    df["date"] = pd.to_datetime(df["date"])
    df["order_count"] = pd.to_numeric(df["order_count"], errors="coerce").fillna(0).astype(int)
    df["is_sample"] = df["is_sample"].astype(int) if "is_sample" in df.columns else 0

    return df


def load_warehouse_orders(path: str) -> pd.DataFrame:
    """加载仓库订单数据"""
    df = _read_file(path)
    df = _apply_column_map(df, WAREHOUSE_ORDER_COLUMN_MAP)

    if "channel" in df.columns and df["channel"].dtype == object:
        df["channel"] = df["channel"].map(CHANNEL_NAME_MAP).fillna(df["channel"])
    if "platform" in df.columns and df["platform"].dtype == object:
        df["platform"] = df["platform"].map(PLATFORM_NAME_MAP).fillna(df["platform"])

    df["date"] = pd.to_datetime(df["date"])
    df["order_count"] = pd.to_numeric(df["order_count"], errors="coerce").fillna(0).astype(int)
    df["is_sample"] = df["is_sample"].astype(int) if "is_sample" in df.columns else 0

    return df


def load_live_schedule(path: str) -> pd.DataFrame:
    """加载直播排期数据（可选）"""
    df = _read_file(path)
    df = _apply_column_map(df, LIVE_SCHEDULE_COLUMN_MAP)
    df["date"] = pd.to_datetime(df["date"])
    df["has_live"] = df["has_live"].astype(int) if "has_live" in df.columns else 0
    return df


def load_commission(path: str) -> pd.DataFrame:
    """加载联盟佣金数据（可选）"""
    df = _read_file(path)
    df = _apply_column_map(df, COMMISSION_COLUMN_MAP)
    if "month" in df.columns:
        df["month"] = pd.to_datetime(df["month"]).dt.strftime("%Y-%m-01")
    return df


def _read_file(path: str) -> pd.DataFrame:
    path = str(path)
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    elif path.endswith(".csv"):
        return pd.read_csv(path, encoding="utf-8-sig")
    elif path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    else:
        raise ValueError(f"不支持的文件格式: {path}，请使用 .csv / .xlsx / .parquet")
