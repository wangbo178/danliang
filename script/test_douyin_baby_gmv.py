import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services.gmv_parser import parse, verify, load_rule
from api.services import expected_gmv as g
from api.services import gmv_actuals


class BabyTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'script/fixtures/gmv_douyin_baby.json').read_text(encoding='utf-8'))
        self.code = (ROOT / 'ecom_forecast/api/gmv_rules/douyin_baby.py').read_text(encoding='utf-8')

    def test_real_sales_gmv_without_conversion_or_double_count(self):
        result = parse(self.snapshot, self.code, 2026)
        self.assertTrue(verify(self.snapshot, result, 2026)['passed'])
        self.assertEqual(len(result['records']), 320)
        self.assertEqual(len(result['schedules']), 3)
        first = next(r for r in result['records'] if r['channel'] == '自播官旗' and r['period'] == '2026-10-01')
        self.assertEqual(first['cell'], 'E7')
        self.assertEqual(first['amount'], 110000)
        self.assertEqual(sum(r['amount'] for r in result['records'] if r['period'] == '10+11月目标合计'), 42020000)
        self.assertEqual(result['channel_order'], ['自播官旗','商城','KOC','DP合计','抖音达播'])
        self.assertFalse(any('快手' in r['channel'] or '澜彩' in r['channel'] for r in result['records']))

    def test_wrong_dp_detail_or_kuaishou_row_rejected(self):
        for code in [self.code.replace("['DP合计']", "['澜彩-小蓝油']"), self.code.replace("['抖音达播']", "['快手达播']")]:
            result = parse(self.snapshot, code, 2026)
            self.assertFalse(verify(self.snapshot, result, 2026)['passed'])
        with self.assertRaises(ValueError):
            load_rule(self.code.replace("'unit_multiplier': 1", "'unit_multiplier': 1.42857"))

    def test_missing_date_value_and_schedule_range_rejected(self):
        for ref in ['E3', 'E7']:
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['cells'] = [c for c in snapshot['cells'] if c['ref'] != ref]
            with self.assertRaises(ValueError):
                parse(snapshot, self.code, 2026)
        result = parse(self.snapshot, self.code, 2026)
        result['schedules'][0]['source_cells'].pop()
        self.assertFalse(verify(self.snapshot, result, 2026)['passed'])

    def test_review_and_actuals_remain_separate_from_adult_store(self):
        result = parse(self.snapshot, self.code, 2026)
        scopes = g.scope_cells(result)
        self.assertEqual(len(scopes), 6)
        self.assertEqual(len(scopes['营销节奏']), 122)
        run = {'rule_key': 'douyin_baby', 'year': 2026, 'result': result}
        with patch.object(gmv_actuals.query_source, 'rows', return_value=[]) as query:
            actual = gmv_actuals.store_actuals(run)
        self.assertEqual(query.call_count, 4)
        self.assertEqual(list(actual['channels']), ['自播官旗', '商城', 'KOC', '抖音达播'])
        self.assertEqual(actual['pending_channels'], ['DP合计'])

    def test_history_versions_and_rollback_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = Path(folder) / 'rules'
            rules.mkdir()
            for key in ['tmall','douyin','douyin_baby']:
                (rules / f'{key}.py').write_text((ROOT / f'ecom_forecast/api/gmv_rules/{key}.py').read_text(encoding='utf-8'), encoding='utf-8')
            with patch.object(g, 'RULES', rules), patch.object(g, 'STORE', Path(folder) / 'data'):
                g.init()
                adult_version = g.active('douyin')['id']
                first = g.run_parse('douyin_baby_oct_nov', self.snapshot)
                self.assertEqual(first['status'], 'success')
                initial = g.active('douyin_baby')
                g.activate('douyin_baby', self.code + '\n# 新版本', '测试', initial['id'])
                g.rollback(initial['id'])
                self.assertEqual(g.active('douyin')['id'], adult_version)
                self.assertEqual(g.active('douyin_baby')['code'], initial['code'])
                with self.assertRaises(ValueError):
                    g.activate('douyin_baby', g.active('douyin')['code'], '禁止复用', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
