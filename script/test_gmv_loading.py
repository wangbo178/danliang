import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services import expected_gmv as g
from api.routers import expected_gmv as router


class LoadingTests(unittest.TestCase):
    def test_request_read_cache_reuses_reads_and_invalidates_on_write(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            g.put('source', 'first', {'id': 'first'})
            with g.read_batch():
                first = g.all_items('source')
                self.assertIs(first, g.all_items('source'))
                with g.read_batch():
                    self.assertIs(first, g.all_items('source'))
                g.put('source', 'second', {'id': 'second'})
                second = g.all_items('source')
                self.assertEqual(len(second), 2)
                self.assertIsNot(first, second)
            self.assertIsNone(g.READ_CACHE.get())
            self.assertIsNot(second, g.all_items('source'))

    def test_compact_overview_and_detail_keep_all_history_data(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)), patch.object(g, 'init'):
            for index in range(3):
                g.put('run', str(index), {'id': str(index), 'source_id': 'manual', 'status': 'success',
                    'created_at': str(index), 'snapshot': {'cells': ['original']},
                    'verification': {'passed': True, 'scope': 'manual_input', 'errors': [], 'mappings': ['large']},
                    'result': {'records': [], 'estimated_orders': [], 'quantity_rows': [{'records': [
                        {'kind': 'day', 'period': '2026-11-01', 'amount': 100},
                        {'kind': 'day', 'period': '2026-11-02', 'amount': 200}]}]}})
            with patch.object(g, 'config', return_value={}):
                compact = router.overview(compact=True)
                full = router.overview(compact=False)
            self.assertEqual([r['id'] for r in compact['runs']], ['2', '1', '0'])
            for run in compact['runs']:
                self.assertNotIn('result', run)
                self.assertNotIn('snapshot', run)
                self.assertNotIn('mappings', run['verification'])
                self.assertEqual(run['day_periods'], ['2026-11-01', '2026-11-02'])
                detail = router.run_detail(run['id'])
                self.assertEqual(detail['result'], next(r for r in full['runs'] if r['id'] == run['id'])['result'])
                self.assertEqual(g.get('run', run['id'])['snapshot'], {'cells': ['original']})


if __name__ == '__main__':
    unittest.main(verbosity=2)
