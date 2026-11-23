import copy
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services.gmv_parser import parse,verify,SALES_FILING,load_rule


class FilingTests(unittest.TestCase):
    def setUp(self):
        self.code=(ROOT/'ecom_forecast/api/gmv_rules/tmall_sales_filing.py').read_text(encoding='utf-8')
        self.snapshot=json.loads((ROOT/'script/fixtures/tmall_oct_nov_sales_filing.json').read_text(encoding='utf-8'))

    def test_reads_filing_coordinates_and_values(self):
        for sid,bounds in [('tmall_oct_nov',[43,54]),('tmall_sep',[10,21])]:
            s=json.loads((ROOT/f'script/fixtures/{sid}_sales_filing.json').read_text(encoding='utf-8'))
            result=parse(s,self.code,2026)
            self.assertTrue(verify(s,result,2026)['passed'])
            self.assertEqual(result['section_rows'],bounds)
            self.assertEqual(result['target_section'],SALES_FILING)
        result=parse(self.snapshot,self.code,2026)
        record=next(r for r in result['records'] if r['channel']=='货架-常规' and r['period']=='2026-10-01')
        self.assertEqual(record['cell'],'H48')
        self.assertEqual(record['amount'],979052)

    def test_cannot_fall_back_to_gmv_or_read_gmv_coordinates(self):
        old=(ROOT/'ecom_forecast/api/gmv_rules/tmall.py').read_text(encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'口径不匹配'):parse(self.snapshot,old,2026)
        result=parse(self.snapshot,self.code,2026)
        result['records'][0]['cell']='H59'
        self.assertFalse(verify(self.snapshot,result,2026)['passed'])

    def test_newlines_parenthesis_and_missing_section(self):
        s=copy.deepcopy(self.snapshot)
        c=next(c for c in s['cells'] if c['ref']=='A43')
        c['value']='渠道目标-销售\n(备案版)'
        self.assertTrue(verify(s,parse(s,self.code,2026),2026)['passed'])
        c['value']='渠道目标-销售'
        with self.assertRaises(ValueError):parse(s,self.code,2026)
        with self.assertRaises(ValueError):load_rule(self.code.replace('（备案版）',''))


if __name__=='__main__':unittest.main(verbosity=2)
