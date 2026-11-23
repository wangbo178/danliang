"""
渠道别名映射模块（从旧版数据管理页面提取）
供 data router 直接调用，与 UI 框架无关。
"""
import pandas as pd
from typing import Dict, List, Tuple


# ── 平台同义词表 ────────────────────────────────────────────────────────────
_PLATFORM_SYNONYMS: Dict[str, List[str]] = {
    "taobao":  ["天猫", "tmall", "淘宝天猫"],
    "douyin":  ["抖音", "dy", "tiktok"],
    "pdd":     ["拼多多", "pinduoduo"],
    "jd":      ["京东", "jingdong"],
    "ks":      ["快手", "kuaishou"],
    "wechat":  ["微信", "视频号", "微信小程序"],
    "xiaohongshu": ["小红书", "xhs", "红书"],
}

# ── 通用渠道中文别名（仅在用户未配置任何平台时兜底）─────────────────────────
_CHANNEL_COMMON_ALIAS: Dict[str, str] = {
    "抖音货架": "douyin_shelf", "抖音搜索": "douyin_shelf", "抖音商城": "douyin_shelf",
    "抖音自播": "douyin_live",  "自播": "douyin_live",
    "抖音达播": "douyin_anchor", "达播": "douyin_anchor", "达人带货": "douyin_anchor",
    "抖音联盟": "douyin_union", "精选联盟": "douyin_union",
    "淘宝货架": "taobao_shelf", "天猫货架": "taobao_shelf",
    "淘宝搜索": "taobao_shelf", "天猫搜索": "taobao_shelf",
    "淘宝直播": "taobao_live",  "天猫直播": "taobao_live",
    "淘宝联盟": "taobao_cps",   "天猫联盟": "taobao_cps",   "淘客": "taobao_cps",
    "拼多多货架": "pdd_shelf",   "pdd货架": "pdd_shelf",
    "快手直播": "ks_live",       "快手货架": "ks_shelf",
    "京东货架": "jd_shelf",      "京东直播": "jd_live",
}

# 中文→英文标准列名映射
COMMON_COL_ALIAS: Dict[str, str] = {
    "日期": "date", "订单汇总时间": "date", "date": "date",
    "平台": "platform", "platform": "platform",
    "店铺id": "shop_id", "店铺ID": "shop_id", "shop_id": "shop_id",
    "店铺编码": "shop_id", "店铺": "shop_id",
    "店铺名称": "shop_name", "店铺名": "shop_name", "shop_name": "shop_name",
    "渠道": "channel", "渠道分类": "channel", "channel": "channel",
    "订单量": "order_count", "订单数量": "order_count", "order_count": "order_count", "单量": "order_count",
    "付款金额": "payment_amount", "订单付款金额": "payment_amount",
    "payment_amount": "payment_amount",
    "是否派样": "is_sample", "is_sample": "is_sample",
    "仓库id": "warehouse_id", "仓库ID": "warehouse_id", "warehouse_id": "warehouse_id",
    "是否直播": "has_live", "has_live": "has_live",
    "主播档级": "anchor_tier", "anchor_tier": "anchor_tier",
    "达人名称": "anchor_name", "主播名称": "anchor_name", "主播": "anchor_name",
    "anchor_name": "anchor_name", "达人": "anchor_name",
    "预计观看人数": "expected_viewers", "expected_viewers": "expected_viewers",
    "预估单量": "expected_orders", "目标单量": "expected_orders",
    "expected_orders": "expected_orders",
    "预估目标": "expected_revenue", "目标gmv": "expected_revenue",
    "预估gmv": "expected_revenue", "预计gmv": "expected_revenue",
    "expected_revenue": "expected_revenue",
    "场次": "session_type", "场次类型": "session_type", "session_type": "session_type",
    "事件类型": "event_type", "event_type": "event_type",
    "对应直播日期": "broadcast_date", "预售场日期": "broadcast_date",
    "开播时间": "scheduled_at", "排期时间": "scheduled_at", "scheduled_at": "scheduled_at",
    "mcn": "mcn", "MCN": "mcn",
    "月份": "month", "month": "month",
    "佣金率": "commission_rate", "commission_rate": "commission_rate",
    "活跃达人数": "active_affiliates", "active_affiliates": "active_affiliates",
    # ── 预售历史数据列名（预售直播数据.xlsx）────────────────────────────────
    "直播时间": "broadcast_date", "broadcast_date": "broadcast_date",
    "尾款时间": "payment_date",   "payment_date": "payment_date",
    "是否预售单": "is_presale_order", "is_presale_order": "is_presale_order",
}


def build_ch_alias_map(cfg: dict, *, include_common_aliases: bool = False) -> Dict[str, str]:
    """
    从配置 dict 构建渠道别名 → channel_id 映射（大小写不敏感）。
    用户配置的 channel_id/name 是最终权威。通用别名只在明确
    传入 include_common_aliases=True 时启用，避免将 Excel 已有的渠道原文
    （例如“达播”）强制改写成历史内部 ID。
    """
    m: Dict[str, str] = {}
    platforms = cfg.get("platforms", [])
    for p in platforms:
        pid = (p.get("platform_id") or "").lower()
        pname = (p.get("name") or "").lower()
        extra = [s.lower() for s in _PLATFORM_SYNONYMS.get(pid, [])]
        all_prefixes = list(dict.fromkeys(filter(None, [pid, pname] + extra)))
        for ch in p.get("channels", []):
            cid = ch["channel_id"]
            cname = (ch.get("name") or "").lower()
            m[cid.lower()] = cid
            if cname:
                m[cname] = cid
                for prefix in all_prefixes:
                    m[f"{prefix}{cname}"] = cid
                    m[f"{prefix}_{cname}"] = cid
                    m[f"{prefix} {cname}"] = cid
    if include_common_aliases and not platforms:
        for k, v in _CHANNEL_COMMON_ALIAS.items():
            m.setdefault(k.lower(), v)
    return m


