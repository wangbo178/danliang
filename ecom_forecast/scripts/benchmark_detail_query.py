r"""验证明细预览的 MySQL 内核执行耗时与索引使用情况。

运行：cd ecom_forecast && ..\.venv\Scripts\python scripts\benchmark_detail_query.py
"""
import re
import statistics
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.database import engine  # noqa: E402


TABLES = {
    "channel_orders": "id,date,shop_id,platform,channel,order_count,is_sample,upload_batch,created_at",
    "warehouse_orders": "id,date,shop_id,platform,channel,warehouse_id,order_count,is_sample,upload_batch,created_at",
    "live_schedules": "id,shop_id,date,event_type,anchor_tier,anchor_name,broadcast_date,expected_viewers,upload_batch,created_at",
    "anchor_live_orders": "id,date,anchor_name,shop_id,order_count,upload_batch,created_at",
    "commissions": "id,shop_id,month,commission_rate,active_affiliates,upload_batch,created_at",
    "presale_history": "id,shop_id,channel,broadcast_date,payment_date,order_count,campaign_type,upload_batch,created_at",
}


def plan_time_ms(plan: str) -> float:
    ranges = re.findall(r"actual time=([0-9.]+)\.\.([0-9.]+)", plan)
    if not ranges:
        raise RuntimeError(f"无法解析 EXPLAIN ANALYZE：{plan}")
    return max(float(end) for _, end in ranges)


def main() -> None:
    failures = []
    with engine.connect() as connection:
        for table, columns in TABLES.items():
            batch = connection.execute(text(
                f"SELECT upload_batch FROM `{table}` "
                "WHERE upload_batch IS NOT NULL ORDER BY id DESC LIMIT 1"
            )).scalar()
            if not batch:
                print(f"[SKIP] {table}: 无带批次数据")
                continue
            sql = text(
                f"EXPLAIN ANALYZE SELECT {columns} FROM `{table}` "
                f"FORCE INDEX (`ix_{table}_batch_id`) "
                "WHERE upload_batch=:batch ORDER BY id DESC LIMIT 51"
            )
            timings = []
            last_plan = ""
            for _ in range(5):
                last_plan = "\n".join(row[0] for row in connection.execute(sql, {"batch": batch}))
                timings.append(plan_time_ms(last_plan))
            median_ms = statistics.median(timings)
            status = "OK" if median_ms <= 1.0 else "FAIL"
            print(f"[{status}] {table}: median={median_ms:.3f}ms batch={batch}")
            if median_ms > 1.0:
                failures.append((table, median_ms, last_plan))
    if failures:
        detail = "\n".join(f"{table}: {ms:.3f}ms\n{plan}" for table, ms, plan in failures)
        raise AssertionError(f"存在超过 1ms 的明细核心 SQL：\n{detail}")


if __name__ == "__main__":
    main()
