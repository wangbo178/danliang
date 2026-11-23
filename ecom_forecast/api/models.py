"""SQLAlchemy ORM 模型。"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date,
    Text, ForeignKey, JSON, UniqueConstraint, Index
)
from api.database import Base


# ──────────────────────────────────────────────
# 基础配置表（5 张）
# ──────────────────────────────────────────────

class Platform(Base):
    __tablename__ = "platforms"
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform_id = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    maintenance_mode = Column(String(16), nullable=False, default="manual")  # manual / auto
    created_at = Column(DateTime, default=datetime.now)


class SystemSetting(Base):
    """服务端集成配置；敏感值以密文保存。"""
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    namespace = Column(String(64), nullable=False)
    setting_key = Column(String(128), nullable=False)
    setting_value = Column(Text, nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("namespace", "setting_key", name="uq_system_setting_namespace_key"),
    )


class UploadBatchLog(Base):
    """独立上传事件日志，不依赖业务明细的首次创建时间。"""
    __tablename__ = "upload_batch_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    data_type = Column(String(32), nullable=False)
    batch_id = Column(String(64), nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    data_start = Column(Date, nullable=True)
    data_end = Column(Date, nullable=True)
    source_filename = Column(String(255), nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.now)
    time_source = Column(String(16), nullable=False, default="exact")  # exact / inferred

    __table_args__ = (
        UniqueConstraint("data_type", "batch_id", name="uq_upload_batch_type_id"),
        Index("ix_upload_batch_logs_type_time", "data_type", "uploaded_at"),
    )


class FeishuScheduleBinding(Base):
    """飞书多维表格与内部店铺的绑定；云表无需再维护 shop_id。"""
    __tablename__ = "feishu_schedule_bindings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=True)
    shop_id = Column(String(64), ForeignKey("shops.shop_id", ondelete="RESTRICT"), nullable=False)
    base_token = Column(String(128), nullable=False)
    table_id = Column(String(128), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Channel(Base):
    __tablename__ = "channels"
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform_id = Column(String(64), ForeignKey("platforms.platform_id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(String(64), nullable=False)
    name = Column(String(128))
    channel_type = Column(String(16), default="stable")  # stable / event / affiliate
    include_in_forecast = Column(Boolean, nullable=False, default=True)
    maintenance_mode = Column(String(16), nullable=False, default="manual")  # manual / auto
    created_at = Column(DateTime, default=datetime.now)
    __table_args__ = (UniqueConstraint("platform_id", "channel_id"),)


class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(String(64), unique=True, nullable=False)
    name = Column(String(128))
    platform_id = Column(String(64), ForeignKey("platforms.platform_id", ondelete="SET NULL"), nullable=True)
    campaign_mode = Column(String(16), default="auto")   # auto / on / off
    binding_type = Column(String(16), default="multi")   # single / multi
    warehouse_id = Column(String(64), ForeignKey("warehouses.warehouse_id", ondelete="SET NULL"), nullable=True)
    effective_date = Column(Date, nullable=True)
    maintenance_mode = Column(String(16), nullable=False, default="manual")  # manual / auto
    created_at = Column(DateTime, default=datetime.now)


class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    warehouse_id = Column(String(64), unique=True, nullable=False)
    name = Column(String(128))
    region = Column(String(64))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class CampaignCalendar(Base):
    __tablename__ = "campaign_calendar"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    campaign_type = Column(String(64))   # 双11 / 618 / 春节 ...
    created_at = Column(DateTime, default=datetime.now)


# ──────────────────────────────────────────────
# 数据表
# ──────────────────────────────────────────────

class ChannelOrder(Base):
    __tablename__ = "channel_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    shop_id = Column(String(64), nullable=False)
    platform = Column(String(64))
    channel = Column(String(64), nullable=False)
    order_count = Column(Integer, default=0)
    payment_amount = Column(Float, nullable=True)
    is_sample = Column(Boolean, default=False)
    upload_batch = Column(String(64))   # 批次标识，便于回溯
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        # 看板按渠道汇总、按日期过滤时直接从索引读取 order_count，避免回表。
        Index("ix_channel_orders_date_channel_orders", "date", "channel", "order_count"),
        Index("ix_channel_orders_channel_orders", "channel", "order_count"),
        Index("ix_channel_orders_shop_date", "shop_id", "date"),
        Index("ix_channel_orders_batch_created", "upload_batch", "created_at"),
        Index("ix_channel_orders_batch_id", "upload_batch", "id"),
        Index("ix_channel_orders_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


class WarehouseOrder(Base):
    __tablename__ = "warehouse_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    shop_id = Column(String(64), nullable=False)
    platform = Column(String(64))
    channel = Column(String(64))
    warehouse_id = Column(String(64), nullable=False)
    order_count = Column(Integer, default=0)
    is_sample = Column(Boolean, default=False)
    upload_batch = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_warehouse_orders_date_warehouse_orders", "date", "warehouse_id", "order_count"),
        Index("ix_warehouse_orders_shop_date", "shop_id", "date"),
        Index("ix_warehouse_orders_batch_created", "upload_batch", "created_at"),
        Index("ix_warehouse_orders_batch_id", "upload_batch", "id"),
        Index("ix_warehouse_orders_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


class LiveSchedule(Base):
    __tablename__ = "live_schedules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(String(64), nullable=False)
    date = Column(Date, nullable=False)          # 普通直播=播出日；预售=尾款日
    event_type = Column(String(32), default="live")  # live | presale_payment
    anchor_tier = Column(String(32))             # A/B/C；不知道可为空
    anchor_name = Column(String(128), nullable=True)  # 达人名称，如 "李佳琦"
    broadcast_date = Column(Date, nullable=True) # 仅 presale_payment 行填写，=定金场播出日
    # 保留旧列，读时忽略
    has_live = Column(Boolean, default=True)
    expected_viewers = Column(Integer)
    expected_orders = Column(Float, nullable=True)
    expected_revenue = Column(Float, nullable=True)
    session_type = Column(String(32), nullable=True)
    platform = Column(String(32), nullable=True)
    channel = Column(String(64), nullable=True)  # 手工指定直播增量归属渠道；为空时由模型识别
    mcn = Column(String(128), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)  # 飞书中的精确开播时间
    source_type = Column(String(32), nullable=True)  # manual | feishu
    source_record_id = Column(String(128), nullable=True)
    products = Column(JSON, nullable=True)
    sales_amount = Column(Float, nullable=True)
    sales_basis = Column(String(16), nullable=True)
    upload_batch = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_live_schedules_shop_date", "shop_id", "date"),
        Index("ix_live_schedules_batch_created", "upload_batch", "created_at"),
        Index("ix_live_schedules_batch_id", "upload_batch", "id"),
        Index("ix_live_schedules_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


class AnchorLiveOrder(Base):
    """达人直播单量历史（每场/每日 × 每位达人的实际带货单量）"""
    __tablename__ = "anchor_live_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)              # 直播日期
    anchor_name = Column(String(128), nullable=False) # 达人名称（须与排期表保持一致）
    shop_id = Column(String(64), nullable=True)       # 店铺ID（多店铺必填）
    order_count = Column(Integer, default=0)           # 该场带货单量
    upload_batch = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "anchor_name", "shop_id", name="uq_anchor_live_order"),
        Index("ix_anchor_live_orders_batch_created", "upload_batch", "created_at"),
        Index("ix_anchor_live_orders_batch_id", "upload_batch", "id"),
        Index("ix_anchor_live_orders_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


class Commission(Base):
    __tablename__ = "commissions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(String(64), nullable=False)
    month = Column(String(8), nullable=False)    # YYYY-MM
    commission_rate = Column(Float)
    active_affiliates = Column(Integer)
    upload_batch = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_commissions_shop_month", "shop_id", "month"),
        Index("ix_commissions_batch_created", "upload_batch", "created_at"),
        Index("ix_commissions_batch_id", "upload_batch", "id"),
        Index("ix_commissions_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


# ──────────────────────────────────────────────
# 训练任务表（2 张）
# ──────────────────────────────────────────────

class TrainingJob(Base):
    __tablename__ = "training_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, nullable=False)
    status = Column(String(32), default="pending")  # pending/running/done/error
    phases = Column(JSON)          # ["global","channel","routing"]
    config_snapshot = Column(JSON) # 训练时配置快照
    progress = Column(Integer, default=0)    # 0-100
    error_msg = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_training_jobs_status_finished", "status", "finished_at"),
        Index("ix_training_jobs_created", "created_at"),
    )


class JobLog(Base):
    __tablename__ = "job_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.job_id", ondelete="CASCADE"), nullable=False)
    log_text = Column(Text)
    log_level = Column(String(16), default="info")  # info/warning/error
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_job_logs_job_created", "job_id", "created_at"),
    )


# ──────────────────────────────────────────────
# 结果表（3 张）
# ──────────────────────────────────────────────

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.job_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    shop_id = Column(String(64), nullable=False)
    channel = Column(String(64), nullable=False)
    platform = Column(String(64))
    predicted_orders = Column(Float)
    raw_predicted_orders = Column(Float)
    base_predicted_orders = Column(Float, nullable=True)
    anchor_predicted_orders = Column(Float, nullable=True)
    # 人工调整单独保存，避免覆盖模型结果，便于随时恢复和审计。
    manual_predicted_orders = Column(Float, nullable=True)
    adjustment_note = Column(String(255), nullable=True)
    adjusted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_predictions_job_date_channel_orders", "job_id", "date", "channel", "predicted_orders"),
        Index("ix_predictions_job_date_shop_channel", "job_id", "date", "shop_id", "channel"),
        Index("ix_predictions_job_shop_date", "job_id", "shop_id", "date"),
        # 可编辑排期表固定按平台/店铺/渠道/日期读取，避免 MySQL 额外 filesort。
        Index(
            "ix_predictions_job_platform_shop_channel_date",
            "job_id", "platform", "shop_id", "channel", "date",
        ),
    )


class WarehousePrediction(Base):
    __tablename__ = "warehouse_predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.job_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    shop_id = Column(String(64), nullable=False)
    channel = Column(String(64))
    warehouse_id = Column(String(64), nullable=False)
    warehouse_orders = Column(Float)
    sample_orders = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_wh_predictions_job_date_wh_orders", "job_id", "date", "warehouse_id", "warehouse_orders"),
        Index("ix_wh_predictions_job_wh_orders", "job_id", "warehouse_id", "warehouse_orders"),
    )


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.job_id", ondelete="CASCADE"), nullable=False)
    level = Column(String(32))      # channel / warehouse
    group_key = Column(String(128)) # 渠道名 / 仓库名
    mae = Column(Float)
    wape = Column(Float)          # 百分数，例如 18.5 表示 18.5%
    rmse = Column(Float)
    mape = Column(Float)
    bias = Column(Float)
    n_samples = Column(Integer)
    total_actual = Column(Float)
    total_predicted = Column(Float)
    raw_wape = Column(Float)
    raw_mape = Column(Float)
    raw_bias = Column(Float)
    raw_rmse = Column(Float)
    raw_total_predicted = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_evaluation_metrics_job_level", "job_id", "level"),
    )


class BacktestDailyResult(Base):
    __tablename__ = "backtest_daily_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.job_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    level = Column(String(32), nullable=False)  # total / warehouse
    group_key = Column(String(128), nullable=False)  # all / warehouse_id
    actual_orders = Column(Float, nullable=False, default=0)
    predicted_orders = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("job_id", "date", "level", "group_key", name="uq_backtest_daily_result"),
        Index("ix_backtest_job_level_date", "job_id", "level", "date"),
        Index("ix_backtest_job_group_date", "job_id", "group_key", "date"),
    )


# ──────────────────────────────────────────────
# 预售历史表（专用，与 channel_orders 分离）
# ──────────────────────────────────────────────

class PresaleHistory(Base):
    __tablename__ = "presale_history"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    shop_id        = Column(String(64), nullable=False)       # 如 CS07
    channel        = Column(String(64), default="直播")       # 默认直播渠道
    broadcast_date = Column(Date, nullable=False)             # 直播时间（定金场）
    payment_date   = Column(Date, nullable=False)             # 尾款时间
    order_count    = Column(Integer, default=0)               # 单量
    campaign_type  = Column(String(32))                       # 自动推断：1111/618/38等
    upload_batch   = Column(String(64))
    created_at     = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("shop_id", "channel", "broadcast_date", "payment_date",
                         name="uq_presale_history"),
        Index("ix_presale_history_payment_channel", "payment_date", "channel"),
        Index("ix_presale_history_batch_created", "upload_batch", "created_at"),
        Index("ix_presale_history_batch_id", "upload_batch", "id"),
        Index("ix_presale_history_batch_shop_id", "upload_batch", "shop_id", "id"),
    )


class AgentWindow(Base):
    __tablename__ = 'operations_agent_windows'
    id = Column(String(32), primary_key=True)
    task_id = Column(String(32), nullable=False, index=True)
    group_id = Column(String(200), nullable=False)
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False, default='reading')
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('task_id','group_id','start_at', name='uq_agent_window_start'),)


class AgentTodo(Base):
    __tablename__ = 'operations_agent_todos'
    id = Column(String(32), primary_key=True)
    task_id = Column(String(32), nullable=False, index=True)
    window_id = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default='needs_info')
    payload = Column(JSON, nullable=False)
    schedule_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AgentQueue(Base):
    __tablename__ = 'operations_agent_queue'
    id = Column(String(32), primary_key=True)
    todo_id = Column(String(32), nullable=False, index=True)
    window_id = Column(String(32), nullable=False, index=True)
    task_id = Column(String(32), nullable=False, index=True)
    parent_id = Column(String(32), nullable=True, unique=True)
    status = Column(String(32), nullable=False, default='pending', index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_until = Column(DateTime, nullable=True)
    owner = Column(String(32), nullable=True)
    system_prompt = Column(Text, nullable=False)
    goal = Column(JSON, nullable=False)
    checkpoint = Column(JSON, nullable=False)
    result = Column(JSON, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AgentOutbox(Base):
    __tablename__ = 'operations_agent_outbox'
    id = Column(String(32), primary_key=True)
    todo_id = Column(String(32), nullable=False, index=True)
    status = Column(String(24), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
