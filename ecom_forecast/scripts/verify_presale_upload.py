"""
验证脚本：预售历史数据上传 & 模型学习效果
========================================
运行方式（在 ecom_forecast/ 目录下）：
    python scripts/verify_presale_upload.py

功能：
1. 检查 presale_history 表是否有数据
2. 验证活动类型推断是否正确
3. 验证 PresaleDistributionModel.fit_from_db() 是否正常工作
4. 输出逐日预售预测 vs 实际单量对比（如果 channel_orders 有对应数据）
5. 验证 channel_orders 中预售单量是否已被剥离（加法模型）
"""

import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import sys
import os

# 加入项目根路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_URL = os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast')
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

# ─── 颜色输出 ───────────────────────────────────────────────────────────────
def ok(msg): print(f"\033[32m✓ {msg}\033[0m")
def warn(msg): print(f"\033[33m⚠ {msg}\033[0m")
def err(msg): print(f"\033[31m✗ {msg}\033[0m")
def info(msg): print(f"  {msg}")


def check_presale_table():
    print("\n" + "="*60)
    print("【1】检查 presale_history 表")
    print("="*60)
    with engine.connect() as conn:
        try:
            result = conn.execute(text(
                "SELECT campaign_type, COUNT(*) as cnt, SUM(order_count) as total "
                "FROM presale_history GROUP BY campaign_type ORDER BY campaign_type"
            ))
            rows = result.fetchall()
            if not rows:
                warn("presale_history 表为空，请先上传预售历史数据")
                return False
            ok(f"presale_history 表有数据：{sum(r.cnt for r in rows)} 条记录")
            print(f"\n  {'活动类型':<12} {'行数':>8} {'总单量':>15}")
            print("  " + "-"*40)
            for r in rows:
                print(f"  {str(r.campaign_type):<12} {r.cnt:>8} {int(r.total or 0):>15,}")
            return True
        except Exception as e:
            err(f"查询 presale_history 失败: {e}")
            return False


def check_shop_mapping():
    print("\n" + "="*60)
    print("【2】检查店铺映射")
    print("="*60)
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT ph.shop_id, s.name "
            "FROM presale_history ph "
            "LEFT JOIN shops s ON s.shop_id = ph.shop_id "
            "GROUP BY ph.shop_id, s.name"
        ))
        rows = result.fetchall()
        for r in rows:
            if r.name:
                ok(f"shop_id={r.shop_id} → {r.name}")
            else:
                warn(f"shop_id={r.shop_id} → 未找到对应店铺（shops 表无此记录）")

        # 检查 NaN shop（shop_id 为 'nan' 或 'None'）
        nan_result = conn.execute(text(
            "SELECT COUNT(*) as cnt FROM presale_history "
            "WHERE shop_id IS NULL OR shop_id IN ('nan', 'None', '')"
        ))
        nan_cnt = nan_result.scalar()
        if nan_cnt:
            err(f"presale_history 中有 {nan_cnt} 条记录 shop_id 为空/nan，请修复！")
        else:
            ok("所有记录 shop_id 均已填写")


def check_model_fit():
    print("\n" + "="*60)
    print("【3】验证 PresaleDistributionModel.fit_from_db()")
    print("="*60)
    db = Session()
    try:
        from src.models.presale_model import PresaleDistributionModel
        model = PresaleDistributionModel.fit_from_db(db)
        if not model.volume_history:
            warn("模型未学到任何数据（volume_history 为空）")
            return model

        ok(f"模型 fit 成功，学到 {len(model.volume_history)} 个(shop,channel,campaign)组合")
        print(f"\n  {'组合键':<40} {'事件数':>6} {'平均总量':>12}")
        print("  " + "-"*62)
        for key, vols in model.volume_history.items():
            avg = sum(vols) / len(vols) if vols else 0
            print(f"  {str(key):<40} {len(vols):>6} {avg:>12,.0f}")
        return model
    except Exception as e:
        err(f"fit_from_db() 失败: {e}")
        import traceback; traceback.print_exc()
        return None
    finally:
        db.close()


