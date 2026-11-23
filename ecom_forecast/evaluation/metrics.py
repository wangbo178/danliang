"""
评估指标模块
- MAPE, Bias, RMSE
- 大促期间专项评估
- 仓级汇总误差
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict


def mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1.0) -> float:
    """
    平均绝对百分比误差（仅对 actual > 0 的行计算，避免零值爆炸）
    """
    y_true = np.array(y_true, dtype=float)
    y_pred = np.array(y_pred, dtype=float)
    mask = y_true > 0
    if mask.sum() == 0:
        return float("nan")
    denom = np.maximum(np.abs(y_true[mask]), eps)
    return float(np.mean(np.abs(y_true[mask] - y_pred[mask]) / denom) * 100)


def wape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    加权绝对百分比误差（WAPE）
    = sum(|actual - pred|) / sum(actual) × 100
    对零值完全免疫，是稀疏渠道最可靠的精度指标。
    """
    y_true = np.array(y_true, dtype=float)
    y_pred = np.array(y_pred, dtype=float)
    # 只使用两者均有限的行（过滤 NaN / Inf）
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    if valid.sum() == 0:
        return float("nan")
    yt = y_true[valid]
    yp = y_pred[valid]
    total = float(np.sum(np.abs(yt)))
    if total == 0:
        return float("nan")
    return float(np.sum(np.abs(yt - yp)) / total * 100)


def bias(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    系统性偏差（正=高估，负=低估）
    单位：与原始单量相同
    """
    return float(np.mean(y_pred - y_true))


def bias_pct(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1.0) -> float:
    """偏差百分比"""
    denom = max(np.mean(np.abs(y_true)), eps)
    return float(np.mean(y_pred - y_true) / denom * 100)


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """平均绝对误差，单位与订单量一致。"""
    return float(np.mean(np.abs(y_true - y_pred)))


def evaluate(
    df: pd.DataFrame,
    actual_col: str = "order_count",
    pred_col: str = "predicted_orders",
    date_col: str = "date",
    group_col: Optional[str] = None,
    campaign_mask_col: Optional[str] = "is_campaign_day",
) -> pd.DataFrame:
    """
    综合评估报告

    参数：
        group_col: 分组维度（如 'channel'、'shop_id'），None 则全局评估
        campaign_mask_col: 大促标记列名（用于大促专项评估）

    返回：
        评估结果 DataFrame
    """
    df = df.copy().dropna(subset=[actual_col, pred_col])
    y_true = df[actual_col].values
    y_pred = df[pred_col].values

    rows = []

    def _compute_row(label: str, yt: np.ndarray, yp: np.ndarray) -> Dict:
        valid = np.isfinite(yt.astype(float)) & np.isfinite(yp.astype(float))
        yt = yt[valid].astype(float)
        yp = yp[valid].astype(float)
        if len(yt) == 0:
            return {
                "segment": label, "n_samples": 0, "n_positive_actual": 0,
                "wape": None, "mae": None, "mape": None, "bias_pct": None,
                "rmse": None, "total_actual": 0.0, "total_predicted": 0.0,
            }
        _wape = wape(yt, yp)
        _mae = mae(yt, yp)
        _mape = mape(yt, yp)
        _bias = bias_pct(yt, yp)
        _rmse = rmse(yt, yp)
        return {
            "segment": label,
            "n_samples": len(yt),
            "n_positive_actual": int((yt > 0).sum()),
            "wape": round(_wape, 2) if pd.notna(_wape) else None,
            "mae": round(_mae, 2) if pd.notna(_mae) else None,
            "mape": round(_mape, 2) if pd.notna(_mape) else None,   # 仅含 actual>0 行
            "bias_pct": round(_bias, 2) if pd.notna(_bias) else None,
            "rmse": round(_rmse, 1) if pd.notna(_rmse) else None,
            "total_actual": round(float(np.nansum(yt)), 2),
            "total_predicted": round(float(np.nansum(yp)), 2),
        }

    # 全局评估
    rows.append(_compute_row("全局", y_true, y_pred))

    # 大促专项
    if campaign_mask_col and campaign_mask_col in df.columns:
        campaign_mask = df[campaign_mask_col].fillna(0).astype(bool)
        if campaign_mask.sum() > 0:
            rows.append(_compute_row("大促期间", y_true[campaign_mask], y_pred[campaign_mask]))
        non_campaign_mask = ~campaign_mask
        if non_campaign_mask.sum() > 0:
            rows.append(_compute_row("非大促期间", y_true[non_campaign_mask], y_pred[non_campaign_mask]))

    # 分组评估
    if group_col and group_col in df.columns:
        for group_val, sub in df.groupby(group_col):
            yt = sub[actual_col].values
            yp = sub[pred_col].values
            rows.append(_compute_row(f"{group_col}={group_val}", yt, yp))

    result = pd.DataFrame(rows)
    return result


def evaluate_warehouse(
    df: pd.DataFrame,
    actual_col: str = "warehouse_orders",
    pred_col: str = "warehouse_orders_pred",
    warehouse_col: str = "warehouse_id",
) -> pd.DataFrame:
    """仓级预测评估"""
    rows = []

    def _compute_row(label: str, yt: np.ndarray, yp: np.ndarray) -> Dict:
        return {
            "warehouse": label,
            "n_samples": len(yt),
            "mape": round(mape(yt, yp), 2),
            "bias_pct": round(bias_pct(yt, yp), 2),
            "total_actual": int(yt.sum()),
            "total_predicted": int(yp.sum()),
        }

    df = df.dropna(subset=[actual_col, pred_col])
    rows.append(_compute_row("全仓合计", df[actual_col].values, df[pred_col].values))

    for wh, sub in df.groupby(warehouse_col):
        rows.append(_compute_row(wh, sub[actual_col].values, sub[pred_col].values))

    return pd.DataFrame(rows)


def print_evaluation_report(eval_df: pd.DataFrame) -> None:
    """格式化打印评估报告"""
    print("\n" + "=" * 60)
    print("预测评估报告")
    print("=" * 60)
    print(eval_df.to_string(index=False))
    print("=" * 60)
    print("MAPE 目标：日粒度 < 15%，大促期间 < 20%")
    print("Bias 目标：|bias_pct| < 5%（避免系统性高/低估）")
