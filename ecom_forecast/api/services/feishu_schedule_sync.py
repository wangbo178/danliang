"""将飞书多维表格中的未来直播排期镜像到数据库。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func

from api import models
from api.database import SessionLocal
from api.services.integration_settings import get_feishu_schedule_config


FIELDS = ["平台", "渠道", "主播", "场次", "日期", "完成状态", "预估目标", "预估单量", "主推", "MCN"]
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_sync_lock = threading.Lock()
_stop_event = threading.Event()
_worker: threading.Thread | None = None
_token_lock = threading.Lock()
_token_cache = {"token": None, "expires_at": 0.0}
_status = {
    "enabled": True,
    "running": False,
    "last_success_at": None,
    "last_attempt_at": None,
    "last_error": None,
    "rows_read": 0,
    "future_rows": 0,
    "deleted_rows": 0,
    "inserted_rows": 0,
    "duration_seconds": None,
}


def _enabled() -> bool:
    return bool(get_feishu_schedule_config()["enabled"])


def _interval_seconds() -> int:
    return int(get_feishu_schedule_config()["interval_seconds"])


def _app_credentials(config: dict) -> tuple[str, str]:
    app_id = str(config.get("app_id") or "").strip()
    app_secret = str(config.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，飞书应用同步未启动")
    return app_id, app_secret


def _request_json(method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"飞书 OpenAPI HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接飞书 OpenAPI：{exc.reason}") from exc
    if int(result.get("code", 0)) != 0:
        raise RuntimeError(f"飞书 OpenAPI 错误 {result.get('code')}：{result.get('msg', '未知错误')}")
    return result


def _tenant_access_token(config: dict) -> str:
    now = monotonic()
    app_id, app_secret = _app_credentials(config)
    with _token_lock:
        if (_token_cache["token"] and _token_cache.get("app_id") == app_id
                and now < float(_token_cache["expires_at"])):
            return str(_token_cache["token"])
        result = _request_json(
            "POST",
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            body={"app_id": app_id, "app_secret": app_secret},
        )
        token = result.get("tenant_access_token")
        if not token:
            raise RuntimeError("飞书未返回 tenant_access_token")
        # 提前 5 分钟刷新，避免长分页任务中途遇到令牌过期。
        expires_in = max(300, int(result.get("expire", 7200)))
        _token_cache.update({"token": token, "expires_at": now + expires_in - 300, "app_id": app_id})
        return str(token)


def _read_records(config: dict | None = None) -> tuple[list[str], list[list]]:
    config = config or get_feishu_schedule_config()
    base_token = str(config["base_token"]).strip()
    table_id = str(config["table_id"]).strip()
    rows: list[list] = []
    record_ids: list[str] = []
    page_token = ""
    token = _tenant_access_token(config)
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}"
            f"/tables/{table_id}/records?{urlencode(params)}"
        )
        payload = _request_json("GET", url, headers={"Authorization": f"Bearer {token}"})
        data = payload.get("data") or {}
        page_rows = data.get("items") or []
        for item in page_rows:
            fields = item.get("fields") or {}
            record_ids.append(str(item.get("record_id") or item.get("id") or ""))
            rows.append([fields.get(field) for field in FIELDS])
        if not data.get("has_more"):
            break
        if not page_rows:
            raise RuntimeError("飞书分页返回 has_more=true 但没有数据")
        page_token = str(data.get("page_token") or "")
        if not page_token:
            raise RuntimeError("飞书分页缺少 page_token")
    return record_ids, rows


def _first(value):
    current = value
    for _ in range(4):
        if isinstance(current, list):
            current = current[0] if current else None
            continue
        if isinstance(current, dict):
            matched = False
            for key in ("value", "text", "name"):
                if key in current:
                    current = current[key]
                    matched = True
                    break
            if matched:
                continue
        break
    return current


def _number(value):
    value = _first(value)
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _text(value):
    value = _first(value)
    return str(value).strip() if value not in (None, "") else None


def _prepare(
    record_ids: list[str], rows: list[list], now: datetime,
    source_type: str | None = None, shop_id: str = "",
) -> list[dict]:
    source_type = source_type or _source_type()
    future: list[dict] = []
    for record_id, values in zip(record_ids, rows):
        raw = dict(zip(FIELDS, values))
        raw_timestamp = _first(raw.get("日期"))
        timestamp_number = None
        if isinstance(raw_timestamp, (int, float)):
            timestamp_number = float(raw_timestamp)
        elif isinstance(raw_timestamp, str) and raw_timestamp.strip().isdigit():
            timestamp_number = float(raw_timestamp.strip())
        if timestamp_number is not None and timestamp_number > 10_000_000_000:
            timestamp = pd.to_datetime(timestamp_number, unit="ms", utc=True, errors="coerce")
        else:
            timestamp = pd.to_datetime(raw_timestamp, errors="coerce")
        if pd.isna(timestamp):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(LOCAL_TZ)
        else:
            timestamp = timestamp.tz_convert(LOCAL_TZ)
        scheduled_at = timestamp.to_pydatetime().replace(tzinfo=None)
        if scheduled_at < now:
            continue
        anchor_name = _text(raw.get("主播"))
        if not anchor_name:
            continue
        future.append({
            "shop_id": shop_id,
            "date": scheduled_at.date(),
            "scheduled_at": scheduled_at,
            "event_type": "live",
            "has_live": True,
            "anchor_name": anchor_name.replace(" ", ""),
            "session_type": _text(raw.get("场次")),
            "platform": _text(raw.get("平台")),
            "channel": _text(raw.get("渠道")),
            "expected_orders": _number(raw.get("预估单量")),
            "expected_revenue": _number(raw.get("预估目标")),
            "mcn": _text(raw.get("MCN")),
            "source_type": source_type,
            "source_record_id": record_id or None,
        })
    return future


def _source_type(config: dict | None = None) -> str:
    """每张飞书表使用独立来源键，避免同步时影响其他店铺或其他表。"""
    config = config or get_feishu_schedule_config()
    table_id = str(config["table_id"]).strip()
    return f"feishu:{table_id}"


def _enabled_bindings() -> list[dict]:
    db = SessionLocal()
    try:
        return [
            {
                "id": row.id,
                "name": row.name,
                "shop_id": row.shop_id,
                "base_token": row.base_token,
                "table_id": row.table_id,
            }
            for row in db.query(models.FeishuScheduleBinding).filter(
                models.FeishuScheduleBinding.enabled.is_(True)
            ).order_by(models.FeishuScheduleBinding.id).all()
        ]
    finally:
        db.close()


def sync_feishu_schedule(trigger: str = "automatic") -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise RuntimeError("已有飞书排期同步任务正在运行")
    started = monotonic()
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    config = get_feishu_schedule_config()
    if not config["enabled"] and trigger == "automatic":
        _sync_lock.release()
        return {"trigger": trigger, "skipped": True, "message": "飞书排期自动同步已关闭"}
    _status.update({"enabled": bool(config["enabled"]), "running": True, "last_attempt_at": now.isoformat(sep=" "), "last_error": None})
    try:
        bindings = _enabled_bindings()
        if not bindings:
            raise RuntimeError("尚未配置启用的飞书表格与店铺绑定")
        prepared: list[tuple[dict, int, list[dict]]] = []
        for binding in bindings:
            binding_config = {**config, **binding}
            record_ids, rows = _read_records(binding_config)
            future = _prepare(
                record_ids, rows, now, _source_type(binding_config), binding["shop_id"]
            )
            prepared.append((binding, len(rows), future))
        batch_id = f"fs-{datetime.now():%Y%m%d%H%M}-{uuid.uuid4().hex[:4]}"
        db = SessionLocal()
        try:
            deleted = 0
            inserted = 0
            all_future: list[dict] = []
            affected_batches: set[str] = set()
            for binding, _, future in prepared:
                source_type = _source_type(binding)
                # 每张表只替换自己的未来记录，其他店铺、表格和手工数据不动。
                old_rows = db.query(models.LiveSchedule.upload_batch).filter(
                    models.LiveSchedule.source_type == source_type,
                    models.LiveSchedule.scheduled_at >= now,
                    models.LiveSchedule.upload_batch.isnot(None),
                ).distinct().all()
                affected_batches.update(row[0] for row in old_rows if row[0])
                deleted += db.query(models.LiveSchedule).filter(
                    models.LiveSchedule.source_type == source_type,
                    models.LiveSchedule.scheduled_at >= now,
                ).delete(synchronize_session=False)
                db.bulk_save_objects([
                    models.LiveSchedule(**item, upload_batch=batch_id) for item in future
                ])
                inserted += len(future)
                all_future.extend(future)
            dates = [item["date"] for item in all_future if item.get("date")]
            db.add(models.UploadBatchLog(
                data_type="schedule",
                batch_id=batch_id,
                row_count=inserted,
                data_start=min(dates) if dates else None,
                data_end=max(dates) if dates else None,
                source_filename=f"飞书自动同步（{len(prepared)} 个表格）",
                uploaded_at=datetime.now(),
                time_source="exact",
            ))
            # 被覆盖的旧同步批次可能仍保留历史排期；只重算这些受影响批次，
            # 避免列表出现已删除记录或过期数量。
            for old_batch in affected_batches:
                count, data_start, data_end = db.query(
                    func.count(models.LiveSchedule.id),
                    func.min(models.LiveSchedule.date),
                    func.max(models.LiveSchedule.date),
                ).filter(models.LiveSchedule.upload_batch == old_batch).first()
                log = db.query(models.UploadBatchLog).filter(
                    models.UploadBatchLog.data_type == "schedule",
                    models.UploadBatchLog.batch_id == old_batch,
                ).first()
                if log is None:
                    continue
                if not count:
                    db.delete(log)
                else:
                    log.row_count = int(count)
                    log.data_start, log.data_end = data_start, data_end
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        rows_read = sum(item[1] for item in prepared)
        future_rows = sum(len(item[2]) for item in prepared)
        result = {
            "trigger": trigger,
            "batch_id": batch_id,
            "rows_read": rows_read,
            "future_rows": future_rows,
            "deleted_rows": int(deleted),
            "inserted_rows": inserted,
            "cutoff": now.isoformat(sep=" "),
            "source_scope": [_source_type(item[0]) for item in prepared],
            "binding_count": len(prepared),
            "message": f"{len(prepared)} 个表格绑定已更新：写入 {future_rows} 条，替换各自旧排期 {deleted} 条；其他店铺与手工数据未改动",
        }
        try:
            from api.services.live_prediction_overlay import refresh_latest_live_overlay
            result["overlay_refresh"] = refresh_latest_live_overlay()
        except Exception as exc:
            # 飞书镜像已成功提交；预测增量刷新失败应被记录，但不能把同步标记为失败。
            result["overlay_refresh"] = {
                "skipped": True,
                "reason": f"直播增量刷新失败：{exc}",
            }
        _status.update(result)
        _status["last_success_at"] = datetime.now(LOCAL_TZ).replace(tzinfo=None).isoformat(sep=" ")
        return result
    except Exception as exc:
        _status["last_error"] = str(exc)
        raise
    finally:
        _status["running"] = False
        _status["duration_seconds"] = round(monotonic() - started, 2)
        _sync_lock.release()


def get_sync_status() -> dict:
    config = get_feishu_schedule_config()
    app_id = config["app_id"]
    bindings = _enabled_bindings()
    return {
        **_status,
        "enabled": bool(config["enabled"]),
        "interval_seconds": int(config["interval_seconds"]),
        "auth_mode": "app_id",
        "app_id_configured": bool(app_id),
        "app_secret_configured": bool(config["app_secret"]),
        "app_id_masked": f"{app_id[:6]}***{app_id[-4:]}" if len(app_id) > 10 else ("已配置" if app_id else None),
        "binding_count": len(bindings),
        "source_scope": [_source_type(item) for item in bindings],
        "storage": "database",
    }


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            sync_feishu_schedule("automatic")
        except Exception as exc:
            print(f"[FeishuScheduleSync] {exc}")
        _stop_event.wait(_interval_seconds())


def start_schedule_sync() -> None:
    global _worker
    if not _enabled() or (_worker and _worker.is_alive()):
        return
    _stop_event.clear()
    _worker = threading.Thread(target=_loop, name="feishu-schedule-sync", daemon=True)
    _worker.start()


def stop_schedule_sync() -> None:
    _stop_event.set()


def reload_schedule_sync() -> None:
    """保存配置后重载后台任务，并清除旧应用令牌。"""
    global _worker
    stop_schedule_sync()
    worker = _worker
    if worker and worker.is_alive() and worker is not threading.current_thread():
        worker.join(timeout=5)
    _worker = None
    with _token_lock:
        _token_cache.update({"token": None, "expires_at": 0.0, "app_id": None})
    start_schedule_sync()


def test_feishu_connection() -> dict:
    """只读验证鉴权和表格访问，不改动任何排期数据。"""
    config = get_feishu_schedule_config()
    bindings = _enabled_bindings()
    if not bindings:
        raise RuntimeError("尚未配置启用的飞书表格与店铺绑定")
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    rows_read = 0
    future_rows = 0
    for binding in bindings:
        binding_config = {**config, **binding}
        record_ids, rows = _read_records(binding_config)
        future = _prepare(
            record_ids, rows, now, _source_type(binding_config), binding["shop_id"]
        )
        rows_read += len(rows)
        future_rows += len(future)
    return {
        "ok": True,
        "binding_count": len(bindings),
        "rows_read": rows_read,
        "future_rows": future_rows,
        "message": f"连接成功，读取 {len(bindings)} 个表格、{rows_read} 条记录，其中未来有效排期 {future_rows} 条",
    }
