"""
验证 channel_type_map 双键映射是否正确：
  - 英文 channel_id（新批次格式）
  - 中文 name（旧批次格式）
  - 打印 channel_orders 中实际使用的渠道名，以及它们被映射到的类型
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

# ── 从 channels 表构建 channel_type_map（模拟 config.py 逻辑）──────────────
with engine.connect() as conn:
    rows = conn.execute(text("SELECT channel_id, name, channel_type FROM channels")).fetchall()

channel_type_map: dict = {}

def _set_ch_type(key, ctype):
    if not key:
        return
    if ctype != "stable" or key not in channel_type_map:
        channel_type_map[key] = ctype

for r in rows:
    ctype = r.channel_type or "stable"
    _set_ch_type(r.channel_id, ctype)
    _set_ch_type(r.name, ctype)

_ALIAS_TYPE_FALLBACK = {
    "douyin_union": "affiliate",
    "达播":         "event",
    "其他":         "stable",
}
for alias, ctype in _ALIAS_TYPE_FALLBACK.items():
    channel_type_map.setdefault(alias, ctype)

print("=== channel_type_map（全部 key→type）===")
for k, v in sorted(channel_type_map.items()):
    print(f"  {k:<30} → {v}")

# ── 查看 channel_orders 中实际使用的渠道标识 ────────────────────────────────
with engine.connect() as conn:
    actual = conn.execute(text("""
        SELECT channel, COUNT(*) AS cnt
        FROM channel_orders
        GROUP BY channel
        ORDER BY cnt DESC
    """)).fetchall()

print("\n=== channel_orders 中的渠道 & 映射结果 ===")
print(f"{'渠道':<30} {'记录数':>8}  {'映射类型'}")
print("-" * 55)
event_cnt = stable_cnt = affiliate_cnt = 0
for r in actual:
    ch = r.channel
    mapped = channel_type_map.get(ch, "stable(默认)")
    print(f"  {ch:<28} {r.cnt:>8}  {mapped}")
    if "event" in mapped:
        event_cnt += r.cnt
    elif "affiliate" in mapped:
        affiliate_cnt += r.cnt
    else:
        stable_cnt += r.cnt

total = event_cnt + stable_cnt + affiliate_cnt
print(f"\n汇总: stable={stable_cnt}({stable_cnt/total*100:.1f}%)  "
      f"event={event_cnt}({event_cnt/total*100:.1f}%)  "
      f"affiliate={affiliate_cnt}({affiliate_cnt/total*100:.1f}%)")

# ── 检查 channels 表原始设置 ─────────────────────────────────────────────────
print("\n=== channels 表（channel_id | name | channel_type）===")
for r in rows:
    print(f"  {r.channel_id:<20} {(r.name or ''):<20} {r.channel_type or 'stable'}")
