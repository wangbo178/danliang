import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from pydantic import ValidationError
from api.services.gmv_source_months import infer_months,resolve_months
from api.services import expected_gmv as g
from api.routers.expected_gmv import Source


class SourceMonthsTests(unittest.TestCase):
    def test_names_years_lists_and_ranges(self):
        cases={'26年5+6月份':[5,6],'2026年10+11月（抖音调整版）':[10,11],
               '25年10+11月份':[10,11],'2026年9月':[9],'7-8月':[7,8],
               '2026年1至3月':[1,2,3],'5月、6月':[5,6],'2026年':[],
               '13月':[],'12-2月':[],'专柜预估单量':[]}
        for name,expected in cases.items():
            with self.subTest(name=name):self.assertEqual(infer_months(name),expected)

    def test_numeric_validation_and_manual_override(self):
        payload=dict(shop='抖音店铺',name='26年5+6月份',year=2026,url='https://example.feishu.cn/wiki/node')
        for months in [[True],['1'],[0],[13],[],[1.5]]:
            with self.subTest(months=months),self.assertRaises(ValidationError):Source(**payload,months=months)
        self.assertEqual(resolve_months(Source(**payload).model_dump()),[5,6])
        self.assertEqual(resolve_months(Source(**payload,months=[9,7,9]).model_dump()),[7,9])
        with self.assertRaises(ValueError):resolve_months({'name':'未注明月份'})

    def test_backfill_is_idempotent_and_keeps_explicit_values(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(g,'STORE',Path(folder)):
            for source in [{'id':'a','name':'26年5+6月份'},{'id':'b','name':'26年9月','months':[10]}, {'id':'c','name':'2026年'}]:
                g.put('source',source['id'],source)
            self.assertEqual(len(g.backfill_source_months()),1)
            self.assertEqual(g.get('source','a')['months'],[5,6])
            self.assertEqual(g.get('source','b')['months'],[10])
            self.assertNotIn('months',g.get('source','c'))
            self.assertEqual(g.backfill_source_months(),[])

if __name__=='__main__':unittest.main()
