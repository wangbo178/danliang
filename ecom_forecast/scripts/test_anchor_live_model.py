import unittest
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.anchor_live_model import AnchorLiveOrderModel


class AnchorLiveOrderModelTest(unittest.TestCase):
    def setUp(self):
        self.actual = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]),
            "shop_id": ["S1"] * 4,
            "anchor_name": ["A"] * 4,
            "order_count": [100, 120, 140, 160],
        })
        self.event = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]),
            "shop_id": ["S1"] * 4,
            "channel": ["dy_anchor"] * 4,
            "order_count": [150, 170, 190, 210],
        })

    def test_removes_actuals_then_adds_future_schedule(self):
        future = pd.DataFrame({
            "date": pd.to_datetime(["2026-02-01"]),
            "shop_id": ["S1"],
            "channel": ["dy_anchor"],
            "event_type": ["live"],
            "anchor_name": ["A"],
            "expected_orders": [180.0],
        })
        model = AnchorLiveOrderModel().fit(self.actual, future, self.event)
        baseline = model.remove_actuals_from_event_history(self.event, self.actual)
        self.assertEqual(baseline["order_count"].tolist(), [50, 50, 50, 50])
        predicted = model.predict_schedule(future)
        self.assertEqual(len(predicted), 1)
        self.assertGreater(predicted.loc[0, "anchor_predicted_orders"], 0)

    def test_unknown_anchor_uses_nonzero_global_fallback(self):
        model = AnchorLiveOrderModel().fit(self.actual, event_order_df=self.event)
        schedule = pd.DataFrame({
            "date": pd.to_datetime(["2026-02-01"]), "shop_id": ["S1"],
            "channel": ["dy_anchor"], "event_type": ["live"],
            "anchor_name": ["NEW"],
        })
        predicted = model.predict_schedule(schedule)
        self.assertGreater(predicted.loc[0, "anchor_predicted_orders"], 0)

    def test_known_anchor_can_infer_missing_shop(self):
        model = AnchorLiveOrderModel().fit(self.actual, event_order_df=self.event)
        schedule = pd.DataFrame({
            "date": pd.to_datetime(["2026-02-01"]), "shop_id": [""],
            "event_type": ["live"], "anchor_name": ["A"],
        })
        predicted = model.predict_schedule(schedule)
        self.assertEqual(predicted.loc[0, "shop_id"], "S1")
        self.assertEqual(predicted.loc[0, "channel"], "dy_anchor")

    def test_manual_channel_overrides_inferred_channel_and_blank_falls_back(self):
        model = AnchorLiveOrderModel().fit(self.actual, event_order_df=self.event)
        schedule = pd.DataFrame({
            "date": pd.to_datetime(["2026-02-01", "2026-02-02"]),
            "shop_id": ["S1", "S1"],
            "channel": ["达人直播间", "  "],
            "event_type": ["live", "live"],
            "anchor_name": ["A", "A"],
        })
        predicted = model.predict_schedule(schedule)
        self.assertEqual(predicted.loc[0, "channel"], "达人直播间")
        self.assertEqual(predicted.loc[1, "channel"], "dy_anchor")

    def test_future_target_stays_missing_when_history_is_adjusted(self):
        model = AnchorLiveOrderModel().fit(self.actual, event_order_df=self.event)
        combined = pd.concat([
            self.event,
            pd.DataFrame({
                "date": pd.to_datetime(["2026-02-01"]), "shop_id": ["S1"],
                "channel": ["dy_anchor"], "order_count": [float("nan")],
            }),
        ], ignore_index=True)
        adjusted = model.remove_actuals_from_event_history(combined, self.actual)
        self.assertTrue(pd.isna(adjusted.iloc[-1]["order_count"]))

    def test_holdout_metrics_are_computed_on_unseen_event(self):
        train = self.actual.iloc[:3].copy()
        schedule = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-22"]), "shop_id": ["S1"],
            "channel": ["dy_anchor"], "event_type": ["live"],
            "anchor_name": ["A"], "expected_orders": [150.0],
        })
        model = AnchorLiveOrderModel().fit(train, event_order_df=self.event.iloc[:3])
        result = model.evaluate_holdout(
            self.actual, schedule, start_date="2026-01-22", end_date="2026-01-22"
        )
        self.assertEqual(result["n"], 1)
        self.assertGreaterEqual(result["wape"], 0)


if __name__ == "__main__":
    unittest.main()
