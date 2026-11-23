"""直连数据源的读写边界、主播去重、全局分仓及分页回归。加 --live 可验证实际只读库。"""
import asyncio
from datetime import date
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ecom_forecast"))
from api import models, schemas
from api.database import Base, get_db
from api.routers import data, training
from api.services import query_source as source
from desktop import DesktopServer
from fastapi import FastAPI
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

LIVE = "--live" in sys.argv
if LIVE:
    sys.argv.remove("--live")


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        cls.app = FastAPI()
        cls.app.include_router(data.router, prefix="/api/data")

        @cls.app.get("/api/health")
        def health():
            return {"status": "ok"}

        def local_session():
            with cls.Session() as db:
                yield db

        cls.app.dependency_overrides[get_db] = local_session
        cls.server = DesktopServer(cls.app)
        cls.server.start(threading.Event(), timeout=10)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.engine.dispose()

    def request(self, path, method="GET"):
        req = urllib.request.Request(self.server.url + path, method=method)
        with self.opener.open(req, timeout=60) as response:
            return json.load(response)

    def test_source_write_routes_are_closed_without_body_or_database_access(self):
        for kind in ("channel", "warehouse", "anchor"):
            for path, method in ((f"/upload/{kind}", "POST"), (f"/save/{kind}", "POST"), (f"/{kind}?delete_all=true", "DELETE")):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request("/api/data" + path, method)
                self.assertEqual(caught.exception.code, 410)

    def test_source_batches_are_empty(self):
        for kind in source.KINDS:
            result = self.request("/api/data/batches?data_type=" + kind)
            self.assertEqual(result, {kind: []})

    def test_remote_options_do_not_require_a_batch(self):
        with patch.object(source, "record_options", return_value={"shops": [{"shop_id": "new"}]}) as call:
            self.assertEqual(self.request("/api/data/record-options?data_type=channel")["shops"][0]["shop_id"], "new")
            call.assert_called_once_with("channel")

    def test_anchor_session_dedupe_and_no_shop_association(self):
        frame = pd.DataFrame([
            dict(id=i, date="2026-10-01", anchor_name="主播甲", anchor_id="aweme-a", source_shop_id="foreign-store",
                 source_shop_name="不匹配的源店铺", live_begin_time=begin, order_count=orders)
            for i, begin, orders in ((1, "2026/10/01 10:00:00", 100),
                                     (2, "2026-10-01 10:00:00", 120),
                                     (3, "2026/10/01 15:00:00", 80))
        ])
        actual = source.prepare_anchor_actuals(frame)
        self.assertEqual(actual["order_count"].tolist(), [200])
        self.assertNotIn("shop_id", actual.columns)
        from src.models.anchor_live_model import AnchorLiveOrderModel
        model = AnchorLiveOrderModel().fit(actual)
        self.assertIn("主播甲", model.anchor_stats)
        self.assertEqual(model.anchor_shop_map, {})

    def test_training_never_reads_old_local_order_batches(self):
        fixtures = {
            "channel_df": pd.DataFrame([dict(date=pd.Timestamp("2026-10-01"), shop_id="new", platform="DOUYIN", channel="其他", order_count=123)]),
            "warehouse_df": pd.DataFrame(), "anchor_df": pd.DataFrame(), "source_read_at": "2026-10-29T12:00:00",
        }
        with self.Session() as db:
            db.add(models.ChannelOrder(date=date(2026, 10, 1), shop_id="old", platform="DOUYIN", channel="其他", order_count=999999, upload_batch="old"))
            db.commit()
            with patch.object(source, "load_training_frames", return_value=fixtures):
                loaded = training._load_data_from_db(db)
            self.assertEqual(loaded["channel_df"]["order_count"].sum(), 123)
            self.assertEqual(loaded["channel_df"]["shop_id"].tolist(), ["new"])

    def test_training_excludes_voided_orders_before_aggregation(self):
        # 执行生产训练 SQL，覆盖混合状态同组、全作废分组及付款金额，明细 SQL 仍保留全部记录。
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("""CREATE TABLE dwh_db_order_summary (
                    summary_key TEXT, order_create_time TEXT, shop_code TEXT, shop_name TEXT,
                    platform TEXT, channel_type TEXT, channel_name TEXT, order_status TEXT,
                    order_qty INTEGER, order_pay_amount NUMERIC)"""))
                statuses = ["-100", "0", "100", "200", "400", "500", "510", "650", "700", "888", "900", "999", " 999 "]
                for index, status in enumerate(statuses):
                    connection.execute(text("""INSERT INTO dwh_db_order_summary VALUES
                        (:key,'2026-10-28 00:00:00','shop-a','店铺A','DOUYIN','其他','',:status,10,100)"""),
                        {"key": str(index), "status": status})
                connection.execute(text("""INSERT INTO dwh_db_order_summary VALUES
                    ('only-voided','2026-10-28 00:00:00','shop-b','店铺B','DOUYIN','其他','','999',500,5000)"""))
                aggregated = source._frame(source.CHANNEL_TRAIN_SQL, connection)
                self.assertEqual(aggregated["shop_id"].tolist(), ["shop-a"])
                self.assertEqual(aggregated["order_count"].tolist(), [110])
                self.assertEqual(aggregated["payment_amount"].tolist(), [1100])
                self.assertEqual(len(source._frame(source.CHANNEL_SQL, connection)), 14)
        finally:
            engine.dispose()

    def test_first_training_requires_all_phases_before_reusing_old_models(self):
        from fastapi import HTTPException
        with self.Session() as db, patch.object(source, "ensure_dimensions") as ensure:
            with self.assertRaises(HTTPException) as caught:
                training.start_training(schemas.TrainingStartRequest(phases=["channel"]), db)
            self.assertEqual(caught.exception.status_code, 400)
            ensure.assert_not_called()

    def test_global_routing_and_holdout_compare_the_same_grain(self):
        from src.pipeline.forecast_pipeline import ForecastPipeline
        config = {
            "warehouses": [{"warehouse_id": name, "is_active": True} for name in ("上海仓", "武汉仓")],
            "shop_warehouse_binding": [], "routing_rule_change_date": "2026-09-01", "routing_transition_days": 0,
        }
        pipeline = ForecastPipeline(config_dict=config)
        orders = pd.DataFrame([dict(date=day, shop_id=shop, channel="其他", platform="DOUYIN", order_count=count)
                               for day in pd.date_range("2026-10-01", periods=10) for shop, count in (("a", 60), ("b", 40))])
        warehouse = pd.DataFrame([dict(date=day, shop_id="", channel="", warehouse_id=name, order_count=count)
                                  for day in pd.date_range("2026-10-01", periods=10) for name, count in (("上海仓", 60), ("武汉仓", 40))])
        warehouse.attrs["routing_scope"] = "global"
        pipeline.train_routing(warehouse[warehouse["date"] <= pd.Timestamp("2026-10-08")])
        self.assertEqual(pipeline.warehouse_router.routing_ratios, {})
        self.assertAlmostEqual(pipeline.warehouse_router.global_default_ratio[0]["上海仓"], 0.6)
        pipeline.channel_ensemble.models["stable"] = SimpleNamespace(shop_sensitivity_map={}, shop_override_map={})
        with patch.object(pipeline, "predict_channels", side_effect=lambda future, history, **kw: future.assign(predicted_orders=future["shop_id"].map({"a": 60, "b": 40}))):
            pipeline.evaluate_on_holdout(orders, warehouse, holdout_days=2)
        merged = pipeline.warehouse_holdout_merged
        self.assertEqual(len(merged), 4)
        self.assertNotIn("shop_id", merged.columns)
        self.assertEqual(float((merged["order_count"] - merged["warehouse_orders_pred"]).abs().sum()), 0)
        self.assertEqual(len(pipeline.warehouse_router.get_routing_summary()), 2)
        pipeline.config_dict["excluded_channels"] = [{"platform_id": "DOUYIN", "channel_id": "达人其他"}]
        with patch.object(pipeline, "predict_channels", side_effect=lambda future, history, **kw: future.assign(predicted_orders=future["shop_id"].map({"a": 60, "b": 40}))):
            pipeline.evaluate_on_holdout(orders, warehouse, holdout_days=2)
        self.assertTrue(pipeline.warehouse_holdout_merged.empty)

    def test_warehouse_mapping_preserves_all_inventory_orders(self):
        cases = {"上海电商仓": "上海仓", "武汉线上仓": "武汉仓", "上海贝芙汀昆明仓": "上海仓", "妮贝湖州2C仓": "湖州仓", "未知库存地点": "未知库存地点"}
        for raw, expected in cases.items():
            self.assertEqual(source.logical_warehouse(raw), expected)

    @unittest.skipUnless(LIVE, "加 --live 运行实际只读库查询")
    def test_live_source_pagination_trends_and_read_only_session(self):
        overview = self.request("/api/data/overview")
        self.assertEqual(overview["source_mode"], "direct")
        self.assertGreater(overview["channel_orders"], 0)
        for kind in ("channel", "warehouse", "anchor"):
            first = self.request(f"/api/data/records?data_type={kind}&page_size=10")
            self.assertTrue(first["has_more"])
            second = self.request(f"/api/data/records?data_type={kind}&page_size=10&after_id=" + urllib.parse.quote(first["next_cursor"]))
            key = (lambda row: (row["date"], row["facility_name"])) if kind == "warehouse" else (lambda row: row["id"])
            self.assertFalse({key(row) for row in first["records"]} & {key(row) for row in second["records"]})
        trend = self.request("/api/data/warehouse-daily?recent_days=7")
        self.assertIn("上海仓", trend["warehouses"])
        self.assertIn("武汉仓", trend["warehouses"])
        self.assertEqual(sum(trend["totals"]), sum(sum(values) for values in trend["series"].values()))
        self.assertIsNone(trend["batch_id"])
        self.assertGreater(self.request("/api/data/aov-daily?recent_days=7")["total_orders"], 0)
        with source.source_connection() as connection:
            self.assertEqual(source.rows("SELECT @@session.transaction_read_only flag", connection=connection)[0]["flag"], 1)
            expected = source.rows("""SELECT SUM(order_qty) orders,SUM(order_pay_amount) amount
                FROM dwh_db_order_summary WHERE COALESCE(TRIM(order_status),'') <> '999'""", connection=connection)[0]
            actual = source.rows(f"SELECT SUM(order_count) orders,SUM(payment_amount) amount FROM ({source.CHANNEL_TRAIN_SQL}) training", connection=connection)[0]
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
