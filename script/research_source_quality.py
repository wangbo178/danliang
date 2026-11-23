"""补充字段质量与本地排期覆盖，全部为 SELECT。"""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ecom_forecast"))
from api.services import query_source as source
from api.database import SessionLocal
from sqlalchemy import text

if __name__ == "__main__":
    with source.source_connection() as connection:
        live = source.rows("""SELECT COUNT(*) records,
            SUM(live_order_num=0) zero_order_rows,SUM(live_order_num IS NULL) missing_order_rows,
            COUNT(DISTINCT author_nick_name) nicknames,
            COUNT(DISTINCT CASE WHEN author_aweme_id IS NOT NULL AND TRIM(author_aweme_id)<>'' THEN author_aweme_id END) author_ids
            FROM ads_dy_shop_live_features""",connection=connection)[0]
    with SessionLocal() as db:
        schedule = dict(db.execute(text("""SELECT COUNT(*) records, MIN(date) first_date,MAX(date) last_date,
            SUM(date>='2026-10-29') future_rows,COUNT(DISTINCT shop_id) shops,
            SUM(anchor_name IS NULL OR TRIM(anchor_name)='') missing_anchor,
            SUM(channel IS NULL OR TRIM(channel)='') missing_channel
            FROM live_schedules""")).mappings().one())
        channel_types = [dict(row) for row in db.execute(text("SELECT platform_id,channel_id,name,channel_type,include_in_forecast FROM channels")).mappings()]
        collisions = {}
        for row in channel_types:
            collisions.setdefault(row["channel_id"],{}).setdefault(row["channel_type"],[]).append(row["platform_id"])
        collisions = {key:value for key,value in collisions.items() if len(value)>1}
    result = {"live":live,"schedule":schedule,"channel_type_conflicts":collisions}
    (ROOT/"output/research_20261029/source_quality.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
