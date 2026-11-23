"""数据保存响应与无效行处理回归测试。"""
import asyncio
import os
import sys
from datetime import date

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import models  # noqa: E402
from api.database import Base  # noqa: E402
from api.channel_mapper import auto_map_channel, auto_map_columns  # noqa: E402
from api.routers.data import (  # noqa: E402
    _build_aov_history,
    _cache_large_channel_upload,
    _cache_large_warehouse_upload,
    _clean_multiple_platforms_per_shop,
    _normalize_import_platform,
    _normalize_import_platforms,
    save_channel_orders,
    save_live_schedules,
    save_warehouse_orders,
)


async def run_test():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        mapped = auto_map_columns(pd.DataFrame([{
            "订单汇总时间": "2026-01-01", "店铺名称": "一号店", "店铺编码": "S1",
            "渠道分类": "达播", "订单数量": 12, "订单付款金额": 1234.5,
        }]))
        assert list(mapped.columns) == [
            "date", "shop_name", "shop_id", "channel", "order_count", "payment_amount"
        ]
        mapped_channel, need_manual = auto_map_channel(pd.DataFrame([{
            "platform": "TB", "channel": "达播",
        }]), {"platforms": []})
        assert mapped_channel.loc[0, "channel"] == "达播"
        assert need_manual == []

        legacy_channel, _ = auto_map_channel(pd.DataFrame([{
            "channel": "达播",
        }]), {"platforms": []})
        assert legacy_channel.loc[0, "channel"] == "douyin_anchor"

        schedule_columns = auto_map_columns(pd.DataFrame([{
            "店铺ID": "S1", "日期": "2026-10-20", "平台": "DOUYIN",
            "渠道": "达人直播间", "主播": "达人A", "场次": "专场",
            "预估目标": 100000, "预估单量": 500, "事件类型": "live",
            "对应直播日期": None,
        }]))
        assert {"shop_id", "date", "platform", "channel", "anchor_name",
                "session_type", "expected_revenue", "expected_orders",
                "event_type", "broadcast_date"}.issubset(schedule_columns.columns)

        aov = _build_aov_history(pd.DataFrame([
            {"date": "2026-01-01", "order_count": 10, "payment_amount": 100},
            {"date": "2026-01-01", "order_count": 30, "payment_amount": 600},
        ]))
        assert aov[0]["avg_order_value"] == 17.5

        assert _normalize_import_platform(" TB ") == "TM"
        assert _normalize_import_platform("tm") == "TM"
        assert _normalize_import_platform("DY") == "DOUYIN"
        assert _normalize_import_platform("douyin") == "DOUYIN"
        assert _normalize_import_platform("JINGDONG") == "JD"
        assert _normalize_import_platform("JDCS") == "JD"
        assert _normalize_import_platform("JDCZ") == "JD"
        assert _normalize_import_platform("TMFX") == "TM"
        assert _normalize_import_platform("PDD") == "PINDUODUO"
        assert _normalize_import_platform("ks") == "KUAISHOU"
        assert _normalize_import_platform("XHS") == "XIAOHONGSHU"
        assert _normalize_import_platform("唯品会") == "VIP"
        assert _normalize_import_platform("得物") == "POIZON"
        assert _normalize_import_platform("有赞") == "YOUZAN"
        # 不同履约/业务场景不做过度合并。
        assert _normalize_import_platform("DOUYINYUNCANG") == "DOUYINYUNCANG"
        assert _normalize_import_platform("TIKTOK") == "TIKTOK"
        assert _normalize_import_platform("SHIPINHAO") == "SHIPINHAO"
        normalized = _normalize_import_platforms(pd.DataFrame([
            {"platform": "TB"}, {"platform": "tm"}, {"platform": "DY"},
        ]))
        assert normalized["platform"].tolist() == ["TM", "TM", "DOUYIN"]

        # TB/TM 是同一个业务平台，统一为 TM 后不再触发多平台剔除。
        cleaned, cleaning = _clean_multiple_platforms_per_shop(pd.DataFrame([
            {"shop_id": "S1", "platform": "TB", "order_count": 1},
            {"shop_id": "s1", "platform": "tb", "order_count": 2},
            {"shop_id": "S1", "platform": "TM", "order_count": 100},
        ]))
        assert len(cleaned) == 3
        assert set(cleaned["platform"]) == {"TM"}
        assert cleaning["affected_shops"] == 0
        assert cleaning["removed_rows"] == 0

        response = await save_channel_orders({"records": [
            {"date": date(2026, 1, 1), "shop_id": "S1", "shop_name": "一号店",
             "platform": "TM", "channel": "达播", "order_count": "12",
             "payment_amount": "1,234.50"},
            {"date": date(2026, 1, 1), "shop_id": "S1", "channel": "tm", "order_count": "invalid"},
        ]}, session)
        assert response["saved"] == 1
        assert response["skipped"] == 1
        assert response["batch_id"]
        assert session.query(models.ChannelOrder).count() == 1
        order = session.query(models.ChannelOrder).one()
        assert order.payment_amount == 1234.5
        channel = session.query(models.Channel).one()
        assert channel.channel_type == "stable"
        assert channel.include_in_forecast is True
        assert channel.maintenance_mode == "auto"
        shop = session.query(models.Shop).one()
        assert shop.name == "一号店"
        assert shop.binding_type == "multi" and shop.warehouse_id is None
        assert shop.maintenance_mode == "auto"
        assert session.query(models.Platform).one().maintenance_mode == "auto"
        assert response["created_config"] == {
            "platforms": ["TM"], "channels": ["TM/达播"], "shops": ["S1"]
        }

        # Pandas 会把同一数值列中的 None 提升为 NaN；保存前必须还原为 NULL。
        nullable_payment_response = await save_channel_orders({"records": [
            {"date": "2026-01-02", "shop_id": "PAYMENT", "platform": "TM",
             "channel": "货架", "order_count": 3, "payment_amount": 99.5},
            {"date": "2026-01-03", "shop_id": "PAYMENT", "platform": "TM",
             "channel": "货架", "order_count": 2, "payment_amount": None},
        ]}, session)
        assert nullable_payment_response["saved"] == 2
        payment_values = [
            row.payment_amount
            for row in session.query(models.ChannelOrder).filter_by(
                shop_id="PAYMENT"
            ).order_by(models.ChannelOrder.date).all()
        ]
        assert payment_values == [99.5, None]

        clean_response = await save_channel_orders({"records": [
            {"date": "2026-01-01", "shop_id": "CONFLICT", "platform": "TB",
             "channel": "货架", "order_count": 1},
            {"date": "2026-01-02", "shop_id": "CONFLICT", "platform": "TB",
             "channel": "货架", "order_count": 1},
            {"date": "2026-01-03", "shop_id": "CONFLICT", "platform": "TM",
             "channel": "货架", "order_count": 100},
        ]}, session)
        assert clean_response["saved"] == 3
        assert clean_response["skipped"] == 0
        assert clean_response["cleaning_summary"]["removed_rows"] == 0
        conflict_orders = session.query(models.ChannelOrder).filter_by(
            shop_id="CONFLICT"
        ).all()
        assert len(conflict_orders) == 3
        assert {order.platform for order in conflict_orders} == {"TM"}

        large_df = pd.DataFrame([{
            "date": "2026-02-01", "shop_id": "S2", "shop_name": "二号店",
            "platform": "DY", "channel": "货架", "order_count": 1,
            "payment_amount": 20,
        } for _ in range(5001)])
        upload_token, large_batch_id = _cache_large_channel_upload(large_df)
        first_chunk = await save_channel_orders({
            "upload_token": upload_token, "offset": 0, "limit": 5000,
        }, session)
        assert first_chunk["saved"] == 5000 and first_chunk["has_more"] is True
        assert first_chunk["batch_id"] == large_batch_id
        retry_chunk = await save_channel_orders({
            "upload_token": upload_token, "offset": 0, "limit": 5000,
        }, session)
        assert retry_chunk == first_chunk
        assert session.query(models.ChannelOrder).filter_by(upload_batch=large_batch_id).count() == 5000
        final_chunk = await save_channel_orders({
            "upload_token": upload_token, "offset": 5000, "limit": 5000,
        }, session)
        assert final_chunk["saved"] == 1 and final_chunk["has_more"] is False
        assert session.query(models.ChannelOrder).filter_by(upload_batch=large_batch_id).count() == 5001

        # 仓库大文件留在服务端并分块入库；相同 offset 重试必须幂等。
        warehouse_df = pd.DataFrame([{
            "date": f"2026-03-0{index + 1}", "shop_id": "S1",
            "platform": "DY", "channel": "货架", "warehouse_id": "KM01",
            "order_count": index + 1, "is_sample": 0,
        } for index in range(3)])
        warehouse_token, warehouse_batch_id = _cache_large_warehouse_upload(warehouse_df)
        warehouse_first = await save_warehouse_orders({
            "upload_token": warehouse_token, "offset": 0, "limit": 2,
        }, session)
        assert warehouse_first["saved"] == 2 and warehouse_first["has_more"] is True
        warehouse_retry = await save_warehouse_orders({
            "upload_token": warehouse_token, "offset": 0, "limit": 2,
        }, session)
        assert warehouse_retry == warehouse_first
        warehouse_final = await save_warehouse_orders({
            "upload_token": warehouse_token, "offset": 2, "limit": 2,
        }, session)
        assert warehouse_final["saved"] == 1 and warehouse_final["has_more"] is False
        warehouse_rows = session.query(models.WarehouseOrder).filter_by(
            upload_batch=warehouse_batch_id
        ).all()
        assert len(warehouse_rows) == 3
        assert {row.platform for row in warehouse_rows} == {"DOUYIN"}

        import api.services.live_prediction_overlay as overlay_service
        original_refresh = overlay_service.refresh_latest_live_overlay
        overlay_service.refresh_latest_live_overlay = lambda: {"updated": 0}
        session.add(models.LiveSchedule(
            shop_id="S1", date=date(2026, 10, 20), event_type="live",
            anchor_name="达人A", upload_batch="old-schedule",
        ))
        session.add(models.UploadBatchLog(
            data_type="schedule", batch_id="old-schedule", row_count=1,
            data_start=date(2026, 10, 20), data_end=date(2026, 10, 20),
        ))
        session.commit()
        try:
            schedule_response = await save_live_schedules({"records": [{
                "shop_id": "S1", "date": "2026-10-20", "event_type": "live",
                "platform": "DOUYIN", "channel": "达人直播间",
                "anchor_name": "达人A", "expected_orders": 500,
            }]}, session)
        finally:
            overlay_service.refresh_latest_live_overlay = original_refresh
        assert schedule_response["saved"] == 1
        saved_schedule = session.query(models.LiveSchedule).one()
        assert saved_schedule.channel == "达人直播间"
        assert saved_schedule.platform == "DOUYIN"
        assert session.query(models.UploadBatchLog).filter_by(
            data_type="schedule", batch_id="old-schedule"
        ).first() is None

        try:
            await save_live_schedules({"records": []}, session)
            raise AssertionError("空排期应被拒绝")
        except HTTPException as exc:
            assert exc.status_code == 400
        print("[OK] channel upload auto-cleans multi-platform shops and reports skipped rows")
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_test())
