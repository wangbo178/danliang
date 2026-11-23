"""
Excel 模板动态生成
根据用户配置的平台/渠道/仓库动态生成数据导入模板
"""

import io
from typing import Dict, List, Optional

import pandas as pd


def _make_excel_bytes(df: pd.DataFrame, sheet_name: str = "数据") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def channel_order_template(cfg: Dict) -> bytes:
    """渠道订单数据模板（含字段说明 Sheet）"""
    channels = []
    platforms = []
    shops = []
    for p in cfg.get("platforms", []):
        platforms.append(p["name"])
        for ch in p.get("channels", []):
            channels.append(ch["name"])
    for s in cfg.get("shops", []):
        shops.append(s.get("name", s["shop_id"]))

    sample_rows = [
        {
            "日期": "2025-01-01",
            "平台": platforms[0] if platforms else "淘宝",
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "渠道": channels[0] if channels else "货架",
            "订单量": 1000,
            "是否派样": 0,
            "是否预售单": 0,
        },
        {
            "日期": "2025-01-01",
            "平台": platforms[0] if platforms else "淘宝",
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "渠道": channels[1] if len(channels) > 1 else "直播",
            "订单量": 500,
            "是否派样": 0,
            "是否预售单": 0,
        },
        {
            "日期": "2025-11-11",
            "平台": platforms[0] if platforms else "淘宝",
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "渠道": channels[1] if len(channels) > 1 else "直播",
            "订单量": 8000,
            "是否派样": 0,
            "是否预售单": 1,
        },
    ]

    notes = pd.DataFrame([
        {"字段名": "日期",       "类型": "日期",  "必填": "是", "说明": "订单日期，格式 YYYY-MM-DD；预售单用尾款支付时间，不是定金时间"},
        {"字段名": "平台",       "类型": "文本",  "必填": "是", "说明": "平台名称，需与基础配置中的平台一致"},
        {"字段名": "店铺ID",     "类型": "文本",  "必填": "是", "说明": "店铺ID，需与基础配置中的店铺一致"},
        {"字段名": "渠道",       "类型": "文本",  "必填": "是", "说明": "渠道名称，需与基础配置中的渠道一致"},
        {"字段名": "订单量",     "类型": "整数",  "必填": "是", "说明": "当日该渠道订单量（件）"},
        {"字段名": "是否派样",   "类型": "整数",  "必填": "否", "说明": "1=派样单（免费赠品），0=正常订单；派样单默认不纳入预测训练"},
        {"字段名": "是否预售单", "类型": "整数",  "必填": "否",
         "说明": "1=大促预售尾款单（直播预售场带来，尾款支付日入仓），0=普通订单；"
                 "该列用于：1)提取预售规模特征 2)样本权重加权(x4.5) 3)评估时单独统计MAPE"},
    ])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(sample_rows).to_excel(writer, index=False, sheet_name="渠道订单")
        notes.to_excel(writer, index=False, sheet_name="字段说明")
    return buf.getvalue()


def warehouse_order_template(cfg: Dict) -> bytes:
    """仓库订单数据模板"""
    warehouses = [w["warehouse_id"] for w in cfg.get("warehouses", [])]
    platforms = [p["name"] for p in cfg.get("platforms", [])]
    channels = []
    for p in cfg.get("platforms", []):
        for ch in p.get("channels", []):
            channels.append(ch["name"])
    shops = [s.get("name", s["shop_id"]) for s in cfg.get("shops", [])]

    sample_rows = [
        {
            "日期": "2025-01-01",
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "平台": platforms[0] if platforms else "淘宝",
            "渠道": channels[0] if channels else "货架",
            "是否派样": 0,
            "仓库ID": warehouses[0] if warehouses else "KM01",
            "订单量": 600,
        },
        {
            "日期": "2025-01-01",
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "平台": platforms[0] if platforms else "淘宝",
            "渠道": channels[0] if channels else "货架",
            "是否派样": 0,
            "仓库ID": warehouses[1] if len(warehouses) > 1 else "WH01",
            "订单量": 400,
        },
    ]
    df = pd.DataFrame(sample_rows)
    return _make_excel_bytes(df, "仓库订单")


