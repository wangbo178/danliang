"""
Plotly 图表封装
"""

from typing import List, Optional
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def line_actual_vs_pred(
    history_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    group_col: Optional[str] = None,
    title: str = "历史实际 vs 预测",
) -> go.Figure:
    """折线图：历史实际值 vs 预测值"""
    fig = go.Figure()

    if group_col and group_col in history_df.columns:
        groups = history_df[group_col].unique()
    else:
        groups = ["全部"]

    for grp in groups:
        if group_col and group_col in history_df.columns:
            h = history_df[history_df[group_col] == grp]
            p = pred_df[pred_df[group_col] == grp] if group_col in pred_df.columns else pred_df
        else:
            h = history_df
            p = pred_df

        # 历史实际
        if "order_count" in h.columns:
            h_agg = h.groupby("date")["order_count"].sum().reset_index()
            fig.add_trace(go.Scatter(
                x=h_agg["date"], y=h_agg["order_count"],
                mode="lines", name=f"{grp} 实际",
                line=dict(width=1.5),
            ))

        # 预测
        pred_col = "predicted_orders" if "predicted_orders" in p.columns else "predicted_shop_orders"
        if pred_col in p.columns:
            p_agg = p.groupby("date")[pred_col].sum().reset_index()
            fig.add_trace(go.Scatter(
                x=p_agg["date"], y=p_agg[pred_col],
                mode="lines", name=f"{grp} 预测",
                line=dict(width=2, dash="dash"),
            ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="订单量",
        hovermode="x unified",
        height=420,
    )
    return fig


def bar_channel_forecast(pred_df: pd.DataFrame) -> go.Figure:
    """柱状图：各渠道预测单量对比"""
    if "channel" not in pred_df.columns:
        return go.Figure()
    pred_col = "predicted_orders" if "predicted_orders" in pred_df.columns else "predicted_shop_orders"
    agg = pred_df.groupby("channel")[pred_col].sum().reset_index()
    agg = agg.sort_values(pred_col, ascending=False)

    fig = px.bar(
        agg, x="channel", y=pred_col,
        title="各渠道预测总单量",
        labels={"channel": "渠道", pred_col: "预测单量"},
        color=pred_col,
        color_continuous_scale="Blues",
    )
    fig.update_layout(height=380)
    return fig


def heatmap_warehouse_ratio(warehouse_pred_df: pd.DataFrame) -> go.Figure:
    """热力图：仓库分配比例（店铺 × 仓库）"""
    if warehouse_pred_df is None or len(warehouse_pred_df) == 0:
        return go.Figure()

    required = {"shop_id", "warehouse_id", "warehouse_orders"}
    if not required.issubset(warehouse_pred_df.columns):
        return go.Figure()

    pivot = (
        warehouse_pred_df
        .groupby(["shop_id", "warehouse_id"])["warehouse_orders"]
        .sum()
        .reset_index()
        .pivot(index="shop_id", columns="warehouse_id", values="warehouse_orders")
        .fillna(0)
    )
    # 转为比例
    pivot_ratio = pivot.div(pivot.sum(axis=1), axis=0).round(4) * 100

    fig = px.imshow(
        pivot_ratio,
        text_auto=".1f",
        labels=dict(x="仓库", y="店铺", color="占比(%)"),
        title="仓库分配比例（%）",
        color_continuous_scale="RdYlGn",
        zmin=0, zmax=100,
    )
    fig.update_layout(height=300 + 40 * len(pivot_ratio))
    return fig


def render_metric_cards(channel_eval_df: pd.DataFrame) -> None:
    """用原生 Streamlit 组件渲染评估指标卡片"""
    import streamlit as st

    if channel_eval_df is None or len(channel_eval_df) == 0:
        st.info("暂无评估结果")
        return

    cols = st.columns(min(len(channel_eval_df), 4))
    for i, (_, row) in enumerate(channel_eval_df.iterrows()):
        # 优先 WAPE（对零值免疫），回退到 MAPE
        wape_val = row.get("wape", float("nan"))
        mape_val = row.get("mape", float("nan"))
        primary  = wape_val if pd.notna(wape_val) else mape_val
        primary_label = "WAPE" if pd.notna(wape_val) else "MAPE"

        bias  = row.get("bias_pct", float("nan"))
        rmse  = row.get("rmse", float("nan"))
        channel = str(row.get("channel", row.get("segment", row.get("group", "全部"))))

        primary_str = f"{primary:.1f}%" if pd.notna(primary) else "—"
        bias_str    = f"{bias:+.1f}%"   if pd.notna(bias)    else "—"
        rmse_str    = f"{rmse:.0f}"      if pd.notna(rmse)    else "—"

        if pd.isna(primary):
            delta_color = "off"
        elif primary < 15:
            delta_color = "normal"   # 绿
        elif primary < 25:
            delta_color = "off"      # 灰
        else:
            delta_color = "inverse"  # 红

        with cols[i % 4]:
            st.metric(
                label=channel,
                value=primary_str,
                delta=f"Bias {bias_str}  RMSE {rmse_str}",
                delta_color=delta_color,
                help=f"{primary_label}：< 15% 优；15-25% 良；> 25% 差",
            )


def bar_feature_importance(importance_df: pd.DataFrame, top_n: int = 20) -> go.Figure:
    """柱状图：特征重要性（gain）Top-N"""
    if importance_df is None or len(importance_df) == 0:
        return go.Figure()
    df = importance_df.nlargest(top_n, "importance").sort_values("importance")
    fig = px.bar(
        df, x="importance", y="feature", orientation="h",
        title=f"特征重要性（Gain，Top {top_n}）",
        labels={"importance": "Gain", "feature": "特征"},
        color="importance",
        color_continuous_scale="Teal",
    )
    fig.update_layout(height=max(300, top_n * 22), showlegend=False)
    return fig


def line_holdout_vs_pred(
    holdout_merged: pd.DataFrame,
    group_col: Optional[str] = None,
    title: str = "Holdout 期：实际 vs 预测",
) -> go.Figure:
    """折线图：holdout 期实际值 vs 预测值（诊断用）"""
    fig = go.Figure()
    if holdout_merged is None or len(holdout_merged) == 0:
        return fig

    if group_col and group_col in holdout_merged.columns:
        groups = sorted(holdout_merged[group_col].unique())
    else:
        groups = ["全部"]

    pred_col = (
        "predicted_orders"
        if "predicted_orders" in holdout_merged.columns
        else "predicted_shop_orders"
    )

    for grp in groups:
        if group_col and group_col in holdout_merged.columns:
            sub = holdout_merged[holdout_merged[group_col] == grp]
        else:
            sub = holdout_merged

        actual_agg = sub.groupby("date")["order_count"].sum().reset_index()
        fig.add_trace(go.Scatter(
            x=actual_agg["date"], y=actual_agg["order_count"],
            mode="lines+markers", name=f"{grp} 实际",
            line=dict(width=2),
        ))

        if pred_col in sub.columns:
            pred_agg = sub.groupby("date")[pred_col].sum().reset_index()
            fig.add_trace(go.Scatter(
                x=pred_agg["date"], y=pred_agg[pred_col],
                mode="lines+markers", name=f"{grp} 预测",
                line=dict(width=2, dash="dash"),
            ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="订单量",
        hovermode="x unified",
        height=400,
    )
    return fig


def metric_cards_html(channel_eval_df: pd.DataFrame) -> str:
    """兼容旧调用：返回空字符串，实际渲染请改用 render_metric_cards()"""
    return ""