def auto_map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将中文列名映射为标准英文列名"""
    rename = {}
    for col in df.columns:
        key = col.strip().lower()
        for alias, std in COMMON_COL_ALIAS.items():
            if key == alias.lower():
                rename[col] = std
                break
    mapped = df.rename(columns=rename)
    # 兼容旧模板：只有“店铺名称”时仍可把名称作为店铺标识；若同时有店铺编码，
    # 则保留 shop_name 供自动建档展示，shop_id 使用编码。
    if "shop_name" in mapped.columns and "shop_id" not in mapped.columns:
        mapped["shop_id"] = mapped["shop_name"]
    return mapped


def resolve_shop_id(df: pd.DataFrame, cfg: dict) -> Tuple[pd.DataFrame, int]:
    """将 shop_id 列中的店铺名称替换为对应 shop_id"""
    if "shop_id" not in df.columns:
        return df, 0
    shops = cfg.get("shops", [])
    if not shops:
        return df, 0
    name_to_id = {s["name"].strip().lower(): s["shop_id"] for s in shops if s.get("name")}
    known_ids = {s["shop_id"] for s in shops}

    df = df.copy()
    cleaned = df["shop_id"].astype(str).str.strip()
    mapped = cleaned.str.lower().map(name_to_id)
    converted_mask = ~cleaned.isin(known_ids) & mapped.notna()
    df["shop_id"] = cleaned.where(~converted_mask, mapped)
    converted = int(converted_mask.sum())
    return df, converted


def auto_map_channel(df: pd.DataFrame, cfg: dict) -> Tuple[pd.DataFrame, List[Tuple[str, List[str]]]]:
    """
    自动将 channel 列映射为标准 channel_id。
    返回：(df, need_manual)
      need_manual: [(原始值/平台id, 候选channel_id列表), ...]
    """
    # Excel 同时提供了平台和渠道时，渠道列是导入数据的权威原值；
    # 只有缺失平台列的旧模板，才使用历史通用别名做兼容。
    alias_map = build_ch_alias_map(
        cfg, include_common_aliases="platform" not in df.columns
    )
    valid_ids = {
        ch["channel_id"]
        for p in cfg.get("platforms", [])
        for ch in p.get("channels", [])
    }
    plat_ch_map: Dict[str, List[str]] = {
        p["platform_id"]: [ch["channel_id"] for ch in p.get("channels", [])]
        for p in cfg.get("platforms", [])
    }

    # (平台标识, 渠道名) → channel_id 直查表
    plat_ch_direct: Dict[Tuple[str, str], str] = {}
    plat_norm: Dict[str, str] = {}
    for p in cfg.get("platforms", []):
        pid   = (p.get("platform_id") or "").strip()
        pname = (p.get("name") or "").strip()
        if pid:
            plat_norm[pid.lower()] = pid
        if pname:
            plat_norm[pname.lower()] = pid
        for ch in p.get("channels", []):
            cid   = ch["channel_id"]
            cname = (ch.get("name") or "").strip().lower()
            if not cname:
                continue
            for pk in filter(None, [pid.lower(), pname.lower()]):
                plat_ch_direct[(pk, cname)] = cid

    df = df.copy()
    if "platform" in df.columns:
        cleaned_platform = df["platform"].astype(str).str.strip()
        normalized_platform = cleaned_platform.str.lower().map(plat_norm)
        df["platform"] = normalized_platform.fillna(cleaned_platform)

    if "channel" in df.columns:
        has_plat = "platform" in df.columns

        channel_names = df["channel"].astype(str).str.strip()
        channel_keys = channel_names.str.lower()
        if has_plat:
            platform_keys = df["platform"].astype(str).str.strip().str.lower()
            df["channel"] = [
                plat_ch_direct.get((platform_key, channel_key))
                or alias_map.get(channel_key, channel_name)
                for platform_key, channel_key, channel_name
                in zip(platform_keys, channel_keys, channel_names)
            ]
        else:
            df["channel"] = [
                alias_map.get(channel_key, channel_name)
                for channel_key, channel_name in zip(channel_keys, channel_names)
            ]

        if not valid_ids:
            return df, []

        unmapped_vals = sorted(set(df["channel"].dropna().unique()) - valid_ids)
        need_manual = []
        for val in unmapped_vals:
            plats = df.loc[df["channel"] == val, "platform"].dropna().unique() if "platform" in df.columns else []
            candidates: List[str] = []
            for pl in plats:
                candidates.extend(plat_ch_map.get(pl, []))
            need_manual.append((val, sorted(set(candidates)) or sorted(valid_ids)))
        return df, need_manual

    # channel 列缺失：从 platform 推导
    if "platform" not in df.columns:
        return df, []

    df["channel"] = ""
    need_manual = []
    for plat in df["platform"].dropna().unique():
        chs = plat_ch_map.get(plat, [])
        mask = df["platform"] == plat
        if len(chs) == 1:
            df.loc[mask, "channel"] = chs[0]
        else:
            need_manual.append((plat, chs or sorted(valid_ids)))

    return df, need_manual


def get_channel_source(ch_val: str, cfg: dict) -> str:
    """返回 channel 值的来源：用户配置 / 通用别名 / 未识别"""
    valid_ids = {
        ch["channel_id"]
        for p in cfg.get("platforms", [])
        for ch in p.get("channels", [])
    }
    common_ids = set(_CHANNEL_COMMON_ALIAS.values())
    if ch_val in valid_ids:
        return "用户配置"
    elif ch_val in common_ids:
        return "通用别名"
    return "未识别"
