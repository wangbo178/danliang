"""
SQLAlchemy 数据库连接与初始化
"""
import os
from pathlib import Path as _EnvPath
from dotenv import load_dotenv

load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/ecom_forecast?charset=utf8mb4')

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 被覆盖索引完全包含的旧索引。仅在新索引创建成功后移除，控制写入放大和磁盘占用。
LEGACY_INDEXES = {
    "channel_orders": {
        "ix_channel_orders_date_channel",
        "ix_channel_orders_upload_batch",
    },
    "warehouse_orders": {
        "ix_warehouse_orders_date_warehouse",
        "ix_warehouse_orders_upload_batch",
    },
    "live_schedules": {"ix_live_schedules_upload_batch"},
    "anchor_live_orders": {"ix_anchor_live_orders_upload_batch"},
    "commissions": {"ix_commissions_upload_batch"},
    "predictions": {"ix_predictions_job_date_channel"},
    "warehouse_predictions": {"ix_warehouse_predictions_job_date_warehouse"},
    "presale_history": {"ix_presale_history_upload_batch"},
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """确保数据库存在并建表"""
    # 先创建库（如不存在）
    root_url = os.environ.get('DATABASE_ROOT_URL', 'mysql+pymysql://root:@localhost:3306/?charset=utf8mb4')
    root_engine = create_engine(root_url)
    with root_engine.connect() as conn:
        conn.execute(text(
            "CREATE DATABASE IF NOT EXISTS ecom_forecast "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
        conn.commit()
    root_engine.dispose()

    from api import models  # noqa: F401 – 触发 Base.metadata 注册
    Base.metadata.create_all(bind=engine)

    # 旧版本没有独立的批次汇总。只为尚未建立汇总的类型做一次历史回填；
    # 之后列表直接读取 upload_batch_logs，不再每次扫描几十万条业务明细。
    batch_sources = {
        "schedule": ("live_schedules", "date"),
        "commission": ("commissions", None),
        "presale": ("presale_history", "payment_date"),
    }
    with engine.begin() as conn:
        for data_type, (table_name, date_column) in batch_sources.items():
            data_start = f"MIN(`{date_column}`)" if date_column else "NULL"
            data_end = f"MAX(`{date_column}`)" if date_column else "NULL"
            conn.execute(text(f"""
                INSERT INTO upload_batch_logs
                    (data_type, batch_id, row_count, data_start, data_end,
                     source_filename, uploaded_at, time_source)
                SELECT :data_type, upload_batch, COUNT(*), {data_start}, {data_end},
                       NULL, MAX(created_at), 'inferred'
                FROM `{table_name}`
                WHERE upload_batch IS NOT NULL
                GROUP BY upload_batch
                ON DUPLICATE KEY UPDATE
                    row_count=VALUES(row_count),
                    data_start=VALUES(data_start),
                    data_end=VALUES(data_end),
                    uploaded_at=IF(time_source='exact', uploaded_at, VALUES(uploaded_at)),
                    time_source=IF(time_source='exact', time_source, VALUES(time_source))
            """), {"data_type": data_type})
            # 批次管理展示当前仍有数据的批次；已被 upsert/同步完全替换的旧批次移除。
            conn.execute(text(f"""
                DELETE log FROM upload_batch_logs AS log
                LEFT JOIN `{table_name}` AS detail
                  ON detail.upload_batch = log.batch_id
                WHERE log.data_type = :data_type AND detail.id IS NULL
            """), {"data_type": data_type})

    # create_all 不会给既有表补建后来新增的索引，显式 checkfirst 保证可重复执行。
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except (OperationalError, ProgrammingError) as exc:
                # 开发环境的 reload 进程可能与当前进程同时建索引；1061 表示另一进程已完成。
                if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1061:
                    raise

    # 新覆盖索引已就绪后，移除其左前缀完全重复的旧索引。
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table_name, legacy_names in LEGACY_INDEXES.items():
            existing_names = {index["name"] for index in inspector.get_indexes(table_name)}
            for index_name in legacy_names & existing_names:
                try:
                    conn.execute(text(f"DROP INDEX `{index_name}` ON `{table_name}`"))
                except (OperationalError, ProgrammingError) as exc:
                    # 1091 表示并发初始化进程已先一步删除。
                    if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1091:
                        raise

    # 兼容旧表：channels 表新增 channel_type 列（已存在则跳过）
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE channels ADD COLUMN channel_type VARCHAR(16) NOT NULL DEFAULT 'stable'"
            ))
            conn.commit()
            print("[DB] channels.channel_type 列已添加。")
        except (OperationalError, ProgrammingError) as exc:
            # MySQL 重复列错误码 1060；其他数据库错误必须暴露，避免启动时静默损坏。
            if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1060:
                raise

    compatibility_columns = {
        "channels": {
            "include_in_forecast": "BOOLEAN NOT NULL DEFAULT TRUE",
            "maintenance_mode": "VARCHAR(16) NOT NULL DEFAULT 'manual'",
        },
        "platforms": {
            "maintenance_mode": "VARCHAR(16) NOT NULL DEFAULT 'manual'",
        },
        "shops": {
            "maintenance_mode": "VARCHAR(16) NOT NULL DEFAULT 'manual'",
        },
        "channel_orders": {
            "payment_amount": "FLOAT NULL",
        },
        "live_schedules": {
            "products": "JSON NULL",
            "sales_amount": "FLOAT NULL",
            "sales_basis": "VARCHAR(16) NULL",
            "expected_orders": "FLOAT NULL",
            "expected_revenue": "FLOAT NULL",
            "session_type": "VARCHAR(32) NULL",
            "platform": "VARCHAR(32) NULL",
            "channel": "VARCHAR(64) NULL",
            "mcn": "VARCHAR(128) NULL",
            "scheduled_at": "DATETIME NULL",
            "source_type": "VARCHAR(32) NULL",
            "source_record_id": "VARCHAR(128) NULL",
        },
        "predictions": {
            "base_predicted_orders": "FLOAT NULL",
            "anchor_predicted_orders": "FLOAT NULL",
        },
    }
    with engine.connect() as conn:
        for table_name, columns in compatibility_columns.items():
            for column_name, column_type in columns.items():
                try:
                    conn.execute(text(
                        f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_type}"
                    ))
                    conn.commit()
                    print(f"[DB] {table_name}.{column_name} 列已添加。")
                except (OperationalError, ProgrammingError) as exc:
                    if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1060:
                        raise

    # 新字段补齐后再创建同步查询索引，兼容从旧数据库原地升级。
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "CREATE INDEX ix_live_schedules_source_time "
                "ON live_schedules (source_type, scheduled_at)"
            ))
            conn.commit()
        except (OperationalError, ProgrammingError) as exc:
            if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1061:
                raise

    # 兼容旧表：live_schedules 表新增 anchor_name 列（已存在则跳过）
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE live_schedules ADD COLUMN anchor_name VARCHAR(128) NULL"
            ))
            conn.commit()
            print("[DB] live_schedules.anchor_name 列已添加。")
        except (OperationalError, ProgrammingError) as exc:
            if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1060:
                raise

    # 评估指标从旧版“把 WAPE 存在 mae 中”升级为明确字段，
    # 同时保留样本数和实际/预测总量，便于正确计算整体 WAPE。
    metric_columns = {
        "wape": "FLOAT NULL",
        "n_samples": "INT NULL",
        "total_actual": "FLOAT NULL",
        "total_predicted": "FLOAT NULL",
        "raw_wape": "FLOAT NULL",
        "raw_mape": "FLOAT NULL",
        "raw_bias": "FLOAT NULL",
        "raw_rmse": "FLOAT NULL",
        "raw_total_predicted": "FLOAT NULL",
    }
    with engine.connect() as conn:
        for column_name, column_type in metric_columns.items():
            try:
                conn.execute(text(
                    f"ALTER TABLE evaluation_metrics ADD COLUMN `{column_name}` {column_type}"
                ))
                conn.commit()
                print(f"[DB] evaluation_metrics.{column_name} 列已添加。")
            except (OperationalError, ProgrammingError) as exc:
                if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1060:
                    raise

    prediction_columns = {
        "raw_predicted_orders": "FLOAT NULL",
        "manual_predicted_orders": "FLOAT NULL",
        "adjustment_note": "VARCHAR(255) NULL",
        "adjusted_at": "DATETIME NULL",
    }
    with engine.connect() as conn:
        for column_name, column_type in prediction_columns.items():
            try:
                conn.execute(text(
                    f"ALTER TABLE predictions ADD COLUMN `{column_name}` {column_type}"
                ))
                conn.commit()
                print(f"[DB] predictions.{column_name} 列已添加。")
            except (OperationalError, ProgrammingError) as exc:
                if getattr(getattr(exc, "orig", None), "args", [None])[0] != 1060:
                    raise

    print(f"[DB] 数据库初始化完成，{len(Base.metadata.tables)} 张表已就绪。")
