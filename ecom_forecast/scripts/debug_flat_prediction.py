"""
调试预测直线问题：
检查 lag 特征是否正确计算，以及是否存在重复日期等数据问题
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

# ── 1. 检查重复日期 ──────────────────────────────────────────────
print("【1】各(shop_id, channel)组合的重复日期检查")
print("-" * 60)
dup = pd.read_sql("""
    SELECT shop_id, channel,
           COUNT(*) as total_rows,
           COUNT(DISTINCT date) as unique_dates,
           COUNT(*) - COUNT(DISTINCT date) as dup_count
    FROM channel_orders
    GROUP BY shop_id, channel
    HAVING dup_count > 0
    ORDER BY dup_count DESC
    LIMIT 15
""", engine)
if dup.empty:
    print("无重复日期 ✓")
else:
    print(dup.to_string(index=False))
    print(f"\n⚠ 共 {len(dup)} 个(shop,channel)存在重复日期！重复日期会导致 shift(N) 按行数而非天数移位，lag 特征全部错误。")

# ── 2. 检查 stable 模型接收的特征是否实际有效 ──────────────────────
print("\n\n【2】模拟 lag 特征计算（薇诺娜天猫旗舰店 / 货架）")
print("-" * 60)

df = pd.read_sql("""
    SELECT date, shop_id, channel, order_count
    FROM channel_orders
    WHERE shop_id='薇诺娜天猫旗舰店' AND channel='货架'
    ORDER BY date
""", engine)
df["date"] = pd.to_datetime(df["date"])

print(f"总行数: {len(df)}, 唯一日期数: {df['date'].nunique()}")
print(f"重复日期数: {len(df) - df['date'].nunique()}")
print()

# 模拟不去重时的 lag7
df_nodup = df.copy().sort_values("date").reset_index(drop=True)
df_nodup["lag7_raw"] = df_nodup["order_count"].shift(7)

# 去重后（每天取sum）的 lag7
df_agg = df.groupby("date")["order_count"].sum().reset_index()
df_agg = df_agg.sort_values("date").reset_index(drop=True)
df_agg["lag7_correct"] = df_agg["order_count"].shift(7)

print("不去重时的 lag7（预测 Apr 1 时，取第 N-7 行，不是7天前）：")
print(df_nodup.tail(10)[["date","order_count","lag7_raw"]].to_string(index=False))
print("\n去重聚合后的 lag7（正确，7天前日期的值）：")
print(df_agg.tail(10)[["date","order_count","lag7_correct"]].to_string(index=False))

# ── 3. 各渠道 avg 预测 vs 历史均值 ──────────────────────────────────
print("\n\n【3】各渠道历史均值 vs 预测均值对比")
print("-" * 60)
hist_avg = pd.read_sql("""
    SELECT channel,
           ROUND(AVG(order_count),1) as hist_avg_all,
           ROUND(AVG(CASE WHEN date >= '2026-01-01' THEN order_count END),1) as hist_avg_2026
    FROM channel_orders GROUP BY channel
""", engine)

latest_job = pd.read_sql("""
    SELECT job_id FROM training_jobs WHERE status='done' ORDER BY created_at DESC LIMIT 1
""", engine)
jid = latest_job.iloc[0]["job_id"]
pred_avg = pd.read_sql(f"""
    SELECT channel, ROUND(AVG(predicted_orders),1) as pred_avg
    FROM predictions WHERE job_id='{jid}'
    GROUP BY channel
""", engine)

cmp = hist_avg.merge(pred_avg, on="channel", how="outer")
print(cmp.to_string(index=False))
print("\n注：若 pred_avg ≈ hist_avg_all（全量历史均值）而与近期均值差异很大，说明模型在预测历史均值（常量问题）")

# ── 4. 检查 channel_type_map 在最新 job 的 config_snapshot ──────────
print("\n\n【4】最新训练 job 使用的 config_snapshot 中的 channel_type_map")
print("-" * 60)
snap = pd.read_sql(f"""
    SELECT config_snapshot FROM training_jobs WHERE job_id='{jid}'
""", engine)
import json
cfg = json.loads(snap.iloc[0]["config_snapshot"])
ctm = cfg.get("channel_type_map", {})
print("channel_type_map:")
for k, v in ctm.items():
    print(f"  {k}: {v}")

print("\n检查 channel_orders 中的渠道是否在 map 中：")
for ch in ["CPS","douyin_union","KOC","其他","直播","货架"]:
    t = ctm.get(ch, "★未匹配→stable（BUG）")
    print(f"  {ch} → {t}")
