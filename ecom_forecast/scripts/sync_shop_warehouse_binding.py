"""
从 config/user_config.json 读取 shop_warehouse_binding，
批量更新 MySQL shops 表的 binding_type / warehouse_id / effective_date 三列。
安全：只更新 JSON 中出现的店铺，其他店铺不动。
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import json
import pymysql
from pathlib import Path

DB_CONFIG = dict(
    host="localhost", port=3306,
    user="root", password=os.environ.get("LOCAL_DB_PASSWORD", ""),
    database="ecom_forecast", charset="utf8mb4",
)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "user_config.json"


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    shops = cfg.get("shops", [])

    # 只处理 binding_type=single 或 warehouse_id 有值的行
    bindings = [
        s for s in shops
        if s.get("binding_type") == "single" or s.get("warehouse_id")
    ]
    print(f"从 user_config.json 读取到 {len(bindings)} 条有仓库绑定的店铺")

    conn = pymysql.connect(**DB_CONFIG)
    updated = skipped = 0
    try:
        with conn.cursor() as cur:
            for s in bindings:
                shop_id      = s["shop_id"]
                binding_type = s.get("binding_type", "multi")
                warehouse_id = s.get("warehouse_id")
                effective_date = s.get("effective_date")

                # 检查 DB 中是否有该店铺
                cur.execute("SELECT shop_id FROM shops WHERE shop_id=%s", (shop_id,))
                if not cur.fetchone():
                    print(f"  [SKIP] {shop_id} 不在 shops 表中，跳过")
                    skipped += 1
                    continue

                cur.execute("""
                    UPDATE shops
                       SET binding_type   = %s,
                           warehouse_id   = %s,
                           effective_date = %s
                     WHERE shop_id = %s
                """, (binding_type, warehouse_id, effective_date, shop_id))
                updated += 1
                print(f"  [OK]   {shop_id:12s}  {binding_type:6s}  {warehouse_id or '—':8s}  {effective_date or ''}")

        conn.commit()
    finally:
        conn.close()

    print(f"\n完成：更新 {updated} 条，跳过 {skipped} 条（不在 DB 中的店铺）")


if __name__ == "__main__":
    main()
