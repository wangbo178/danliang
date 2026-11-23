"""
验证偏差修正效果：
  - 用最近14天计算 correction_scale
  - 对比修正前/后的滚动预测走势
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys, os, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from sqlalchemy import create_engine

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

# ── 加载数据 ────────────────────────────────────────────────────────────────
raw = pd.read_sql("""
    SELECT date, shop_id, channel, platform, order_count
    FROM channel_orders
""", engine)
raw["date"] = pd.to_datetime(raw["date"])
channel_df = raw.groupby(["date","shop_id","channel","platform"], as_index=False)["order_count"].sum()
print(f"训练数据: {len(channel_df)} 条，日期 {channel_df['date'].min().date()} ~ {channel_df['date'].max().date()}")

# ── 加载已训练模型（不重新训练，直接用保存的模型）──────────────────────────
from src.pipeline.forecast_pipeline import ForecastPipeline
model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
pipeline = ForecastPipeline(model_dir=model_dir)
try:
    pipeline.load()
    print("模型加载成功")
except Exception as e:
    print(f"模型加载失败: {e}")
    sys.exit(1)

last_date = channel_df["date"].max()
future_dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=28)
sp_df = channel_df[["shop_id","channel","platform"]].drop_duplicates()
CHUNK_SIZE = 7

# ── Step1：计算偏差修正系数 ─────────────────────────────────────────────────
BIAS_WINDOW = 14
bias_cutoff = last_date - pd.Timedelta(days=BIAS_WINDOW)
recent_actual = channel_df[channel_df["date"] > bias_cutoff]
recent_hist   = channel_df[channel_df["date"] <= bias_cutoff]

print(f"\n计算修正系数（近{BIAS_WINDOW}天: {bias_cutoff.date()} ~ {last_date.date()}）...")
recent_inp = recent_actual[["date","shop_id","channel","platform"]].copy()
recent_inp["order_count"] = np.nan
recent_pred = pipeline.predict_channels(recent_inp, recent_hist)

act_ch  = recent_actual.groupby("channel")["order_count"].mean()
pred_ch = recent_pred.groupby("channel")["predicted_orders"].mean()
scale_ch = (act_ch / pred_ch).clip(0.3, 3.0).fillna(1.0)
print("\n渠道偏差修正系数 (actual/pred, 限幅0.3~3.0):")
for ch, sc in scale_ch.sort_values(ascending=False).items():
    a = act_ch.get(ch, float("nan"))
    p = pred_ch.get(ch, float("nan"))
    print(f"  {ch:<20} actual={a:.0f}  pred={p:.0f}  scale={sc:.2f}x")

# 构建 (shop_id, channel) → scale 映射
act_sp  = recent_actual.groupby(["shop_id","channel"])["order_count"].mean()
pred_sp = recent_pred.groupby(["shop_id","channel"])["predicted_orders"].mean()
correction_scale = (act_sp / pred_sp).clip(0.3, 3.0).fillna(1.0).to_dict()

# ── Step2：对比修正前/后的滚动预测 ─────────────────────────────────────────
def rolling_predict(apply_correction):
    rolling = channel_df.copy()
    chunks = []
    for i in range(0, len(future_dates), CHUNK_SIZE):
        chunk_dates = future_dates[i:i+CHUNK_SIZE]
        rows = [{"date": dt, "shop_id": r["shop_id"], "channel": r["channel"],
                 "platform": r["platform"], "order_count": np.nan}
                for dt in chunk_dates for _, r in sp_df.iterrows()]
        cf = pd.DataFrame(rows)
        pc = pipeline.predict_channels(cf, rolling)
        if apply_correction:
            pc["_sc"] = pc.apply(
                lambda r: correction_scale.get((r["shop_id"], r["channel"]), 1.0), axis=1)
            pc["predicted_orders"] = (pc["predicted_orders"] * pc["_sc"]).clip(0)
            pc = pc.drop(columns=["_sc"])
        chunks.append(pc)
        ph = cf.merge(pc[["date","shop_id","channel","predicted_orders"]],
                      on=["date","shop_id","channel"], how="left")
        ph["order_count"] = ph["predicted_orders"].fillna(0)
        ph = ph.drop(columns=["predicted_orders"])
        rolling = pd.concat([rolling, ph], ignore_index=True)
    return pd.concat(chunks, ignore_index=True)

pred_before = rolling_predict(False)
pred_after  = rolling_predict(True)

# ── 汇总对比 ────────────────────────────────────────────────────────────────
print("\n\n修正前/后每周总单量对比（全渠道汇总）:")
print(f"{'周次':<6} {'修正前':>10} {'修正后':>10} {'变化':>8}")
print("-"*38)
for wk in range(4):
    d0 = last_date + pd.Timedelta(days=1+wk*7)
    d1 = last_date + pd.Timedelta(days=7+wk*7)
    b = pred_before[(pred_before["date"]>=d0)&(pred_before["date"]<=d1)]["predicted_orders"].sum()
    a = pred_after [(pred_after ["date"]>=d0)&(pred_after ["date"]<=d1)]["predicted_orders"].sum()
    print(f"W{wk+1:<5} {b:>10.0f} {a:>10.0f} {(a/b-1)*100:>+7.1f}%")

print("\n修正后各渠道逐周对比:")
print(f"{'渠道':<20} {'W1':>8} {'W2':>8} {'W3':>8} {'W4':>8}  W2/W1  W3/W2")
print("-"*75)
for ch, g in pred_after.groupby("channel"):
    g = g.sort_values("date")
    weeks = []
    for wk in range(4):
        d0 = last_date + pd.Timedelta(days=1+wk*7)
        d1 = last_date + pd.Timedelta(days=7+wk*7)
        v = g[(g["date"]>=d0)&(g["date"]<=d1)]["predicted_orders"].mean()
        weeks.append(v)
    if pd.isna(weeks[0]) or weeks[0] < 1:
        continue
    r1 = weeks[1]/weeks[0] if weeks[0] else float("nan")
    r2 = weeks[2]/weeks[1] if weeks[1] else float("nan")
    print(f"{ch:<20} {weeks[0]:>8.0f} {weeks[1]:>8.0f} {weeks[2]:>8.0f} {weeks[3]:>8.0f}  {r1:.2f}   {r2:.2f}")

print("\n与近期真实数据对比:")
recent_ch_avg = recent_actual.groupby("channel")["order_count"].mean()
after_w1 = pred_after[
    (pred_after["date"] >= last_date+pd.Timedelta(days=1)) &
    (pred_after["date"] <= last_date+pd.Timedelta(days=7))
].groupby("channel")["predicted_orders"].mean()

print(f"{'渠道':<20} {'近期实际/日':>12} {'W1预测/日':>12} {'偏差':>8}")
print("-"*55)
for ch in sorted(set(recent_ch_avg.index) & set(after_w1.index)):
    a = recent_ch_avg[ch]
    p = after_w1[ch]
    bias = (p/a - 1)*100
    print(f"{ch:<20} {a:>12.0f} {p:>12.0f} {bias:>+7.1f}%")
