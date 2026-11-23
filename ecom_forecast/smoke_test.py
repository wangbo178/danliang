"""
快速冒烟测试：验证各模块可正常运行
不依赖真实数据，全程使用生成的样本数据
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np


def test_campaign_features():
    from src.features.campaign_features import build_campaign_features
    dates = pd.date_range("2024-10-20", periods=30)
    df = pd.DataFrame({"date": dates, "value": np.random.randint(100, 500, 30)})
    out = build_campaign_features(df)
    assert "is_campaign_day" in out.columns
    assert "campaign_intensity" in out.columns
    # 双十一期间应有大促标记
    campaign_days = out[out["is_campaign_day"] == 1]
    assert len(campaign_days) > 0, "应检测到双十一大促日"
    print("  [OK] campaign_features")


def test_time_features():
    from src.features.time_features import build_all_time_features
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=60),
        "shop_id": "SHOP_A",
        "order_count": np.random.randint(100, 500, 60),
    })
    out = build_all_time_features(df, "order_count", ["shop_id"])
    assert "day_of_week" in out.columns
    assert "order_count_lag7" in out.columns
    assert "order_count_roll_mean7" in out.columns
    print("  [OK] time_features")


def test_warehouse_router():
    from src.routing.warehouse_router import WarehouseRouter

    # 生成模拟仓库订单数据（只有规则变更后的数据）
    dates = pd.date_range("2024-09-01", "2025-03-01")
    rows = []
    for dt in dates:
        for wh, ratio in [("KM01", 0.5), ("WH01", 0.3), ("HZ01", 0.2)]:
            rows.append({
                "date": dt,
                "shop_id": "SHOP_TB_01",
                "is_sample": 0,
                "warehouse_id": wh,
                "order_count": int(1000 * ratio),
            })

    wh_df = pd.DataFrame(rows)
    router = WarehouseRouter(rule_change_date="2024-08-01")
    router.fit(wh_df)

    assert len(router.routing_ratios) > 0, "应学到路由比例"
    ratios = router.routing_ratios.get("SHOP_TB_01", {}).get(0, {})
    assert abs(sum(ratios.values()) - 1.0) < 0.01, "比例之和应为1"
    print(f"  [OK] warehouse_router, 学到比例: {ratios}")


def test_full_pipeline_smoke():
    """端到端冒烟测试（小数据集，仅验证不报错）"""
    from data.generate_sample_data import (
        generate_order_data,
        generate_warehouse_order_data,
        generate_live_schedule,
        generate_commission_data,
    )
    from src.pipeline.forecast_pipeline import ForecastPipeline

    print("  生成小规模样本数据...")
    order_df = generate_order_data("2024-06-01", "2026-03-11")
    wh_df = generate_warehouse_order_data(order_df)
    schedule_df = generate_live_schedule(order_df)
    commission_df = generate_commission_data(order_df)

    pipeline = ForecastPipeline(model_dir="models_test")

    print("  训练全局模型...")
    pipeline.train_global(order_df)

    print("  训练渠道模型...")
    pipeline.train_channels(order_df, schedule_df=schedule_df, commission_df=commission_df)

    print("  训练路由模型...")
    pipeline.train_routing(wh_df)

    print("  评估...")
    channel_eval, wh_eval, _ = pipeline.evaluate_on_holdout(order_df, wh_df, holdout_days=14)
    print(f"  渠道 MAPE: {channel_eval[channel_eval['segment']=='全局']['mape'].values[0]}%")

    print("  [OK] full_pipeline_smoke")


if __name__ == "__main__":
    print("运行冒烟测试...")
    print()
    test_campaign_features()
    test_time_features()
    test_warehouse_router()
    test_full_pipeline_smoke()
    print()
    print("所有测试通过。")
