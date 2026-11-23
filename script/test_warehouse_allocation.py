import copy
import sys
import unittest
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ecom_forecast'))
from api.services import gmv_warehouse_allocation as w


class WarehouseAllocationTests(unittest.TestCase):
    def test_daily_mean_not_volume_weighted_and_empty_days_skipped(self):
        rows=[{'date':day,'facility_name':facility,'order_count':qty}
              for day,k,h in [('2026-11-04',1,9),('2026-11-05',90,10),('2026-11-06',0,0)]
              for facility,qty in [('昆明线上2C发货仓',k),('湖州线上2C发货仓',h)]]
        rows.append({'date':'2026-11-11','facility_name':'武汉线上2C发货仓','order_count':900})
        ratios,days=w.average_shares(rows,'2026-11-04','2026-11-11')
        self.assertEqual(days,['2026-11-04','2026-11-05'])
        self.assertEqual(ratios['昆明仓'],Decimal('.5'))
        self.assertEqual(ratios['湖州仓'],Decimal('.5'))
        self.assertEqual(ratios['武汉仓'],0)
        self.assertEqual(w.average_shares([],'2026-11-04','2026-11-11'),(None,[]))

    def test_integer_conservation_and_null_values(self):
        ratios={name:Decimal(1)/3 if name!='上海仓' else Decimal(0) for name in w.WAREHOUSES}
        for qty in [0,1,2,7,100,115844,10**12]:
            values=w.distribute(qty,ratios)
            self.assertEqual(sum(values.values()),qty)
            self.assertTrue(all(isinstance(v,int) and v>=0 for v in values.values()))
            self.assertEqual(values['上海仓'],0)
        self.assertTrue(all(v is None for v in w.distribute(None,ratios).values()))
        self.assertTrue(all(v is None for v in w.distribute(10,None).values()))

    def sample(self):
        names=list(w.PROFILES)+list(w.FIXED)+['其它渠道']
        return {'dates':['2026-11-01','2026-11-02'],'groups':[
            {'name':name,'totals':{'2026-11-01':7,'2026-11-02':None},'rows':[]} for name in names],
            'totals':{'2026-11-01':7*len(names),'2026-11-02':None}}

    def query(self,sql,params,connection):
        return [{'date':f'2026-11-{day:02}','facility_name':facility,'order_count':amount}
                for day in range(4,11) for facility,amount in [('昆明线上2C发货仓',1),('湖州线上2C发货仓',2),('武汉线上2C发货仓',4)]]

    def test_channel_policies_query_ranges_and_totals(self):
        original=self.sample();before=copy.deepcopy(original)
        with patch.object(w,'preview',return_value=original),patch.object(w.query_source,'source_connection',return_value=nullcontext(object())),patch.object(w.query_source,'rows',side_effect=self.query) as query:
            result=w.allocation(today=date(2026,11,11))
        self.assertEqual(original,before)
        self.assertEqual(query.call_count,3)
        for call in query.call_args_list:
            sql,params,connection=call.args
            self.assertIn('ads_ofs_store_facility_order_daily_summary',sql)
            self.assertEqual(params['start'],'2026-11-04')
            self.assertEqual(params['end'],'2026-11-11')
        self.assertEqual(query.call_args_list[1].args[1]['shop3'],'抖音-薇诺娜护肤旗舰店2')
        self.assertEqual(query.call_args_list[2].args[1]['shop1'],'阿里健康大药房医药连锁有限公司（昆明贝泰妮）')
        groups={g['name']:g for g in result['groups']}
        for name,fixed in w.FIXED.items():
            amounts={r['label']:r['values']['2026-11-01']['amount'] for r in groups[name]['rows']}
            self.assertEqual(amounts[fixed],7)
            self.assertEqual(sum(amounts.values()),7)
        for name in ['天猫旗舰店','抖音店铺','阿里健康渠道','其它渠道']:
            self.assertEqual([r['values']['2026-11-01']['amount'] for r in groups[name]['rows']],[1,2,4,0])
        self.assertEqual(result['totals'],original['totals'])
        self.assertEqual(sum(row['2026-11-01'] for row in result['warehouse_totals'].values()),original['totals']['2026-11-01'])
        self.assertEqual(result['warnings'],[])

    def test_missing_dynamic_history_is_unallocated_and_fixed_still_works(self):
        original=self.sample()
        with patch.object(w,'preview',return_value=original),patch.object(w.query_source,'source_connection',return_value=nullcontext(object())),patch.object(w.query_source,'rows',return_value=[]):
            result=w.allocation(today=date(2026,11,11))
        self.assertEqual(len(result['warnings']),3)
        self.assertEqual(result['unallocated_totals']['2026-11-01'],28)
        self.assertEqual(result['totals']['2026-11-01']+28,original['totals']['2026-11-01'])


if __name__=='__main__':unittest.main()
