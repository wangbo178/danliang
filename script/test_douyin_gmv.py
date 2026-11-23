"""抖音独立解析、纯销换算、范围核验及店铺隔离。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services.gmv_parser import parse, verify
from api.services import expected_gmv as g
from api.services import gmv_actuals
from api.services.gmv_douyin_parser import conversion_from_source


class DouyinTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'script/fixtures/gmv_douyin.json').read_text(encoding='utf-8'))
        self.code = (ROOT / 'ecom_forecast/api/gmv_rules/douyin.py').read_text(encoding='utf-8')

    def test_real_table_and_conversion(self):
        result = parse(self.snapshot, self.code, 2026)
        self.assertTrue(verify(self.snapshot, result, 2026)['passed'])
        self.assertEqual(len(result['records']), 192)
        self.assertEqual(len(result['schedules']), 8)
        self.assertEqual(result['channel_order'], ['抖音自播', '抖音商城', '抖音达播'])
        first = result['records'][0]
        self.assertEqual(first['cell'], 'D3')
        self.assertAlmostEqual(first['amount'], 2406000 / .7)
        self.assertEqual(result['actuals_status'], 'pending_sql')
        self.assertTrue(all(r['source_cells'][0][0] in 'DEF' for r in result['records']))

    def test_summary_uses_daily_sources_once(self):
        result = parse(self.snapshot, self.code, 2026)
        for channel in result['channel_order']:
            rows = [r for r in result['records'] if r['channel'] == channel]
            daily = [r for r in rows if r['kind'] == 'day']
            total = next(r for r in rows if r['period'] == '总计')
            self.assertEqual(len(total['source_cells']), 61)
            self.assertTrue(total['derived'])
            self.assertAlmostEqual(total['amount'], sum(r['amount'] for r in daily), places=5)

    def test_wrong_column_and_schedule_mapping_rejected(self):
        result = parse(self.snapshot, self.code.replace("['抖音自播']", "['快手自播']"), 2026)
        self.assertFalse(verify(self.snapshot, result, 2026)['passed'])
        wrong_schedule = self.code.replace("'date': 'M'", "'date': 'K'").replace("'anchor': 'K'", "'anchor': 'M'")
        with self.assertRaisesRegex(ValueError, '达人排期规则与原表表头不符'):
            parse(self.snapshot, wrong_schedule, 2026)

    def test_may_june_duplicate_channel_blocks_and_no_schedule(self):
        snapshot = json.loads((ROOT/'script/fixtures/gmv_douyin_may_jun.json').read_text(encoding='utf-8'))
        result = parse(snapshot, self.code, 2026)
        self.assertTrue(verify(snapshot, result, 2026)['passed'])
        daily = [r for r in result['records'] if r['kind'] == 'day']
        self.assertEqual(len(daily), 183)
        self.assertEqual(result['schedules'], [])
        self.assertEqual(daily[0]['amount'], 1118162)
        self.assertEqual({r['cell'][0] for r in daily}, set('DEF'))
        tampered = copy.deepcopy(result)
        tampered['records'][0].update(cell='K3', source_cells=['K3'], amount=1597374.22235023)
        self.assertFalse(verify(snapshot, tampered, 2026)['passed'])
        ambiguous = copy.deepcopy(snapshot)
        ambiguous['cells'] = [c for c in ambiguous['cells'] if c['ref'] != 'I1']
        with self.assertRaisesRegex(ValueError, '列缺失或不唯一'):
            parse(ambiguous, self.code, 2026)
        missing_raw_header = copy.deepcopy(snapshot)
        missing_raw_header['cells'] = [c for c in missing_raw_header['cells'] if c['ref'] != 'D2']
        with self.assertRaisesRegex(ValueError, '列缺失或不唯一'):
            parse(missing_raw_header, self.code, 2026)

    def test_schedule_uses_header_semantics(self):
        result = parse(self.snapshot, self.code, 2026)
        lookup = {c['ref']: c['value'] for c in self.snapshot['cells']}
        first = result['schedules'][0]
        self.assertTrue(first['cell'].startswith('M'))
        row = first['cell'][1:]
        self.assertEqual(first['date'], str(lookup['M'+row]).strip())
        self.assertIn('达人：'+str(lookup['K'+row]), first['text'])

    def test_early_year_missing_date_header_and_mall_alias(self):
        code = self.code.replace("'date_column': 'A'", "'date_column': 'B'").replace("['商城汇总']", "['商城汇总', '商城']")
        for name, days in [('gmv_douyin_jan.json',31),('gmv_douyin_feb.json',28)]:
            snapshot = json.loads((ROOT/'script/fixtures'/name).read_text(encoding='utf-8'))
            result = parse(snapshot, code, 2026)
            self.assertTrue(verify(snapshot, result, 2026)['passed'])
            self.assertEqual(sum(r['kind']=='day' for r in result['records']), days*3)
            self.assertEqual(result['records'][0]['cell'], 'E3')
            self.assertEqual(result['schedules'], [])
            with self.assertRaises(ValueError):
                parse(snapshot, code, 2025)
            altered = copy.deepcopy(result)
            altered['records'][0]['period'] = '2025-01-01'
            self.assertFalse(verify(snapshot, altered, 2026)['passed'])

    def test_missing_date_value_year_or_conversion_rejected(self):
        for ref in ['A33', 'D3', 'B1']:
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['cells'] = [c for c in snapshot['cells'] if c['ref'] != ref]
            with self.assertRaises(ValueError):
                parse(snapshot, self.code, 2026)
        with self.assertRaises(ValueError):
            parse(self.snapshot, self.code, 2025)
        with self.assertRaises(ValueError):
            parse(self.snapshot, self.code.replace("'gmv_divisor': 0.7", "'gmv_divisor': 0.8"), 2026)

    def test_review_requires_only_douyin_ranges(self):
        result = parse(self.snapshot, self.code, 2026)
        scopes = g.scope_cells(result)
        self.assertEqual(set(scopes), {'抖音自播', '抖音商城', '抖音达播', '达人意向排期'})
        self.assertEqual(len(scopes['抖音自播']), 61)
        review = {'approved': True, 'issues': [], 'summary': '通过', 'scope_checks': [
            {'target': name, 'cells': refs, 'correct': True, 'reason': '按抖音原表独立定位'} for name, refs in scopes.items()]}
        g.validate_scope_review(review, result)
        review['scope_checks'].pop()
        with self.assertRaises(ValueError):
            g.validate_scope_review(review, result)

    def test_september_explicit_sales_conversion_and_empty_schedule(self):
        snapshot=json.loads((ROOT/'script/fixtures/gmv_douyin_sep.json').read_text(encoding='utf-8'))
        result=parse(snapshot,self.code,2026)
        self.assertTrue(verify(snapshot,result,2026)['passed'])
        self.assertEqual(len(result['records']),96)
        self.assertEqual(result['schedules'],[])
        self.assertIn('销售÷0.7',result['conversion_note'])
        self.assertEqual({r['cell'][0] for r in result['records'] if r['kind']=='day'},set('DEF'))
        with self.assertRaises(ValueError):parse(snapshot,self.code.replace("'gmv_divisor': 0.7","'gmv_divisor': 0.8"),2026)

    def test_missing_ambiguous_conflicting_or_invalid_conversion_rejected(self):
        for value in ['销售 0.7','GMV维度/0.7','销售 如果gmv维度/0',
                      '销售 如果gmv维度/0.7；销售 如果GMV维度/0.8',
                      '销售 如果GMV维度/0.0','销售 如果GMV维度/1.2']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                conversion_from_source({'cells':[{'row':1,'value':value}]})

    def test_source_value_policy_and_independent_verification(self):
        snapshot = json.loads((ROOT/'script/fixtures/gmv_douyin_sep.json').read_text(encoding='utf-8'))
        converted = parse(snapshot, self.code, 2026)
        snapshot['amount_mode'] = 'source_value'
        result = parse(snapshot, self.code, 2026)
        self.assertTrue(verify(snapshot, result, 2026)['passed'])
        self.assertFalse(verify(snapshot, converted, 2026)['passed'])
        self.assertEqual(result['unit_multiplier'], 1)
        first = next(r for r in result['records'] if r['channel'] == '抖音自播' and r['period'] == '2026-09-01')
        self.assertEqual(first['amount'], 1090691)
        self.assertEqual(first['cell'], 'D3')
        for channel in result['channel_order']:
            daily = [r for r in result['records'] if r['channel'] == channel and r['kind'] == 'day']
            total = next(r for r in result['records'] if r['channel'] == channel and r['period'] == '总计')
            self.assertAlmostEqual(total['amount'], sum(r['amount'] for r in daily), places=5)
        self.assertAlmostEqual(parse(self.snapshot, self.code, 2026)['records'][0]['amount'], 2406000 / .7)
        october = copy.deepcopy(self.snapshot)
        october['amount_mode'] = 'source_value'
        original = parse(october, self.code, 2026)
        self.assertTrue(verify(october, original, 2026)['passed'])
        self.assertEqual(original['records'][0]['amount'], 2406000)
        first_day = {r['channel']: r['amount'] for r in original['records'] if r['period'] == '2026-10-01'}
        self.assertEqual(first_day, {'抖音自播':2406000, '抖音商城':800000, '抖音达播':900000})

    def test_source_policy_persists_in_run_without_changing_old_history(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(g, 'STORE', Path(folder)):
                g.activate('douyin', self.code, '测试规则', None)
                source = {'id':'sep', 'shop':'抖音店铺', 'year':2026, 'rule_key':'douyin', 'parser':'douyin'}
                g.put('source', 'sep', source)
                snapshot = json.loads((ROOT/'script/fixtures/gmv_douyin_sep.json').read_text(encoding='utf-8'))
                previous = g.run_parse('sep', snapshot)
                source['amount_mode'] = 'source_value'
                g.put('source', 'sep', source)
                current = g.run_parse('sep', snapshot)
                self.assertEqual(current['status'], 'success')
                self.assertEqual(current['result']['records'][0]['amount'], 1090691)
                self.assertEqual(g.get('run', previous['id'])['result']['records'][0]['amount'], 1558130)
                self.assertNotIn('amount_mode', snapshot)

    def test_store_rules_are_isolated_and_actuals_do_not_query_tmall(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = Path(folder) / 'rules'
            rules.mkdir()
            tmall = (ROOT / 'ecom_forecast/api/gmv_rules/tmall.py').read_text(encoding='utf-8')
            (rules / 'tmall.py').write_text(tmall, encoding='utf-8')
            (rules / 'douyin.py').write_text(self.code, encoding='utf-8')
            with patch.object(g, 'RULES', rules), patch.object(g, 'STORE', Path(folder) / 'data'):
                g.init()
                tmall_version = g.active('tmall')['id']
                result = g.run_parse('douyin_oct_nov', self.snapshot)
                self.assertEqual(result['status'], 'success')
                self.assertEqual(g.active('tmall')['id'], tmall_version)
                with self.assertRaises(ValueError):
                    g.activate('tmall', self.code, '禁止混用', None)
                with self.assertRaises(ValueError):
                    g.activate('douyin', tmall, '禁止混用', None)
                with patch.object(gmv_actuals.query_source, 'rows') as query:
                    self.assertFalse(gmv_actuals.tmall_actuals(g.get('run', result['id']))['supported'])
                    query.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
