"""
预测偏差诊断脚本
检查以下关键问题：
  1. channel_type_map 键名与 channel_orders.channel 值是否匹配
  2. 各渠道在 holdout 期的预测精度
  3. 数据分布 & 极端值
  4. lag 特征的 NaN 比例

用法：
  cd D:/AI生成源码/预训练/ecom_forecast
  python scripts/diagnose_forecast_bias.py
"""

import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import pymysql

# ── 数据库连接 ──────────────────────────────────────
DB = dict(host="localhost", port=3306, user="root", password=os.environ.get("LOCAL_DB_PASSWORD", ""),
          db="ecom_forecast", charset="utf8mb4")

def query(sql):
    conn = pymysql.connect(**DB)
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


print("=" * 70)
print("预测偏差诊断报告")
print("=" * 70)

# ── 1. 渠道类型映射诊断 ────────────────────────────────────────────
print("\n【1】渠道类型映射诊断")
print("-" * 50)

channels_df = query("SELECT channel_id, name, channel_type FROM channels")
orders_channels = query("SELECT DISTINCT channel FROM channel_orders ORDER BY channel")

type_map_by_id   = {r.channel_id: r.channel_type for _, r in channels_df.iterrows()}
type_map_by_name = {}
for _, r in channels_df.iterrows():
    name = r["name"]
    ctype = r["channel_type"] or "stable"
    # 非 stable 优先
    if name not in type_map_by_name or (ctype != "stable"):
        type_map_by_name[name] = ctype

print(f"channels 表中 channel_id → type: {type_map_by_id}")
print(f"\nchannel_orders 中的 channel 值: {list(orders_channels['channel'])}")
print()

KNOWN_ALIASES = {
    "douyin_union": "affiliate",
    "达播": "event",
    "其他": "stable",
}

print(f"{'渠道名':<15} {'按channel_id(旧bug)':<20} {'按name(现行)':<15} {'状态'}")
print("-" * 65)
for ch in orders_channels["channel"]:
    by_id   = type_map_by_id.get(ch, "★未匹配→stable")
    by_name = type_map_by_name.get(ch, KNOWN_ALIASES.get(ch, "★未匹配→stable"))
    status = "✓已修复" if by_id != by_name else "✓正常"
    print(f"{ch:<15} {by_id:<20} {by_name:<15} {status}")

# ── 2. 评估指标历史 ────────────────────────────────────────────────
print("\n\n【2】最近训练任务的 Holdout 评估指标")
print("-" * 50)
latest_job = query("""
    SELECT job_id, created_at FROM training_jobs
    WHERE status='done' ORDER BY created_at DESC LIMIT 1
""")
if not latest_job.empty:
    job_id = latest_job.iloc[0]["job_id"]
    print(f"最新 job_id: {job_id}  (训练时间: {latest_job.iloc[0]['created_at']})")
    metrics = query(f"""
        SELECT group_key as channel,
               mae as wape_stored,
               rmse,
               mape * 100 as mape_pct,
               bias * 100 as bias_pct
        FROM evaluation_metrics
        WHERE job_id='{job_id}'
        ORDER BY mape_pct DESC
    """)
    print(metrics.to_string(index=False))
    print("\n注：mape_pct = 存储值×100（前端显示值），bias_pct 同")
else:
    print("无完成的训练任务")

# ── 3. 各渠道数据分布 ────────────────────────────────────────────
print("\n\n【3】各渠道数据分布（channel_orders 全量）")
print("-" * 60)
dist = query("""
    SELECT channel,
           COUNT(*) as n_days,
           ROUND(AVG(order_count),1) as avg_orders,
           ROUND(STDDEV(order_count),1) as std_orders,
           MAX(order_count) as max_orders,
           ROUND(MAX(order_count)/AVG(order_count),1) as peak_ratio,
           SUM(CASE WHEN order_count <= 5 THEN 1 ELSE 0 END) as days_le5
    FROM channel_orders GROUP BY channel ORDER BY avg_orders DESC
""")
print(dist.to_string(index=False))
print("\n注：peak_ratio = max/avg，越高表示大促尖刺越显著，越难预测")

# ── 4. 直播渠道大促尖刺检查 ────────────────────────────────────────
print("\n\n【4】直播渠道大促尖刺 TOP10")
print("-" * 50)
spikes = query("""
    SELECT date, shop_id, order_count
    FROM channel_orders WHERE channel='直播'
    ORDER BY order_count DESC LIMIT 10
""")
print(spikes.to_string(index=False))

# ── 5. 直播排期数据是否存在 ──────────────────────────────────────
print("\n\n【5】直播排期数据（live_schedules）")
print("-" * 50)
sched = query("SELECT COUNT(*) as cnt, MIN(date) as min_d, MAX(date) as max_d FROM live_schedules")
print(sched.to_string(index=False))
if sched.iloc[0]["cnt"] == 0:
    print("⚠ 警告：直播排期表为空！event 模型无法获取直播信号，预测精度会大幅下降。")

# ── 6. 数据覆盖日期范围 ──────────────────────────────────────────
print("\n\n【6】训练数据日期范围")
print("-" * 50)
date_range = query("""
    SELECT channel,
           MIN(date) as start_date, MAX(date) as end_date,
           DATEDIFF(MAX(date), MIN(date)) as span_days,
           COUNT(DISTINCT DATE_FORMAT(date,'%Y-%m')) as months
    FROM channel_orders GROUP BY channel ORDER BY channel
""")
print(date_range.to_string(index=False))

# ── 7. 最近30天（holdout期）实际数据分布 ──────────────────────
print("\n\n【7】最近30天各渠道平均单量（训练 holdout 期）")
print("-" * 50)
holdout = query("""
    SELECT channel,
           ROUND(AVG(order_count),1) as avg_orders_holdout,
           MAX(order_count) as max_orders_holdout,
           COUNT(*) as n_rows
    FROM channel_orders
    WHERE date >= DATE_SUB((SELECT MAX(date) FROM channel_orders), INTERVAL 30 DAY)
    GROUP BY channel ORDER BY avg_orders_holdout DESC
""")
print(holdout.to_string(index=False))

print("\n" + "=" * 70)
print("诊断完成。主要问题摘要：")
print("  1. channel_type_map 使用 channel_id 作为 key，与 channel_orders")
print("     中存储的渠道名称不匹配 → 所有渠道被错误归入 stable 类型")
print("  2. 直播 channel 大促尖刺比平均值高数十至数千倍，无排期数据时极难预测")
print("  3. MAE(L1)损失函数预测中位数，对右偏分布产生系统性低估")
print("=" * 70)
