import calendar
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ecom_forecast'))
from api.services import expected_gmv as g, query_source, gmv_alliance_forecast as f
from api.services.cn_rest_calendar import classify


class AllianceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = patch.object(g,'STORE',Path(self.folder.name)); self.store.start()
        self.engine = create_engine('sqlite://'); self.conn = self.engine.connect()
        self.conn.execute(text('CREATE TABLE ads_ofs_order_daily_summary (pay_date TEXT, store_name TEXT, order_type TEXT, order_count NUMERIC, pay_amount NUMERIC)'))
        days = ['2026-10-01','2026-10-08','2026-10-10','2026-11-01']
        self.run = {'id':'dy','source_id':'dy','rule_key':'douyin','status':'success','source':{'shop':'抖音店铺'},
                    'result':{'parser':'douyin','records':[{'channel':channel,'kind':'day','period':day,'amount':10000}
                              for channel in ['抖音自播','抖音商城','抖音达播'] for day in days]}}
        g.put('run','dy',self.run)

    def tearDown(self):
        self.conn.close(); self.engine.dispose(); self.store.stop(); self.folder.cleanup()

    def insert(self, day, qty, store=None, kind=None):
        self.conn.execute(text('INSERT INTO ads_ofs_order_daily_summary VALUES (:day,:store,:kind,:qty,0)'),
                          {'day':day,'qty':qty,'store':store or f.DOUYIN_STORES[0],'kind':kind or f.ORDER_TYPES[0]})

    def seed(self, month, ordinary=100, special=200, omit=None):
        year, mon = map(int,month.split('-'))
        for n in range(1,calendar.monthrange(year,mon)[1]+1):
            day=f'{month}-{n:02d}'
            if day==omit: continue
            qty=special if classify(day)['special'] else ordinary
            self.insert(day,qty/2)
            self.insert(day,qty/2,store=f.DOUYIN_STORES[1],kind=f.ORDER_TYPES[1])
            self.insert(day,0,store=f.DOUYIN_STORES[2])

    def preview(self, months):
        original=query_source.rows
        with patch.object(f.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.conn)) as query:
            result=f.preview('dy',months)
        return result,query

    def test_sql_daily_sum_whole_month_base_special_factor_and_cross_month(self):
        self.seed('2026-09'); self.seed('2026-10',300,600)
        self.insert('2026-09-01',999999,store='抖音-薇诺娜护肤旗舰店2')
        self.insert('2026-09-01',999999,kind='货架')
        result,query=self.preview(['2026-10','2026-11'])
        self.assertEqual(query.call_count,2)
        self.assertEqual(result['channel'],'抖音精选联盟订单')
        sep=result['rows'][0]
        self.assertEqual(sep['valid_actual_days'],30)
        self.assertEqual(sep['ordinary_mean'],100)
        self.assertEqual(sep['special_mean'],200)
        self.assertEqual(sep['multiplier'],2)
        self.assertEqual(sep['uplift_rate'],1)
        self.assertEqual(sep['mean_order_qty'],round((sep['ordinary_days']*100+sep['special_days']*200)/30,2))
        forecast={row['date']:row for row in result['forecasts']}
        self.assertEqual(forecast['2026-10-01']['amount'],sep['special_estimate'])
        self.assertEqual(forecast['2026-10-08']['amount'],sep['ordinary_estimate'])
        self.assertEqual(forecast['2026-10-10']['amount'],sep['special_estimate'])
        self.assertEqual(forecast['2026-11-01']['history_month'],'2026-10')
        self.assertEqual(query.call_args.args[1]['start'],'2026-10-01')
        self.assertEqual(query.call_args.args[1]['end'],'2026-11-01')
        self.assertEqual({query.call_args.args[1][f'store{i}'] for i in range(3)},set(f.DOUYIN_STORES))
        self.assertEqual({query.call_args.args[1]['type0'],query.call_args.args[1]['type1']},set(f.ORDER_TYPES))
        self.assertNotIn('order_status',query.call_args.args[0])
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_missing_actuals_do_not_become_zero(self):
        self.seed('2026-09',omit='2026-09-03')
        result,_=self.preview(['2026-10'])
        self.assertEqual(result['rows'][0]['status'],'incomplete_actuals')
        self.assertTrue(all(row['amount'] is None for row in result['forecasts']))

    def test_zero_denominator_zero_orders_and_real_negative_uplift(self):
        self.seed('2026-09',0,100)
        result,_=self.preview(['2026-10'])
        self.assertIsNone(result['rows'][0]['multiplier'])
        self.assertIsNone(result['forecasts'][0]['amount'])
        self.assertIsNotNone(next(r['amount'] for r in result['forecasts'] if r['date']=='2026-10-08'))
        self.conn.execute(text('DELETE FROM ads_ofs_order_daily_summary'))
        self.seed('2026-09',0,0)
        result,_=self.preview(['2026-10'])
        self.assertEqual(result['rows'][0]['multiplier'],1)
        self.assertTrue(all(row['amount']==0 for row in result['forecasts']))
        self.conn.execute(text('DELETE FROM ads_ofs_order_daily_summary'))
        self.seed('2026-09',200,100)
        result,_=self.preview(['2026-10'])
        self.assertEqual(result['rows'][0]['uplift_rate'],-.5)

    def test_official_holidays_weekends_and_unknown_year(self):
        self.assertTrue(classify('2026-10-01')['special'])
        self.assertEqual(classify('2026-10-03')['label'],'国庆节、周末')
        self.assertFalse(classify('2026-10-08')['special'])
        self.assertTrue(classify('2026-10-10')['special']) # 按用户要求周六调休工作日仍算周末。
        with self.assertRaises(ValueError): classify('2027-10-01')
        with self.assertRaises(ValueError): f.preview('dy',['2026-12'])


if __name__=='__main__': unittest.main()
