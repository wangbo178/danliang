"""
一次性迁移脚本：为 shops 表新增 binding_type / warehouse_id / effective_date 三列。
MySQL 8.0 支持 IF NOT EXISTS 语法，可安全重复执行。
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

import pymysql

DB_CONFIG = dict(
    host="localhost",
    port=3306,
    user="root",
    password=os.environ.get("LOCAL_DB_PASSWORD", ""),
    database="ecom_forecast",
    charset="utf8mb4",
)

COLUMNS = [
    ("binding_type",   "ALTER TABLE shops ADD COLUMN binding_type   VARCHAR(16) NOT NULL DEFAULT 'multi' COMMENT 'single=指定单仓, multi=多仓路由'"),
    ("warehouse_id",   "ALTER TABLE shops ADD COLUMN warehouse_id   VARCHAR(64) NULL COMMENT '指定仓库 ID（binding_type=single 时生效）'"),
    ("effective_date", "ALTER TABLE shops ADD COLUMN effective_date  DATE        NULL COMMENT '绑定生效日期'"),
]

def column_exists(cur, col_name: str) -> bool:
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='ecom_forecast' AND TABLE_NAME='shops' AND COLUMN_NAME=%s
    """, (col_name,))
    return cur.fetchone()[0] > 0

if __name__ == "__main__":
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            for col_name, ddl in COLUMNS:
                if column_exists(cur, col_name):
                    print(f"[SKIP] 列 {col_name} 已存在")
                else:
                    cur.execute(ddl)
                    print(f"[OK]   列 {col_name} 已添加")
        conn.commit()
        print("\nALTER TABLE shops 执行完毕")

        # 验证列已存在
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = 'ecom_forecast'
                  AND TABLE_NAME   = 'shops'
                  AND COLUMN_NAME IN ('binding_type','warehouse_id','effective_date')
                ORDER BY ORDINAL_POSITION;
            """)
            rows = cur.fetchall()
        print("\n新增列信息：")
        for r in rows:
            print(f"  {r[0]:20s}  {r[1]:20s}  nullable={r[2]}  default={r[3]}")
    finally:
        conn.close()
