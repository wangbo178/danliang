import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services.gmv_tmall_baby_manual import resolve_dates, build, INPUTS
from api.services import expected_gmv as g


class ManualTests(unittest.TestCase):
    def payload(self):
        return {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02',
                'values': {'预估单量': {'2026-11-01': 100, '2026-11-02': 0},
                           '直播单量': {'2026-11-01': 20, '2026-11-02': 0},
                           '预售单量': {'2026-11-01': 30}}}

    def test_four_rows_null_and_zero_and_total_without_double_count(self):
        payload = self.payload()
        dates, following = resolve_dates(payload, [], [])
        snapshot, result = build(payload, dates, following)
        self.assertEqual([r['channel'] for r in result['quantity_rows']], INPUTS + ['合计总单量'])
        total = result['quantity_rows'][-1]['records']
        self.assertEqual(total[0]['amount'], 150)
        self.assertEqual(total[1]['amount'], 0)
        self.assertEqual(total[-1]['amount'], 150)
        self.assertEqual(total[0]['source_cells'], ['B2', 'B3', 'B4'])
        payload['values']['预售单量']['2026-11-02'] = 0
        _, result = build(payload, dates, following)
        self.assertEqual(result['quantity_rows'][-1]['records'][1]['amount'], 0)
        self.assertEqual(result['quantity_rows'][-1]['records'][-1]['amount'], 150)

    def test_invalid_ranges_and_values_rejected(self):
        for changes in [{'mode': 'bad'}, {'end_date': '2026-10-01'}, {'end_date': '2028-11-01'}]:
            with self.assertRaises(ValueError):
                resolve_dates({**self.payload(), **changes}, [], [])
        for value in [-1, 0.5, True, '1,00', 'bad', 10**13]:
            payload = self.payload()
            payload['values']['预估单量']['2026-11-01'] = value
            with self.assertRaises(ValueError):
                build(payload, ['2026-11-01', '2026-11-02'], None)
        payload = self.payload()
        payload['values']['预估单量']['2026-11-03'] = 1
        with self.assertRaises(ValueError):
            build(payload, ['2026-11-01', '2026-11-02'], None)

    def test_versions_and_concurrent_save_protection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            first = g.save_tmall_baby(self.payload())
            changed = self.payload()
            changed['base_run_id'] = first['id']
            changed['values']['预估单量']['2026-11-01'] = 200
            second = g.save_tmall_baby(changed)
            self.assertEqual(len(g.all_items('run')), 2)
            self.assertEqual(g.get('run', first['id'])['result']['quantity_rows'][0]['records'][0]['amount'], 100)
            self.assertEqual(second['result']['quantity_rows'][0]['records'][0]['amount'], 200)
            with self.assertRaises(ValueError):
                g.save_tmall_baby(changed)

    def test_partial_formatted_inputs_and_existing_totals_recalculated(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = self.payload()
            payload['values'] = {name: {} for name in INPUTS}
            payload['values']['预估单量']['2026-11-01'] = '2,597'
            first = g.save_tmall_baby(payload)
            totals = first['result']['quantity_rows'][-1]['records']
            self.assertEqual(totals[0]['amount'], 2597)
            self.assertIsNone(totals[1]['amount'])
            self.assertEqual(totals[-1]['amount'], 2597)
            original = g.get('run', first['id'])
            original['result'].pop('totals_calculation')
            original['result']['quantity_rows'][-1]['records'][0]['amount'] = None
            g.put('run', first['id'], original)
            g.refresh_tmall_baby_totals()
            latest = g.all_items('run')[0]
            self.assertNotEqual(first['id'], latest['id'])
            self.assertEqual(latest['result']['quantity_rows'][-1]['records'][0]['amount'], 2597)
            self.assertIsNone(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][0]['amount'])
            g.refresh_tmall_baby_totals()
            self.assertEqual(len(g.all_items('run')), 2)

    def test_follow_changes_preserve_overlap_leave_new_dates_blank(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            source = {'id': 'tmall_test', 'rule_key': 'tmall'}
            g.put('source', source['id'], source)
            following = {'id': 'old', 'source_id': source['id'], 'rule_key': 'tmall', 'status': 'success', 'created_at': '2026-01-01',
                         'result': {'records': [{'kind': 'day', 'period': d} for d in ['2026-11-01', '2026-11-02']]}}
            g.put('run', following['id'], following)
            payload = {**self.payload(), 'mode': 'follow', 'tmall_source_id': source['id']}
            first = g.save_tmall_baby(payload)
            following.update(id='new', created_at='2026-12-01')
            following['result']['records'].append({'kind': 'day', 'period': '2026-11-03'})
            g.put('run', following['id'], following)
            g.sync_tmall_baby_dates()
            newest = next(r for r in g.all_items('run') if r['source_id'] == first['source_id'])
            self.assertNotEqual(newest['id'], first['id'])
            self.assertEqual(newest['result']['manual_settings']['end_date'], '2026-11-03')
            daily = [r for r in newest['result']['quantity_rows'][0]['records'] if r['kind'] == 'day']
            self.assertEqual([r['amount'] for r in daily], [100, 0, None])
            count = len(g.all_items('run'))
            g.sync_tmall_baby_dates()
            self.assertEqual(len(g.all_items('run')), count)
            self.assertEqual(g.get('run', first['id'])['result']['manual_settings']['end_date'], '2026-11-02')


if __name__ == '__main__':
    unittest.main(verbosity=2)
