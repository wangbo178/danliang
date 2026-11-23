"""只读查看数据源结构与维度；不输出数据库口令。"""
import json
import sys
from pathlib import Path
import pymysql
from dotenv import dotenv_values

settings = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
connection = pymysql.connect(
    host=settings["DATA_QUERY_DB_HOST"], port=int(settings["DATA_QUERY_DB_PORT"]),
    user=settings["DATA_QUERY_DB_USER"], password=settings["DATA_QUERY_DB_PASSWORD"],
    database=settings["DATA_QUERY_DB_NAME"], charset="utf8mb4",
    connect_timeout=10, read_timeout=30, cursorclass=pymysql.cursors.DictCursor,
)
try:
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        if "--spot" in sys.argv:
            for sql in (
                "SELECT order_status,SUM(order_qty) orders FROM dwh_db_order_summary GROUP BY order_status",
                "SELECT id,shop_name,author_nick_name,author_aweme_id,live_begin_time,live_date,live_order_num FROM ads_dy_shop_live_features WHERE author_aweme_id='04209019l' AND live_begin_time='2026/02/16 20:14:02'",
                "SELECT DISTINCT facility_name FROM ads_ofs_facility_order_daily_summary ORDER BY facility_name",
            ):
                cursor.execute(sql)
                print(json.dumps(cursor.fetchall(), ensure_ascii=False, default=str))
            sys.exit(0)
        if "--profile" in sys.argv:
            queries = {
                "channel_range": "SELECT COUNT(*) n, MIN(order_create_time) first_date, MAX(order_create_time) last_date FROM dwh_db_order_summary",
                "channel_types": "SELECT platform,channel_type,COUNT(*) n FROM dwh_db_order_summary GROUP BY platform,channel_type",
                "order_status": "SELECT order_status,SUM(order_qty) orders FROM dwh_db_order_summary GROUP BY order_status",
                "facilities": "SELECT facility_name, SUM(order_count) orders FROM ads_ofs_facility_order_daily_summary GROUP BY facility_name",
                "warehouse_range": "SELECT COUNT(*) n, MIN(pay_date) first_date,MAX(pay_date) last_date FROM ads_ofs_facility_order_daily_summary",
                "live_grain": "SELECT statistics_date_type,COUNT(*) n, MIN(live_date) first_date,MAX(live_date) last_date FROM ads_dy_shop_live_features GROUP BY statistics_date_type",
                "live_duplicates": "SELECT author_aweme_id,live_begin_time,COUNT(*) n FROM ads_dy_shop_live_features GROUP BY author_aweme_id,live_begin_time HAVING COUNT(*)>1 LIMIT 3",
            }
            for name, sql in queries.items():
                cursor.execute(sql)
                print(json.dumps({name: cursor.fetchall()}, ensure_ascii=False, default=str), flush=True)
            sys.exit(0)
        for table in ("dwh_db_order_summary", "ads_ofs_facility_order_daily_summary", "ads_dy_shop_live_features"):
            cursor.execute(f"SHOW FULL COLUMNS FROM `{table}`")
            columns = cursor.fetchall()
            cursor.execute(f"SELECT * FROM `{table}` LIMIT 2")
            print(json.dumps({"table": table, "columns": columns, "sample": cursor.fetchall()}, ensure_ascii=False, default=str))
finally:
    connection.close()
