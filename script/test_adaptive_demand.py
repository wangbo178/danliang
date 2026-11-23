"""实验算法行为与状态边界回归，不连接数据库。"""
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ecom_forecast"))
from src.models.adaptive_demand import candidate_forecasts,profile_history,adaptive_combination


class AdaptiveTests(unittest.TestCase):
    def forecasts(self,history):
        dates = pd.date_range("2025-01-01",periods=len(history)+30)
        return candidate_forecasts(history,dates[:len(history)],dates[len(history):])

    def test_weekday_alignment_and_constant_level(self):
        weekly = np.tile(np.arange(7)+100,52).astype(float)
        history = np.column_stack([weekly,np.ones(len(weekly))*100])
        before = history.copy()
        forecasts = self.forecasts(history)
        np.testing.assert_allclose(forecasts["seasonal_naive7"][:,0],np.resize(np.arange(7)+100,30))
        np.testing.assert_allclose(forecasts["weekday8"][:,0],np.resize(np.arange(7)+100,30))
        np.testing.assert_allclose(forecasts["mean28"][:,1],100)
        np.testing.assert_array_equal(history,before)

    def test_intermittent_probability_decays_and_zero_series_stays_zero(self):
        history = np.zeros((365,2))
        history[:200:5,0] = 100
        forecasts = self.forecasts(history)
        self.assertLess(forecasts["tsb"][0,0],forecasts["croston_sba"][0,0]/100)
        for prediction in forecasts.values():
            self.assertTrue(np.isfinite(prediction).all())
            self.assertTrue((prediction>=0).all())
            np.testing.assert_array_equal(prediction[:,1],0)

    def test_classification_uses_scale_changes_and_lifecycle(self):
        history = np.ones((365,4))*100
        history[-28:,1] = 400
        history[-60:,2] = 0
        history[:,3] = 0
        history[-20:,3] = 100
        self.assertEqual(profile_history(history)["tag"].tolist(),["stable","regime_change","dormant","cold_start"])

    def test_selection_uses_earlier_validation_not_future_labels(self):
        history = np.ones((365,2))*100
        actual = np.ones((30,2))*100
        candidates = {"mean7":actual.copy(),"mean28":np.zeros_like(actual)}
        prediction,selection = adaptive_combination(candidates,[(actual,candidates)],history,
                                                    np.array([["a","p","c"],["b","p","c"]]))
        self.assertTrue((prediction>95).all())
        np.testing.assert_allclose(selection["weights"].sum(axis=0),1)

    def test_invalid_or_unprocessed_missing_history_is_rejected(self):
        with self.assertRaises(ValueError):
            self.forecasts(np.zeros((20,1)))
        with self.assertRaises(ValueError):
            self.forecasts(np.full((100,1),np.nan))


if __name__ == "__main__":
    unittest.main()
