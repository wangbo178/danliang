"""
从渠道订单数据反推历史直播日：
  - 对 event 类渠道（tm_zb / dy_zb / ks_shelf 等）
  - 单日订单量 > 该店铺该渠道 rolling_median × threshold 倍，标记为疑似直播日
  - 输出 Excel 供人工确认后上传（直接可用作 live_schedules 模板）
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

# ── 1. 加载 event 渠道订单 ────────────────────────────────────────────────
with engine.connect() as conn:
    ch_rows = conn.execute(text("SELECT channel_id, channel_type FROM channels")).fetchall()
event_channels = {r.channel_id for r in ch_rows if r.channel_type == "event"}
print(f"event 渠道: {sorted(event_channels)}")

raw = pd.read_sql(
    "SELECT date, shop_id, channel, SUM(order_count) AS order_count "
    "FROM channel_orders GROUP BY date, shop_id, channel",
    engine,
)
raw["date"] = pd.to_datetime(raw["date"])
event_df = raw[raw["channel"].isin(event_channels)].copy()
print(f"event 渠道数据: {len(event_df)} 条, 店铺数: {event_df['shop_id'].nunique()}")

# ── 2. 计算每个 (shop_id, channel) 的滚动中位数基线 ─────────────────────
WINDOW   = 28   # 过去28天滚动中位数
THRESHOLD = 2.5  # 超过基线 2.5 倍 → 疑似直播日

event_df = event_df.sort_values(["shop_id", "channel", "date"])

def label_live_days(grp):
    grp = grp.copy().set_index("date").sort_index()
    # 滚动中位数（不含当天，避免自身影响）
    baseline = grp["order_count"].shift(1).rolling(WINDOW, min_periods=7).median()
    # 绝对量阈值：至少 20 单才算有意义的直播信号
    grp["baseline"] = baseline
    grp["ratio"] = grp["order_count"] / baseline.clip(lower=1)
    grp["is_likely_live"] = ((grp["ratio"] >= THRESHOLD) & (grp["order_count"] >= 20)).astype(int)
    return grp.reset_index()

result = event_df.groupby(["shop_id", "channel"], group_keys=False).apply(label_live_days)

# ── 3. 汇总统计 ──────────────────────────────────────────────────────────
print(f"\n疑似直播日统计（ratio ≥ {THRESHOLD}x, 单量 ≥ 20）:")
summary = (
    result[result["is_likely_live"] == 1]
    .groupby(["shop_id", "channel"])
    .agg(live_days=("date", "count"), max_ratio=("ratio", "max"),
         avg_peak=("order_count", "mean"), total_days=("date", "nunique"))
)
# 算总天数
total_days_map = result.groupby(["shop_id", "channel"])["date"].nunique()
summary["total_days"] = total_days_map
summary["live_rate"] = (summary["live_days"] / summary["total_days"] * 100).round(1)
print(summary.sort_values("live_days", ascending=False).to_string())

# ── 4. 生成排期模板 Excel ────────────────────────────────────────────────
# Sheet1: 所有 event 渠道日期，has_live 预填（供人工复核）
output_rows = []
for (shop, ch), grp in result.groupby(["shop_id", "channel"]):
    for _, row in grp.iterrows():
        output_rows.append({
            "shop_id": shop,
            "channel": ch,
            "date": row["date"].strftime("%Y-%m-%d"),
            "order_count": int(row["order_count"]),
            "baseline_median": round(row["baseline"], 1) if pd.notna(row["baseline"]) else "",
            "ratio_vs_baseline": round(row["ratio"], 2) if pd.notna(row["ratio"]) else "",
            "has_live": int(row["is_likely_live"]),   # 待人工确认
            "anchor_tier": "",                         # 待填写：A/B/C
            "expected_viewers": "",                    # 待填写
        })

df_out = pd.DataFrame(output_rows)

# Sheet2: 只显示疑似直播日（方便核对）
df_live = df_out[df_out["has_live"] == 1].copy()

# Sheet3: 字段说明
df_desc = pd.DataFrame([
    {"字段名": "shop_id",           "说明": "店铺ID，与系统配置一致"},
    {"字段名": "channel",           "说明": "渠道标识（tm_zb/dy_zb/ks_shelf）"},
    {"字段名": "date",              "说明": "日期"},
    {"字段名": "order_count",       "说明": "实际订单量（参考用，无需上传）"},
    {"字段名": "baseline_median",   "说明": "过去28天滚动中位数（参考用）"},
    {"字段名": "ratio_vs_baseline", "说明": "当天/基线倍数，≥2.5x 系统标记为疑似直播"},
    {"字段名": "has_live",          "说明": "是否有直播：1=有，0=无。请人工核实后修改"},
    {"字段名": "anchor_tier",       "说明": "主播档级：A=头部百万+，B=腰部10万+，C=尾部；不知道可留空"},
    {"字段名": "expected_viewers",  "说明": "预计观看人数；不知道可留空"},
])

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_schedule_draft.xlsx")
with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
    df_out.to_excel(writer, sheet_name="全部日期（可上传）", index=False)
    df_live.to_excel(writer, sheet_name="疑似直播日（待核实）", index=False)
    df_desc.to_excel(writer, sheet_name="字段说明", index=False)

    # 调整列宽
    for sheet_name in writer.sheets:
        ws = writer.sheets[sheet_name]
        for col in ws.columns:
            max_len = max((len(str(c.value)) if c.value else 0) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

print(f"\n已生成: {out_path}")
print(f"  全部日期: {len(df_out)} 行")
print(f"  疑似直播日: {len(df_live)} 行")
print("\n请打开文件，在「疑似直播日」Sheet 核实 has_live 是否正确，")
print("填写 anchor_tier（A/B/C）和 expected_viewers，")
print("然后将「全部日期（可上传）」Sheet 的数据上传到系统。")
print("上传时只需保留 shop_id / date / has_live / anchor_tier / expected_viewers 五列。")
