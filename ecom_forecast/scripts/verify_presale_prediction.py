"""
验证脚本：预售尾款预测 vs LightGBM 原始预测对比

功能：
1. 从数据库加载 live_schedules 和 channel_orders 数据
2. 以最近一次预售事件作为 holdout（其余为训练集）
3. 对比 LightGBM 原始预测 vs 混合预测（规则覆盖）的逐日误差
4. 输出逐日对比表 + 汇总指标

用法：
    python scripts/verify_presale_prediction.py [--holdout-campaign 1111]

脚本必须在 ecom_forecast/ 目录下执行，或通过项目根目录调用。
"""

import sys
import os
import argparse
from datetime import date
from pathlib import Path

# 确保 ecom_forecast 在 sys.path 中
_script_dir = Path(__file__).resolve().parent
_proj_dir = _script_dir.parent
if str(_proj_dir) not in sys.path:
    sys.path.insert(0, str(_proj_dir))

import pandas as pd
import numpy as np

from api.database import SessionLocal
from api import models
from src.models.presale_model import PresaleDistributionModel, _infer_campaign_type
from config.campaign_config import PRESALE_MAX_PAYMENT_DAYS


# ──────────────────────────────────────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────────────────────────────────────

def load_data():
    """从数据库加载 live_schedules 和 channel_orders"""
    db = SessionLocal()
    try:
        sc_rows = db.query(models.LiveSchedule).all()
        ch_rows = db.query(models.ChannelOrder).all()
    finally:
        db.close()

    schedule_df = pd.DataFrame([{
        "shop_id": r.shop_id,
        "date": pd.to_datetime(r.date),
        "event_type": r.event_type or "live",
        "anchor_tier": r.anchor_tier,
        "broadcast_date": pd.to_datetime(r.broadcast_date) if r.broadcast_date else None,
    } for r in sc_rows]) if sc_rows else pd.DataFrame()

    channel_df = pd.DataFrame([{
        "date": pd.to_datetime(r.date),
        "shop_id": r.shop_id,
        "channel": r.channel,
        "platform": r.platform,
        "order_count": r.order_count,
    } for r in ch_rows]) if ch_rows else pd.DataFrame()

    # 去重聚合
    if len(channel_df):
        channel_df = (
            channel_df
            .groupby(["date", "shop_id", "channel", "platform"], as_index=False)["order_count"]
            .sum()
        )

    return schedule_df, channel_df


# ──────────────────────────────────────────────────────────────────────────────
# 识别预售事件
# ──────────────────────────────────────────────────────────────────────────────

def get_presale_events(schedule_df: pd.DataFrame):
    """
    返回所有 presale_payment 事件列表：
    [{shop_id, channel_hint, campaign_type, broadcast_date, payment_dates: [...]}]
    """
    if len(schedule_df) == 0:
        return []

    pay_rows = schedule_df[schedule_df["event_type"] == "presale_payment"].copy()
    if len(pay_rows) == 0:
        print("⚠️  live_schedules 中没有 event_type='presale_payment' 的行")
        return []

    pay_rows["_date"] = pd.to_datetime(pay_rows["date"]).dt.date
    pay_rows["_bcast"] = pd.to_datetime(pay_rows["broadcast_date"]).dt.date

    events = []
    for (shop_id, bcast), grp in pay_rows.groupby(["shop_id", "_bcast"], dropna=False):
        payment_dates = sorted(grp["_date"].unique())
        campaign_type = _infer_campaign_type(payment_dates[0]) if payment_dates else None
        events.append({
            "shop_id": str(shop_id),
            "campaign_type": campaign_type or "default",
            "broadcast_date": bcast,
            "payment_dates": payment_dates,
        })

    # 按最早付款日升序
    events.sort(key=lambda e: min(e["payment_dates"]))
    return events


