"""结果查询的轻量回归测试，不依赖本机 MySQL 数据。"""
import os
import sys
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import models  # noqa: E402
from api.database import Base  # noqa: E402
from api.routers.results import get_backtest_daily, get_latest_predictions, get_results_summary  # noqa: E402
from api.routers.data import aov_daily, list_detail_records, warehouse_daily  # noqa: E402
from api.routers.training import _load_data_from_db, _resolve_data_batch  # noqa: E402


def main():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(models.TrainingJob(
            job_id="job-latest", status="done", progress=100,
            finished_at=datetime(2026, 1, 1, 10, 0, 0),
        ))
        session.add_all([
            models.Prediction(job_id="job-latest", date=date(2026, 1, 2), shop_id="S1", channel="tm", platform="tm", predicted_orders=10, raw_predicted_orders=8),
            models.Prediction(job_id="job-latest", date=date(2026, 1, 2), shop_id="S2", channel="tm", platform="tm", predicted_orders=20, raw_predicted_orders=16),
            models.Prediction(job_id="job-latest", date=date(2026, 1, 2), shop_id="S1", channel="dy", platform="dy", predicted_orders=30, raw_predicted_orders=25),
        ])
        session.add_all([
            models.EvaluationMetric(job_id="job-latest", level="channel", group_key="tm", mae=20, mape=.3, bias=-.1),
            models.EvaluationMetric(job_id="job-latest", level="channel", group_key="dy", mae=10, mape=.2, bias=.1),
            models.EvaluationMetric(
                job_id="job-latest", level="overall", group_key="all",
                mae=8, wape=12.3, mape=.25, bias=-.02,
                n_samples=60, total_actual=1000, total_predicted=980,
                raw_wape=18.0, raw_bias=-.12, raw_total_predicted=880,
            ),
        ])
        session.add_all([
            models.ChannelOrder(date=date(2026, 1, 1), shop_id="S1", platform="tm", channel="tm", order_count=10, payment_amount=100, upload_batch="batch-a", created_at=datetime(2026, 1, 1, 10)),
            models.ChannelOrder(date=date(2026, 1, 2), shop_id="S1", platform="tm", channel="tm", order_count=20, payment_amount=300, upload_batch="batch-a", created_at=datetime(2026, 1, 1, 10)),
            models.ChannelOrder(date=date(2026, 1, 3), shop_id="S2", platform="dy", channel="dy", order_count=30, upload_batch="batch-a", created_at=datetime(2026, 1, 1, 10)),
            models.ChannelOrder(date=date(2026, 2, 1), shop_id="S3", platform="jd", channel="店铺", order_count=40, payment_amount=800, upload_batch="batch-new", created_at=datetime(2026, 2, 1, 10)),
            models.WarehouseOrder(date=date(2026, 1, 1), shop_id="S1", platform="tm", channel="tm", warehouse_id="W1", order_count=10, upload_batch="wh-old", created_at=datetime(2026, 1, 1, 10)),
            models.WarehouseOrder(date=date(2026, 2, 1), shop_id="S3", platform="jd", channel="店铺", warehouse_id="W2", order_count=40, upload_batch="wh-new", created_at=datetime(2026, 2, 1, 10)),
            models.LiveSchedule(date=date(2026, 3, 1), shop_id="S1", channel="直播", event_type="live", anchor_name="主播A", expected_orders=10, upload_batch="schedule-old", created_at=datetime(2026, 1, 1, 10)),
            models.LiveSchedule(date=date(2026, 3, 1), shop_id="S1", channel="直播", event_type="live", anchor_name="主播A", expected_orders=20, upload_batch="schedule-new", created_at=datetime(2026, 2, 1, 10)),
            models.LiveSchedule(date=date(2026, 3, 2), shop_id="S1", channel="直播", event_type="live", anchor_name="主播B", expected_orders=30, upload_batch="schedule-old", created_at=datetime(2026, 1, 1, 10)),
        ])
        session.add_all([
            models.BacktestDailyResult(job_id="job-latest", date=date(2026, 1, 1), level="total", group_key="all", actual_orders=100, predicted_orders=90),
            models.BacktestDailyResult(job_id="job-latest", date=date(2026, 1, 2), level="total", group_key="all", actual_orders=120, predicted_orders=132),
            models.BacktestDailyResult(job_id="job-latest", date=date(2026, 1, 1), level="warehouse", group_key="W1", actual_orders=60, predicted_orders=55),
            models.BacktestDailyResult(job_id="job-latest", date=date(2026, 1, 2), level="warehouse", group_key="W1", actual_orders=70, predicted_orders=77),
        ])
        session.commit()

        assert _resolve_data_batch(session, models.ChannelOrder) == "batch-new"
        assert _resolve_data_batch(session, models.WarehouseOrder) == "wh-new"
        latest_data = _load_data_from_db(session)
        assert len(latest_data["channel_df"]) == 1
        assert latest_data["channel_df"].iloc[0]["shop_id"] == "S3"
        assert len(latest_data["warehouse_df"]) == 1
        assert len(latest_data["schedule_df"]) == 2
        revised_schedule = latest_data["schedule_df"].loc[
            latest_data["schedule_df"]["anchor_name"].eq("主播A")
        ].iloc[0]
        assert revised_schedule["expected_orders"] == 20
        manual_data = _load_data_from_db(session, "batch-a", "wh-old")
        assert len(manual_data["channel_df"]) == 3
        assert len(manual_data["warehouse_df"]) == 1
        try:
            _resolve_data_batch(session, models.ChannelOrder, "missing-batch")
            raise AssertionError("不存在的手动批次应被拒绝")
        except ValueError:
            pass

        common = dict(shop_id=None, channel=None, keyword=None, page=1, page_size=10, db=session)
        trend = get_latest_predictions(view="trend", **common)
        assert trend["total"] == 2
        assert trend["channels"] == ["dy", "tm"]
        tm_row = next(row for row in trend["predictions"] if row["channel"] == "tm")
        assert tm_row["predicted_orders"] == 30
        assert tm_row["raw_predicted_orders"] == 24

        detail = get_latest_predictions(view="detail", **common)
        assert detail["total"] == 3
        assert len(detail["predictions"]) == 3

        filtered = get_latest_predictions(view="detail", **{**common, "keyword": "S2"})
        assert filtered["total"] == 1

        summary = get_results_summary(session)
        assert summary["total_predicted_orders"] == 60
        assert summary["avg_wape"] == 12.3
        assert summary["overall_wape"] == 12.3
        assert summary["wape_method"] == "overall"
        assert summary["evaluation_samples"] == 60
        assert summary["evaluation_total_actual"] == 1000
        assert summary["channel_count"] == 2
        assert summary["raw_wape"] == 18.0
        assert summary["wape_improvement"] == 5.7

        first_page = list_detail_records(
            data_type="channel", batch_id="batch-a", shop_id=None, channel=None,
            date_start=None, date_end=None, after_id=None, page_size=2, db=session,
        )
        assert [r["order_count"] for r in first_page["records"]] == [30, 20]
        assert first_page["has_more"] is True
        second_page = list_detail_records(
            data_type="channel", batch_id="batch-a", shop_id=None, channel=None,
            date_start=None, date_end=None, after_id=first_page["next_cursor"],
            page_size=2, db=session,
        )
        assert [r["order_count"] for r in second_page["records"]] == [10]

        warehouse_trend = warehouse_daily(recent_days=7, db=session)
        assert warehouse_trend["batch_id"] == "wh-new"
        assert len(warehouse_trend["dates"]) == 7
        assert warehouse_trend["dates"][-1] == "2026-02-01"
        assert warehouse_trend["totals"][-1] == 40

        aov_trend = aov_daily(recent_days=7, shop_id=None, channel=None, db=session)
        assert aov_trend["batch_id"] == "batch-new"
        assert len(aov_trend["series"]) == 7
        assert aov_trend["series"][-1]["avg_order_value"] == 20.0
        assert aov_trend["average_aov"] == 20.0
        assert aov_trend["filters"]["channels"] == ["店铺"]

        backtest = get_backtest_daily(days=7, db=session)
        assert backtest["requires_retrain"] is False
        assert backtest["dates"][-1] == "2026-01-02"
        assert backtest["total"]["summary"] == {
            "actual": 220, "predicted": 222, "gap": 2, "wape": 10.0,
        }
        assert backtest["warehouses"]["W1"]["summary"]["gap"] == 2
        print("[OK] API query, dashboard trends and indexed data preview")
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
