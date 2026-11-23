"""数据库中的第三方集成配置，以及敏感字段加密。"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from api import models
from api.database import SessionLocal


NAMESPACE = "feishu_schedule"
DEFAULT_BASE_TOKEN = "BOIlbhJbfamSDjseHobcTcbgnWQ"
DEFAULT_TABLE_ID = "tblP3PXnJIXNuIOB"
SECRET_KEY = "app_secret"
SETTING_KEYS = {"app_id", SECRET_KEY, "base_token", "table_id", "enabled", "interval_seconds"}


def _key_path() -> Path:
    configured = os.getenv("INTEGRATION_SETTINGS_KEY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "data" / ".integration_settings.key"


def _fernet() -> Fernet:
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # 独立于数据库保存；数据库备份本身不能直接还原密钥明文。
        path.write_bytes(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("飞书 App Secret 无法解密，请在前端重新保存") from exc


def _defaults() -> dict:
    return {
        "app_id": os.getenv("FEISHU_APP_ID", "").strip(),
        "app_secret": os.getenv("FEISHU_APP_SECRET", "").strip(),
        "base_token": os.getenv("FEISHU_SCHEDULE_BASE_TOKEN", DEFAULT_BASE_TOKEN).strip(),
        "table_id": os.getenv("FEISHU_SCHEDULE_TABLE_ID", DEFAULT_TABLE_ID).strip(),
        "enabled": os.getenv("FEISHU_SCHEDULE_SYNC_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "interval_seconds": max(60, int(os.getenv("FEISHU_SCHEDULE_SYNC_INTERVAL_SECONDS", "900"))),
    }


def get_feishu_schedule_config(db: Session | None = None) -> dict:
    own_session = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(models.SystemSetting).filter(
            models.SystemSetting.namespace == NAMESPACE
        ).all()
        values = _defaults()
        for row in rows:
            if row.setting_key not in SETTING_KEYS:
                continue
            value = row.setting_value or ""
            if row.setting_key == SECRET_KEY:
                values[row.setting_key] = _decrypt(value)
            elif row.setting_key == "enabled":
                values[row.setting_key] = value.lower() in {"1", "true", "yes", "on"}
            elif row.setting_key == "interval_seconds":
                values[row.setting_key] = max(60, int(value or 900))
            else:
                values[row.setting_key] = value.strip()
        return values
    finally:
        if own_session:
            db.close()


def public_feishu_schedule_config(db: Session | None = None) -> dict:
    config = get_feishu_schedule_config(db)
    app_id = config["app_id"]
    return {
        "app_id": app_id,
        "app_id_masked": f"{app_id[:6]}***{app_id[-4:]}" if len(app_id) > 10 else ("已配置" if app_id else None),
        "app_id_configured": bool(app_id),
        "app_secret_configured": bool(config["app_secret"]),
        "enabled": bool(config["enabled"]),
        "interval_seconds": int(config["interval_seconds"]),
        "storage": "database",
    }


def save_feishu_schedule_config(payload: dict, db: Session) -> dict:
    existing = {
        row.setting_key: row
        for row in db.query(models.SystemSetting).filter(
            models.SystemSetting.namespace == NAMESPACE
        ).all()
    }
    current = get_feishu_schedule_config(db)
    values = {
        "app_id": str(payload["app_id"]).strip(),
        "enabled": "true" if payload.get("enabled", True) else "false",
        "interval_seconds": str(max(60, int(payload.get("interval_seconds", 900)))),
    }
    supplied_secret = str(payload.get("app_secret") or "").strip()
    if supplied_secret:
        values[SECRET_KEY] = _encrypt(supplied_secret)
    elif SECRET_KEY not in existing and current[SECRET_KEY]:
        # 首次切换到数据库配置时迁移旧环境变量中的密钥。
        values[SECRET_KEY] = _encrypt(current[SECRET_KEY])

    for key, value in values.items():
        row = existing.get(key)
        if row is None:
            row = models.SystemSetting(namespace=NAMESPACE, setting_key=key)
            db.add(row)
        row.setting_value = value
        row.is_secret = key == SECRET_KEY
    db.commit()
    return public_feishu_schedule_config(db)
