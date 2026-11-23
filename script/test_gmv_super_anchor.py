import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine,text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services import expected_gmv as g,gmv_super_anchor_forecast as f
from api.services.gmv_parser import parse,load_rule


class SuperAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=patch.object(g,'STORE',Path(self.tmp.name));self.store.start()
        code=(g.RULES/'tmall.py').read_text(encoding='utf-8')
        rule=load_rule(code);rule.update(section='渠道GMV',end_section='sheet_end',schedule_aliases={'店铺节奏':['店铺力度']},blank_policy='preserve')
        for year,filename,source in [(2026,'gmv_oct_nov.json',code),(2025,'gmv_tmall_2025.json','RULE = '+repr(rule))]:
            snapshot=json.loads((ROOT/'script/fixtures'/filename).read_text(encoding='utf-8'))
            run={'id':str(year),'source_id':str(year),'rule_key':'tmall','status':'success','source':{'shop':'天猫旗舰店'},
                 'year':year,'created_at':f'{year}-11-01','snapshot':snapshot,'result':parse(snapshot,source,year)}
            g.put('run',run['id'],run)
        self.actuals=[{'date':f'2025-10-{day:02d}','source_rows':1,'qty_rows':1,'amount_rows':1,'order_qty':100,'payment_amount':10000} for day in range(20,26)]

    def tearDown(self):
        self.store.stop();self.tmp.cleanup()

    def test_daily_rate_aov_formula_tail_dates_and_query_scope(self):
        with patch.object(f.query_source,'rows',return_value=self.actuals) as query:
            r=f.preview('2026')
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['trigger_dates'],['2026-10-20'])
        self.assertEqual([x['date'] for x in r['rows']],[f'2026-10-{d}' for d in range(20,26)])
        self.assertEqual(r['statistics']['fillable_days'],6)
        self.assertEqual(r['rows'][0]['history_date'],'2025-10-20')
        self.assertEqual(r['rows'][0]['aov'],100)
        target=Decimal(str(r['history_rows'][0]['target_gmv']))
        expected=int((Decimal('262500000')/100*(Decimal(10000)/target)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        self.assertEqual(r['rows'][0]['estimated_order_qty'],expected)
        sql,params=query.call_args.args
        self.assertNotIn('order_status',sql);self.assertIn('shop_name = :shop',sql);self.assertNotIn('platform',sql)
        self.assertEqual(params,{'channel':'达播','anchor':'李佳琦','shop':'薇诺娜天猫旗舰店','start':'2025-10-01','end':'2025-11-15'})
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_continuous_mode_caps_at_november_14(self):
        with patch.object(f.query_source,'rows',return_value=self.actuals):r=f.preview('2026','through_1114')
        self.assertEqual(len(r['rows']),26)
        self.assertEqual(r['rows'][0]['date'],'2026-10-20')
        self.assertEqual(r['rows'][-1]['date'],'2026-11-14')
        self.assertIsNone(r['rows'][-1]['estimated_order_qty'])
        self.assertEqual(r['rows'][-1]['history_date'],'2025-11-14')
        self.assertFalse(r['rows'][-1]['is_tail_day'])

    def test_each_day_has_its_own_rate_and_aov(self):
        actuals=copy.deepcopy(self.actuals)
        for row,qty,paid in zip(actuals,[10,20,30,40,50,60],[300,600,1200,400,2000,6000]):
            row.update(order_qty=qty,payment_amount=paid)
        with patch.object(f.query_source,'rows',return_value=actuals):r=f.preview('2026')
        self.assertEqual([row['aov'] for row in r['rows']],[30,30,40,10,40,100])
        self.assertNotIn('aov',r['statistics'])
        for row,history in zip(r['rows'],r['history_rows']):
            self.assertEqual(row['achievement_rate'],history['actual_gmv']/history['target_gmv'])
            expected=int((Decimal(str(row['target_gmv']))*Decimal(str(history['actual_order_qty']))/Decimal(str(history['target_gmv']))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            self.assertEqual(row['estimated_order_qty'],expected)

    def test_missing_actuals_and_no_historical_trigger_do_not_invent(self):
        with patch.object(f.query_source,'rows',return_value=self.actuals[:-1]):r=f.preview('2026')
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['statistics']['fillable_days'],5)
        self.assertEqual(r['statistics']['skipped_days'],1)
        self.assertIsNone(r['rows'][-1]['estimated_order_qty'])
        old=g.get('run','2025');old['year']=2024;g.put('run','2025',old)
        with patch.object(f.query_source,'rows') as query:r=f.preview('2026')
        self.assertEqual(r['status'],'no_history_trigger');query.assert_not_called()

    def test_shifted_trigger_matches_relative_day_not_calendar_day(self):
        run=g.get('run','2026')
        for day,amount in [('2026-10-19',262500000),('2026-10-20',1000000)]:
            record=next(row for row in run['result']['records'] if row['kind']=='day' and row['channel']=='达播-超头' and row['period']==day)
            record.update(amount=amount,raw=amount)
            next(cell for cell in run['snapshot']['cells'] if cell['ref']==record['cell'])['value']=amount
        g.put('run','2026',run)
        with patch.object(f.query_source,'rows',return_value=self.actuals):r=f.preview('2026')
        self.assertEqual(r['rows'][0]['date'],'2026-10-19')
        self.assertEqual(r['rows'][0]['history_date'],'2025-10-20')
        self.assertEqual(r['rows'][1]['history_date'],'2025-10-21')

    def test_zero_historical_aov_skips_one_day_and_valid_reference_allows_zero_target(self):
        actuals=copy.deepcopy(self.actuals)
        actuals[2].update(order_qty=0,payment_amount=0)
        actuals.append({'date':'2025-11-14','source_rows':1,'qty_rows':1,'amount_rows':1,'order_qty':10,'payment_amount':200})
        with patch.object(f.query_source,'rows',return_value=actuals):r=f.preview('2026','through_1114')
        self.assertIsNone(r['rows'][2]['estimated_order_qty'])
        self.assertEqual(r['rows'][-1]['estimated_order_qty'],0)

    def test_threshold_union_six_days_and_november_cap(self):
        values={d:{'amount':Decimal(v)} for d,v in [('2026-09-30','300000000'),('2026-10-01','200000000'),('2026-11-10','200000001'),('2026-11-11','200000001'),('2026-11-15','300000000')]}
        # 实际targets已排除非10-11月，检测函数也应独立遵守月份约束。
        triggers,days=f.tail_dates(values,2026)
        self.assertEqual(triggers,['2026-11-10','2026-11-11'])
        self.assertEqual(days,[f'2026-11-{d}' for d in range(10,15)])

    def test_second_anchor_daily_parameters_and_eight_million_threshold(self):
        with patch.object(f.query_source,'rows',return_value=self.actuals) as query:
            r=f.preview('2026','tail_only','达播-二梯队')
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['channel'],'达播-二梯队')
        self.assertEqual(r['threshold'],8000000)
        self.assertIn('channel_name <> :anchor',query.call_args.args[0])
        self.assertEqual(r['rows'][0]['aov'],100)
        first=r['rows'][0];previous=r['history_rows'][0]
        expected=int((Decimal(str(first['target_gmv']))*Decimal(100)/Decimal(str(previous['target_gmv']))).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        self.assertEqual(first['estimated_order_qty'],expected)
        self.assertTrue(first['source_cell'].endswith('64'))
        values={d:{'amount':Decimal(v)} for d,v in [('2026-10-19','8000000'),('2026-10-20','8000001')]}
        self.assertEqual(f.tail_dates(values,2026,f.SECOND_THRESHOLD)[0],['2026-10-20'])

    def test_second_anchor_identifies_own_trigger_independent_of_super(self):
        run=g.get('run','2026')
        for day,amount in [('2026-10-19',8000001),('2026-10-20',8000000)]:
            record=next(row for row in run['result']['records'] if row['kind']=='day' and row['channel']=='达播-二梯队' and row['period']==day)
            record.update(amount=amount,raw=amount)
            next(cell for cell in run['snapshot']['cells'] if cell['ref']==record['cell'])['value']=amount
        g.put('run','2026',run)
        with patch.object(f.query_source,'rows',return_value=self.actuals):r=f.preview('2026','tail_only','达播-二梯队')
        self.assertEqual(r['trigger_dates'],['2026-10-19'])
        self.assertEqual(r['rows'][0]['history_date'],'2025-10-20')

    def test_second_sql_excludes_exact_li_jiaqi_and_null_names(self):
        engine=create_engine('sqlite://')
        with engine.connect() as c:
            c.execute(text('CREATE TABLE dwh_db_order_summary (order_create_time TEXT,channel_type TEXT,channel_name TEXT,order_qty INTEGER,order_pay_amount NUMERIC,shop_name TEXT DEFAULT "薇诺娜天猫旗舰店")'))
            for name,qty,paid in [('达人A',10,1000),('李佳琦直播间',20,4000),('',30,9000),('李佳琦',999,99999),(None,999,99999)]:
                c.execute(text("INSERT INTO dwh_db_order_summary (order_create_time,channel_type,channel_name,order_qty,order_pay_amount) VALUES ('2025-10-20 23:59:59','达播',:name,:qty,:paid)"),locals())
            c.execute(text("INSERT INTO dwh_db_order_summary VALUES ('2025-10-20','达播','达人A',999999,1,'其他店')"))
            from api.services import query_source
            rows=query_source.rows(f.SECOND_ACTUAL_SQL,{'channel':'达播','anchor':'李佳琦','shop':'薇诺娜天猫旗舰店','start':'2025-10-01','end':'2025-11-15'},c)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['order_qty'],60)
            self.assertEqual(rows[0]['payment_amount'],14000)
        engine.dispose()


if __name__=='__main__':unittest.main(verbosity=2)
