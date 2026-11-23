"""
st.session_state 统一管理
集中定义所有 session key，避免拼写错误
"""

import streamlit as st
from typing import Any, Optional


# ─── Key 常量 ───────────────────────────────────────────────────────────────
KEY_CONFIG          = "user_config"
KEY_CHANNEL_DF      = "channel_orders_df"
KEY_WAREHOUSE_DF    = "warehouse_orders_df"
KEY_SCHEDULE_DF     = "live_schedule_df"
KEY_COMMISSION_DF   = "commission_df"
KEY_PIPELINE        = "pipeline"
KEY_CHANNEL_PRED    = "channel_pred_df"
KEY_WAREHOUSE_PRED  = "warehouse_pred_df"
KEY_CHANNEL_EVAL    = "channel_eval_df"
KEY_WAREHOUSE_EVAL  = "warehouse_eval_df"
KEY_HOLDOUT_MERGED  = "holdout_merged_df"
KEY_TRAIN_LOG       = "train_log"
KEY_LAST_TRAIN_TIME = "last_train_time"
KEY_DATA_RANGE      = "data_date_range"


def get(key: str, default: Any = None) -> Any:
    val = st.session_state.get(key)
    if val is not None:
        return val
    # KEY_LAST_TRAIN_TIME 从配置文件兜底读取，避免刷新后丢失
    if key == KEY_LAST_TRAIN_TIME:
        cfg = st.session_state.get(KEY_CONFIG, {})
        return cfg.get("last_train_time", default)
    return default


def set(key: str, value: Any) -> None:
    st.session_state[key] = value


def is_trained() -> bool:
    pipeline = get(KEY_PIPELINE)
    return pipeline is not None and getattr(pipeline, "is_trained", False)


def has_channel_data() -> bool:
    df = get(KEY_CHANNEL_DF)
    return df is not None and len(df) > 0


def has_warehouse_data() -> bool:
    df = get(KEY_WAREHOUSE_DF)
    return df is not None and len(df) > 0


def has_predictions() -> bool:
    return get(KEY_CHANNEL_PRED) is not None


def get_config() -> dict:
    from ui.config_manager import load_config
    if KEY_CONFIG not in st.session_state:
        st.session_state[KEY_CONFIG] = load_config()
    return st.session_state[KEY_CONFIG]


def save_config(cfg: dict) -> None:
    from ui.config_manager import save_config as _save
    st.session_state[KEY_CONFIG] = cfg
    _save(cfg)
