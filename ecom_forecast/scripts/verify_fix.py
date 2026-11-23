"""
验证修复：训练后检查 stable 模型对 lag 特征的响应是否恢复正常
期望：不同 lag7 输入 → 不同预测值（不再输出常数）
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
import warnings
warnings.filterwarnings("ignore")

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast'))

TARGET_SHOP = "薇诺娜天猫旗舰店"
TARGET_CH   = "货架"

# ── 加载并聚合数据（去重：每 date/shop/channel/platform 一行）────────────
print("【1】加载并聚合数据...")
raw = pd.read_sql("""
    SELECT date, shop_id, channel, platform, order_count
    FROM channel_orders
""", engine)
raw["date"] = pd.to_datetime(raw["date"])
channel_df = raw.groupby(["date","shop_id","channel","platform"], as_index=False)["order_count"].sum()
print(f"  聚合后: {len(channel_df)} 条  (原始 {len(raw)} 条)")

# ── 训练 stable 模型 ─────────────────────────────────────────────────────
print("\n【2】在聚合数据上训练 stable 模型...")
from src.models.channel_models import ChannelTypeModel

stable_data = channel_df[channel_df["channel"].isin(["货架", "其他"])].copy()
print(f"  stable 训练样本: {len(stable_data)} 条")

m = ChannelTypeModel("stable")
m.fit(stable_data)
print(f"  训练完成，特征数: {len(m.feature_cols)}")

# ── 构造预测特征 ──────────────────────────────────────────────────────────
print("\n【3】构造预测特征（Apr 1-7）...")
from src.features.time_features import build_all_time_features, fill_future_lag_features

FUTURE_DATES = pd.date_range("2026-04-01", "2026-04-07")
sub_hist = channel_df[(channel_df.shop_id==TARGET_SHOP) & (channel_df.channel==TARGET_CH)].copy()

future_rows = [{"date": dt, "shop_id": TARGET_SHOP, "channel": TARGET_CH,
                "platform": "天猫", "order_count": np.nan}
               for dt in FUTURE_DATES]
combined = pd.concat([sub_hist, pd.DataFrame(future_rows)]).sort_values("date")
feat = build_all_time_features(combined, target_col="order_count",
                                group_cols=["shop_id","channel"], date_col="date")
feat = fill_future_lag_features(feat, "order_count", ["shop_id","channel"])

future_feat = feat[feat["date"].isin(FUTURE_DATES)].copy()
print("  lag7 值:", future_feat["order_count_lag7"].values)

# ── 直接调用模型 predict ──────────────────────────────────────────────────
from src.features.campaign_features import build_campaign_features
from src.features.channel_features import build_presale_order_features

sub_for_pred = pd.concat([sub_hist, pd.DataFrame(future_rows)]).sort_values(["shop_id","channel","date"])
sub_for_pred = build_campaign_features(sub_for_pred, date_col="date", shop_col="shop_id",
                                        shop_sensitivity_map=m.shop_sensitivity_map,
                                        shop_override_map=m.shop_override_map)
sub_for_pred = build_all_time_features(sub_for_pred, target_col="order_count",
                                        group_cols=["shop_id","channel"], date_col="date")
sub_for_pred = build_presale_order_features(sub_for_pred, group_cols=["shop_id","channel"], date_col="date")
for col in ["shop_id","channel"]:
    sub_for_pred[f"{col}_code"] = sub_for_pred[col].map(m.encoders.get(col,{})).fillna(-1).astype(int)
sub_for_pred = fill_future_lag_features(sub_for_pred, "order_count", ["shop_id","channel"])

target_rows = sub_for_pred[sub_for_pred["date"].isin(FUTURE_DATES)].copy()
X = target_rows.reindex(columns=m.feature_cols, fill_value=0)
preds = m.model.predict(X)
import numpy as np
preds_orig = np.expm1(np.clip(preds, 0, None))

print("\n【4】预测结果（Apr 1-7）：")
for d, lag7, p in zip(FUTURE_DATES, target_rows["order_count_lag7"].values, preds_orig):
    print(f"  {d.date()}  lag7={lag7:.0f}  → 预测={p:.1f}")

print(f"\n  预测值是否全部相同（直线）: {np.allclose(preds_orig, preds_orig[0], rtol=0.01)}")
print(f"  预测值范围: {preds_orig.min():.1f} ~ {preds_orig.max():.1f}")

# ── 敏感度测试 ────────────────────────────────────────────────────────────
print("\n【5】敏感度测试：改变 lag7 输入，预测是否变化？")
X_test = X.copy()
base = m.model.predict(X_test)[0]
print(f"  原始 lag7={X_test['order_count_lag7'].iloc[0]:.0f} → 对数空间={base:.3f} → 预测={np.expm1(base):.1f}")
for test_lag in [1000, 5000, 10000, 20000, 50000]:
    X2 = X_test.copy()
    X2["order_count_lag7"] = np.log1p(test_lag)   # 注意 lag 特征本身未做 log，只是目标做了
    p_log = m.model.predict(X2)[0]
    p = np.expm1(max(p_log, 0))
    X2b = X_test.copy()
    X2b["order_count_lag7"] = test_lag
    p_log2 = m.model.predict(X2b)[0]
    p2 = np.expm1(max(p_log2, 0))
    print(f"  lag7_raw={test_lag}  → 预测={p2:.1f}")

print("\n✓ 如果各行预测值不同，说明模型已正常响应 lag 特征，直线问题已修复。")