def live_schedule_template(cfg: Dict) -> bytes:
    """直播排期模板（含预售场标记）"""
    shops = [s.get("name", s["shop_id"]) for s in cfg.get("shops", [])]
    sample_rows = [
        {
            "店铺ID":       shops[0] if shops else "SHOP_TB_01",
            "日期":         "2025-10-20",
            "平台":         "抖音",
            "渠道":         "达人直播间",
            "主播":         "示例达人A",
            "场次":         "专场",
            "预估目标":   320000,
            "预估单量":   1000,
            "MCN":          "示例MCN",
            "是否直播":     1,
            "主播档级":     "A",
            "预计观看人数": 500000,
            "是否预售场":   1,          # 1=预售专场直播（定金期），0=普通直播
            "预售场日期":   "2025-10-20",  # 该预售场的直播日期（与"日期"同值即可，便于校对）
        },
        {
            "店铺ID":       shops[0] if shops else "SHOP_TB_01",
            "日期":         "2025-10-25",
            "平台":         "抖音",
            "渠道":         "自营直播间",
            "主播":         "示例达人B",
            "场次":         "混场",
            "预估目标":   160000,
            "预估单量":   500,
            "MCN":          "示例MCN",
            "是否直播":     1,
            "主播档级":     "A",
            "预计观看人数": 800000,
            "是否预售场":   0,
            "预售场日期":   "",
        },
        {
            "店铺ID":       shops[0] if shops else "SHOP_TB_01",
            "日期":         "2025-10-28",
            "平台":         "抖音",
            "渠道":         "达人直播间",
            "主播":         "",
            "场次":         "",
            "预估目标":   0,
            "预估单量":   0,
            "MCN":          "",
            "是否直播":     0,
            "主播档级":     "",
            "预计观看人数": 0,
            "是否预售场":   0,
            "预售场日期":   "",
        },
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(sample_rows).to_excel(writer, index=False, sheet_name="直播排期")

        # ── 字段说明 Sheet ─────────────────────────────────────
        notes = pd.DataFrame([
            {"字段名": "店铺ID",       "类型": "文本",   "必填": "是", "说明": "与渠道订单表保持一致"},
            {"字段名": "日期",         "类型": "日期",   "必填": "是", "说明": "直播当天日期，格式 YYYY-MM-DD"},
            {"字段名": "平台",         "类型": "文本",   "必填": "否", "说明": "与飞书排期的平台字段一致"},
            {"字段名": "渠道",         "类型": "文本",   "必填": "建议", "说明": "手动指定直播增量归属的实际渠道；留空时由模型按店铺历史识别"},
            {"字段名": "主播",         "类型": "文本",   "必填": "是", "说明": "须与达人直播实绩中的达人名称一致"},
            {"字段名": "场次",         "类型": "文本",   "必填": "否", "说明": "专场或混场"},
            {"字段名": "预估目标",     "类型": "数值",   "必填": "否", "说明": "排期预估GMV"},
            {"字段名": "预估单量",     "类型": "数值",   "必填": "否", "说明": "飞书原始目标；模型会结合达人历史校准"},
            {"字段名": "MCN",          "类型": "文本",   "必填": "否", "说明": "达人所属MCN"},
            {"字段名": "是否直播",     "类型": "整数",   "必填": "是", "说明": "1=当天有直播，0=无直播"},
            {"字段名": "主播档级",     "类型": "文本",   "必填": "否", "说明": "A=头部主播，B=腰部主播，C=尾部主播，无主播留空"},
            {"字段名": "预计观看人数", "类型": "整数",   "必填": "否", "说明": "预计直播间观看人次（用于流量规模特征）"},
            {"字段名": "是否预售场",   "类型": "整数",   "必填": "否",
             "说明": "1=预售专场直播（大促定金期），0=普通直播；"
                     "模型会专门提取 7~15 天内预售场的最高主播档级作为预测特征"},
            {"字段名": "预售场日期",   "类型": "日期/文本", "必填": "否",
             "说明": "预售场直播发生的日期，通常与[日期]列相同，主要用于人工校对，留空也可"},
        ])
        notes.to_excel(writer, index=False, sheet_name="字段说明")

    return buf.getvalue()


def commission_template(cfg: Dict) -> bytes:
    """联盟佣金模板"""
    shops = [s.get("name", s["shop_id"]) for s in cfg.get("shops", [])]
    sample_rows = [
        {
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "月份": "2025-01",
            "佣金率": 0.15,
            "活跃达人数": 120,
        },
        {
            "店铺ID": shops[0] if shops else "SHOP_TB_01",
            "月份": "2025-02",
            "佣金率": 0.18,
            "活跃达人数": 150,
        },
    ]
    df = pd.DataFrame(sample_rows)
    return _make_excel_bytes(df, "联盟佣金")
