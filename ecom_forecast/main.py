"""
主入口：端到端训练 + 评估示例
用法：
    cd D:/AI生成源码/预训练/ecom_forecast
    python main.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from pathlib import Path

from data.generate_sample_data import save_sample_data
from src.pipeline.forecast_pipeline import ForecastPipeline
from evaluation.metrics import print_evaluation_report


DATA_DIR = "data/sample"
MODEL_DIR = "models"


def load_data():
    """加载样本数据，如不存在则自动生成"""
    if not Path(f"{DATA_DIR}/channel_orders.parquet").exists():
        print("样本数据不存在，正在生成...")
        save_sample_data(DATA_DIR)

    order_df = pd.read_parquet(f"{DATA_DIR}/channel_orders.parquet")
    wh_df = pd.read_parquet(f"{DATA_DIR}/warehouse_orders.parquet")
    schedule_df = pd.read_parquet(f"{DATA_DIR}/live_schedule.parquet")
    commission_df = pd.read_parquet(f"{DATA_DIR}/commission.parquet")

    order_df["date"] = pd.to_datetime(order_df["date"])
    wh_df["date"] = pd.to_datetime(wh_df["date"])
    schedule_df["date"] = pd.to_datetime(schedule_df["date"])

    return order_df, wh_df, schedule_df, commission_df


def main():
    print("=" * 60)
    print("电商单量预测模型 - 端到端训练流水线")
    print("=" * 60)

    # 1. 加载数据
    print("\n[1] 加载数据...")
    order_df, wh_df, schedule_df, commission_df = load_data()
    print(f"    渠道订单: {len(order_df)} 条，日期范围: {order_df['date'].min().date()} ~ {order_df['date'].max().date()}")
    print(f"    仓库订单: {len(wh_df)} 条")

    # 2. 初始化流水线
    pipeline = ForecastPipeline(model_dir=MODEL_DIR)

    # 3. 训练全部三层模型
    print("\n[2] 开始训练...")
    pipeline.train_all(
        order_df=order_df,
        warehouse_order_df=wh_df,
        schedule_df=schedule_df,
        commission_df=commission_df,
    )

    # 4. Holdout 评估
    print("\n[3] Holdout 评估（最近 30 天）...")
    eval_cutoff = order_df["date"].max() - pd.Timedelta(days=30)
    eval_pipeline = ForecastPipeline(model_dir="models_eval")
    eval_pipeline.train_channels(
        order_df[order_df["date"] <= eval_cutoff],
        schedule_df=schedule_df[schedule_df["date"] <= eval_cutoff],
        commission_df=commission_df,
    )
    eval_pipeline._schedule_df = schedule_df
    eval_pipeline._commission_df = commission_df
    eval_pipeline.train_routing(wh_df[wh_df["date"] <= eval_cutoff])
    channel_eval, warehouse_eval, _ = eval_pipeline.evaluate_on_holdout(
        order_df=order_df,
        warehouse_order_df=wh_df,
        holdout_days=30,
    )

    print("\n渠道级评估：")
    print_evaluation_report(channel_eval)

    print("\n仓库级评估：")
    print(warehouse_eval.to_string(index=False))

    # 5. 保存模型
    print("\n[4] 保存模型...")
    pipeline.save()

    # 6. 演示：预测未来 7 天
    print("\n[5] 演示：预测未来 7 天渠道单量...")
    last_date = order_df["date"].max()
    future_dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=7)

    shop_platform = order_df[["shop_id", "platform"]].drop_duplicates()
    channels = order_df[["shop_id", "channel", "platform"]].drop_duplicates()

    future_rows = []
    for dt in future_dates:
        for _, row in channels.iterrows():
            future_rows.append({
                "date": dt,
                "shop_id": row["shop_id"],
                "channel": row["channel"],
                "platform": row["platform"],
                "order_count": np.nan,
            })
    future_df = pd.DataFrame(future_rows)

    pred_df = pipeline.predict_channels(future_df, order_df, schedule_df, commission_df)
    summary = (
        pred_df
        .groupby(["date", "shop_id"])["predicted_orders"]
        .sum()
        .reset_index()
        .rename(columns={"predicted_orders": "predicted_shop_total"})
    )
    summary["predicted_shop_total"] = summary["predicted_shop_total"].round(0).astype(int)
    print(summary.to_string(index=False))

    print("\n预测仓库分配...")
    wh_pred = pipeline.predict_warehouse(pred_df)
    wh_summary = (
        wh_pred
        .groupby(["date", "warehouse_id"])["warehouse_orders"]
        .sum()
        .reset_index()
    )
    wh_summary["warehouse_orders"] = wh_summary["warehouse_orders"].round(0).astype(int)
    print(wh_summary.to_string(index=False))

    print("\n完成。")


if __name__ == "__main__":
    main()
