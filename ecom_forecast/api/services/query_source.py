"""远端事实表的统一只读入口；订单不复制到本地，不使用上传批次。"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import lru_cache
import json
import os
from pathlib import Path
import time

import pandas as pd
from dotenv import dotenv_values
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL

KINDS = {"channel", "warehouse", "anchor"}
TABLES = {
    "channel": "dwh_db_order_summary",
    "warehouse": "ads_ofs_facility_order_daily_summary",
    "anchor": "ads_dy_shop_live_features",
}
# 按用户要求优先匹配上海、武汉；其余城市/大区继续按名称归并。
WAREHOUSE_KEYWORDS = (
    "上海", "武汉", "昆明", "湖州", "北京", "天津", "广州", "成都", "杭州",
    "南京", "沈阳", "济南", "福州", "西安", "郑州", "长沙", "嘉兴", "镇江",
    "华东", "华中", "华北", "华南",
)


def logical_warehouse(value):
    value = str(value or "").strip()
    return next((city + "仓" for city in WAREHOUSE_KEYWORDS if city in value), value or "未标注库存地点")


WAREHOUSE_CASE = "CASE " + " ".join(
    f"WHEN facility_name LIKE '%{city}%' THEN '{city}仓'" for city in WAREHOUSE_KEYWORDS
) + " ELSE facility_name END"
CHANNEL_SQL = """SELECT summary_key AS id, DATE(order_create_time) AS date, order_create_time AS source_time,
    shop_code AS shop_id, shop_name, platform,
    COALESCE(NULLIF(TRIM(channel_type), ''), '其他') AS channel,
    channel_name, order_status, order_qty AS order_count,
    order_pay_amount AS payment_amount FROM dwh_db_order_summary"""
# 仅排除用户明确说明的作废订单；其余待处理、发货中、已发货状态均保留。
TRAINING_EXCLUDED_ORDER_STATUSES = ["999"]
CHANNEL_TRAIN_SQL = f"""SELECT date,shop_id,platform,channel,SUM(order_count) order_count,
    SUM(payment_amount) payment_amount FROM ({CHANNEL_SQL}) s
    WHERE COALESCE(TRIM(order_status), '') <> '999'
    GROUP BY date,shop_id,platform,channel"""
WAREHOUSE_SQL = f"""SELECT pay_date AS date, facility_name,
    {WAREHOUSE_CASE} AS warehouse_id, order_count
    FROM ads_ofs_facility_order_daily_summary"""
ANCHOR_SQL = """SELECT id, live_date AS date, author_nick_name AS anchor_name,
    author_aweme_id AS anchor_id, shop_name AS source_shop_name, shop_id AS source_shop_id,
    live_room_id, live_begin_time, live_end_time, statistics_date_type,
    live_order_num AS order_count, live_order_amount AS payment_amount,
    live_viewers_num AS viewers, live_duration, net_transaction_order_num,
    live_order_refund_num FROM ads_dy_shop_live_features"""
ANCHOR_TRAIN_SQL = """SELECT date,anchor_name,SUM(order_count) order_count FROM (
    SELECT live_date date,TRIM(author_nick_name) anchor_name,live_order_num order_count,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(NULLIF(TRIM(author_aweme_id),''),TRIM(author_nick_name)),
                COALESCE(NULLIF(TRIM(shop_id),''),COALESCE(TRIM(shop_name),'')),
                COALESCE(STR_TO_DATE(REPLACE(live_begin_time,'/','-'),'%Y-%m-%d %H:%i:%s'),CAST(live_date AS DATETIME))
            ORDER BY id DESC
        ) AS session_version
    FROM ads_dy_shop_live_features
    WHERE live_date IS NOT NULL AND TRIM(COALESCE(author_nick_name,''))<>''
        AND live_order_num IS NOT NULL AND live_order_num >= 0
) sessions WHERE session_version=1 GROUP BY date,anchor_name"""
SOURCE_SQL = {"channel": CHANNEL_SQL, "warehouse": WAREHOUSE_SQL, "anchor": ANCHOR_SQL}


@lru_cache(maxsize=1)
def source_engine():
    values = {**dotenv_values(Path(__file__).resolve().parents[3] / ".env"), **os.environ}
    required = ("HOST", "USER", "PASSWORD", "NAME")
    if any(not values.get("DATA_QUERY_DB_" + key) for key in required):
        raise HTTPException(503, "尚未配置只读数据源，请在项目 .env 中配置 DATA_QUERY_DB_*。")
    engine = create_engine(URL.create(
        "mysql+pymysql", username=values["DATA_QUERY_DB_USER"], password=values["DATA_QUERY_DB_PASSWORD"],
        host=values["DATA_QUERY_DB_HOST"], port=int(values.get("DATA_QUERY_DB_PORT", 3306)),
        database=values["DATA_QUERY_DB_NAME"], query={"charset": "utf8mb4"},
    ), isolation_level="REPEATABLE READ", pool_pre_ping=True, pool_recycle=1800, pool_size=4, max_overflow=4,
        connect_args={"connect_timeout": 10, "read_timeout": 90, "write_timeout": 10})

    @event.listens_for(engine, "connect")
    def read_only(connection, record):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")

    return engine


@contextmanager
def source_connection():
    try:
        with source_engine().connect() as connection:
            yield connection
    except HTTPException:
        raise
    except Exception as exc:
        # 连接详情与底层 SQL 不返回浏览器。
        raise HTTPException(503, "只读数据源查询失败，请检查网络、账号权限和 DATA_QUERY_DB_NAME 配置。") from exc


def rows(sql, params=None, connection=None):
    if connection is not None:
        return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]
    with source_connection() as connection:
        return rows(sql, params, connection)


def _frame(sql, connection):
    return pd.read_sql_query(text(sql), connection)


def prepare_anchor_actuals(frame):
    """同源店铺×主播账号×真实场次去重；只按主播建模，不认源店铺为系统店铺。"""
    if frame.empty:
        return pd.DataFrame(columns=["date", "anchor_name", "order_count"])
    frame = frame.copy()
    frame["anchor_name"] = frame["anchor_name"].astype("string").str.strip().replace("", pd.NA)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["order_count"] = pd.to_numeric(frame["order_count"], errors="coerce")
    frame = frame.dropna(subset=["date", "anchor_name", "order_count"])
    frame = frame[frame["order_count"].ge(0)].copy()
    frame["_identity"] = frame["anchor_id"].fillna("").astype(str).str.strip()
    frame.loc[frame["_identity"].eq(""), "_identity"] = frame["anchor_name"]
    frame["_shop"] = frame["source_shop_id"].fillna("").astype(str).str.strip()
    missing_shop = frame["_shop"].eq("")
    frame.loc[missing_shop, "_shop"] = frame.loc[missing_shop, "source_shop_name"].fillna("")
    frame["_session"] = pd.to_datetime(frame["live_begin_time"], errors="coerce", format="mixed")
    # 没有开播时间时按日粒度去重，避免相同日汇总被重复累计。
    frame["_session"] = frame["_session"].fillna(frame["date"])
    frame = frame.sort_values("id").drop_duplicates(["_identity", "_shop", "_session"], keep="last")
    return frame.groupby(["date", "anchor_name"], as_index=False)["order_count"].sum()


def load_training_frames():
    # 同一 REPEATABLE READ 事务内取三张表，单次训练保留一致的数据快照。
    with source_connection() as connection:
        channel = _frame(CHANNEL_TRAIN_SQL, connection)
        warehouse = _frame(f"SELECT date,warehouse_id,SUM(order_count) order_count FROM ({WAREHOUSE_SQL}) s GROUP BY date,warehouse_id", connection)
        # 只传输去重后的主播日实绩，避免将大体量特征明细全量拉入桌面内存。
        anchor = _frame(ANCHOR_TRAIN_SQL, connection)
    channel["date"] = pd.to_datetime(channel["date"])
    warehouse["date"] = pd.to_datetime(warehouse["date"])
    anchor["date"] = pd.to_datetime(anchor["date"])
    channel["is_sample"] = 0
    # 源表缺少店铺维度，仅用于整体路由比例和仓级评估。
    warehouse["shop_id"] = ""
    warehouse["channel"] = ""
    warehouse["platform"] = ""
    warehouse["is_sample"] = 0
    warehouse.attrs["routing_scope"] = "global"
    return {"channel_df": channel, "warehouse_df": warehouse, "anchor_df": anchor,
            "channel_batch_id": None, "warehouse_batch_id": None,
            "source_read_at": datetime.now().isoformat(timespec="seconds")}


def source_overview():
    with source_connection() as connection:
        ch = rows(f"SELECT COUNT(*) n, MIN(date) first_date,MAX(date) last_date FROM ({CHANNEL_SQL}) s", connection=connection)[0]
        wh = rows(f"SELECT COUNT(*) n, MIN(date) first_date,MAX(date) last_date FROM ({WAREHOUSE_SQL}) s", connection=connection)[0]
        an = rows(f"SELECT COUNT(*) n, MIN(date) first_date,MAX(date) last_date FROM ({ANCHOR_SQL}) s", connection=connection)[0]
        stats = rows(f"SELECT channel,COUNT(*) record_count,SUM(order_count) total_orders FROM ({CHANNEL_SQL}) s GROUP BY channel", connection=connection)
        facilities = rows(f"SELECT DISTINCT facility_name,warehouse_id FROM ({WAREHOUSE_SQL}) s ORDER BY warehouse_id,facility_name", connection=connection)
    return {
        "channel_orders": ch["n"], "warehouse_orders": wh["n"], "anchor_live_orders": an["n"],
        "channel_batch_id": None, "channel_stats": stats,
        "date_range": {"min": ch["first_date"], "max": ch["last_date"]},
        "source_mode": "direct", "queried_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [{"kind": kind, "table": TABLES[kind], "count": item["n"],
                     "date_start": item["first_date"], "date_end": item["last_date"]}
                    for kind, item in (("channel", ch), ("warehouse", wh), ("anchor", an))],
        "warehouse_mapping": facilities, "routing_scope": "global", "anchor_scope": "anchor_only",
    }


def _date_bounds(connection, kind, start, end, recent_days=None, conditions="", params=None):
    span = rows(f"SELECT MIN(date) first_date,MAX(date) last_date FROM ({SOURCE_SQL[kind]}) s {conditions}", params, connection)[0]
    if recent_days and not start and not end and span["last_date"]:
        end = span["last_date"]
        start = end - timedelta(days=recent_days - 1)
    return start, end, {"min": span["first_date"], "max": span["last_date"]}


def warehouse_daily(start=None, end=None, recent_days=None):
    with source_connection() as connection:
        start, end, span = _date_bounds(connection, "warehouse", start, end, recent_days)
        conditions, params = _conditions("warehouse", date_start=start, date_end=end)
        data = rows(f"SELECT date,warehouse_id,SUM(order_count) total FROM ({WAREHOUSE_SQL}) s {conditions} GROUP BY date,warehouse_id ORDER BY date,warehouse_id", params, connection)
    return _warehouse_series(data, start, end, span)


def _warehouse_series(data, start=None, end=None, span=None):
    def as_date(value):
        return date.fromisoformat(str(value)[:10]) if value else None
    first = as_date(start) or (data[0]["date"] if data else None)
    last = as_date(end) or (data[-1]["date"] if data else None)
    dates = [str(first + timedelta(days=i)) for i in range((last - first).days + 1)] if first and last else []
    warehouses = sorted({row["warehouse_id"] for row in data})
    lookup = {(str(row["date"]), row["warehouse_id"]): int(row["total"] or 0) for row in data}
    series = {wh: [lookup.get((d, wh), 0) for d in dates] for wh in warehouses}
    return {"dates": dates, "warehouses": warehouses, "series": series,
            "totals": [sum(series[wh][i] for wh in warehouses) for i in range(len(dates))],
            "date_range": span or {}, "batch_id": None, "source_mode": "direct"}


def aov_daily(start=None, end=None, recent_days=7, shop_id=None, channel=None):
    conditions, params = _conditions("channel", shop_id=shop_id, channel=channel)
    conditions += (" AND " if conditions else " WHERE ") + "payment_amount IS NOT NULL"
    with source_connection() as connection:
        start, end, span = _date_bounds(connection, "channel", start, end, recent_days, conditions, params)
        filters, dated_params = _conditions("channel", shop_id=shop_id, channel=channel, date_start=start, date_end=end)
        filters += (" AND " if filters else " WHERE ") + "payment_amount IS NOT NULL"
        data = rows(f"SELECT date,SUM(order_count) order_count,SUM(payment_amount) payment_amount FROM ({CHANNEL_SQL}) s {filters} GROUP BY date ORDER BY date", dated_params, connection)
        shops = rows("SELECT shop_code shop_id,MAX(shop_name) name FROM dwh_db_order_summary GROUP BY shop_code ORDER BY shop_code", connection=connection)
        channels = rows(f"SELECT DISTINCT channel FROM ({CHANNEL_SQL}) s ORDER BY channel", connection=connection)
    series = []
    for row in data:
        orders, amount = int(row["order_count"] or 0), float(row["payment_amount"] or 0)
        series.append({"date": str(row["date"]), "order_count": orders, "payment_amount": round(amount, 2),
                       "avg_order_value": round(amount / orders, 2) if orders else None})
    total_orders = sum(item["order_count"] for item in series)
    total_amount = sum(item["payment_amount"] for item in series)
    return {"series": series, "total_orders": total_orders, "total_amount": round(total_amount, 2),
            "average_aov": round(total_amount / total_orders, 2) if total_orders else None,
            "filters": {"shops": shops, "channels": [row["channel"] for row in channels]},
            "date_range": span, "batch_id": None, "source_mode": "direct"}


def _conditions(kind, shop_id=None, channel=None, date_start=None, date_end=None, anchor_name=None):
    predicates, params = [], {}
    for key, value, operator in (("date_start", date_start, ">="), ("date_end", date_end, "<=")):
        if value:
            if kind == "channel":
                predicates.append(f"source_time >= :{key}" if key == "date_start" else f"source_time < DATE_ADD(:{key}, INTERVAL 1 DAY)")
            else:
                predicates.append(f"date {operator} :{key}")
            params[key] = value
    if shop_id and kind == "channel":
        predicates.append("shop_id = :shop_id")
        params["shop_id"] = shop_id
    if channel and kind == "channel":
        predicates.append("channel = :channel")
        params["channel"] = channel
    if anchor_name and kind == "anchor":
        predicates.append("anchor_name = :anchor_name")
        params["anchor_name"] = anchor_name
    return ("WHERE " + " AND ".join(predicates) if predicates else ""), params


def detail_records(kind, after_id=None, page_size=50, **filters):
    conditions, params = _conditions(kind, **filters)
    order = {"channel": "source_time DESC,id DESC", "warehouse": "date DESC,facility_name DESC", "anchor": "id DESC"}[kind]
    if after_id:
        if kind == "warehouse":
            try:
                cursor = json.loads(base64.urlsafe_b64decode(after_id).decode())
                cursor_date = date.fromisoformat(cursor[0])
                facility = cursor[1]
                if not isinstance(facility, str):
                    raise ValueError()
            except Exception as exc:
                raise HTTPException(400, "仓库明细游标无效，请重新查询。") from exc
            predicate = "(date < :cursor_date OR (date = :cursor_date AND facility_name < :facility))"
            params.update(cursor_date=cursor_date, facility=facility)
        elif kind == "channel":
            try:
                cursor_time, cursor_id = json.loads(base64.urlsafe_b64decode(after_id).decode())
                datetime.fromisoformat(cursor_time)
                if not isinstance(cursor_id, str) or len(cursor_id) != 64:
                    raise ValueError()
            except Exception as exc:
                raise HTTPException(400, "渠道明细游标无效，请重新查询。") from exc
            predicate = "(source_time < :cursor_time OR (source_time = :cursor_time AND id < :cursor_id))"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        else:
            if kind == "anchor" and not str(after_id).isdigit():
                raise HTTPException(400, "主播明细游标无效。")
            predicate = "id < :cursor_id"
            params["cursor_id"] = int(after_id) if kind == "anchor" else str(after_id)
        conditions += (" AND " if conditions else " WHERE ") + predicate
    params["limit"] = page_size + 1
    started = time.perf_counter()
    data = rows(f"SELECT * FROM ({SOURCE_SQL[kind]}) s {conditions} ORDER BY {order} LIMIT :limit", params)
    elapsed = (time.perf_counter() - started) * 1000
    has_more = len(data) > page_size
    data = data[:page_size]
    next_cursor = None
    if has_more and data:
        last = data[-1]
        if kind == "anchor":
            next_cursor = str(last["id"])
        else:
            parts = [str(last["date"]), last["facility_name"]] if kind == "warehouse" else [str(last["source_time"]), last["id"]]
            next_cursor = base64.urlsafe_b64encode(json.dumps(parts, ensure_ascii=False).encode()).decode()
    return {"records": data, "columns": list(data[0]) if data else [], "has_more": has_more,
            "next_cursor": next_cursor, "query_ms": elapsed, "source_mode": "direct",
            "source_table": TABLES[kind], "index_strategy": "source primary-key cursor"}


def record_options(kind):
    shops = rows("SELECT shop_code shop_id,MAX(shop_name) name FROM dwh_db_order_summary GROUP BY shop_code ORDER BY shop_code") if kind == "channel" else []
    return {"shops": shops, "source_mode": "direct"}


def dimension_records():
    return rows(f"SELECT shop_id,MAX(shop_name) shop_name,platform,channel FROM ({CHANNEL_SQL}) s GROUP BY shop_id,platform,channel")


def logical_warehouses():
    return [row["warehouse_id"] for row in rows(f"SELECT DISTINCT warehouse_id FROM ({WAREHOUSE_SQL}) s ORDER BY warehouse_id")]


def ensure_dimensions(db):
    """缺失的维度增量登记到本地；保留已有人工配置，绝不写远端事实库。"""
    from api import models
    from api.routers.data import _ensure_channel_config

    records = dimension_records()
    _ensure_channel_config(db, records)
    existing = {warehouse.warehouse_id for warehouse in db.query(models.Warehouse).all()}
    for name in logical_warehouses():
        if name not in existing:
            db.add(models.Warehouse(warehouse_id=name, name=name,
                                   region=name.removesuffix("仓"), is_active=True))
    db.commit()


def effective_routing_config(config):
    """用实际逻辑仓替换旧 Excel 仓号；不伪造源表中不存在的店铺级路由。"""
    config = dict(config)
    names = logical_warehouses()
    old = {row["warehouse_id"]: row for row in config.get("warehouses", [])}
    config["warehouses"] = [
        {"warehouse_id": name, "name": name, "region": name.removesuffix("仓"),
         "is_active": bool(old.get(name, {}).get("is_active", True))} for name in names
    ]
    bindings = []
    for binding in config.get("shop_warehouse_binding", []):
        binding = dict(binding)
        previous = binding.get("warehouse_id")
        mapped = logical_warehouse(old.get(previous, {}).get("name", previous)) if previous else None
        if mapped in names:
            binding["warehouse_id"] = mapped
        elif binding.get("binding_type") == "single":
            binding.update(binding_type="multi", warehouse_id=None)
        bindings.append(binding)
    config["shop_warehouse_binding"] = bindings
    config["source_mode"] = "direct"
    return config
