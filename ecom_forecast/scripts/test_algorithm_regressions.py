r"""算法关键路径回归测试。

运行：cd ecom_forecast && ..\.venv\Scripts\python scripts\test_algorithm_regressions.py
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_channel_only_training_preserves_existing_router() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    config = {
        "warehouses": [
            {"warehouse_id": "W1", "name": "仓1", "is_active": True},
            {"warehouse_id": "W2", "name": "仓2", "is_active": True},
        ]
    }
    with TemporaryDirectory() as temp_dir:
        original = ForecastPipeline(model_dir=temp_dir, config_dict=config)
        original.warehouse_router.routing_ratios = {
            "S1": {0: {"W1": 0.8, "W2": 0.2}}
        }
        original.warehouse_router.global_default_ratio = {
            0: {"W1": 0.7, "W2": 0.3}
        }
        router_path = Path(temp_dir) / "warehouse_router.joblib"
        original.warehouse_router.save(str(router_path))
        router_before = router_path.read_bytes()

        channel_only = ForecastPipeline(model_dir=temp_dir, config_dict=config)
        restored = channel_only.restore_untrained_components(["channel"])
        assert restored == ["routing"]
        assert channel_only.warehouse_router._get_ratio("S1") == {
            "W1": 0.8, "W2": 0.2
        }

        channel_only.save(phases=["channel"])
        assert router_path.read_bytes() == router_before


def test_calendar_day_lags() -> None:
    from src.features.time_features import build_all_time_features

    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-08"]),
        "shop_id": ["S1"] * 3,
        "channel": ["C1"] * 3,
        "order_count": [10, 20, 80],
    })
    out = build_all_time_features(df, "order_count", ["shop_id", "channel"])
    jan8 = out[out["date"] == pd.Timestamp("2026-01-08")].iloc[0]
    assert jan8["order_count_lag7"] == 10
    assert pd.isna(jan8["order_count_lag1"]), "lag1 不能错误地取上一条观测"


def test_live_schedule_does_not_duplicate_rows() -> None:
    from src.features.channel_features import build_live_features

    base = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10"]),
        "shop_id": ["S1"],
        "channel": ["tm_zb"],
        "order_count": [100],
    })
    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10", "2026-01-10"]),
        "shop_id": ["S1", "S1"],
        "event_type": ["live", "live"],
        "anchor_tier": ["B", "A"],
        "anchor_name": ["达人乙", "达人甲"],
    })
    out = build_live_features(base, schedule_df=schedule)
    assert len(out) == 1
    assert out.iloc[0]["anchor_tier_code"] == 3


def test_live_schedule_is_excluded_from_event_base_model() -> None:
    from src.models.channel_models import _schedule_for_base_model

    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10", "2026-01-15"]),
        "shop_id": ["S1", "S1"],
        "event_type": ["live", "presale_payment"],
    })
    base_schedule = _schedule_for_base_model(schedule)
    assert base_schedule is not None
    assert base_schedule["event_type"].tolist() == ["presale_payment"]


def test_explicit_live_target_overrides_anchor_history() -> None:
    from src.models.anchor_live_model import AnchorLiveOrderModel

    model = AnchorLiveOrderModel()
    model.is_fitted = True
    model.anchor_stats = {"A": (10, 100.0, 100.0)}
    model.shop_channel_map = {"S1": "达播"}
    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-20"]),
        "shop_id": ["S1"],
        "anchor_name": ["A"],
        "event_type": ["live"],
        "channel": ["达播"],
        "expected_orders": [751747.0],
    })

    out = model.predict_schedule(schedule)
    assert out.iloc[0]["anchor_predicted_orders"] == 751747.0


def test_shadow_live_schedule_does_not_double_count() -> None:
    from src.models.anchor_live_model import AnchorLiveOrderModel

    model = AnchorLiveOrderModel()
    model.is_fitted = True
    model.anchor_stats = {"A": (10, 100.0, 100.0)}
    model.shop_channel_map = {"S1": "达播"}
    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-20", "2026-10-20"]),
        "shop_id": ["S1", "S1"],
        "anchor_name": ["A", "A"],
        "event_type": ["live", "live"],
        "channel": [None, "达播"],
        "expected_orders": [np.nan, 12345.0],
        "source_record_id": [None, None],
        "scheduled_at": [pd.NaT, pd.NaT],
    })

    out = model.predict_schedule(schedule)
    assert len(out) == 1
    assert out.iloc[0]["anchor_predicted_orders"] == 12345.0


def test_manual_channel_placeholder_does_not_duplicate_anchor_prediction() -> None:
    from api.routers.training import _build_manual_channel_placeholders

    pred = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-20"]),
        "shop_id": ["S1"],
        "channel": ["达播"],
        "platform": ["TM"],
        "predicted_orders": [12345.0],
    })
    meta = pd.DataFrame({
        "shop_id": ["S1", "S2"],
        "channel": ["达播", "达播"],
        "platform": ["TM", "TM"],
    })

    placeholders = _build_manual_channel_placeholders(
        pred, meta, pd.date_range("2026-10-20", periods=1)
    )
    assert len(placeholders) == 1
    assert placeholders.iloc[0]["shop_id"] == "S2"


def test_live_schedule_only_changes_anchor_overlay() -> None:
    from src.models.channel_models import ChannelEnsemble

    class FakeEventModel:
        raw_fallback_channels = set()

        def predict_with_raw(self, frame, schedule_df=None, return_guardrail=False, **kwargs):
            # 如果普通直播错误进入基础模型，这里会把 base 从 10 改成 110。
            has_base_schedule = schedule_df is not None and len(schedule_df) > 0
            base = np.full(len(frame), 110.0 if has_base_schedule else 10.0)
            raw = np.full(len(frame), 8.0)
            if return_guardrail:
                return base, raw, base.copy()
            return base, raw

    class FakeAnchorModel:
        is_fitted = True

        @staticmethod
        def remove_actuals_from_event_history(frame, anchor_df):
            return frame

        @staticmethod
        def predict_schedule(schedule_df):
            if schedule_df is None or len(schedule_df) == 0:
                return pd.DataFrame(columns=[
                    "date", "shop_id", "channel", "anchor_predicted_orders"
                ])
            return pd.DataFrame({
                "date": pd.to_datetime(["2026-01-10"]),
                "shop_id": ["S1"],
                "channel": ["达人直播间"],
                "anchor_predicted_orders": [50.0],
            })

    ensemble = ChannelEnsemble(channel_type_map={"达人直播间": "event"})
    ensemble.models["event"] = FakeEventModel()
    ensemble.anchor_live_model = FakeAnchorModel()
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10"]),
        "shop_id": ["S1"], "channel": ["达人直播间"],
        "order_count": [np.nan],
    })
    live_schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10"]),
        "shop_id": ["S1"], "channel": ["达人直播间"],
        "event_type": ["live"], "anchor_name": ["达人甲"],
    })
    without_live = ensemble.predict(frame, schedule_df=None)
    with_live = ensemble.predict(frame, schedule_df=live_schedule)
    assert without_live["base_predicted_orders"].tolist() == [10.0]
    assert with_live["base_predicted_orders"].tolist() == [10.0]
    assert with_live["anchor_predicted_orders"].tolist() == [50.0]
    assert with_live["predicted_orders"].tolist() == [60.0]

    # 手工指定的新渠道即使不在基础预测组合中，也应补成一条纯直播增量。
    ensemble.models["stable"] = FakeEventModel()
    shelf_frame = frame.assign(channel="店铺")
    expanded = ensemble.predict(shelf_frame, schedule_df=live_schedule)
    new_channel = expanded[expanded["channel"].eq("达人直播间")]
    assert len(new_channel) == 1
    assert new_channel.iloc[0]["base_predicted_orders"] == 0
    assert new_channel.iloc[0]["anchor_predicted_orders"] == 50

    # 预测上下文只保留近期历史，但排期可能包含更早的历史直播。历史场次
    # 不能被当作“新渠道”补回预测结果，否则 _is_future 会出现 NaN 并使
    # Pandas 在筛选未来行时报列索引错误。
    class FakeAnchorWithHistorical(FakeAnchorModel):
        @staticmethod
        def predict_schedule(schedule_df):
            return pd.DataFrame({
                "date": pd.to_datetime(["2024-01-01", "2026-01-10"]),
                "shop_id": ["S1", "S1"],
                "channel": ["达人直播间", "达人直播间"],
                "anchor_predicted_orders": [40.0, 50.0],
            })

    ensemble.anchor_live_model = FakeAnchorWithHistorical()
    mixed_frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-12-31", "2026-01-10"]),
        "shop_id": ["S1", "S1"],
        "channel": ["店铺", "店铺"],
        "order_count": [10.0, np.nan],
        "_is_future": [False, True],
    })
    mixed_schedule = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2026-01-10"]),
        "shop_id": ["S1", "S1"],
        "channel": ["达人直播间", "达人直播间"],
        "event_type": ["live", "live"],
        "anchor_name": ["历史达人", "未来达人"],
    })
    mixed = ensemble.predict(mixed_frame, schedule_df=mixed_schedule)
    added = mixed[mixed["channel"].eq("达人直播间")]
    assert len(added) == 1
    assert added.iloc[0]["date"] == pd.Timestamp("2026-01-10")
    assert bool(added.iloc[0]["_is_future"]) is True
    assert mixed["_is_future"].fillna(False).astype(bool).sum() == 2


def test_anchor_history_is_past_only() -> None:
    from src.features.channel_features import build_live_features

    dates = pd.to_datetime(["2026-01-01", "2026-01-10"])
    base = pd.DataFrame({
        "date": dates, "shop_id": ["S1", "S1"],
        "channel": ["tm_zb", "tm_zb"], "order_count": [100, 1000],
    })
    schedule = pd.DataFrame({
        "date": dates, "shop_id": ["S1", "S1"],
        "event_type": ["live", "live"], "anchor_tier": ["A", "A"],
        "anchor_name": ["达人甲", "达人甲"],
    })
    anchor_orders = pd.DataFrame({
        "date": dates, "anchor_name": ["达人甲", "达人甲"],
        "order_count": [100, 1000],
    })
    out = build_live_features(base, schedule_df=schedule, anchor_df=anchor_orders)
    assert out.loc[out["date"] == dates[0], "anchor_hist_mean"].iloc[0] == 0
    assert out.loc[out["date"] == dates[1], "anchor_hist_mean"].iloc[0] == 100


def test_anchor_history_is_scoped_to_shop() -> None:
    from src.features.channel_features import build_live_features

    base = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10", "2026-01-10"]),
        "shop_id": ["S1", "S2"], "channel": ["tm_zb", "tm_zb"],
        "order_count": [100, 100],
    })
    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10", "2026-01-10"]),
        "shop_id": ["S1", "S2"], "event_type": ["live", "live"],
        "anchor_tier": ["A", "A"], "anchor_name": ["达人甲", "达人甲"],
    })
    anchor_orders = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
        "shop_id": ["S1", "S2"], "anchor_name": ["达人甲", "达人甲"],
        "order_count": [100, 10000],
    })
    out = build_live_features(base, schedule_df=schedule, anchor_df=anchor_orders)
    got = out.set_index("shop_id")["anchor_hist_mean"].to_dict()
    assert got == {"S1": 100.0, "S2": 10000.0}


def test_anchor_history_accepts_mixed_datetime_resolutions() -> None:
    from src.features.channel_features import build_live_features

    base = pd.DataFrame({
        "date": pd.Series(["2026-01-10"], dtype="datetime64[us]"),
        "shop_id": ["S1"], "channel": ["tm_zb"], "order_count": [np.nan],
    })
    schedule = pd.DataFrame({
        "date": pd.Series(["2026-01-10"], dtype="datetime64[s]"),
        "shop_id": ["S1"], "event_type": ["live"],
        "anchor_tier": ["A"], "anchor_name": ["达人甲"],
    })
    anchor_orders = pd.DataFrame({
        "date": pd.Series(["2026-01-01"], dtype="datetime64[s]"),
        "shop_id": ["S1"], "anchor_name": ["达人甲"], "order_count": [123],
    })
    out = build_live_features(base, schedule_df=schedule, anchor_df=anchor_orders)
    assert out.iloc[0]["anchor_hist_mean"] == 123


def test_precomputed_anchor_stats_match_raw_history() -> None:
    from src.features.channel_features import build_live_features, prepare_anchor_history_stats

    dates = pd.to_datetime(["2026-01-10", "2026-01-11"])
    base = pd.DataFrame({
        "date": dates, "shop_id": ["S1", "S1"],
        "channel": ["tm_zb", "tm_zb"], "order_count": [np.nan, np.nan],
    })
    schedule = pd.DataFrame({
        "date": dates, "shop_id": ["S1", "S1"], "event_type": ["live", "live"],
        "anchor_tier": ["A", "A"], "anchor_name": ["达人甲", "达人甲"],
    })
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-05"]),
        "shop_id": ["S1", "S1"], "anchor_name": ["达人甲", "达人甲"],
        "order_count": [100, 300],
    })
    raw = build_live_features(base, schedule_df=schedule, anchor_df=history)
    cached = build_live_features(
        base, schedule_df=schedule, anchor_df=prepare_anchor_history_stats(history)
    )
    assert np.allclose(raw["anchor_hist_mean"], cached["anchor_hist_mean"])
    assert np.allclose(raw["anchor_hist_recent"], cached["anchor_hist_recent"])


def test_presale_is_allocated_once_to_event_channel() -> None:
    from api.routers.training import _resolve_presale_allocations

    orders = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-28", "2026-05-28", "2026-05-28"]),
        "shop_id": ["S1"] * 3,
        "platform": ["tm"] * 3,
        "channel": ["tm_zb", "dy_zb", "tm_hj"],
        "order_count": [80, 20, 100],
    })
    history = [{
        "shop_id": "S1",
        "channel": "直播",
        "broadcast_date": "2026-05-20",
        "payment_date": "2026-05-28",
        "order_count": 50,
        "campaign_type": "618",
    }]
    allocation, unresolved = _resolve_presale_allocations(
        orders,
        history,
        {"tm_zb": "event", "dy_zb": "event", "tm_hj": "stable"},
    )
    by_channel = allocation.groupby("channel")["order_count"].sum().to_dict()
    assert np.isclose(allocation["order_count"].sum(), 50)
    assert np.isclose(by_channel["tm_zb"], 40)
    assert np.isclose(by_channel["dy_zb"], 10)
    assert np.isclose(unresolved, 0)


def test_future_presale_schedule_is_not_replaced_by_history() -> None:
    from api.routers.training import _merge_schedule_sources

    uploaded = pd.DataFrame({
        "shop_id": ["S1", "S1", "S1"],
        "date": pd.to_datetime(["2026-06-18", "2026-10-20", "2026-10-15"]),
        "event_type": ["presale_payment", "presale_payment", "live"],
        "anchor_name": ["A", "A", "A"],
        "broadcast_date": pd.to_datetime(["2026-06-01", "2026-10-15", None]),
    })
    generated = pd.DataFrame({
        "shop_id": ["S1"], "channel": ["tm_zb"],
        "date": pd.to_datetime(["2026-06-18"]),
        "event_type": ["presale_payment"],
        "broadcast_date": pd.to_datetime(["2026-06-01"]),
    })
    out = _merge_schedule_sources(
        uploaded, generated, history_end=pd.Timestamp("2026-09-22")
    )

    future_pay = out[
        out["event_type"].eq("presale_payment")
        & out["date"].eq(pd.Timestamp("2026-10-20"))
    ]
    historical_pay = out[
        out["event_type"].eq("presale_payment")
        & out["date"].eq(pd.Timestamp("2026-06-18"))
    ]
    assert len(future_pay) == 1, "用户上传的未来尾款日不能被历史排期覆盖"
    assert len(historical_pay) == 1
    assert historical_pay.iloc[0]["channel"] == "tm_zb"
    assert out[out["event_type"].eq("live")].shape[0] == 1


def test_schedule_audit_reports_horizon_and_anchor_match() -> None:
    from api.routers.training import _audit_future_schedule

    schedule = pd.DataFrame({
        "shop_id": ["S1", "S1", "S1", "S1"],
        "date": pd.to_datetime([
            "2026-10-15", "2026-10-20", "2026-10-23", "2026-10-16"
        ]),
        "event_type": ["live", "presale_payment", "presale_payment", "live"],
        "anchor_name": [" 达人甲 ", None, None, "未知达人"],
    })
    history = pd.DataFrame({
        "shop_id": ["S1"], "anchor_name": ["达人甲"], "order_count": [100]
    })
    audit = _audit_future_schedule(
        schedule, history, pd.Timestamp("2026-09-23"), pd.Timestamp("2026-10-22")
    )
    assert audit["in_window"] == 3
    assert audit["live"] == 2
    assert audit["presale"] == 1
    assert audit["after_window"] == 1
    assert audit["live_with_anchor"] == 2
    assert audit["matched_anchor"] == 1
    assert audit["unmatched_anchor_names"] == ["未知达人"]


def test_anchor_history_enriches_historical_live_schedule() -> None:
    from api.routers.training import _enrich_schedule_with_anchor_history

    orders = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"]),
        "shop_id": ["S1", "S1", "S2"],
        "channel": ["tm_zb", "tm_hj", "tm_hj"],
        "order_count": [500, 1000, 100],
    })
    anchors = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"]),
        "shop_id": ["S1", "S1", "S2"],
        "anchor_name": ["达人甲", "达人乙", "达人丙"],
        "order_count": [100, 300, 999],
    })
    explicit = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-03"]), "shop_id": ["S1"],
        "event_type": ["live"], "anchor_name": ["达人丁"],
    })
    out, inferred_count = _enrich_schedule_with_anchor_history(
        explicit, anchors, orders,
        {"tm_zb": "event", "tm_hj": "stable"},
        history_end=pd.Timestamp("2026-01-02"),
    )
    inferred = out[out["date"].eq(pd.Timestamp("2026-01-01"))]
    assert inferred_count == 1
    assert len(inferred) == 1
    assert inferred.iloc[0]["anchor_name"] == "达人乙", "应选同店同日单量最大的达人"
    assert inferred.iloc[0]["channel"] == "tm_zb"
    assert not out["shop_id"].eq("S2").any(), "无直播渠道的店铺不能被强行映射"


def test_overall_wape_is_volume_weighted_not_channel_average() -> None:
    from evaluation.metrics import evaluate

    # 大渠道：1000 单，误差 10%；小渠道：10 单，误差 100%。
    # 整体 WAPE=(100+10)/(1000+10)=10.89%，不是渠道算术平均 55%。
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
        "channel": ["大渠道", "小渠道"],
        "order_count": [1000.0, 10.0],
        "predicted_orders": [900.0, 0.0],
    })
    report = evaluate(frame, group_col="channel", campaign_mask_col=None)
    overall = report[report["segment"].eq("全局")].iloc[0]
    channel_mean = report[report["segment"].str.startswith("channel=")]["wape"].mean()
    assert np.isclose(overall["wape"], 10.89)
    assert np.isclose(overall["mae"], 55.0)
    assert channel_mean == 55.0
    assert overall["total_actual"] == 1010.0


def test_presale_schedule_adds_volume_on_payment_day() -> None:
    from src.models.channel_models import ChannelTypeModel

    model = ChannelTypeModel("event")
    model.presale_model.fit([{
        "shop_id": "S1", "channel": "tm_zb", "campaign_type": "1111",
        "broadcast_date": "2025-10-15", "payment_date": "2025-10-20",
        "day_index": 0, "daily_volume": 1000,
    }])
    feature_df = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-20"]),
        "shop_id": ["S1"], "channel": ["tm_zb"],
        "is_presale_payment": [1],
    })
    schedule = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-20"]),
        "shop_id": ["S1"], "event_type": ["presale_payment"],
        "broadcast_date": pd.to_datetime(["2026-10-15"]),
    })
    out = model._apply_presale_override(feature_df, np.array([100.0]), schedule)
    assert out[0] > 100.0, "未来尾款排期必须触发预售加量"


def test_router_ignores_sample_classification() -> None:
    from src.routing.warehouse_router import WarehouseRouter

    rows = []
    for day in pd.date_range("2026-02-01", periods=10):
        rows.extend([
            {"date": day, "shop_id": "S1", "channel": "C1", "is_sample": 1,
             "warehouse_id": "A", "order_count": 90},
            {"date": day, "shop_id": "S1", "channel": "C1", "is_sample": 0,
             "warehouse_id": "B", "order_count": 10},
        ])
    router = WarehouseRouter(
        rule_change_date="2026-01-01",
        transition_days=0,
        warehouse_config=[
            {"warehouse_id": "A", "is_active": True},
            {"warehouse_id": "B", "is_active": True},
        ],
        shop_warehouse_binding=[],
    ).fit(pd.DataFrame(rows))
    routed = router.route(pd.DataFrame([{
        "date": pd.Timestamp("2026-03-01"),
        "shop_id": "S1",
        "channel": "C1",
        "predicted_orders": 100,
    }]))
    assert np.isclose(routed["warehouse_orders"].sum(), 100)
    assert set(routed["is_sample"]) == {0}
    warehouse_totals = routed.set_index("warehouse_id")["warehouse_orders"].to_dict()
    assert np.isclose(warehouse_totals["A"], 90)
    assert np.isclose(warehouse_totals["B"], 10)


def test_recursive_channel_prediction() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    class FakeEnsemble:
        def predict(self, df, **kwargs):
            out = df.copy()
            observed_max = float(out["order_count"].dropna().max())
            out["predicted_orders"] = out["order_count"].fillna(observed_max + 1)
            return out

    pipeline = ForecastPipeline(config_dict={
        "warehouse_config": [{"warehouse_id": "A", "is_active": True}],
        "shop_warehouse_binding": [],
    })
    pipeline.channel_ensemble = FakeEnsemble()
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]),
        "shop_id": ["S1"], "channel": ["C1"], "platform": ["P1"],
        "order_count": [10.0],
    })
    future = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
        "shop_id": ["S1", "S1"], "channel": ["C1", "C1"],
        "platform": ["P1", "P1"], "order_count": [np.nan, np.nan],
    })
    out = pipeline.predict_channels(future, history)
    assert out["predicted_orders"].tolist() == [11.0, 12.0]


def test_recursive_history_excludes_anchor_increment() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    class FakeEnsemble:
        def predict(self, df, **kwargs):
            out = df.copy()
            observed_max = float(out["order_count"].dropna().max())
            is_future = out["order_count"].isna()
            out["base_predicted_orders"] = np.where(is_future, observed_max, out["order_count"])
            out["anchor_predicted_orders"] = np.where(is_future, 100.0, 0.0)
            out["predicted_orders"] = (
                out["base_predicted_orders"] + out["anchor_predicted_orders"]
            )
            return out

    pipeline = ForecastPipeline(config_dict={
        "warehouse_config": [{"warehouse_id": "A", "is_active": True}],
        "shop_warehouse_binding": [],
    })
    pipeline.channel_ensemble = FakeEnsemble()
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]),
        "shop_id": ["S1"], "channel": ["达人直播间"], "platform": ["DOUYIN"],
        "order_count": [10.0],
    })
    future = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
        "shop_id": ["S1", "S1"], "channel": ["达人直播间", "达人直播间"],
        "platform": ["DOUYIN", "DOUYIN"], "order_count": [np.nan, np.nan],
    })
    out = pipeline.predict_channels(future, history)
    assert out["base_predicted_orders"].tolist() == [10.0, 10.0]
    assert out["predicted_orders"].tolist() == [110.0, 110.0]


def test_channel_prediction_trims_feature_history() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    class InspectingEnsemble:
        def __init__(self):
            self.min_dates = []

        def predict(self, df, **kwargs):
            self.min_dates.append(pd.to_datetime(df["date"]).min())
            out = df.copy()
            out["predicted_orders"] = 1.0
            return out

    pipeline = ForecastPipeline(config_dict={
        "training_config": {"prediction_history_days": 400},
        "warehouse_config": [{"warehouse_id": "A", "is_active": True}],
        "shop_warehouse_binding": [],
    })
    inspector = InspectingEnsemble()
    pipeline.channel_ensemble = inspector
    history_dates = pd.date_range("2024-01-01", "2026-01-01")
    history = pd.DataFrame({
        "date": history_dates, "shop_id": "S1", "channel": "C1",
        "platform": "P1", "order_count": 1.0,
    })
    future = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-02")], "shop_id": ["S1"],
        "channel": ["C1"], "platform": ["P1"], "order_count": [np.nan],
    })
    pipeline.predict_channels(future, history)
    assert inspector.min_dates == [pd.Timestamp("2024-11-28")]


def test_channel_model_only_scores_future_rows() -> None:
    from src.models.channel_models import ChannelTypeModel

    class FakeBooster:
        def __init__(self):
            self.rows_scored = None

        def predict(self, x):
            self.rows_scored = len(x)
            return np.log1p(np.full(len(x), 10.0))

    model = ChannelTypeModel("stable")
    booster = FakeBooster()
    model.model = booster
    model.feature_cols = []
    model._prepare_features = lambda df, *args, **kwargs: df.copy()
    frame = pd.DataFrame({
        "_is_future": [False, False, True],
        "channel": ["C1", "C1", "C1"],
        "order_count": [1.0, 2.0, np.nan],
    })
    predictions = model.predict(frame)
    assert booster.rows_scored == 1
    assert np.isnan(predictions[:2]).all()
    assert np.isclose(predictions[2], 10.0)


def test_channel_calibration_keeps_raw_prediction_auditable() -> None:
    from src.models.channel_models import ChannelTypeModel

    class FakeBooster:
        def predict(self, x):
            return np.log1p(np.full(len(x), 10.0))

    model = ChannelTypeModel("event")
    model.model = FakeBooster()
    model.feature_cols = []
    model.smearing_factor = 1.2
    model.channel_calibration_factors = {"tm_zb": 1.5}
    model.live_peak_factors = {"tm_zb": 2.0}
    model.presale_model = None
    model._prepare_features = lambda df, *args, **kwargs: df.copy()
    frame = pd.DataFrame({
        "_is_future": [True], "channel": ["tm_zb"], "has_live": [1],
        "order_count": [np.nan],
    })
    calibrated, raw = model.predict_with_raw(frame)
    assert np.isclose(raw[0], 10.0)
    # (exp(log(11)) * 1.2 - 1) * 1.5 * 2
    assert np.isclose(calibrated[0], 36.6)


def test_channel_holdout_fallback_preserves_stable_baseline() -> None:
    from src.models.channel_models import ChannelTypeModel

    class FakeBooster:
        def predict(self, x):
            return np.log1p(np.full(len(x), 10.0))

    model = ChannelTypeModel("stable")
    model.model = FakeBooster()
    model.feature_cols = []
    model.smearing_factor = 1.4
    model.channel_calibration_factors = {"店铺": 0.7}
    model.stable_baseline_factor = 1.0
    model.stable_baseline_weight = 1.0
    model.raw_fallback_channels = {"店铺"}
    model._prepare_features = lambda df, *args, **kwargs: df.copy()
    frame = pd.DataFrame({
        "_is_future": [True, True],
        "channel": ["店铺", "其他"],
        "order_count_roll_mean7": [20.0, 20.0],
        "order_count": [np.nan, np.nan],
    })
    calibrated, raw, fallback = model.predict_with_raw(frame, return_guardrail=True)
    assert np.isclose(raw[0], 10.0)
    assert np.isclose(fallback[0], 20.0)
    assert np.isclose(calibrated[0], fallback[0])
    assert calibrated[1] > raw[1]


def test_holdout_guardrail_never_worsens_channel_wape() -> None:
    from src.pipeline.forecast_pipeline import apply_channel_calibration_guardrail

    frame = pd.DataFrame({
        "channel": ["店铺", "店铺", "其他", "其他"],
        "order_count": [100.0, 100.0, 100.0, 100.0],
        "raw_predicted_orders": [95.0, 105.0, 80.0, 80.0],
        "predicted_orders": [60.0, 60.0, 90.0, 90.0],
    })
    guarded, details = apply_channel_calibration_guardrail(frame)
    assert [item["channel"] for item in details] == ["店铺"]
    assert guarded.loc[guarded["channel"] == "店铺", "predicted_orders"].tolist() == [95.0, 105.0]
    assert guarded.loc[guarded["channel"] == "其他", "predicted_orders"].tolist() == [90.0, 90.0]
    guarded_error = (guarded["order_count"] - guarded["predicted_orders"]).abs().sum()
    raw_error = (guarded["order_count"] - guarded["raw_predicted_orders"]).abs().sum()
    assert guarded_error <= raw_error


def test_holdout_guardrail_prefers_baseline_preserving_candidate() -> None:
    from src.pipeline.forecast_pipeline import apply_channel_calibration_guardrail

    frame = pd.DataFrame({
        "channel": ["店铺", "店铺"],
        "order_count": [100.0, 100.0],
        "predicted_orders": [60.0, 60.0],
        "raw_predicted_orders": [20.0, 20.0],
        "guardrail_predicted_orders": [95.0, 105.0],
    })
    guarded, details = apply_channel_calibration_guardrail(frame)
    assert len(details) == 1
    assert guarded["predicted_orders"].tolist() == [95.0, 105.0]
    assert details[0]["fallback_wape"] == 5.0


def test_global_prediction_keeps_group_alignment() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    class FakeGlobal:
        def predict(self, df):
            return np.where(df["shop_id"].eq("S1"), 101.0, 202.0)

    pipeline = ForecastPipeline(config_dict={
        "warehouse_config": [{"warehouse_id": "A", "is_active": True}],
        "shop_warehouse_binding": [],
    })
    pipeline.global_model = FakeGlobal()
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
        "shop_id": ["S1", "S2"], "platform": ["P1", "P2"],
        "order_count": [10, 20],
    })
    combos = history[["shop_id", "platform"]]
    out = pipeline.predict_global(pd.DatetimeIndex([pd.Timestamp("2026-01-02")]), combos, history)
    got = out.set_index("shop_id")["predicted_shop_orders"].to_dict()
    assert got == {"S1": 101.0, "S2": 202.0}


def test_recursive_prediction_distinguishes_platform_in_merge_key() -> None:
    from src.pipeline.forecast_pipeline import ForecastPipeline

    class FakeChannelEnsemble:
        def predict(self, frame, **kwargs):
            out = frame.copy()
            out["predicted_orders"] = np.where(out["platform"].eq("P1"), 10.0, 20.0)
            return out

    pipeline = ForecastPipeline(config_dict={
        "warehouse_config": [{"warehouse_id": "A", "is_active": True}],
        "shop_warehouse_binding": [],
    })
    pipeline.channel_ensemble = FakeChannelEnsemble()
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
        "shop_id": ["S1", "S1"], "channel": ["其他", "其他"],
        "platform": ["P1", "P2"], "order_count": [1.0, 2.0],
    })
    future = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
        "shop_id": ["S1", "S1"], "channel": ["其他", "其他"],
        "platform": ["P1", "P2"], "order_count": [np.nan, np.nan],
    })
    out = pipeline.predict_channels(future, history)
    got = out.set_index("platform")["predicted_orders"].to_dict()
    assert got == {"P1": 10.0, "P2": 20.0}


def test_api_calendar_shape_conversion() -> None:
    from config.routing_config import _normalize_campaign_calendar

    base = {2026: {"1111": {
        "presale_start": "2026-10-14", "start": "2026-10-21", "end": "2026-11-11"
    }}}
    converted = _normalize_campaign_calendar([{
        "name": "2025 618", "campaign_type": "618",
        "start_date": "2025-06-01", "end_date": "2025-06-18",
    }], base)
    assert converted[2025]["618"]["start"] == "2025-06-01"
    assert "1111" in converted[2026]


def test_stable_baseline_selection_can_choose_recent_level() -> None:
    from src.models.channel_models import _select_stable_baseline

    actual = np.array([100.0, 110.0, 90.0, 105.0])
    model_pred = np.array([60.0, 65.0, 55.0, 60.0])
    rolling_mean7 = np.array([98.0, 102.0, 96.0, 100.0])
    factor, weight = _select_stable_baseline(actual, model_pred, rolling_mean7)
    assert 0.95 <= factor <= 1.10
    assert weight >= 0.75


def test_stable_prediction_applies_recent_level_blend() -> None:
    from src.models.channel_models import ChannelTypeModel

    class FakeBooster:
        def predict(self, x):
            return np.log1p(np.full(len(x), 10.0))

    model = ChannelTypeModel("stable")
    model.model = FakeBooster()
    model.feature_cols = []
    model.smearing_factor = 1.0
    model.stable_baseline_factor = 1.1
    model.stable_baseline_weight = 1.0
    model._prepare_features = lambda df, *args, **kwargs: df.copy()
    frame = pd.DataFrame({
        "_is_future": [True], "channel": ["店铺"],
        "order_count": [np.nan], "order_count_roll_mean7": [20.0],
    })
    calibrated, raw = model.predict_with_raw(frame)
    assert np.isclose(raw[0], 10.0)
    assert np.isclose(calibrated[0], 22.0)


def test_stable_low_cardinality_channel_uses_stronger_recent_blend() -> None:
    from src.models.channel_models import ChannelTypeModel

    class FakeBooster:
        def predict(self, x):
            return np.log1p(np.full(len(x), 10.0))

    model = ChannelTypeModel("stable")
    model.model = FakeBooster()
    model.feature_cols = []
    model.smearing_factor = 1.0
    model.stable_baseline_factor = 1.0
    model.stable_baseline_weight = 0.25
    model.stable_low_cardinality_channels = {"店铺"}
    model._prepare_features = lambda df, *args, **kwargs: df.copy()
    frame = pd.DataFrame({
        "_is_future": [True, True], "channel": ["店铺", "其他"],
        "order_count": [np.nan, np.nan],
        "order_count_roll_mean7": [20.0, 20.0],
    })
    calibrated, _ = model.predict_with_raw(frame)
    assert np.isclose(calibrated[0], 14.0)  # 低基数渠道至少40%近期基线
    assert np.isclose(calibrated[1], 12.5)  # 高基数渠道使用内层选出的25%


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print(f"通过 {len(tests)} 项算法回归测试")


if __name__ == "__main__":
    main()
