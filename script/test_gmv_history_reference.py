import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services import expected_gmv as g, gmv_history_reference as ref, query_source
from api.services.gmv_parser import parse


def plan(run_id, source_id, month, amounts, created, year=2026):
    cells = [dict(row=r,col=c,ref=f'{c}{r}',value=v) for r,c,v in [(2,'B','日期'),(5,'A','渠道目标-GMV'),(6,'B','货架'),(8,'B','货架'),(9,'B','货架-常规'),(10,'A','核心指标')]]
    records = []
    for i, (day, amount) in enumerate(amounts.items()):
        col = chr(68+i)
        cells.append(dict(row=2,col=col,ref=f'{col}2',value=f'{month}月{day}日'))
        for row in [6,8]: cells.append(dict(row=row,col=col,ref=f'{col}{row}',value=amount*2))
        cells.append(dict(row=9,col=col,ref=f'{col}9',value=amount))
        records.append(dict(channel='货架-常规',kind='day',period=f'{year}-{month:02d}-{day:02d}',amount=amount,cell=f'{col}9'))
    return dict(id=run_id,source_id=source_id,rule_key='tmall',status='success',year=year,shop='天猫旗舰店',created_at=created,
                source={'shop':'天猫旗舰店'},snapshot={'cells':cells},result={'records':records})


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.store = patch.object(g,'STORE',Path(self.folder.name)); self.store.start()
        g.put('run','current',plan('current','tmall_oct_nov',10,{1:100},'2026-10-01'))
        g.put('run','old',plan('old','tmall_sep',9,{1:90,2:100,3:110,4:111,5:89,6:105,7:95},'2026-09-01'))
        self.engine = create_engine('sqlite://'); self.connection = self.engine.connect()
        self.connection.execute(text('CREATE TABLE ads_ofs_order_daily_summary (pay_date TEXT, store_name TEXT, order_type TEXT, order_count NUMERIC, pay_amount NUMERIC)'))

    def tearDown(self):
        self.connection.close(); self.engine.dispose(); self.store.stop(); self.folder.cleanup()

    def insert(self, day, qty, paid, store='薇诺娜天猫旗舰店', order_type='货架'):
        self.connection.execute(text('INSERT INTO ads_ofs_order_daily_summary VALUES (:day,:store,:order_type,:qty,:paid)'), locals())

    def douyin_plan(self, run_id, fixture, values, created):
        snapshot = json.loads((ROOT / 'script/fixtures' / fixture).read_text(encoding='utf-8'))
        snapshot['amount_mode'] = 'source_value'
        for cell in snapshot['cells']:
            if cell['col'] == 'D' and cell['row'] >= 3:
                cell['value'] = values.get(cell['row'], 1000)
        code = (ROOT/'ecom_forecast/api/gmv_rules/douyin.py').read_text(encoding='utf-8')
        return dict(id=run_id, source_id=run_id, rule_key='douyin', status='success', year=2026,
                    shop='抖音店铺', source={'shop':'抖音店铺'}, created_at=created,
                    snapshot=snapshot, result=parse(snapshot, code, 2026))

    def test_douyin_three_store_daily_aggregation_and_average_fill(self):
        g.put('run','dy_current',self.douyin_plan('dy_current','gmv_douyin.json',{3:100},'2026-10-01'))
        g.put('run','dy_old',self.douyin_plan('dy_old','gmv_douyin_sep.json',{3:90,4:100,5:110,6:111},'2026-09-01'))
        for store, qty in zip(ref.DOUYIN_STORES, [10,20,30]):
            self.insert('2026-09-01',qty,qty*10,store=store)
        self.insert('2026-09-02',120,2400,store=ref.DOUYIN_STORES[0])
        self.insert('2026-09-03',0,0,store=ref.DOUYIN_STORES[0])
        self.insert('2026-09-01',99999,99999,store='抖音-薇诺娜护肤旗舰店2')
        self.insert('2026-09-02',99999,99999,store=ref.DOUYIN_STORES[0],order_type='自播')
        self.insert('2026-10-01',99999,99999,store=ref.DOUYIN_STORES[0])
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result = ref.reference('dy_current','2026-10-01')
        self.assertEqual(result['channel'], '抖音自播')
        self.assertEqual(result['target_channel'], '抖音自播')
        self.assertEqual(result['target_gmv'], 100)
        self.assertEqual(result['target_source_cells'], ['D3'])
        self.assertTrue(all(row['source_cells'][0].startswith('D') for row in result['rows']))
        self.assertEqual(result['statistics']['matched_count'], 3)
        self.assertEqual(result['statistics']['mean_order_qty'], 60)
        self.assertEqual(result['statistics']['sample_count'], 3)
        self.assertFalse(result['fallback_enabled'])
        self.assertEqual(result['reference_source'], 'current_year')
        self.assertEqual(result['available_history_months'], ['2026-09'])
        self.assertEqual(query.call_args.args[1], {'start':'2026-01-01','end':min('2027-01-01',ref.date.today().isoformat()),
            'order_type':'货架', **{f'store{i}':store for i,store in enumerate(ref.DOUYIN_STORES)}})
        self.assertEqual({r['run_id'] for r in result['rows']}, {'dy_old'})
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            averages = ref.reference_averages('dy_current')
        self.assertEqual(averages['channel'], '抖音自播')
        self.assertEqual(averages['rows'][0]['mean_order_qty'], 60)
        self.assertEqual(query.call_count, 1) # 全年实绩只查询一次，并按预测日筛选。
        self.assertEqual(g.all_items('forecast_input'), [])

    def test_douyin_no_match_skips_without_last_year_fallback(self):
        g.put('run','dy_current',self.douyin_plan('dy_current','gmv_douyin.json',{3:100},'2026-10-01'))
        g.put('run','dy_old',self.douyin_plan('dy_old','gmv_douyin_sep.json',{},'2026-09-01'))
        with patch.object(ref.query_source,'rows') as query:
            result = ref.reference('dy_current','2026-10-01')
        self.assertEqual(result['status'], 'no_match')
        self.assertIsNone(result['statistics']['mean_order_qty'])
        self.assertEqual(result['searched_months'], [f'2026-{month:02d}' for month in range(1,13)])
        query.assert_not_called()

    def test_douyin_sampling_monthly_average_three_stores_cross_month(self):
        g.put('run','dy_current',self.douyin_plan('dy_current','gmv_douyin.json',{},'2026-10-01'))
        for month, count, amounts in [('2026-09',30,[100,20,5]),('2026-10',31,[200,30,10])]:
            for n in range(1,count+1):
                for store, qty in zip(ref.DOUYIN_STORES,amounts):
                    self.insert(f'{month}-{n:02d}',qty,0,store=store,order_type='派样')
        self.insert('2026-09-01',99999,0,store='抖音-薇诺娜护肤旗舰店2',order_type='派样')
        self.insert('2026-09-01',99999,0,store=ref.DOUYIN_STORES[0],order_type='货架')
        self.insert('2026-11-01',99999,0,store=ref.DOUYIN_STORES[0],order_type='派样')
        original=query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result=ref.sampling_monthly_averages('dy_current',['2026-10','2026-11'])
        self.assertEqual(query.call_count,2)
        self.assertEqual(result['channel'],'派样')
        self.assertEqual(result['stores'],list(ref.DOUYIN_STORES))
        self.assertEqual([r['estimated_order_qty'] for r in result['rows']],[125,240])
        self.assertEqual(result['rows'][0]['total_order_qty'],3750)
        self.assertEqual(query.call_args.args[1]['order_type'],'派样')
        self.assertEqual(query.call_args.args[1]['start'],'2026-10-01')
        self.assertEqual(query.call_args.args[1]['end'],'2026-11-01')
        self.assertNotIn('order_status',query.call_args.args[0])
        with self.assertRaises(ValueError): ref.cps_monthly_averages('dy_current',['2026-10'])
        self.connection.execute(text("DELETE FROM ads_ofs_order_daily_summary WHERE pay_date='2026-09-02'"))
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)):
            incomplete=ref.sampling_monthly_averages('dy_current',['2026-10'])
        self.assertIsNone(incomplete['rows'][0]['estimated_order_qty'])
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_douyin_annual_history_includes_other_months_and_excludes_self_future_and_other_year(self):
        current = self.douyin_plan('dy_current','gmv_douyin.json',{3:100,4:100},'2026-10-01')
        september = self.douyin_plan('dy_old','gmv_douyin_sep.json',{3:100},'2026-09-01')
        may = self.douyin_plan('dy_may','gmv_douyin_may_jun.json',{3:100},'2026-05-01')
        g.put('run','dy_current',current)
        g.put('run','dy_old',september)
        g.put('run','dy_may',may)
        self.insert('2026-05-01',10,100,store=ref.DOUYIN_STORES[0])
        self.insert('2026-09-01',30,300,store=ref.DOUYIN_STORES[1])
        self.insert('2026-10-01',99999,99999,store=ref.DOUYIN_STORES[0])
        self.insert('2026-10-02',99999,99999,store=ref.DOUYIN_STORES[0])
        self.insert('2025-05-01',99999,99999,store=ref.DOUYIN_STORES[0])
        # 同一天的旧解析版本不能作为另一个样本重复计入。
        duplicate = dict(may, id='dy_may_duplicate', created_at='2026-04-01')
        g.put('run',duplicate['id'],duplicate)
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result = ref.reference('dy_current','2026-10-01')
        self.assertEqual(result['statistics']['sample_count'], 2)
        self.assertEqual(result['statistics']['mean_order_qty'], 20)
        self.assertEqual({r['date'] for r in result['rows']}, {'2026-05-01','2026-09-01'})
        self.assertEqual(result['available_history_months'], ['2026-05','2026-06','2026-09'])
        self.assertEqual(result['history_end_exclusive'], '2026-10-01')
        self.assertEqual(query.call_args.args[1]['start'], '2026-01-01')
        g.put('source','dy_may',{'id':'dy_may','history_enabled':False})
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)):
            disabled = ref.reference('dy_current','2026-10-01')
        self.assertEqual(disabled['statistics']['mean_order_qty'],30)
        self.assertEqual({r['date'] for r in disabled['rows']}, {'2026-09-01'})

    def test_ten_percent_boundaries_payment_sql_weighted_aov_and_statistics(self):
        self.insert('2026-09-01',10,2000)
        self.insert('2026-09-02',20,2000); self.insert('2026-09-02',40,8000)
        self.insert('2026-09-03',90,9000)
        self.insert('2026-09-06',None,1000)
        self.insert('2026-09-07',0,0)
        self.insert('2026-09-02',999,99999,store='其他店')
        self.insert('2026-09-02',999,99999,order_type='自播')
        self.insert('2026-10-01',999,99999)
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result = ref.reference('current','2026-10-01')
        query.assert_called_once()
        self.assertIn('ads_ofs_order_daily_summary',query.call_args.args[0])
        self.assertNotIn('order_status',query.call_args.args[0])
        self.assertEqual(query.call_args.args[1],{'start':'2026-09-01','end':'2026-10-01','store':'薇诺娜天猫旗舰店','order_type':'货架'})
        self.assertEqual(result['target_gmv'],100) # 与主表解析值一致，不使用200元的父级货架合计。
        self.assertEqual(result['target_source_cells'], ['D9'])
        self.assertEqual([r['date'] for r in result['rows']],['2026-09-02','2026-09-06','2026-09-07','2026-09-01','2026-09-03'])
        self.assertEqual(result['rows'][0]['payment_amount'],10000)
        self.assertEqual(result['rows'][0]['order_qty'],60)
        self.assertEqual(result['rows'][0]['avg_order_value'],166.67)
        self.assertEqual(result['statistics'],{'matched_count':5,'sample_count':4,'missing_actual_count':1,'median_order_qty':35,'mean_order_qty':40})

    def test_no_match_missing_history_and_zero_never_invent_samples(self):
        for target,status in [(1000,'no_match'),(0,'zero_target')]:
            g.put('run','current',plan('current','tmall_oct_nov',10,{1:target},'2026-10-01'))
            with patch.object(ref.query_source,'rows') as query:
                result=ref.reference('current','2026-10-01')
            self.assertEqual(result['status'],status); self.assertIsNone(result['statistics']['mean_order_qty']); query.assert_not_called()
        g.put('run','current',plan('current','tmall_oct_nov',11,{1:100},'2026-11-01'))
        with patch.object(ref.query_source,'rows') as query:
            self.assertEqual(ref.reference('current','2026-11-01')['status'],'no_history')
        query.assert_not_called()

    def test_latest_plan_per_date_and_missing_actuals(self):
        older=plan('older','tmall_sep',9,{2:100},'2026-08-01'); g.put('run','older',older)
        with patch.object(ref.query_source,'rows',return_value=[]): result=ref.reference('current','2026-10-01')
        self.assertEqual(result['statistics']['matched_count'],5)
        self.assertEqual(result['statistics']['sample_count'],0)
        self.assertTrue(all(r['run_id']=='old' for r in result['rows']))
        self.assertIsNone(result['statistics']['median_order_qty'])

    def test_parent_totals_ignored_but_wrong_canonical_source_rejected(self):
        with self.assertRaises(ValueError): ref.reference('current','2026-10-02')
        current=g.get('run','current'); current['snapshot']['cells'][-2]['value']=999
        g.put('run','current',current)
        self.assertEqual(ref.shelf_targets(current)['2026-10-01']['amount'],100)
        current['result']['records'][0]['amount']=999
        g.put('run','current',current)
        with self.assertRaises(ValueError): ref.reference('current','2026-10-01')

    def test_per_day_fallback_uses_last_year_same_month_and_actuals(self):
        g.put('run','current',plan('current','tmall_oct_nov',10,{1:100,2:200,3:500},'2026-10-01'))
        g.put('run','last_year',plan('last_year','tmall_2025',10,{1:180,2:200,3:220},'2025-10-01',year=2025))
        self.insert('2025-10-01',10,2000)
        self.insert('2025-10-02',30,9000)
        self.insert('2025-10-03',50,20000)
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result = ref.reference('current','2026-10-02')
        self.assertEqual(result['status'],'success')
        self.assertTrue(result['fallback_used'])
        self.assertEqual(result['history_month'],'2025-10')
        self.assertEqual(result['searched_months'],['2026-09','2025-10'])
        self.assertEqual(query.call_args.args[1]['start'],'2025-10-01')
        self.assertEqual(query.call_args.args[1]['end'],'2025-11-01')
        self.assertEqual([r['date'] for r in result['rows']],['2025-10-02','2025-10-01','2025-10-03'])
        self.assertEqual(result['statistics']['median_order_qty'],30)
        self.assertEqual(result['statistics']['mean_order_qty'],30)
        self.assertEqual(result['rows'][0]['avg_order_value'],300)
        self.assertEqual([(o['eligible_count'],o['reference_month']) for o in result['targets']],
                         [(5,'2026-09'),(3,'2025-10'),(0,'2025-10')])

    def test_previous_month_matches_take_priority_even_without_actuals(self):
        g.put('run','last_year',plan('last_year','tmall_2025',10,{1:100},'2025-10-01',year=2025))
        with patch.object(ref.query_source,'rows',return_value=[]) as query:
            result = ref.reference('current','2026-10-01')
        self.assertFalse(result['fallback_used'])
        self.assertEqual(result['history_month'],'2026-09')
        self.assertEqual(result['statistics']['sample_count'],0)
        self.assertEqual(query.call_args.args[1]['start'],'2026-09-01')

    def test_missing_previous_plan_still_searches_last_year(self):
        g.put('run','current',plan('current','tmall_oct_nov',11,{1:100},'2026-11-01'))
        g.put('run','last_year',plan('last_year','tmall_2025',11,{2:100},'2025-11-01',year=2025))
        with patch.object(ref.query_source,'rows',return_value=[]):
            result = ref.reference('current','2026-11-01')
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['history_month'],'2025-11')
        self.assertEqual(result['statistics']['matched_count'],1)

    def test_january_fallback_year_and_month_boundaries(self):
        g.put('run','current',plan('current','tmall_jan',1,{1:100},'2026-01-01'))
        g.put('run','last_year',plan('last_year','tmall_2025',1,{2:100},'2025-01-01',year=2025))
        with patch.object(ref.query_source,'rows',return_value=[]) as query:
            result = ref.reference('current','2026-01-01')
        self.assertEqual(result['searched_months'],['2025-12','2025-01'])
        self.assertEqual(query.call_args.args[1]['end'],'2025-02-01')

    def test_all_averages_skip_missing_and_reuse_actual_month_queries(self):
        g.put('run','current',plan('current','tmall_oct_nov',10,{1:100,2:200,3:500,4:0,5:101},'2026-10-01'))
        g.put('run','last_year',plan('last_year','tmall_2025',10,{1:200},'2025-10-01',year=2025))
        raw = [{'date':'2026-09-02','source_rows':1,'qty_rows':1,'amount_rows':1,'order_count':25,'pay_amount':2500},
               {'date':'2025-10-01','source_rows':1,'qty_rows':1,'amount_rows':1,'order_count':0,'pay_amount':0}]
        with patch.object(ref.query_source,'rows',return_value=raw) as query:
            result = ref.reference_averages('current')
        self.assertEqual(query.call_count,2)
        self.assertEqual(result['available_count'],3)
        self.assertEqual(result['skipped_count'],2)
        self.assertEqual([r['mean_order_qty'] for r in result['rows']],[25,0,None,None,25])
        self.assertEqual(result['rows'][1]['history_month'],'2025-10')
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_previous_month_minimum_actual_qty_ties_and_gmv(self):
        self.insert('2026-09-01',80,8000)
        self.insert('2026-09-02',10,1000); self.insert('2026-09-02',30,6000)
        self.insert('2026-09-03',40,9000)
        self.insert('2026-09-04',None,1000)
        self.insert('2026-09-05',3.5,500)
        self.insert('2026-09-06',1,1,store='其他店')
        self.insert('2026-09-06',1,1,order_type='自播')
        self.insert('2026-10-01',1,1)
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result=ref.previous_month_minimum('current','2026-10')
        self.assertEqual(result['minimum_order_qty'],40)
        self.assertEqual(result['minimum_days'],[
            {'date':'2026-09-02','order_qty':40,'payment_amount':7000},
            {'date':'2026-09-03','order_qty':40,'payment_amount':9000}])
        self.assertEqual(result['valid_actual_days'],3)
        self.assertEqual(result['missing_actual_days'],27)
        self.assertEqual(query.call_args.args[1]['start'],'2026-09-01')
        self.assertEqual(query.call_args.args[1]['end'],'2026-10-01')
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_minimum_zero_missing_month_and_january(self):
        with patch.object(ref.query_source,'rows',return_value=[]):
            result=ref.previous_month_minimum('current','2026-10')
        self.assertEqual(result['status'],'no_actuals')
        self.assertIsNone(result['minimum_order_qty'])
        self.insert('2026-09-01',0,0)
        original = query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)):
            self.assertEqual(ref.previous_month_minimum('current','2026-10')['minimum_order_qty'],0)
        with patch.object(ref.query_source,'rows') as query:
            with self.assertRaises(ValueError):ref.previous_month_minimum('current','2026-12')
            query.assert_not_called()
        g.put('run','current',plan('current','tmall_jan',1,{1:100},'2026-01-01'))
        with patch.object(ref.query_source,'rows',return_value=[]) as query:
            result=ref.previous_month_minimum('current','2026-01')
        self.assertEqual(result['history_month'],'2025-12')
        self.assertEqual(query.call_args.args[1]['start'],'2025-12-01')
        self.assertEqual(query.call_args.args[1]['end'],'2026-01-01')

    def test_cps_full_month_average_uses_daily_sum_and_calendar_days(self):
        current=g.get('run','current')
        current['result']['records'].append({'channel':'货架-CPS','kind':'day','period':'2026-11-01','amount':100})
        g.put('run','current',current)
        for day in range(1,31):self.insert(f'2026-09-{day:02d}',1460,None,order_type='天猫官旗CPS')
        self.insert('2026-09-01',20,0,order_type='天猫官旗CPS')
        for day in range(1,32):self.insert(f'2026-10-{day:02d}',4166,0,order_type='天猫官旗CPS')
        self.insert('2026-10-01',1,0,order_type='天猫官旗CPS')
        self.insert('2026-09-01',999999,0,order_type='货架')
        self.insert('2026-09-01',999999,0,store='其他店',order_type='天猫官旗CPS')
        original=query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result=ref.cps_monthly_averages('current',['2026-10','2026-11'])
        self.assertEqual(query.call_count,2)
        self.assertEqual(query.call_args.args[1]['order_type'],'天猫官旗CPS')
        self.assertEqual([row['total_order_qty'] for row in result['rows']],[43820,129147])
        self.assertEqual([row['mean_order_qty'] for row in result['rows']],[1460.67,4166.03])
        self.assertEqual([row['estimated_order_qty'] for row in result['rows']],[1461,4166])
        self.assertEqual(g.all_items('forecast_input'),[])

    def test_cps_incomplete_month_does_not_fill_missing_days_as_zero(self):
        self.insert('2026-09-01',50,0,order_type='天猫官旗CPS')
        original=query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)):
            row=ref.cps_monthly_averages('current',['2026-10'])['rows'][0]
        self.assertEqual(row['missing_actual_days'],29)
        self.assertEqual(row['status'],'incomplete_actuals')
        self.assertIsNone(row['estimated_order_qty'])
        with patch.object(ref.query_source,'rows') as query:
            with self.assertRaises(ValueError):ref.cps_monthly_averages('current',['2026-12'])
            query.assert_not_called()

    def test_cps_zero_month_and_january_bounds(self):
        g.put('run','current',plan('current','tmall_jan',1,{1:100},'2026-01-01'))
        for day in range(1,32):self.insert(f'2025-12-{day:02d}',0,0,order_type='天猫官旗CPS')
        original=query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            row=ref.cps_monthly_averages('current',['2026-01'])['rows'][0]
        self.assertEqual(query.call_args.args[1]['start'],'2025-12-01')
        self.assertEqual(query.call_args.args[1]['end'],'2026-01-01')
        self.assertEqual(row['estimated_order_qty'],0)
        self.assertEqual(row['status'],'success')

    def test_sampling_average_uses_sampling_only_and_rounds(self):
        for day in range(1,31):self.insert(f'2026-09-{day:02d}',100,0,order_type='派样')
        self.insert('2026-09-01',15,0,order_type='派样')
        self.insert('2026-09-01',999999,0,order_type='天猫官旗CPS')
        original=query_source.rows
        with patch.object(ref.query_source,'rows',side_effect=lambda sql,params:original(sql,params,self.connection)) as query:
            result=ref.sampling_monthly_averages('current',['2026-10'])
        self.assertEqual(query.call_args.args[1]['order_type'],'派样')
        self.assertEqual(result['channel'],'派样')
        self.assertEqual(result['rows'][0]['total_order_qty'],3015)
        self.assertEqual(result['rows'][0]['mean_order_qty'],100.5)
        self.assertEqual(result['rows'][0]['estimated_order_qty'],101)


if __name__=='__main__': unittest.main(verbosity=2)
