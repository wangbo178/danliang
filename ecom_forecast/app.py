"""
电商单量预测系统 - Web UI 主入口
运行方式：
    cd D:/AI生成源码/预训练/ecom_forecast
    streamlit run app.py
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(
    page_title="电商单量预测系统",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 预加载配置到 session_state
from ui import state
_ = state.get_config()

st.sidebar.title("📦 预测系统")
st.sidebar.caption("电商单量预测 · 三层架构")

# 侧边栏状态摘要
st.sidebar.divider()
trained = state.is_trained()
has_data = state.has_channel_data()

st.sidebar.markdown("**系统状态**")
st.sidebar.markdown(f"- 数据: {'✅ 已上传' if has_data else '❌ 未上传'}")
st.sidebar.markdown(f"- 模型: {'✅ 已训练' if trained else '❌ 未训练'}")
st.sidebar.markdown(f"- 预测: {'✅ 可查看' if state.has_predictions() else '❌ 未生成'}")

st.title("欢迎使用电商单量预测系统")
st.markdown("""
请从左侧导航栏选择功能页面，或按以下流程开始：

| 步骤 | 页面 | 说明 |
|------|------|------|
| 1 | ⚙️ 基础配置 | 配置平台、店铺、仓库、大促日历 |
| 2 | 📁 数据管理 | 下载模板、上传数据 |
| 3 | 🚀 模型训练 | 一键训练三层模型 |
| 4 | 📊 预测结果 | 查看图表与评估指标 |
| 5 | 📥 结果导出 | 导出 Excel / JSON |
""")
