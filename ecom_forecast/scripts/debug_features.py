"""
逐步还原预测特征，精确找到"直线"根因
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
import joblib

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

TARGET_SHOP = "薇诺娜天猫旗舰店"
TARGET_CH   = "货架"
FUTURE_DATES = pd.date_range("2026-04-01", "2026-04-07")

# ── 加载历史数据（去重聚合，与训练完全一致） ──────────────────────
print("【1】加载并聚合历史数据...")
raw = pd.read_sql("""
    SELECT date, shop_id, channel, platform, order_count
    FROM channel_orders
""", engine)
raw["date"] = pd.to_datetime(raw["date"])
channel_df = raw.groupby(["date","shop_id","channel","platform"], as_index=False)["order_count"].sum()
print(f"  聚合后: {len(channel_df)} 条")

# 目标店铺/渠道的历史
sub_hist = channel_df[(channel_df.shop_id==TARGET_SHOP) & (channel_df.channel==TARGET_CH)].sort_values("date")
print(f"  目标 ({TARGET_SHOP}/{TARGET_CH}) 历史条数: {len(sub_hist)}")
print(f"  日期范围: {sub_hist['date'].min().date()} ~ {sub_hist['date'].max().date()}")
print(f"  最近7天 order_count: {sub_hist.tail(7)['order_count'].values}")

# ── 构造第1周未来行，拼接历史 ─────────────────────────────────────
print("\n【2】构造预测特征（第1周 Apr 1-7）...")
future_rows = []
for dt in FUTURE_DATES:
    future_rows.append({
        "date": dt, "shop_id": TARGET_SHOP,
        "channel": TARGET_CH, "platform": "天猫",
        "order_count": np.nan,
    })
future_df = pd.DataFrame(future_rows)
combined = pd.concat([channel_df, future_df]).sort_values(["shop_id","channel","date"]).reset_index(drop=True)

# ── 手动计算 lag 特征（还原 build_all_time_features）────────────────
from src.features.time_features import build_all_time_features, fill_future_lag_features

sub_combined = combined[(combined.shop_id==TARGET_SHOP) & (combined.channel==TARGET_CH)].copy()
sub_feat = build_all_time_features(
    sub_combined, target_col="order_count",
    group_cols=["shop_id","channel"], date_col="date",
)
sub_feat = fill_future_lag_features(sub_feat, "order_count", ["shop_id","channel"])

future_feat = sub_feat[sub_feat["date"].isin(FUTURE_DATES)]
print("\n  预测期特征（仅关键列）:")
cols_to_show = ["date", "order_count_lag7", "order_count_lag14", "order_count_lag28",
                "order_count_roll_mean7", "order_count_roll_mean14", "day_of_week"]
cols_to_show = [c for c in cols_to_show if c in future_feat.columns]
print(future_feat[cols_to_show].to_string(index=False))

# ── 检查 lag 在预测期是否真的变化 ────────────────────────────────
lags = future_feat[["order_count_lag7","order_count_lag14","order_count_lag28",
                     "order_count_roll_mean7","order_count_roll_mean14"]].values
print(f"\n  lag 特征行间是否全部相同: {(lags == lags[0]).all()}")
print(f"  lag7 唯一值数: {future_feat['order_count_lag7'].nunique()}")
print(f"  roll_mean7 唯一值数: {future_feat['order_count_roll_mean7'].nunique()}")

# ── 加载已训练模型，直接预测 ─────────────────────────────────────
print("\n【3】用训练好的模型预测 Apr 1-7...")
model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
stable_path = os.path.join(model_dir, "channel_models", "stable_model.joblib")
if not os.path.exists(stable_path):
    print(f"  ⚠ 模型文件不存在: {stable_path}")
else:
    from src.models.channel_models import ChannelTypeModel
    stable_model = ChannelTypeModel.load(stable_path)
    print(f"  模型特征列数: {len(stable_model.feature_cols)}")
    print(f"  模型使用特征: {stable_model.feature_cols[:10]}...")

    # 为整个 stable 数据集构造特征（与 predict_channels 等价）
    stable_hist = channel_df[(channel_df.channel.isin(["货架","其他"]))].copy()
    stable_combined = pd.concat([stable_hist, future_df]).sort_values(["shop_id","channel","date"])

    from src.features.campaign_features import build_campaign_features
    from src.features.channel_features import build_presale_order_features

    stable_combined = build_campaign_features(
        stable_combined, date_col="date", shop_col="shop_id",
        shop_sensitivity_map=stable_model.shop_sensitivity_map,
        shop_override_map=stable_model.shop_override_map,
    )
    stable_combined = build_all_time_features(
        stable_combined, target_col="order_count",
        group_cols=["shop_id","channel"], date_col="date",
    )
    stable_combined = build_presale_order_features(
        stable_combined, group_cols=["shop_id","channel"], date_col="date"
    )
    # encode
    for col in ["shop_id","channel"]:
        out_col = f"{col}_code"
        stable_combined[out_col] = stable_combined[col].map(
            stable_model.encoders.get(col, {})
        ).fillna(-1).astype(int)

    stable_combined = fill_future_lag_features(stable_combined, "order_count", ["shop_id","channel"])

    target_rows = stable_combined[
        (stable_combined.shop_id==TARGET_SHOP) &
        (stable_combined.channel==TARGET_CH) &
        (stable_combined["date"].isin(FUTURE_DATES))
    ].copy()

    avail_feats = [c for c in stable_model.feature_cols if c in target_rows.columns]
    X = target_rows.reindex(columns=stable_model.feature_cols, fill_value=0)
    preds = stable_model.model.predict(X)

    print("\n  各天特征 & 预测值:")
    target_rows["_pred"] = preds
    show_cols = ["date","order_count_lag7","order_count_roll_mean7","day_of_week","shop_id_code","_pred"]
    show_cols = [c for c in show_cols if c in target_rows.columns]
    print(target_rows[show_cols].to_string(index=False))

    # 测试：不同 lag7 输入是否会产生不同输出
    print("\n【4】敏感度测试：改变 lag7 输入，预测是否变化？")
    test_X = X.copy()
    base_pred = stable_model.model.predict(test_X)[0]
    print(f"  原始 lag7={test_X['order_count_lag7'].iloc[0]:.0f} → 预测={base_pred:.1f}")
    for test_lag in [5000, 10000, 20000, 50000]:
        test_X2 = test_X.copy()
        test_X2["order_count_lag7"] = test_lag
        p = stable_model.model.predict(test_X2)[0]
        print(f"  lag7={test_lag} → 预测={p:.1f}")
