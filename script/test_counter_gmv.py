import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services.gmv_counter_import import read_excel
from api.services import expected_gmv as g, gmv_actuals


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.payload = (ROOT / 'script/fixtures/gmv_counter.xlsx').read_bytes()

    def mutate(self, ref, value):
        output = BytesIO()
        with ZipFile(BytesIO(self.payload)) as original, ZipFile(output, 'w') as changed:
            for item in original.infolist():
                data = original.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    root = ET.fromstring(data)
                    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    cell = root.find(f'.//s:c[@r="{ref}"]/s:v', ns)
                    cell.text = value
                    data = ET.tostring(root)
                changed.writestr(item, data)
        return output.getvalue()

    def test_real_file_only_estimated_quantity_with_ghost_rows(self):
        year, snapshot, result = read_excel(self.payload, '专柜.xlsx')
        self.assertEqual(year, 2026)
        self.assertEqual(snapshot['sheet_name'], '2026年单量预估')
        self.assertEqual(snapshot['range'], 'A1:D63')
        self.assertTrue(result['quantity_only'])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['channel_order'], [])
        daily = [r for r in result['estimated_orders'] if r['kind'] == 'day']
        self.assertEqual(len(daily), 62)
        self.assertEqual(daily[0]['period'], '2026-09-30')
        self.assertEqual(daily[0]['amount'], 300)
        self.assertEqual(daily[0]['source_cells'], ['C2', 'D2'])
        self.assertEqual(daily[-1]['period'], '2026-11-30')
        self.assertEqual(daily[-1]['amount'], 6500)
        totals = {r['period']: r['amount'] for r in result['estimated_orders'] if r['kind'] == 'summary'}
        self.assertEqual(totals, {'9月合计': 300, '10月合计': 114400, '11月合计': 98000, '导入合计': 212700})
        with patch.object(gmv_actuals.query_source, 'rows') as query:
            self.assertFalse(gmv_actuals.store_actuals({'result': result})['supported'])
        query.assert_not_called()

    def test_invalid_quantity_missing_date_and_duplicates_rejected(self):
        for ref, value in [('D3', '-1'), ('D3', '1.5'), ('D3', None), ('C3', None), ('C3', '46295')]:
            with self.subTest(ref=ref, value=value), self.assertRaises(ValueError):
                read_excel(self.mutate(ref, value), '专柜.xlsx')
        for payload, filename in [(b'bad', '专柜.xlsx'), (self.payload, '专柜.csv')]:
            with self.assertRaises(ValueError):
                read_excel(payload, filename)

    def test_import_versions_preserve_snapshot_and_original_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'STORE', Path(folder)):
            first = g.import_counter_excel(self.payload, '专柜.xlsx')
            second = g.import_counter_excel(self.mutate('D3', '400'), '专柜更新.xlsx')
            self.assertNotEqual(first['id'], second['id'])
            self.assertEqual(first['source_id'], second['source_id'])
            saved = g.get('run', first['id'])
            self.assertEqual(saved['result']['estimated_orders'][1]['amount'], 300)
            self.assertEqual(g.get('run', second['id'])['result']['estimated_orders'][1]['amount'], 400)
            self.assertEqual((Path(folder) / 'imports' / f'{first["id"]}.xlsx').read_bytes(), self.payload)
            with patch.object(g, 'active') as active, self.assertRaises(ValueError):
                g.run_parse(first['source_id'])
            active.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
