import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services.gmv_tmall_baby_manual import build
from api.services import expected_gmv as g


class PrivateTests(unittest.TestCase):
    def test_other_channel_daily_100_activity_zero_and_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02', 'values': {'预计单量': {}, '活动单量': {}}}
            first = g.save_other_channel(payload)
            self.assertEqual(first['shop'], '其它渠道')
            self.assertEqual([r['channel'] for r in first['result']['quantity_rows']], ['预计单量', '活动单量', '合计'])
            self.assertEqual(first['result']['quantity_rows'][0]['records'][0]['amount'], 100)
            self.assertEqual(first['result']['quantity_rows'][-1]['records'][-1]['amount'], 200)
            second = g.save_other_channel({**payload, 'base_run_id': first['id'], 'values': {'预计单量': {'2026-11-01': 0}, '活动单量': {'2026-11-02': '2,597'}}})
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][-1]['amount'], 2697)
            self.assertEqual(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][-1]['amount'], 200)
            health = g.save_ali_health_channel(payload)
            self.assertEqual(health['result']['quantity_rows'][-1]['records'][-1]['amount'], 2000)

    def test_ali_health_defaults_activity_history_and_follow(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02', 'values': {'预计单量': {}, '活动单量': {}}}
            first = g.save_ali_health_channel(payload)
            self.assertEqual(first['shop'], '阿里健康渠道')
            self.assertEqual([row['channel'] for row in first['result']['quantity_rows']], ['预计单量', '活动单量', '合计'])
            self.assertEqual(first['result']['quantity_rows'][0]['records'][0]['amount'], 1000)
            self.assertEqual(first['result']['quantity_rows'][-1]['records'][-1]['amount'], 2000)
            second = g.save_ali_health_channel({**payload, 'base_run_id': first['id'], 'values': {'预计单量': {'2026-11-01': 0}, '活动单量': {'2026-11-02': '2,597'}}})
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][-1]['amount'], 3597)
            self.assertEqual(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][-1]['amount'], 2000)
            ka = g.save_ka_channel(payload)
            self.assertEqual(ka['result']['quantity_rows'][-1]['records'][-1]['amount'], 3000)
            g.put('source', 'tmall', {'id': 'tmall', 'rule_key': 'tmall'})
            g.put('run', 'tmall', {'id': 'tmall', 'source_id': 'tmall', 'rule_key': 'tmall', 'status': 'success', 'created_at': '2026-12-01',
                'result': {'records': [{'kind': 'day', 'period': d} for d in ['2026-11-01', '2026-11-02', '2026-11-03']]}})
            following = g.save_ali_health_channel({**payload, 'mode': 'follow', 'tmall_source_id': 'tmall', 'base_run_id': second['id']})
            self.assertEqual(following['result']['quantity_rows'][-1]['records'][-1]['amount'], 3000)

    def test_pdd_manual_additions_total_and_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02', 'values': {'预计单量': {}, '手工增加单量': {}}}
            first = g.save_pdd_channel(payload)
            rows = first['result']['quantity_rows']
            self.assertEqual([r['channel'] for r in rows], ['预计单量', '手工增加单量', '合计'])
            self.assertIsNone(rows[0]['records'][0]['amount'])
            self.assertIsNone(rows[-1]['records'][-1]['amount'])
            values = {'预计单量': {'2026-11-01': '2,597', '2026-11-02': 0}, '手工增加单量': {'2026-11-01': 100}}
            second = g.save_pdd_channel({**payload, 'base_run_id': first['id'], 'values': values})
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][0]['amount'], 2697)
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][1]['amount'], 0)
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][-1]['amount'], 2697)
            self.assertIsNone(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][-1]['amount'])
            with self.assertRaises(ValueError):
                g.save_pdd_channel({**payload, 'base_run_id': second['id'], 'values': {'预计单量': {}, '活动单量': {}}})

    def test_ka_default_1500_can_be_changed_and_is_independent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02', 'values': {'预计单量': {}, '活动单量': {}}}
            first = g.save_ka_channel(payload)
            self.assertEqual(first['shop'], 'KA-京东-快手-小红书')
            self.assertEqual(first['result']['quantity_rows'][0]['records'][0]['amount'], 1500)
            self.assertEqual(first['result']['quantity_rows'][-1]['records'][-1]['amount'], 3000)
            second = g.save_ka_channel({**payload, 'base_run_id': first['id'], 'values': {'预计单量': {'2026-11-01': 0}, '活动单量': {'2026-11-02': '2,597'}}})
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][-1]['amount'], 4097)
            private = g.save_private_channel(payload)
            self.assertEqual(private['result']['quantity_rows'][-1]['records'][-1]['amount'], 400)
            self.assertEqual(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][-1]['amount'], 3000)

    def test_default_daily_200_and_activity_total(self):
        days = ['2026-11-01', '2026-11-02', '2026-11-03']
        payload = {'mode': 'custom', 'values': {'预计单量': {'2026-11-02': 0, '2026-11-03': None}, '活动单量': {'2026-11-01': '2,597'}}}
        snapshot, result = build(payload, days, None, 'private_manual')
        self.assertEqual([row['channel'] for row in result['quantity_rows']], ['预计单量', '活动单量', '合计'])
        daily = lambda i: [r['amount'] for r in result['quantity_rows'][i]['records'] if r['kind'] == 'day']
        self.assertEqual(daily(0), [200, 0, None])
        self.assertEqual(daily(1), [2597, None, None])
        self.assertEqual(daily(2), [2797, 0, None])
        self.assertEqual(result['quantity_rows'][2]['records'][-1]['amount'], 2797)
        self.assertEqual(snapshot['range'], 'A1:D4')
        self.assertEqual(result['quantity_rows'][2]['records'][0]['source_cells'], ['B2', 'B3'])
        self.assertEqual(next(c for c in snapshot['cells'] if c['ref'] == 'B4')['formula'], '=SUM(B2:B3)')

    def test_history_and_tmall_baby_remain_independent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            payload = {'mode': 'custom', 'start_date': '2026-11-01', 'end_date': '2026-11-02', 'values': {'预计单量': {}, '活动单量': {}}}
            first = g.save_private_channel(payload)
            self.assertEqual(first['shop'], '私密渠道')
            self.assertEqual(first['result']['quantity_rows'][-1]['records'][-1]['amount'], 400)
            second = g.save_private_channel({**payload, 'base_run_id': first['id'], 'values': {'预计单量': {'2026-11-01': 300}, '活动单量': {'2026-11-01': 20}}})
            self.assertEqual(second['result']['quantity_rows'][-1]['records'][-1]['amount'], 520)
            self.assertEqual(g.get('run', first['id'])['result']['quantity_rows'][-1]['records'][-1]['amount'], 400)
            baby = g.save_tmall_baby({**payload, 'values': {'预估单量': {}, '直播单量': {}, '预售单量': {}}})
            self.assertEqual(len(baby['result']['quantity_rows']), 4)
            self.assertIsNone(baby['result']['quantity_rows'][-1]['records'][-1]['amount'])

    def test_follow_new_dates_get_200_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            g.put('source', 'tmall', {'id': 'tmall', 'rule_key': 'tmall'})
            following = {'id': 'old', 'source_id': 'tmall', 'rule_key': 'tmall', 'status': 'success', 'created_at': '2026-01-01',
                         'result': {'records': [{'kind': 'day', 'period': '2026-11-01'}]}}
            g.put('run', 'old', following)
            g.sync_tmall_baby_dates('private_manual')
            private = next(r for r in g.all_items('run') if r['source_id'] == 'private_manual')
            payload = {'mode': 'follow', 'tmall_source_id': 'tmall', 'base_run_id': private['id'], 'values': {'预计单量': {'2026-11-01': 0}, '活动单量': {'2026-11-01': 100}}}
            g.save_private_channel(payload)
            following.update(id='new', created_at='2026-12-01')
            following['result']['records'].append({'kind': 'day', 'period': '2026-11-02'})
            g.put('run', 'new', following)
            g.sync_tmall_baby_dates('private_manual')
            newest = next(r for r in g.all_items('run') if r['source_id'] == 'private_manual')
            self.assertEqual([r['amount'] for r in newest['result']['quantity_rows'][0]['records'] if r['kind'] == 'day'], [0, 200])
            self.assertEqual(newest['result']['quantity_rows'][-1]['records'][-1]['amount'], 300)


if __name__ == '__main__':
    unittest.main(verbosity=2)
