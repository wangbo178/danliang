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
from api.services import expected_gmv as g, gmv_actuals


class VipTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'script/fixtures/gmv_vip.json').read_text(encoding='utf-8'))
        self.code = (ROOT / 'ecom_forecast/api/gmv_rules/vip.py').read_text(encoding='utf-8')

    def test_live_targets_and_quantity_keep_source_discrepancy(self):
        result = parse(self.snapshot, self.code, 2026)
        self.assertTrue(verify(self.snapshot, result, 2026)['passed'])
        self.assertEqual(len(result['records']), 64)
        self.assertEqual(len(result['estimated_orders']), 64)
        self.assertEqual(len(result['schedules']), 78)
        for key, values in [('records', [59500000, 30500000, 29000000]), ('estimated_orders', [371875, 190620, 181245])]:
            self.assertEqual([r['amount'] for r in result[key] if r['kind'] == 'summary'], values)
        self.assertEqual(result['estimated_orders'][0]['cell'], 'D22')
        self.assertEqual(result['estimated_orders'][0]['amount'], 4062)
        self.assertIn('相差10单', result['conversion_note'])
        scopes = g.scope_cells(result)
        self.assertEqual(len(scopes['预估单量']), 62)
        self.assertIn('C22', scopes['预估单量'])
        with patch.object(gmv_actuals.query_source, 'rows') as query:
            self.assertFalse(gmv_actuals.store_actuals({'result': result, 'rule_key': 'vip'})['supported'])
        query.assert_not_called()

    def test_wrong_metrics_or_historical_quantity_rejected(self):
        for code in [self.code.replace("'GMV目标'", "'纯销'"), self.code.replace("'26年预估单量'", "'25年单量'")]:
            self.assertFalse(verify(self.snapshot, parse(self.snapshot, code, 2026), 2026)['passed'])
        with self.assertRaises(ValueError):
            load_rule(self.code.replace("'unit_multiplier': 1", "'unit_multiplier': 10000"))
        with self.assertRaises(ValueError):
            parse(self.snapshot, self.code, 2025)

    def test_missing_dates_values_or_changed_evidence_rejected(self):
        for ref in ['D1', 'D7', 'D22']:
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['cells'] = [c for c in snapshot['cells'] if c['ref'] != ref]
            with self.assertRaises(ValueError):
                parse(snapshot, self.code, 2026)
        for key in ['records', 'estimated_orders', 'schedules']:
            result = parse(self.snapshot, self.code, 2026)
            result[key].pop()
            self.assertFalse(verify(self.snapshot, result, 2026)['passed'])

    def test_direct_sheet_link_uses_spreadsheet_without_wiki(self):
        source = next(s for s in g.DEFAULT_SOURCES if s.get('parser') == 'vip')
        with patch.object(g, 'cli', side_effect=[{'spreadsheet': {'spreadsheet': {'title': '唯品会'}}}, {}]) as cli, patch.object(g, 'normalize', return_value=copy.deepcopy(self.snapshot)):
            snapshot = g.read_sheet(source)
        self.assertEqual(snapshot['title'], '唯品会')
        self.assertEqual(cli.call_args_list[0].args[0][:2], ['sheets', '+workbook-info'])
        self.assertEqual(cli.call_args_list[1].args[0][3], 'ZrewsaqnnhmwVst2BO5c29w1nBe')

    def test_history_rollback_and_family_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = Path(folder) / 'rules'
            rules.mkdir()
            for key in ['tmall', 'vip']:
                (rules / f'{key}.py').write_text((ROOT / f'ecom_forecast/api/gmv_rules/{key}.py').read_text(encoding='utf-8'), encoding='utf-8')
            with patch.object(g, 'RULES', rules), patch.object(g, 'STORE', Path(folder) / 'data'):
                g.init()
                initial = g.active('vip')
                first = g.run_parse('vip_oct_nov', self.snapshot)
                self.assertEqual(first['status'], 'success')
                g.activate('vip', self.code + '\n# updated', '测试', initial['id'])
                g.rollback(initial['id'])
                self.assertEqual(g.active('vip')['code'], initial['code'])
                with self.assertRaises(ValueError):
                    g.activate('vip', g.active('tmall')['code'], '禁止跨店铺复用', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
