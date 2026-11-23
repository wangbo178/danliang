import calendar
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine,text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services import expected_gmv as g,query_source,gmv_baby_aov_forecast as f


class BabyAovTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.store=patch.object(g,'STORE',Path(self.folder.name));self.store.start()
        self.engine=create_engine('sqlite://');self.conn=self.engine.connect()
        self.conn.execute(text('CREATE TABLE ads_ofs_order_daily_summary (pay_date TEXT, store_name TEXT, order_count NUMERIC, pay_amount NUMERIC)'))
        channels=f.CHANNELS+['抖音达播']
        self.run={'id':'baby','source_id':'baby','rule_key':'douyin_baby','status':'success',
                  'result':{'parser':'douyin_baby','records':[{'channel':c,'kind':'day','period':day,'amount':9100}
                            for c in channels for day in ['2026-10-01','2026-11-01']]}}
        g.put('run','baby',self.run)

    def tearDown(self):
        self.conn.close();self.engine.dispose();self.store.stop();self.folder.cleanup()

    def insert(self,day,qty,paid,shop='抖音-薇诺娜宝贝母婴旗舰店'):
        self.conn.execute(text('INSERT INTO ads_ofs_order_daily_summary VALUES (:day,:shop,:qty,:paid)'),locals())

    def seed(self):
        for month,count in [('2026-09',30),('2026-10',31)]:
            for n in range(1,count+1):
                qty,paid=((100,1000) if n==1 else (900,90000) if n==2 else (0,0)) if month=='2026-09' else (1,200)
                self.insert(f'{month}-{n:02d}',qty,paid)

    def preview(self,months):
        original=query_source.rows
        with patch.object(f.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.conn)) as query:
            result=f.preview('baby',months)
        return result,query

    def test_weighted_aov_exact_shop_filters_cross_month_and_no_dabo(self):
        self.seed()
        self.insert('2026-09-01',99999,1,'天猫-薇诺娜宝贝旗舰店')
        self.insert('2026-09-01',99999,1,'抖音-薇诺娜旗舰店')
        self.insert('2026-09-01',99999,1,'抖音-其他宝贝旗舰店')
        self.insert('2026-10-01',1,200,'抖音-薇诺娜宝贝专卖店')
        self.insert('2026-11-01',99999,1)
        result,query=self.preview(['2026-10','2026-11'])
        self.assertEqual([r['mean_aov'] for r in result['rows']],[91,200])
        self.assertEqual(result['rows'][0]['total_order_qty'],1000)
        self.assertEqual(result['rows'][0]['total_pay_amount'],91000)
        self.assertEqual({r['channel'] for r in result['forecasts']},set(f.CHANNELS))
        self.assertEqual({r['amount'] for r in result['forecasts'] if r['date']=='2026-10-01'},{100})
        self.assertEqual({r['amount'] for r in result['forecasts'] if r['date']=='2026-11-01'},{46})
        self.assertEqual(query.call_count,2)
        self.assertEqual(query.call_args.args[1],{'store0':'抖音-薇诺娜宝贝专卖店','store1':'抖音-薇诺娜宝贝母婴旗舰店','start':'2026-10-01','end':'2026-11-01'})
        self.assertNotIn('LIKE',query.call_args.args[0])
        self.assertEqual(result['rows'][1]['total_order_qty'],32)
        self.assertNotIn('order_type',query.call_args.args[0])
        self.assertNotIn('order_status',query.call_args.args[0])
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_incomplete_zero_aov_missing_gmv_skip(self):
        self.seed()
        self.conn.execute(text("DELETE FROM ads_ofs_order_daily_summary WHERE pay_date='2026-09-03'"))
        result,_=self.preview(['2026-10'])
        self.assertEqual(result['rows'][0]['status'],'incomplete_actuals')
        self.assertTrue(all(r['amount'] is None for r in result['forecasts']))
        self.insert('2026-09-03',0,0)
        self.conn.execute(text("UPDATE ads_ofs_order_daily_summary SET pay_amount=0 WHERE pay_date<'2026-10-01'"))
        result,_=self.preview(['2026-10'])
        self.assertIsNone(result['rows'][0]['mean_aov'])
        self.assertTrue(all(r['amount'] is None for r in result['forecasts']))
        self.run['result']['records'][0]['amount']=None;g.put('run','baby',self.run)
        with self.assertRaises(ValueError):f.preview('baby',['2026-12'])


if __name__=='__main__':unittest.main()
