"""
配置读写管理器
- 读取 config/user_config.json
- 保存用户修改
- 将 user_config 转换为 pipeline 可注入的 config_dict
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "user_config.json"


def _default_config() -> Dict:
    return {
        "platforms": [],
        "shops": [],
        "warehouses": [],
        "routing": {
            "rule_change_date": "2026-01-06",
            "transition_days": 7,
            "temp_overrides": [],
        },
        "campaign_calendar": {},
        "model_params": {
            "global_model": {
                "n_estimators": 500,
                "learning_rate": 0.05,
                "num_leaves": 63,
            },
            "channel_models": {
                "stable":    {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31},
                "event":     {"n_estimators": 400, "learning_rate": 0.05, "num_leaves": 63},
                "affiliate": {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31},
            },
            "training": {
                "validation_days": 30,
                "campaign_sample_weight": 3.0,
                "early_stopping_rounds": 50,
                "forecast_horizon": 30,
            },
        },
    }


def load_config() -> Dict:
    """加载用户配置，文件不存在则返回默认配置并写入"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 合并缺失字段
        default = _default_config()
        for k, v in default.items():
            cfg.setdefault(k, v)
        return cfg
    else:
        cfg = _default_config()
        save_config(cfg)
        return cfg


def save_config(cfg: Dict) -> None:
    """保存配置到 JSON 文件"""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def to_pipeline_config(cfg: Dict) -> Dict:
    """
    将 user_config dict 转换为可注入 ForecastPipeline 的 config_dict。
    """
    routing = cfg.get("routing", {})
    model_params = cfg.get("model_params", {})
    training = model_params.get("training", {})

    # 构建 warehouse_config（pipeline 格式）
    warehouse_config = [
        {
            "warehouse_id": w["warehouse_id"],
            "name": w["name"],
            "is_active": w.get("is_active", True),
            "open_date": w.get("open_date", "2022-01-01"),
            "close_date": w.get("close_date"),
            "priority": w.get("priority", 99),
        }
        for w in cfg.get("warehouses", [])
    ]

    # 构建 shop_warehouse_binding（pipeline 格式）
    shop_warehouse_binding = [
        {
            "shop_id": s["shop_id"],
            "platform": s.get("platform_id", ""),
            "binding_type": s.get("binding_type", "multi"),
            "warehouse_id": s.get("warehouse_id"),
            "effective_date": s.get("effective_date", "2022-01-01"),
        }
        for s in cfg.get("shops", [])
    ]

    # 构建 channel_type_map
    channel_type_map = {}
    for p in cfg.get("platforms", []):
        for ch in p.get("channels", []):
            channel_type_map[ch["channel_id"]] = ch["type"]

    return {
        "routing_rule_change_date": routing.get("rule_change_date", "2026-01-06"),
        "routing_transition_days": routing.get("transition_days", 7),
        "temp_routing_overrides": routing.get("temp_overrides", []),
        "warehouse_config": warehouse_config,
        "shop_warehouse_binding": shop_warehouse_binding,
        "channel_type_map": channel_type_map,
        "campaign_calendar": cfg.get("campaign_calendar") or {},
    }


def get_all_channel_ids(cfg: Dict) -> List[str]:
    """返回所有渠道 ID 列表"""
    ids = []
    for p in cfg.get("platforms", []):
        for ch in p.get("channels", []):
            ids.append(ch["channel_id"])
    return ids


def get_all_platform_ids(cfg: Dict) -> List[str]:
    return [p["platform_id"] for p in cfg.get("platforms", [])]


def get_platform_options(cfg: Dict) -> List[Dict]:
    return cfg.get("platforms", [])


def get_warehouse_options(cfg: Dict) -> List[Dict]:
    return cfg.get("warehouses", [])
