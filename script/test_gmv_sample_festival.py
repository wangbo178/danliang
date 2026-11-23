import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine,text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services import gmv_sample_festival as f,query_source


def row(day,qty,paid,**extra):
    return {'date':day,'source_rows':1,'qty_rows':1,'amount_rows':1,'order_qty':qty,'payment_amount':paid,**extra}


class SampleFestivalTests(unittest.TestCase):
    def test_annual_sums_before_year_average(self):
        rows=[row('2023-10-01',120001,600005),row('2023-10-02',180001,900005),
              row('2024-10-03',400001,2000005),row('2022-10-01',50000,100000),
              row('2026-10-01',900000,1000),row('2024-09-30',800000,1000)]
        result=f.summarize(rows,2026)
        self.assertEqual(result['status'],'success')
        self.assertEqual([r['order_qty'] for r in result['yearly_rows']],[300002,400001])
        self.assertEqual(result['statistics']['year_count'],2)
        self.assertEqual(result['statistics']['matched_days'],3)
        self.assertEqual(result['statistics']['mean_order_qty'],350001.5)
        self.assertEqual(result['statistics']['estimated_order_qty'],350002)
        self.assertEqual(result['unmatched_years'],[2022])

    def test_strict_thresholds_full_precision_missing_and_zero_gmv(self):
        rows=[row('2025-10-01',100000,1000),row('2025-10-02',100001,1000010),
              row('2025-10-03',100001,'1000009.99'),row('2025-10-04',200000,0),
              row('2025-10-05',500000,None,amount_rows=0),row('2025-10-06',200000,-1)]
        result=f.summarize(rows,2026)
        self.assertEqual([r['date'] for r in result['daily_rows']],['2025-10-03','2025-10-04'])
        self.assertEqual(result['statistics']['estimated_order_qty'],300001)
        self.assertEqual(result['invalid_actual_days'],2)
        self.assertEqual(f.summarize([],2026)['status'],'no_samples')

    def test_query_groups_days_excludes_target_year_and_uses_anchor_scope(self):
        engine=create_engine('sqlite://')
        with engine.connect() as connection:
            connection.connection.driver_connection.create_function('MONTH',1,lambda value:int(str(value)[5:7]))
            connection.connection.driver_connection.create_function('DAYOFMONTH',1,lambda value:int(str(value)[8:10]))
            connection.execute(text('CREATE TABLE dwh_db_order_summary (order_create_time TEXT,channel_type TEXT,channel_name TEXT,order_qty NUMERIC,order_pay_amount NUMERIC,shop_name TEXT DEFAULT "薇诺娜天猫旗舰店")'))
            for day,channel,anchor,qty,paid in [
                ('2025-10-01 00:00:00','达播','李佳琦',60001,300000),
                ('2025-10-01 23:59:59','达播','李佳琦',60001,300000),
                ('2026-10-01','达播','李佳琦',999999,1),
                ('2025-09-01','达播','李佳琦',999999,1),
                ('2025-10-01','达播','其他达人',999999,1),
                ('2025-10-01','店铺','李佳琦',999999,1)]:
                connection.execute(text('INSERT INTO dwh_db_order_summary (order_create_time,channel_type,channel_name,order_qty,order_pay_amount) VALUES (:day,:channel,:anchor,:qty,:paid)'),locals())
            connection.execute(text("INSERT INTO dwh_db_order_summary VALUES ('2025-10-10','达播','李佳琦',999999,1,'其他店')"))
            original=query_source.rows
            run={'year':2026,'rule_key':'tmall','source':{'shop':'天猫旗舰店'}}
            with patch.object(f,'context',return_value=(run,[],['2026-10-10'])),patch.object(f.query_source,'rows',side_effect=lambda sql,params:original(sql,params,connection)) as query:
                result=f.preview('run')
            self.assertEqual(result['statistics']['estimated_order_qty'],120002)
            self.assertEqual(result['statistics']['matched_days'],1)
            self.assertEqual(query.call_args.args[1]['end'],'2026-01-01')
            self.assertEqual(query.call_args.args[1]['shop'],'薇诺娜天猫旗舰店')
            self.assertEqual(result['allowed_dates'],['2026-10-10'])
        engine.dispose()

    def test_t_plus_one_uses_strict_thresholds_and_never_chains(self):
        rows=[row('2025-10-01',100001,500005),row('2025-10-02',40001,200005),
              row('2025-10-03',50000,250000),row('2025-10-05',100001,500005),
              row('2025-10-06',40000,200000),row('2025-10-10',100001,500005),
              row('2025-10-11',50000,500000)]
        result=f.summarize(list(reversed(rows)),2026)
        self.assertEqual([r['date'] for r in result['daily_rows']],['2025-10-01','2025-10-02','2025-10-05','2025-10-10'])
        self.assertEqual(result['statistics']['estimated_order_qty'],340004)
        self.assertEqual(result['statistics']['nearby_days'],1)
        self.assertEqual(result['daily_rows'][1]['seed_dates'],['2025-10-01'])
        self.assertEqual(result['daily_rows'][1]['offset_days'],1)

    def test_overlapping_seeds_deduplicate_and_nearby_does_not_add_new_year(self):
        rows=[row('2024-10-01',100001,1),row('2024-10-02',200001,1),row('2024-10-03',50001,1),
              row('2025-10-01',50001,1)]
        result=f.summarize(rows,2026)
        self.assertEqual(result['statistics']['year_count'],1)
        self.assertEqual(result['statistics']['total_order_qty'],350003)
        self.assertEqual(result['yearly_rows'][0]['seed_days'],2)
        self.assertEqual(result['yearly_rows'][0]['nearby_days'],1)
        self.assertEqual(result['unmatched_years'],[2025])

    def test_calendar_neighbor_crosses_month_and_missing_day_does_not_jump(self):
        rows=[row('2025-10-31',100001,500005),row('2025-11-01',60001,300005),
              row('2025-10-01',100001,500005),row('2025-10-03',60001,300005)]
        result=f.summarize(rows,2026)
        self.assertEqual([r['date'] for r in result['daily_rows']],['2025-10-01','2025-10-31','2025-11-01'])
        self.assertEqual(result['statistics']['estimated_order_qty'],260003)
        self.assertEqual(f.summarize(rows,2026,0)['statistics']['estimated_order_qty'],200002)
        self.assertEqual(f.summarize(rows,2026,2)['statistics']['estimated_order_qty'],320004)
        with self.assertRaises(ValueError):f.summarize(rows,2026,-1)


if __name__=='__main__':unittest.main(verbosity=2)
