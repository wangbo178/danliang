"""
模型超参数配置
"""

# ============================================================
# LightGBM 全局模型（第一层）
# ============================================================
GLOBAL_MODEL_PARAMS = {
    # regression_l2（MSE）：预测条件均值，使 lag/rolling 特征对树分裂有实质贡献。
    # 配合训练层的 log1p 目标变换压缩右偏，避免大促尖刺主导损失。
    "objective": "regression_l2",
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "max_depth": -1,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "verbose": -1,
    "n_jobs": -1,
    "seed": 42,
    "feature_fraction_seed": 42,
    "bagging_seed": 42,
}

# ============================================================
# 渠道子模型（第二层）
# ============================================================
CHANNEL_MODEL_PARAMS = {
    "stable": {
        # regression_l2 + log1p 变换：lag 特征对均值预测的残差贡献显著，
        # 不再输出全量历史中位数（直线问题根因）。
        "objective": "regression_l2",
        "n_estimators": 300,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 10,
        "feature_fraction": 0.8,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    },
    "event": {
        # 直播渠道：log1p 变换后分布接近正态，L2 不再被极端尖刺主导。
        # 增加 estimators 和降低 learning_rate 提升对稀疏大促信号的拟合。
        "objective": "regression_l2",
        "n_estimators": 500,
        "learning_rate": 0.03,
        "num_leaves": 63,
        "min_child_samples": 5,     # 直播数据稀疏，降低最小样本数
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    },
    "affiliate": {
        # 联盟/KOC/CPS：佣金驱动，较稳定，L2 即可
        "objective": "regression_l2",
        "n_estimators": 300,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 10,
        "feature_fraction": 0.8,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    },
}

# ============================================================
# 训练配置
# ============================================================
TRAINING_CONFIG = {
    "validation_days": 30,           # 最近30天作为验证集
    "campaign_sample_weight": 3.0,   # 大促期间样本权重放大
    "early_stopping_rounds": 50,
    "forecast_horizon": 30,          # 默认预测未来30天
    # 预测特征最长只需去年同期(±3天)、90天预售窗口和30天滚动窗口。
    # 400天留足闰年余量，避免每个预测日重算全部3~4年历史。
    "prediction_history_days": 400,
}
