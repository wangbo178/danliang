from datetime import datetime

from api.services.feishu_schedule_sync import FIELDS, _prepare


def _row(**values):
    return [values.get(field) for field in FIELDS]


def test_prepare_keeps_only_future_rows_and_maps_fields():
    now = datetime(2026, 10, 13, 12, 0, 0)
    rows = [
        _row(平台=["抖音"], 渠道="达人直播间", 主播="达人 A", 场次=["专场"], 日期="2026-10-13T13:00:00+08:00", 预估目标=100000, 预估单量="625", MCN="机构A"),
        _row(平台=["抖音"], 主播="达人 B", 日期="2026-10-13T11:00:00+08:00", 预估单量="100"),
        _row(平台=["抖音"], 主播="", 日期="2026-10-14T09:00:00+08:00", 预估单量="100"),
    ]

    result = _prepare(["future", "past", "blank"], rows, now)

    assert len(result) == 1
    assert result[0]["source_record_id"] == "future"
    assert result[0]["anchor_name"] == "达人A"
    assert result[0]["scheduled_at"] == datetime(2026, 10, 13, 13, 0, 0)
    assert result[0]["expected_orders"] == 625.0
    assert result[0]["expected_revenue"] == 100000.0
    assert result[0]["platform"] == "抖音"
    assert result[0]["channel"] == "达人直播间"
    assert result[0]["session_type"] == "专场"


def test_prepare_treats_naive_datetime_as_shanghai_time():
    now = datetime(2026, 10, 13, 12, 0, 0)
    result = _prepare(
        ["row1"],
        [_row(平台="抖音", 主播="达人C", 日期="2026-10-13 12:30:00")],
        now,
    )
    assert result[0]["scheduled_at"] == datetime(2026, 10, 13, 12, 30, 0)


def test_prepare_supports_openapi_millisecond_and_formula_shapes():
    now = datetime(2026, 10, 13, 12, 0, 0)
    # 2026-10-14 09:00:00 Asia/Shanghai
    millis = 1791939600000
    result = _prepare(
        ["openapi-row"],
        [_row(
            平台="抖音",
            主播=[{"text": "达人D"}],
            日期=millis,
            预估单量=[{"type": "number", "value": [625]}],
        )],
        now,
    )
    assert result[0]["scheduled_at"] == datetime(2026, 10, 14, 9, 0, 0)
    assert result[0]["expected_orders"] == 625.0
