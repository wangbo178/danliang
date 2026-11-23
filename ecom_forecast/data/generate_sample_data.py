"""
样本数据生成器
生成用于开发和测试的模拟电商订单数据
"""

import pandas as pd
import numpy as np
from datetime import date, timedelta
from pathlib import Path


np.random.seed(42)


PLATFORMS = ["taobao", "douyin"]
SHOPS = {
    "taobao": ["SHOP_TB_01"],
    "douyin": ["SHOP_DY_01"],
}
CHANNELS = {
    "taobao": ["taobao_shelf", "taobao_live", "taobao_cps"],
    "douyin": ["douyin_shelf", "douyin_live", "douyin_anchor", "douyin_union"],
}
WAREHOUSES = ["KM01", "WH01", "HZ01"]

# 渠道基础日均单量
BASE_ORDERS = {
    "taobao_shelf": 800,
    "taobao_live": 300,
    "taobao_cps": 200,
    "douyin_shelf": 600,
    "douyin_live": 400,
    "douyin_anchor": 500,
    "douyin_union": 150,
}

# 大促日期（示例，覆盖历史和未来）
CAMPAIGN_DATES = {
    "618":   pd.date_range("2024-06-01", "2024-06-18"),
    "1111":  pd.date_range("2024-10-21", "2024-11-11"),
    "38_25": pd.date_range("2025-03-08", "2025-03-10"),
    "618_25": pd.date_range("2025-06-01", "2025-06-18"),
    "1111_25": pd.date_range("2025-10-21", "2025-11-11"),
}

# 仓库分配比例（规则变更后的稳定期）
WAREHOUSE_RATIOS = {
    # shop_id: {is_sample: {wh: ratio}}
    "SHOP_TB_01": {
        0: {"KM01": 0.50, "WH01": 0.30, "HZ01": 0.20},
        1: {"KM01": 0.60, "WH01": 0.25, "HZ01": 0.15},
    },
    "SHOP_DY_01": {
        0: {"KM01": 0.45, "WH01": 0.35, "HZ01": 0.20},
        1: {"KM01": 0.55, "WH01": 0.30, "HZ01": 0.15},
    },
}

# 派样比例
SAMPLE_RATIO = {"taobao_cps": 0.3, "douyin_union": 0.2}


def _campaign_multiplier(dt: pd.Timestamp) -> float:
    """大促期间单量倍数"""
    for dates in CAMPAIGN_DATES.values():
        if dt in dates:
            return np.random.uniform(2.5, 4.0)
    # 预售期（大促前10天）
    for dates in CAMPAIGN_DATES.values():
        if (dates[0] - timedelta(days=10)) <= dt < dates[0]:
            return np.random.uniform(1.2, 1.8)
    return 1.0


def generate_order_data(start_date: str = "2024-01-01", end_date: str = "2026-03-11") -> pd.DataFrame:
    """生成渠道粒度日单量数据"""
    date_range = pd.date_range(start_date, end_date)
    rows = []

    for platform, shops in SHOPS.items():
        for shop_id in shops:
            for channel in CHANNELS[platform]:
                base = BASE_ORDERS.get(channel, 200)

                for dt in date_range:
                    # 季节性
                    seasonal = 1 + 0.2 * np.sin(2 * np.pi * dt.dayofyear / 365)
                    # 周末效应
                    weekend_boost = 1.2 if dt.dayofweek >= 5 else 1.0
                    # 大促效应
                    campaign_mult = _campaign_multiplier(dt)
                    # 随机噪声
                    noise = np.random.lognormal(0, 0.15)

                    orders = max(0, int(base * seasonal * weekend_boost * campaign_mult * noise))
                    is_sample = int(np.random.random() < SAMPLE_RATIO.get(channel, 0.05))

                    rows.append({
                        "date": dt,
                        "platform": platform,
                        "shop_id": shop_id,
                        "channel": channel,
                        "order_count": orders,
                        "is_sample": is_sample,
                    })

    return pd.DataFrame(rows)


def generate_warehouse_order_data(order_df: pd.DataFrame) -> pd.DataFrame:
    """根据渠道单量生成仓库粒度数据"""
    rows = []

    for _, row in order_df.iterrows():
        shop_id = row["shop_id"]
        is_sample = row["is_sample"]
        total = row["order_count"]

        ratios = WAREHOUSE_RATIOS.get(shop_id, {}).get(is_sample, {
            "WH_A": 0.50, "WH_B": 0.30, "WH_C": 0.20
        })

        for wh_id, ratio in ratios.items():
            wh_orders = int(total * ratio * np.random.uniform(0.95, 1.05))
            rows.append({
                "date": row["date"],
                "platform": row["platform"],
                "shop_id": shop_id,
                "channel": row["channel"],
                "is_sample": is_sample,
                "warehouse_id": wh_id,
                "order_count": max(0, wh_orders),
            })

    return pd.DataFrame(rows)


def generate_live_schedule(order_df: pd.DataFrame) -> pd.DataFrame:
    """生成直播排期数据"""
    live_channels = ["taobao_live", "douyin_live", "douyin_anchor"]
    dates = order_df["date"].unique()
    rows = []

    for shop_id in order_df["shop_id"].unique():
        for dt in dates:
            # 每周直播 3~4 次
            has_live = int(np.random.random() < 0.5)
            tier = np.random.choice(["A", "B", "C"], p=[0.1, 0.4, 0.5]) if has_live else ""
            viewers = {
                "A": np.random.randint(80000, 200000),
                "B": np.random.randint(20000, 80000),
                "C": np.random.randint(5000, 20000),
                "":  0,
            }[tier]
            rows.append({
                "shop_id": shop_id,
                "date": dt,
                "has_live": has_live,
                "anchor_tier": tier,
                "expected_viewers": viewers,
            })

    return pd.DataFrame(rows)


def generate_commission_data(order_df: pd.DataFrame) -> pd.DataFrame:
    """生成联盟佣金月度数据"""
    months = order_df["date"].dt.to_period("M").unique()
    rows = []
    for shop_id in order_df["shop_id"].unique():
        for m in months:
            rows.append({
                "shop_id": shop_id,
                "month": str(m.to_timestamp().date()),
                "commission_rate": round(np.random.uniform(0.05, 0.20), 3),
                "active_affiliates": np.random.randint(10, 500),
            })
    return pd.DataFrame(rows)


def save_sample_data(output_dir: str = "data/sample") -> None:
    """生成并保存所有样本数据"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print("生成渠道订单数据...")
    order_df = generate_order_data()
    order_df.to_parquet(f"{output_dir}/channel_orders.parquet", index=False)
    print(f"  -> {len(order_df)} 条，保存至 {output_dir}/channel_orders.parquet")

    print("生成仓库订单数据...")
    wh_df = generate_warehouse_order_data(order_df)
    wh_df.to_parquet(f"{output_dir}/warehouse_orders.parquet", index=False)
    print(f"  -> {len(wh_df)} 条，保存至 {output_dir}/warehouse_orders.parquet")

    print("生成直播排期数据...")
    schedule_df = generate_live_schedule(order_df)
    schedule_df.to_parquet(f"{output_dir}/live_schedule.parquet", index=False)
    print(f"  -> {len(schedule_df)} 条")

    print("生成佣金数据...")
    commission_df = generate_commission_data(order_df)
    commission_df.to_parquet(f"{output_dir}/commission.parquet", index=False)
    print(f"  -> {len(commission_df)} 条")

    print("样本数据生成完成。")


if __name__ == "__main__":
    save_sample_data("D:/AI生成源码/预训练/ecom_forecast/data/sample")
