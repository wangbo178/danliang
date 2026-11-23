import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine,text
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services import expected_gmv as g,query_source,gmv_special_shelf_forecast as f
from script.test_gmv_history_reference import plan


class SpecialShelfTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.store=patch.object(g,'STORE',Path(self.folder.name));self.store.start()
        g.put('run','current',plan('current','tmall',10,{1:15000000,2:20000000,3:26000000},'2026-10-01'))
        self.engine=create_engine('sqlite://');self.conn=self.engine.connect()
        self.conn.execute(text('CREATE TABLE ads_ofs_order_daily_summary (pay_date TEXT, store_name TEXT, order_type TEXT, order_count NUMERIC, pay_amount NUMERIC)'))

    def tearDown(self):
        self.conn.close();self.engine.dispose();self.store.stop();self.folder.cleanup()

    def insert(self,day,qty,paid,store='薇诺娜天猫旗舰店',kind='货架'):
        self.conn.execute(text('INSERT INTO ads_ofs_order_daily_summary VALUES (:day,:store,:kind,:qty,:paid)'),locals())

    def preview(self,scope='rolling_year',start=None,end=None):
        original=query_source.rows
        with patch.object(f.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.conn)) as query:
            result=f.preview('current',start,end,scope)
        return result,query

    def seed(self):
        self.insert('2025-10-02',10,15000000)
        self.insert('2025-10-01',999,20000000)
        self.insert('2026-09-02',5,5000000);self.insert('2026-09-02',15,15000000)
        self.insert('2026-09-03',30,25000000)
        self.insert('2026-09-04',999,25000001)
        self.insert('2026-09-05',0,22000000)
        self.insert('2026-09-06',None,21000000)
        self.insert('2026-10-02',999,20000000)
        self.insert('2026-10-03',999,26000000)
        self.insert('2026-09-02',999,20000000,store='其他店铺')
        self.insert('2026-09-02',999,20000000,kind='自播')

    def test_threshold_actual_gmv_match_inclusive_tolerance_daily_sum_and_no_leakage(self):
        self.seed();result,query=self.preview()
        self.assertEqual([r['date'] for r in result['rows']],['2026-10-02','2026-10-03'])
        row=result['rows'][0]
        self.assertEqual(row['closest_date'],'2026-09-02')
        self.assertEqual(row['nearest_actual']['date'],'2026-09-02')
        self.assertEqual(row['sample_count'],4)
        self.assertEqual(row['matched_count'],5)
        self.assertEqual(row['mean_order_qty'],15)
        self.assertEqual(row['estimated_order_qty'],15)
        self.assertNotIn('2026-09-04',[r['date'] for r in row['samples']])
        self.assertNotIn('2026-10-02',[r['date'] for r in row['samples']])
        self.assertEqual(query.call_count,1)
        self.assertEqual(query.call_args.args[1],{'start':'2025-10-02','end':'2026-10-03','store':'薇诺娜天猫旗舰店','order_type':'货架'})
        self.assertNotIn('order_status',query.call_args.args[0])
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_scope_dates_missing_samples_and_ordinary_dates_skip(self):
        self.seed()
        result,_=self.preview('previous_month','2026-10-02','2026-10-02')
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(result['rows'][0]['history_start'],'2026-09-01')
        self.assertEqual(result['rows'][0]['history_end_exclusive'],'2026-10-01')
        self.assertEqual(result['rows'][0]['estimated_order_qty'],17)
        result,_=self.preview('current_year','2026-10-02','2026-10-02')
        self.assertEqual(result['rows'][0]['estimated_order_qty'],17)
        result,query=self.preview(start='2026-10-01',end='2026-10-01')
        self.assertEqual(result['rows'],[]);query.assert_not_called()
        self.conn.execute(text('DELETE FROM ads_ofs_order_daily_summary'))
        result,_=self.preview()
        self.assertEqual(result['available_count'],0)
        self.assertTrue(all(r['mean_order_qty'] is None for r in result['rows']))
        self.assertEqual(f.history_start(date(2024,2,29),'rolling_year'),date(2023,2,28))
        with self.assertRaises(ValueError):f.preview('current',scope='bad')

    def test_default_history_includes_2024_october_and_keeps_tolerance(self):
        self.insert('2024-10-21',129066,22000000)
        self.insert('2024-09-30',999999,20000000)
        self.insert('2024-11-11',999999,25000001)
        result,query=self.preview('since_2024_10','2026-10-02','2026-10-02')
        row=result['rows'][0]
        self.assertEqual(row['closest_date'],'2024-10-21')
        self.assertEqual(row['sample_count'],1)
        self.assertEqual(row['estimated_order_qty'],129066)
        self.assertEqual(query.call_args.args[1]['start'],'2024-10-01')
        excluded,_=self.preview('rolling_year','2026-10-02','2026-10-02')
        self.assertEqual(excluded['available_count'],0)

    def test_history_two_matches_actual_gmv_ten_percent_without_high_target_threshold(self):
        from api.services.gmv_special_shelf_forecast import history_two
        g.put('run','current',plan('current','tmall',10,{1:100,2:200},'2026-10-01'))
        for day,qty,paid in [('2024-10-02',10,90),('2024-10-03',20,100),('2024-10-04',30,110),
                             ('2024-10-05',999,111),('2024-10-06',None,105),('2024-10-07',0,95),('2026-10-01',999,100)]:
            self.insert(day,qty,paid)
        original=query_source.rows
        with patch.object(f.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.conn)):
            result=history_two('current',['2026-10-01'])
        self.assertEqual(result['mode'],'reference_two')
        self.assertEqual(result['tolerance'],.1)
        self.assertEqual(len(result['rows']),1)
        row=result['rows'][0]
        self.assertEqual(row['matched_count'],5)
        self.assertEqual(row['sample_count'],4)
        self.assertEqual(row['estimated_order_qty'],15)
        self.assertEqual(row['mean_order_qty'],15)
        self.assertEqual(row['closest_date'],'2024-10-03')
        self.assertNotIn('2024-10-05',[sample['date'] for sample in row['samples']])
        self.assertNotIn('2026-10-01',[sample['date'] for sample in row['samples']])
        with self.assertRaises(ValueError):history_two('current',['2026-12-01'])
        self.assertEqual(g.all_items('forecast_input'),[])

if __name__=='__main__':unittest.main()
