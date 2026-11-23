"""可编辑预测排期表回归测试（使用内存数据库，不触碰业务数据）。"""
from datetime import date, datetime
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.database import Base
from api import models, schemas
from api.routers.results import (
    get_forecast_sheet,
    get_results_summary,
    update_forecast_sheet,
)


def main() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        db.add(models.TrainingJob(
            job_id="sheet-test-job",
            status="done",
            progress=100,
            finished_at=datetime(2026, 1, 1, 10, 0, 0),
            config_snapshot={
                "platforms": [{
                    "platform_id": "platform-a", "name": "实际平台",
                    "channels": [
                        {"channel_id": "channel-a", "name": "实际渠道"},
                        {"channel_id": "manual-channel", "name": "达人排期"},
                    ],
                }],
                "shops": [{"shop_id": "shop-a", "name": "实际店铺"}],
                "excluded_channels": [{
                    "platform_id": "platform-a", "channel_id": "manual-channel",
                    "channel_name": "达人排期",
                }],
            },
        ))
        prediction = models.Prediction(
            job_id="sheet-test-job",
            date=date(2026, 1, 2),
            shop_id="shop-a",
            channel="channel-a",
            platform="platform-a",
            predicted_orders=100,
            raw_predicted_orders=95,
            base_predicted_orders=80,
            anchor_predicted_orders=20,
        )
        db.add(prediction)
        db.flush()
        db.add_all([
            models.WarehousePrediction(
                job_id="sheet-test-job", date=date(2026, 1, 2),
                shop_id="shop-a", channel="channel-a", warehouse_id="wh-a",
                warehouse_orders=60, sample_orders=6,
            ),
            models.WarehousePrediction(
                job_id="sheet-test-job", date=date(2026, 1, 2),
                shop_id="shop-a", channel="channel-a", warehouse_id="wh-b",
                warehouse_orders=40, sample_orders=4,
            ),
        ])
        db.commit()
        prediction_id = prediction.id

        manual_prediction = models.Prediction(
            job_id="sheet-test-job", date=date(2026, 1, 2), shop_id="shop-a",
            channel="manual-channel", platform="platform-a", predicted_orders=0,
            raw_predicted_orders=0,
        )
        db.add(manual_prediction)
        db.commit()

        sheet = get_forecast_sheet(
            job_id=None, keyword=None, platform=None, channel=None, db=db,
        )
        assert sheet["dates"] == ["2026-01-02"]
        records = {row["channel"]: row for row in sheet["records"]}
        assert records["channel-a"]["effective_predicted_orders"] == 100
        assert records["channel-a"]["base_predicted_orders"] == 80
        assert records["channel-a"]["anchor_predicted_orders"] == 20
        assert records["channel-a"]["shop_name"] == "实际店铺"
        assert records["channel-a"]["channel_name"] == "实际渠道"
        assert records["channel-a"]["platform_name"] == "实际平台"
        assert records["manual-channel"]["include_in_forecast"] is False
        assert records["manual-channel"]["channel_name"] == "达人排期"

        result = update_forecast_sheet(
            schemas.ForecastSheetUpdate(
                job_id="sheet-test-job",
                edits=[schemas.ForecastCellEdit(
                    prediction_id=prediction_id,
                    manual_predicted_orders=120,
                    adjustment_note="活动加量",
                )],
            ),
            db=db,
        )
        assert result == {"updated": 1, "adjusted_count": 1}
        db.refresh(prediction)
        assert prediction.manual_predicted_orders == 120
        assert prediction.adjustment_note == "活动加量"
        assert get_results_summary(db=db)["total_predicted_orders"] == 120
        warehouse_total = sum(
            row.warehouse_orders
            for row in db.query(models.WarehousePrediction).all()
        )
        assert round(warehouse_total) == 120

        result = update_forecast_sheet(
            schemas.ForecastSheetUpdate(
                job_id="sheet-test-job",
                edits=[schemas.ForecastCellEdit(
                    prediction_id=prediction_id,
                    manual_predicted_orders=None,
                )],
            ),
            db=db,
        )
        assert result == {"updated": 1, "adjusted_count": 0}
        db.refresh(prediction)
        assert prediction.manual_predicted_orders is None
        assert get_results_summary(db=db)["total_predicted_orders"] == 100
        warehouse_total = sum(
            row.warehouse_orders
            for row in db.query(models.WarehousePrediction).all()
        )
        assert round(warehouse_total) == 100

        update_forecast_sheet(
            schemas.ForecastSheetUpdate(
                job_id="sheet-test-job",
                edits=[schemas.ForecastCellEdit(
                    prediction_id=manual_prediction.id,
                    manual_predicted_orders=50,
                    adjustment_note="运营排期",
                )],
            ),
            db=db,
        )
        manual_warehouses = db.query(models.WarehousePrediction).filter_by(
            channel="manual-channel"
        ).all()
        assert len(manual_warehouses) == 2
        assert round(sum(row.warehouse_orders for row in manual_warehouses)) == 50

    print("forecast sheet adjustment tests passed")


if __name__ == "__main__":
    main()