def check_prediction_vs_actual(model):
    print("\n" + "="*60)
    print("【4】预售预测 vs 实际单量对比")
    print("="*60)
    if model is None or not model.volume_history:
        warn("模型为空，跳过对比")
        return

    with engine.connect() as conn:
        # 取 presale_history 中每个 payment_date 的实际单量（合并后）
        result = conn.execute(text(
            "SELECT shop_id, channel, payment_date, campaign_type, "
            "SUM(order_count) as actual_total "
            "FROM presale_history "
            "GROUP BY shop_id, channel, payment_date, campaign_type "
            "ORDER BY payment_date"
        ))
        ph_rows = result.fetchall()
        if not ph_rows:
            warn("presale_history 无数据，跳过")
            return

        # 从 channel_orders 中查找对应日期实际总单量（含 presale）
        result2 = conn.execute(text(
            "SELECT date, shop_id, channel, SUM(order_count) as total "
            "FROM channel_orders "
            "GROUP BY date, shop_id, channel"
        ))
        co_rows = result2.fetchall()
        co_lookup = {(str(r.date), r.shop_id, r.channel): int(r.total or 0) for r in co_rows}

    # 按 (shop_id, channel, campaign_type) 分组，对比每个 payment_date
    from datetime import date as date_type
    from collections import defaultdict
    events = defaultdict(list)
    for r in ph_rows:
        events[(r.shop_id, r.channel, r.campaign_type)].append(r.payment_date)

    total_mae = 0
    count = 0
    print(f"\n  {'店铺':<8} {'渠道':<10} {'活动':<6} {'日期':<12} {'预测':>10} {'实际':>10} {'误差%':>8}")
    print("  " + "-"*70)

    for (shop_id, channel, campaign_type), pay_dates in events.items():
        pay_dates_sorted = sorted(set(pay_dates))
        try:
            pred_map = model.predict_daily_volumes(
                payment_dates=pay_dates_sorted,
                shop_id=shop_id,
                channel=channel,
                campaign_type=campaign_type,
            )
        except Exception as e:
            warn(f"预测失败 ({shop_id},{channel},{campaign_type}): {e}")
            continue

        for pd_date in pay_dates_sorted:
            actual_ps = sum(
                r.actual_total for r in ph_rows
                if r.shop_id == shop_id and r.channel == channel
                and r.payment_date == pd_date and r.campaign_type == campaign_type
            )
            pred_vol = pred_map.get(pd_date, 0) or 0
            err_pct = abs(pred_vol - actual_ps) / actual_ps * 100 if actual_ps > 0 else 0
            total_mae += abs(pred_vol - actual_ps)
            count += 1
            flag = "⚠" if err_pct > 30 else " "
            print(f"  {shop_id:<8} {channel:<10} {campaign_type:<6} {str(pd_date):<12} "
                  f"{pred_vol:>10,.0f} {actual_ps:>10,.0f} {err_pct:>7.1f}% {flag}")

    if count:
        ok(f"\n  平均绝对误差（预售分布预测 vs 历史实际）：{total_mae/count:,.0f} 单/天")
    else:
        warn("无对比数据")


def check_channel_orders_adjusted():
    print("\n" + "="*60)
    print("【5】验证 channel_orders 中预售单是否需要剥离")
    print("="*60)
    with engine.connect() as conn:
        # 找到 presale_history 的付款日，检查 channel_orders 中同日的单量水平
        result = conn.execute(text(
            "SELECT ph.payment_date, ph.shop_id, SUM(ph.order_count) as presale_total, "
            "co.total as channel_total "
            "FROM presale_history ph "
            "LEFT JOIN ("
            "  SELECT date, shop_id, SUM(order_count) as total "
            "  FROM channel_orders GROUP BY date, shop_id"
            ") co ON co.date = ph.payment_date AND co.shop_id = ph.shop_id "
            "GROUP BY ph.payment_date, ph.shop_id, co.total "
            "ORDER BY ph.payment_date"
        ))
        rows = result.fetchall()
        if not rows:
            warn("无法对比（presale_history 或 channel_orders 为空）")
            return

        print(f"\n  {'日期':<12} {'店铺':<8} {'预售单':>10} {'渠道总单':>10} {'预售占比':>10}")
        print("  " + "-"*56)
        for r in rows:
            ch_total = int(r.channel_total or 0)
            ps_total = int(r.presale_total or 0)
            ratio = ps_total / ch_total * 100 if ch_total > 0 else 0
            flag = " ⚠高" if ratio > 80 else ""
            print(f"  {str(r.payment_date):<12} {r.shop_id:<8} {ps_total:>10,} {ch_total:>10,} {ratio:>9.1f}%{flag}")
        info("\n  提示：训练时会自动从 channel_orders 中减去预售单量，使 LightGBM 仅学习普通单")


if __name__ == "__main__":
    print("=" * 60)
    print("预售历史数据验证脚本")
    print("=" * 60)

    has_data = check_presale_table()
    if has_data:
        check_shop_mapping()
        model = check_model_fit()
        check_prediction_vs_actual(model)
        check_channel_orders_adjusted()
    else:
        print("\n请先上传预售历史数据再运行此脚本。")
        print("上传步骤：前端「数据管理」→「预售历史」Tab → 上传预售直播数据.xlsx")

    print("\n" + "="*60)
    print("验证完成")
    print("="*60)
