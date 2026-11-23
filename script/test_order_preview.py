import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecom_forecast'))
from api.services import expected_gmv as g,gmv_forecast_inputs as f,gmv_order_preview as p
from api.services.gmv_parser import CHANNELS


class OrderPreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.store=patch.object(g,'STORE',Path(self.folder.name));self.store.start()
        self.run={'id':'current','source_id':'tmall','rule_key':'tmall','status':'success','created_at':'2026-10-01',
                  'source':{'shop':'天猫旗舰店','name':'2026年10+11月'},
                  'result':{'records':[{'channel':c,'kind':'day','period':day,'amount':1000} for c in CHANNELS for day in ['2026-10-01','2026-11-01','2026-11-02']]}}
        old=copy.deepcopy(self.run);old.update(id='older',created_at='2026-09-01');g.put('run','older',old)
        g.put('run','current',self.run)
        values=f.read('current')['values']
        for channel,qty in {'货架-常规':100,'货架-CPS':20,'百补':10,'达播-超头':30,'达播-二梯队':40,'派样':5}.items():
            values['orders'][channel]['2026-11-01']=qty
        values['aov']['货架-常规']['2026-11-02']=10
        values['orders']['货架-CPS']['2026-11-02']=0
        values['orders']['达播-二梯队']['2026-11-02']=20
        f.save('current',{'values':values,'base_version':None})

    def tearDown(self):
        self.store.stop();self.folder.cleanup()

    def test_latest_month_four_groups_live_sum_no_duplicate_and_no_write(self):
        before=g.all_items('forecast_input')
        result=p.preview()
        self.assertEqual(result['selected_month'],'2026-11')
        self.assertEqual(result['months'],['2026-10','2026-11'])
        self.assertEqual(len(result['dates']),30)
        self.assertTrue(result['read_only'])
        group=result['groups'][0];rows={r['label']:r for r in group['rows']}
        self.assertEqual(group['name'],'天猫旗舰店')
        self.assertEqual(list(rows),['货架订单数','CPS订单数','派样订单数','直播订单数'])
        self.assertEqual(rows['货架订单数']['values']['2026-11-01']['amount'],110)
        self.assertEqual(rows['直播订单数']['values']['2026-11-01']['amount'],70)
        self.assertEqual(result['totals']['2026-11-01'],205)
        self.assertEqual(result['totals']['2026-11-02'],120)
        self.assertEqual(rows['CPS订单数']['values']['2026-11-02']['amount'],0)
        self.assertIsNone(result['totals']['2026-11-03'])
        self.assertEqual(rows['货架订单数']['values']['2026-11-01']['run_id'],'current')
        self.assertEqual(g.all_items('forecast_input'),before)

    def test_month_and_date_filters_and_overlapping_sources(self):
        self.assertEqual(len(p.preview('2026-10')['dates']),31)
        self.assertEqual(p.preview(start_date='2026-10-31',end_date='2026-11-02')['dates'],['2026-10-31','2026-11-01','2026-11-02'])
        newer=copy.deepcopy(self.run);newer.update(id='overlap',source_id='new_source',created_at='2026-11-01')
        g.put('run','overlap',newer)
        values=f.read('overlap')['values'];values['orders']['货架-常规']['2026-11-01']=200
        f.save('overlap',{'values':values,'base_version':None})
        result=p.preview()
        self.assertEqual(result['totals']['2026-11-01'],200)
        self.assertEqual(result['groups'][0]['rows'][0]['values']['2026-11-01']['run_id'],'overlap')
        with self.assertRaises(ValueError):p.preview('2026-12')
        with self.assertRaises(ValueError):p.preview(start_date='2026-11-02',end_date='2026-11-01')

    def test_all_shops_share_dates_without_counting_summary_rows_twice(self):
        from api.services.gmv_douyin_parser import CHANNELS as douyin_channels
        run=copy.deepcopy(self.run)
        run.update(id='douyin',source_id='douyin',rule_key='douyin',source={'shop':'抖音店铺','name':'抖音11月'})
        run['result']={'parser':'douyin','records':[{'channel':c,'kind':'day','period':'2026-11-01','amount':1000} for c in douyin_channels]}
        g.put('run','douyin',run)
        values=f.read('douyin')['values']
        for channel,qty in {'抖音自播':10,'抖音商城':20,'抖音达播':30,'抖音精选联盟订单':40,'派样':5}.items():
            values['orders'][channel]['2026-11-01']=qty
        f.save('douyin',{'values':values,'base_version':None})
        manual=copy.deepcopy(self.run)
        manual.update(id='manual',source_id='manual',rule_key='private_manual',source={'shop':'私密渠道','name':'私密11月'})
        manual['result']={'parser':'private_manual','quantity_rows':[
            {'channel':c,'metric':m,'records':[{'kind':'day','channel':c,'period':'2026-11-01','amount':n}]}
            for c,m,n in [('预计单量','预计单量',200),('活动单量','活动单量',50),('合计','total_orders',250)]]}
        g.put('run','manual',manual)
        imported=copy.deepcopy(self.run)
        imported.update(id='excel',source_id='excel',rule_key='counter_excel',source={'shop':'专柜商城','name':'专柜11月'})
        imported['result']={'parser':'counter_excel','estimated_orders':[{'kind':'day','channel':'预估单量','period':'2026-11-01','amount':100}]}
        g.put('run','excel',imported)
        result=p.preview()
        groups={x['name']:x for x in result['groups']}
        self.assertEqual(len(groups),4)
        self.assertEqual(groups['抖音店铺']['totals']['2026-11-01'],85)
        douyin_rows={row['label']:row for row in groups['抖音店铺']['rows']}
        self.assertNotIn('抖音达播',douyin_rows)
        self.assertNotIn('商城订单数',douyin_rows)
        for label,amount in {'货架单量':10,'抖音精选联盟':40,'直播单量':30,'派样单量':5}.items():
            self.assertEqual(douyin_rows[label]['values']['2026-11-01']['amount'],amount)
        self.assertEqual(groups['私密渠道']['totals']['2026-11-01'],250)
        self.assertEqual(groups['专柜商城']['totals']['2026-11-01'],100)
        self.assertEqual(result['totals']['2026-11-01'],640)
        for group in groups.values():self.assertEqual(list(group['totals']),result['dates'])

if __name__=='__main__':unittest.main()
