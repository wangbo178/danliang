"""
Pydantic 请求/响应模型
"""
from datetime import date, datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, field_validator


# ──────────────────────────────────────────────
# 基础配置
# ──────────────────────────────────────────────

class ChannelBase(BaseModel):
    channel_id: str
    name: Optional[str] = None
    channel_type: Optional[str] = "stable"   # stable / event / affiliate
    include_in_forecast: bool = True
    maintenance_mode: str = "manual"

class ChannelOut(ChannelBase):
    id: int
    platform_id: str
    class Config:
        from_attributes = True

class PlatformBase(BaseModel):
    platform_id: str
    name: str
    maintenance_mode: str = "manual"

class PlatformCreate(PlatformBase):
    channels: Optional[List[ChannelBase]] = []

class PlatformOut(PlatformBase):
    id: int
    channels: List[ChannelOut] = []
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class ShopBase(BaseModel):
    shop_id: str
    name: Optional[str] = None
    platform_id: Optional[str] = None
    campaign_mode: Optional[str] = "auto"
    binding_type: Optional[str] = "multi"
    warehouse_id: Optional[str] = None
    effective_date: Optional[date] = None
    maintenance_mode: str = "manual"

class ShopCreate(ShopBase):
    pass

class ShopOut(ShopBase):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class WarehouseBase(BaseModel):
    warehouse_id: str
    name: Optional[str] = None
    region: Optional[str] = None
    is_active: Optional[bool] = True

class WarehouseCreate(WarehouseBase):
    pass

class WarehouseOut(WarehouseBase):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class CampaignCalendarBase(BaseModel):
    name: str
    start_date: date
    end_date: date
    campaign_type: Optional[str] = None

class CampaignCalendarCreate(CampaignCalendarBase):
    pass

class CampaignCalendarOut(CampaignCalendarBase):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# 配置整体导出（供 ML 核心使用的 config_dict 格式）
# ──────────────────────────────────────────────

class ConfigExport(BaseModel):
    platforms: List[Dict[str, Any]] = []
    shops: List[Dict[str, Any]] = []
    warehouses: List[Dict[str, Any]] = []
    campaign_calendar: List[Dict[str, Any]] = []
    channel_type_map: Dict[str, str] = {}   # {channel_id: "stable"/"event"/"affiliate"}
    excluded_channels: List[Dict[str, str]] = []
    shop_warehouse_binding: List[Dict[str, Any]] = []


# ──────────────────────────────────────────────
# 训练
# ──────────────────────────────────────────────

class TrainingStartRequest(BaseModel):
    phases: List[str] = ["global", "channel", "routing"]
    forecast_days: int = 30
    holdout_days: int = 30
    global_model_params: Optional[Dict[str, Any]] = None
    channel_model_params: Optional[Dict[str, Any]] = None
    training_config: Optional[Dict[str, Any]] = None


class FeishuScheduleConfigUpdate(BaseModel):
    app_id: str
    app_secret: Optional[str] = None  # 留空时保留数据库中的原密钥
    enabled: bool = True
    interval_seconds: int = 900


class FeishuScheduleBindingCreate(BaseModel):
    name: Optional[str] = None
    shop_id: str
    base_token: str
    table_id: str
    enabled: bool = True

class TrainingJobOut(BaseModel):
    job_id: str
    status: str
    progress: int
    phases: Optional[List[str]] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    error_msg: Optional[str] = None
    class Config:
        from_attributes = True

class JobLogOut(BaseModel):
    id: int
    job_id: str
    log_text: Optional[str]
    log_level: str
    created_at: Optional[datetime]
    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# 预测结果
# ──────────────────────────────────────────────

class PredictionOut(BaseModel):
    id: int
    job_id: str
    date: date
    shop_id: str
    channel: str
    platform: Optional[str]
    predicted_orders: Optional[float]
    raw_predicted_orders: Optional[float] = None
    base_predicted_orders: Optional[float] = None
    anchor_predicted_orders: Optional[float] = None
    manual_predicted_orders: Optional[float] = None
    adjustment_note: Optional[str] = None
    adjusted_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class ForecastCellEdit(BaseModel):
    """预测排期表中的单个可编辑单元格。"""
    prediction_id: int
    manual_predicted_orders: Optional[float] = None
    adjustment_note: Optional[str] = None

    @field_validator("manual_predicted_orders")
    @classmethod
    def validate_manual_orders(cls, value):
        if value is not None and value < 0:
            raise ValueError("人工预测单量不能小于 0")
        if value is not None and value > 100_000_000:
            raise ValueError("人工预测单量超出合理范围")
        return value

    @field_validator("adjustment_note")
    @classmethod
    def normalize_note(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None


class ForecastSheetUpdate(BaseModel):
    job_id: str
    edits: List[ForecastCellEdit]

class WarehousePredictionOut(BaseModel):
    id: int
    job_id: str
    date: date
    shop_id: str
    channel: Optional[str]
    warehouse_id: str
    warehouse_orders: Optional[float]
    sample_orders: Optional[float]
    class Config:
        from_attributes = True

class EvaluationMetricOut(BaseModel):
    id: int
    job_id: str
    level: Optional[str]
    group_key: Optional[str]
    mae: Optional[float]
    wape: Optional[float] = None
    rmse: Optional[float]
    mape: Optional[float]
    bias: Optional[float]
    n_samples: Optional[int] = None
    total_actual: Optional[float] = None
    total_predicted: Optional[float] = None
    raw_wape: Optional[float] = None
    raw_mape: Optional[float] = None
    raw_bias: Optional[float] = None
    raw_rmse: Optional[float] = None
    raw_total_predicted: Optional[float] = None
    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# 通用响应
# ──────────────────────────────────────────────

class Msg(BaseModel):
    message: str
    detail: Optional[Any] = None
