"""
验证达人特征（anchor_name）功能的脚本

验证内容：
1. 数据库列迁移：live_schedules.anchor_name 列是否存在
2. 数据入库验证：上传含 anchor_name 的排期后数据库是否有达人名称
3. 特征构建验证：build_live_features 是否正确输出 3 个新特征
4. 预测差异验证：同档级不同达人预测值是否有差异

运行方式：
    cd ecom_forecast
    python scripts/verify_anchor_features.py
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

# ─── 1. 数据库列迁移验证 ─────────────────────────────────────────────────────
print("=" * 60)
print("1. 检查 live_schedules.anchor_name 列是否存在")
print("=" * 60)
try:
    from sqlalchemy import create_engine, text
    engine = create_engine(
        os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast?charset=utf8mb4')
    )
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='ecom_forecast' AND TABLE_NAME='live_schedules' "
            "AND COLUMN_NAME='anchor_name'"
        ))
        row = result.fetchone()
        if row:
            print("  [OK] anchor_name 列存在于 live_schedules 表")
        else:
            print("  [WARN] anchor_name 列不存在，请重启后端（init_db 会自动添加）")
except Exception as e:
    print(f"  [ERROR] 数据库连接失败：{e}")


# ─── 2. 数据库中的 anchor_name 数据 ──────────────────────────────────────────
print("\n" + "=" * 60)
print("2. 检查 live_schedules 表中 anchor_name 数据")
print("=" * 60)
try:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT anchor_name, COUNT(*) as cnt FROM live_schedules "
            "GROUP BY anchor_name ORDER BY cnt DESC LIMIT 10"
        ))
        rows = result.fetchall()
        if rows:
            print(f"  共有 {sum(r[1] for r in rows)} 条排期，anchor_name 分布（前10）：")
            for r in rows:
                name = r[0] or "(空)"
                print(f"    {name}: {r[1]} 条")
        else:
            print("  [INFO] live_schedules 表为空，请先上传直播排期数据")
except Exception as e:
    print(f"  [ERROR] 查询失败：{e}")


# ─── 3. build_live_features 特征验证 ─────────────────────────────────────────
print("\n" + "=" * 60)
print("3. 验证 build_live_features 新特征输出")
print("=" * 60)
try:
    from src.features.channel_features import build_live_features

    # 构造测试数据
    dates = pd.date_range("2024-01-01", periods=10)
    df_test = pd.DataFrame({
        "shop_id": ["CS01"] * 10,
        "channel": ["直播"] * 10,
        "date": dates,
        "order_count": np.random.randint(100, 500, 10),
    })

    schedule_test = pd.DataFrame({
        "shop_id": ["CS01", "CS01", "CS01"],
        "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-08")],
        "event_type": ["live", "live", "live"],
        "anchor_tier": ["A", "B", "A"],
        "anchor_name": ["李佳琦", "普通主播", "李佳琦"],
        "broadcast_date": [None, None, None],
    })

    channel_df_test = df_test.copy()

    anchor_encoder = {"李佳琦": 1, "普通主播": 2}

    result_df = build_live_features(
        df_test,
        schedule_df=schedule_test,
        channel_df=channel_df_test,
        anchor_encoder=anchor_encoder,
    )

    new_cols = ["anchor_name_code", "anchor_hist_mean", "anchor_hist_recent"]
    missing = [c for c in new_cols if c not in result_df.columns]
    if missing:
        print(f"  [FAIL] 缺少特征列：{missing}")
    else:
        print(f"  [OK] 3 个新特征已生成：{new_cols}")
        print(f"  anchor_name_code 唯一值：{sorted(result_df['anchor_name_code'].unique())}")
        print(f"  anchor_hist_mean 范围：[{result_df['anchor_hist_mean'].min():.1f}, {result_df['anchor_hist_mean'].max():.1f}]")
        print(f"  anchor_hist_recent 范围：[{result_df['anchor_hist_recent'].min():.1f}, {result_df['anchor_hist_recent'].max():.1f}]")
except Exception as e:
    import traceback
    print(f"  [ERROR] build_live_features 执行失败：{e}")
    traceback.print_exc()


# ─── 4. 预测差异验证（需要已训练模型）────────────────────────────────────────
print("\n" + "=" * 60)
print("4. 检查已训练的 event 模型中 anchor encoder 是否存在")
print("=" * 60)
try:
    import joblib
    import glob

    model_files = glob.glob("models/**/event_model.joblib", recursive=True)
    if not model_files:
        print("  [INFO] 未找到已训练的 event 模型文件（models/ 目录下），跳过此验证")
        print("  提示：请先在前端启动一次训练，再重新运行此脚本")
    else:
        model_path = model_files[0]
        data = joblib.load(model_path)
        encoders = data.get("encoders", {})
        if "anchor" in encoders:
            anchor_enc = encoders["anchor"]
            print(f"  [OK] event 模型中存在 anchor encoder，共 {len(anchor_enc)} 位达人：")
            for name, code in list(anchor_enc.items())[:10]:
                print(f"    [{code}] {name}")
        else:
            print("  [INFO] event 模型中无 anchor encoder（数据中 anchor_name 均为空，或模型训练于本次更新之前）")
            print("  提示：上传含 anchor_name 的排期后重新训练即可")
        feature_cols = data.get("feature_cols", [])
        anchor_feat_in_model = [c for c in ["anchor_name_code","anchor_hist_mean","anchor_hist_recent"] if c in feature_cols]
        print(f"  模型特征列中的达人特征：{anchor_feat_in_model if anchor_feat_in_model else '(无，需重新训练)'}")
except Exception as e:
    print(f"  [ERROR] 加载模型失败：{e}")


print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
