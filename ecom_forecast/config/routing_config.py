"""
路由相关配置
- 规则变更日期（硬截断点）
- 临时调仓时间段（大促应急期）
- 仓库状态
- 店铺-仓库绑定关系
"""

import pandas as pd


def _normalize_campaign_calendar(raw_calendar, base_calendar: dict) -> dict:
    """将 API 导出的活动列表转换成特征模块使用的按年嵌套字典。"""
    if isinstance(raw_calendar, dict):
        return raw_calendar
    if not isinstance(raw_calendar, list):
        raise TypeError("campaign_calendar 必须是 dict 或 list")

    # 数据库通常只维护部分年份；保留代码内置的其他年份作为兜底。
    normalized = {
        int(year): {name: dict(values) for name, values in campaigns.items()}
        for year, campaigns in base_calendar.items()
    }
    for item in raw_calendar:
        start_value = item.get("start") or item.get("start_date")
        end_value = item.get("end") or item.get("end_date")
        if not start_value or not end_value:
            raise ValueError(f"活动日历缺少开始或结束日期: {item}")
        start = pd.Timestamp(start_value)
        campaign_name = str(
            item.get("campaign_type") or item.get("name") or "custom"
        )
        normalized.setdefault(start.year, {})[campaign_name] = {
            "presale_start": item.get("presale_start"),
            "start": start.strftime("%Y-%m-%d"),
            "end": pd.Timestamp(end_value).strftime("%Y-%m-%d"),
        }
    return normalized


def load_from_dict(config_dict: dict) -> None:
    """
    从外部 dict 覆盖本模块的全局配置变量。
    config_dict 可包含以下 key（均可选）：
      routing_rule_change_date, routing_transition_days,
      temp_routing_overrides, warehouse_config,
      shop_warehouse_binding, channel_type_map,
      campaign_calendar
    """
    import sys
    mod = sys.modules[__name__]

    key_map = {
        "routing_rule_change_date": "ROUTING_RULE_CHANGE_DATE",
        "routing_transition_days":  "ROUTING_TRANSITION_DAYS",
        "temp_routing_overrides":   "TEMP_ROUTING_OVERRIDES",
        "warehouse_config":         "WAREHOUSE_CONFIG",
        "shop_warehouse_binding":   "SHOP_WAREHOUSE_BINDING",
        "channel_type_map":         "CHANNEL_TYPE_MAP",
    }
    for src_key, mod_attr in key_map.items():
        if src_key in config_dict:
            setattr(mod, mod_attr, config_dict[src_key])

    # 大促日历单独处理：同步更新 campaign_calendar 模块并重建事件列表
    if "campaign_calendar" in config_dict and config_dict["campaign_calendar"]:
        import config.campaign_calendar as _cal_mod
        import src.features.campaign_features as _feat_mod

        normalized = _normalize_campaign_calendar(
            config_dict["campaign_calendar"], _cal_mod.CAMPAIGN_CALENDAR
        )
        # 原地更新，确保 ``from ... import CAMPAIGN_CALENDAR`` 的模块也能看到新值。
        _cal_mod.CAMPAIGN_CALENDAR.clear()
        _cal_mod.CAMPAIGN_CALENDAR.update(normalized)
        _feat_mod._CAMPAIGN_EVENTS = _feat_mod._build_campaign_events(normalized)

# ============================================================
# 路由规则变更配置
# 历史上只发生过一次正式规则变更，历史规则已无存档
# 策略：只用变更日期之后的数据训练路由层
# ============================================================
ROUTING_RULE_CHANGE_DATE = "2026-01-06"   # 规则正式生效日期（根据实际情况修改）
ROUTING_TRANSITION_DAYS = 7               # 变更前后各N天数据排除（过渡期噪声）

# ============================================================
# 临时调仓时间段
# 大促期间仓库压力过大时的应急分流，结束后恢复原规则
# 这些时段的仓分布不反映正常规则，排除出路由训练数据
# ============================================================
TEMP_ROUTING_OVERRIDES = [
    # {"start": "2024-11-01", "end": "2024-11-15", "reason": "双十一大促仓 A 压力过大"},
    # {"start": "2024-06-01", "end": "2024-06-20", "reason": "618大促应急"},
    # 根据实际情况填入历史记录
]

# ============================================================
# 仓库状态配置
# ============================================================
WAREHOUSE_CONFIG = [
    {
        "warehouse_id": "KM01",
        "name": "昆明仓",
        "is_active": True,
        "open_date": "2022-01-01",
        "close_date": None,
        "priority": 1,       # 库存瀑布优先级（1最高）
    },
    {
        "warehouse_id": "WH01",
        "name": "武汉仓",
        "is_active": True,
        "open_date": "2022-01-01",
        "close_date": None,
        "priority": 2,
    },
    {
        "warehouse_id": "HZ01",
        "name": "湖州仓",
        "is_active": True,
        "open_date": "2022-06-01",
        "close_date": None,
        "priority": 3,
    },
]

# ============================================================
# 店铺-仓库绑定配置
# binding_type: "single" = 全量走固定仓; "multi" = 走路由逻辑
# ============================================================
SHOP_WAREHOUSE_BINDING = [
    {
        "shop_id": "SHOP_TB_01",
        "platform": "taobao",
        "binding_type": "multi",
        "warehouse_id": None,
        "effective_date": "2022-01-01",
    },
    {
        "shop_id": "SHOP_DY_01",
        "platform": "douyin",
        "binding_type": "multi",
        "warehouse_id": None,
        "effective_date": "2022-01-01",
    },
    # 单仓店铺示例
    # {
    #     "shop_id": "SHOP_TB_02",
    #     "platform": "taobao",
    #     "binding_type": "single",
    #     "warehouse_id": "WH_A",
    #     "effective_date": "2022-01-01",
    # },
]

# ============================================================
# 渠道分类配置
# ============================================================
CHANNEL_TYPE_MAP = {
    # 稳定流量型
    "taobao_shelf":    "stable",
    "douyin_shelf":    "stable",
    # 事件驱动型
    "taobao_live":     "event",
    "douyin_live":     "event",     # 自播
    "douyin_anchor":   "event",     # 达播
    # 达人/联盟型
    "taobao_cps":      "affiliate",
    "douyin_union":    "affiliate",  # 精选联盟
}