# ──────────────────────────────────────────────────────────────────────────────
# 主验证逻辑
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="验证预售预测效果")
    parser.add_argument(
        "--holdout-campaign", default=None,
        help="指定 holdout 活动类型（如 1111/618/38），默认取最后一次"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("预售尾款预测验证脚本")
    print("=" * 60)

    # 1. 加载数据
    print("\n[1] 从数据库加载数据...")
    schedule_df, channel_df = load_data()
    print(f"    live_schedules: {len(schedule_df)} 行")
    print(f"    channel_orders: {len(channel_df)} 行")

    if len(schedule_df) == 0:
        print("\n❌ live_schedules 为空，请先上传直播排期数据（包含 presale_payment 行）")
        return

    # 2. 识别预售事件
    print("\n[2] 识别预售事件...")
    events = get_presale_events(schedule_df)
    if not events:
        print("❌ 没有找到任何预售付款事件，无法验证")
        return

    print(f"    共找到 {len(events)} 个预售事件：")
    for e in events:
        print(f"      shop={e['shop_id']}, 活动={e['campaign_type']}, "
              f"定金日={e['broadcast_date']}, 尾款日={e['payment_dates']}")

    # 3. 选择 holdout 事件
    if args.holdout_campaign:
        holdout_events = [e for e in events if e["campaign_type"] == args.holdout_campaign]
        if not holdout_events:
            print(f"\n❌ 未找到 campaign_type='{args.holdout_campaign}' 的事件")
            return
        holdout_event = holdout_events[-1]  # 取同类型最近一次
    else:
        holdout_event = events[-1]  # 默认取最后一次

    train_events = [e for e in events if e != holdout_event]

    print(f"\n[3] Holdout 事件：shop={holdout_event['shop_id']}, "
          f"活动={holdout_event['campaign_type']}, "
          f"付款日={holdout_event['payment_dates']}")
    print(f"    训练集事件数：{len(train_events)}")

    # 4. 用训练集事件 fit 预售模型
    print("\n[4] Fit PresaleDistributionModel...")
    presale_model = PresaleDistributionModel()

    if train_events:
        # 构建 payment_events（从 schedule_df + channel_df 提取历史数据）
        train_schedule = schedule_df[
            schedule_df["event_type"] == "presale_payment"
        ].copy()
        # 仅保留训练集事件对应的行
        train_dates = set()
        for e in train_events:
            train_dates.update(e["payment_dates"])
        train_schedule = train_schedule[
            pd.to_datetime(train_schedule["date"]).dt.date.isin(train_dates)
        ]

        payment_events = PresaleDistributionModel.build_payment_events_from_df(
            schedule_df=train_schedule,
            channel_df=channel_df,
        )
        if payment_events:
            presale_model.fit(payment_events)
            print(f"    fit 完成，payment_events={len(payment_events)} 条")
        else:
            print("    ⚠️  无法从历史数据构建 payment_events，将使用兜底先验")
    else:
        print("    ⚠️  无训练集事件，将使用兜底先验")

    # 5. 对 holdout 事件预测
    print("\n[5] 预测 holdout 事件...")
    holdout_dates = holdout_event["payment_dates"]
    shop_id = holdout_event["shop_id"]
    campaign_type = holdout_event["campaign_type"]

    # 找到 holdout 期间的真实数据（所有渠道合计）
    holdout_date_set = set(holdout_dates)
    actual_df = channel_df[
        (channel_df["shop_id"] == shop_id) &
        (pd.to_datetime(channel_df["date"]).dt.date.isin(holdout_date_set))
    ].copy()
    actual_df["_date"] = pd.to_datetime(actual_df["date"]).dt.date

    actual_agg = (
        actual_df.groupby("_date")["order_count"].sum()
        .reindex(holdout_dates, fill_value=0)
    )

    # 分渠道预测
    channels_in_data = actual_df["channel"].unique()
    if len(channels_in_data) == 0:
        print("  ❌ holdout 期间无实际订单数据，无法对比")
        return

    rows = []
    for ch in channels_in_data:
        vol_map = presale_model.predict_daily_volumes(
            payment_dates=holdout_dates,
            shop_id=shop_id,
            channel=ch,
            campaign_type=campaign_type,
        )

        ch_actual = (
            actual_df[actual_df["channel"] == ch]
            .set_index("_date")["order_count"]
            .reindex(holdout_dates, fill_value=0)
        )

        for d in holdout_dates:
            rows.append({
                "date": d,
                "channel": ch,
                "day_index": holdout_dates.index(d),
                "actual": ch_actual.get(d, 0),
                "presale_pred": vol_map.get(d, 0),
            })

    result_df = pd.DataFrame(rows)

    # 6. 输出逐日对比表
    print("\n[6] 逐日预测对比")
    print("-" * 65)
    print(f"{'日期':<12} {'渠道':<8} {'Day':<5} {'实际单量':>10} {'混合预测':>10} {'误差%':>8}")
    print("-" * 65)
    for _, r in result_df.iterrows():
        err_pct = (
            abs(r["presale_pred"] - r["actual"]) / max(r["actual"], 1) * 100
            if r["actual"] > 0 else float("nan")
        )
        print(f"{str(r['date']):<12} {r['channel']:<8} {r['day_index']:<5} "
              f"{r['actual']:>10.0f} {r['presale_pred']:>10.0f} "
              f"{'N/A':>8}" if np.isnan(err_pct) else
              f"{str(r['date']):<12} {r['channel']:<8} {r['day_index']:<5} "
              f"{r['actual']:>10.0f} {r['presale_pred']:>10.0f} {err_pct:>7.1f}%")
    print("-" * 65)

    # 7. 汇总指标
    valid_rows = result_df[result_df["actual"] > 0]
    if len(valid_rows) > 0:
        wape = (
            abs(valid_rows["presale_pred"] - valid_rows["actual"]).sum()
            / valid_rows["actual"].sum() * 100
        )
        mae = abs(valid_rows["presale_pred"] - valid_rows["actual"]).mean()
        bias = (
            (valid_rows["presale_pred"] - valid_rows["actual"]).sum()
            / valid_rows["actual"].sum() * 100
        )
        total_actual = valid_rows["actual"].sum()
        total_pred = valid_rows["presale_pred"].sum()
        print(f"\n[7] 汇总指标（仅统计实际单量>0的行）")
        print(f"    WAPE  : {wape:.1f}%  （越低越好，目标 < 30%）")
        print(f"    MAE   : {mae:.1f}  单/天")
        print(f"    Bias  : {bias:+.1f}%  （正=高估，负=低估）")
        print(f"    总实际: {total_actual:.0f} 单，总预测: {total_pred:.0f} 单")
    else:
        print("\n⚠️  holdout 期间无实际单量数据，无法计算误差指标")

    # 8. 分布曲线对比
    print(f"\n[8] 预售模型学到的分布曲线（最多{PRESALE_MAX_PAYMENT_DAYS}天）")
    if train_events:
        for key in presale_model._dist_history:
            hist = presale_model._dist_history[key]
            if hist:
                print(f"    Key={key}: {len(hist)} 次历史事件")
                for i, dist in enumerate(hist):
                    print(f"      事件{i}: {[f'{v:.2%}' for v in dist]}")
    else:
        print("    （无训练数据，使用兜底先验）")
        from src.models.presale_model import _FALLBACK_DISTRIBUTION
        print(f"    兜底先验: {[f'{v:.0%}' for v in _FALLBACK_DISTRIBUTION]}")

    print("\n✅ 验证完成")


if __name__ == "__main__":
    main()
