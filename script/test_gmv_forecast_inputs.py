import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services import expected_gmv as g, gmv_forecast_inputs as f
from api.services.gmv_parser import CHANNELS


class ForecastInputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = patch.object(g, 'STORE', Path(self.folder.name))
        self.store.start()
        self.run = {'id': 'tmall_run', 'source_id': 'tmall_oct_nov', 'rule_key': 'tmall', 'status': 'success',
                    'result': {'records': [{'channel': channel, 'kind': 'day', 'period': day, 'amount': 259700}
                                         for channel in CHANNELS for day in ['2026-11-01', '2026-11-02']]},
                    'snapshot': {'cells': ['original GMV data']}}
        g.put('run', self.run['id'], self.run)

    def tearDown(self):
        self.store.stop(); self.folder.cleanup()

    def test_empty_values_and_manual_overrides_zero_clear_and_history(self):
        empty = f.read(self.run['id'])
        self.assertIsNone(empty['version'])
        values = empty['values']
        values['aov']['货架-常规']['2026-11-01'] = '100.00'
        values['orders']['货架-常规']['2026-11-02'] = '2,597'
        first = f.save(self.run['id'], {'values': values})
        stored = g.get('forecast_input', first['version'])
        shelf = [r for r in stored['effective_orders'] if r['channel'] == '货架-常规']
        self.assertEqual([r['amount'] for r in shelf], [2597, 2597])
        self.assertEqual([r['origin'] for r in shelf], ['calculated', 'manual'])
        values = first['values']
        values['aov']['货架-常规']['2026-11-01'] = 200
        values['orders']['货架-常规']['2026-11-02'] = 0
        second = f.save(self.run['id'], {'values': values, 'base_version': first['version']})
        shelf = [r for r in g.get('forecast_input', second['version'])['effective_orders'] if r['channel'] == '货架-常规']
        self.assertEqual([r['amount'] for r in shelf], [1299, 0])
        old = f.read(self.run['id'], first['version'])
        self.assertEqual(old['values']['aov']['货架-常规']['2026-11-01'], 100)
        self.assertEqual(old['current_version'], second['version'])
        self.assertEqual(g.get('run', self.run['id']), self.run)
        with self.assertRaises(ValueError):
            f.save(self.run['id'], {'values': values, 'base_version': first['version']})
        values['orders']['货架-常规']['2026-11-02'] = None
        third = f.save(self.run['id'], {'values': values, 'base_version': second['version']})
        self.assertEqual(len(third['history']), 3)
        self.assertIsNone([r for r in g.get('forecast_input', third['version'])['effective_orders'] if r['channel']=='货架-常规'][1]['amount'])

    def test_invalid_numbers_channels_dates_and_cross_source_history(self):
        for metric, bad in [('aov', 0), ('aov', -1), ('aov', '1.001'), ('orders', '1.5'), ('orders', -1), ('orders', True), ('orders', 10**13)]:
            values = f.read(self.run['id'])['values']; values[metric]['货架-常规']['2026-11-01'] = bad
            with self.subTest(metric=metric, bad=bad), self.assertRaises(ValueError):
                f.save(self.run['id'], {'values': values})
        for key in ['wrong_channel', '2026-12-01']:
            values = f.read(self.run['id'])['values']
            if key == 'wrong_channel': values['aov'][key] = {}
            else: values['aov']['货架-常规'][key] = 100
            with self.assertRaises(ValueError): f.save(self.run['id'], {'values': values})
        first = f.save(self.run['id'], {'values': f.read(self.run['id'])['values']})
        other = copy.deepcopy(self.run); other.update(id='other', source_id='tmall_sep')
        g.put('run', other['id'], other)
        with self.assertRaises(ValueError): f.read(other['id'], first['version'])
        other.update(id='douyin', rule_key='douyin'); g.put('run', other['id'], other)
        with self.assertRaises(ValueError): f.read(other['id'])

    def test_missing_gmv_with_price_stays_unknown(self):
        values = f.read(self.run['id'])['values']
        self.run['result']['records'][0]['amount'] = None
        values['aov']['货架-常规']['2026-11-01'] = 100
        result = f.effective_orders(self.run, values)
        self.assertIsNone(result[0]['amount'])
        self.assertEqual(result[0]['origin'], 'missing_gmv')

    def test_sampling_orders_save_history_and_legacy_compatibility(self):
        first=f.read(self.run['id'])
        self.assertIsNone(first['values']['orders']['派样']['2026-11-01'])
        self.assertNotIn('派样',first['values']['aov'])
        values=first['values']
        values['orders']['派样']['2026-11-01']='7,631'
        saved=f.save(self.run['id'],{'values':values,'base_version':None})
        stored=g.get('forecast_input',saved['version'])
        sample=[r for r in stored['effective_orders'] if r['channel']=='派样']
        self.assertEqual([r['amount'] for r in sample],[7631,None])
        values=saved['values'];values['orders']['派样']['2026-11-01']=0
        newer=f.save(self.run['id'],{'values':values,'base_version':saved['version']})
        self.assertEqual(newer['values']['orders']['派样']['2026-11-01'],0)
        self.assertEqual(f.read(self.run['id'],saved['version'])['values']['orders']['派样']['2026-11-01'],7631)
        self.assertEqual(g.get('run',self.run['id']),self.run)
        legacy=copy.deepcopy(newer['values']);legacy['orders'].pop('派样')
        preserved=f.save(self.run['id'],{'values':legacy,'base_version':newer['version']})
        self.assertEqual(preserved['values']['orders']['派样']['2026-11-01'],0)

    def test_douyin_three_channels_save_calculation_history_and_isolation(self):
        from api.services.gmv_parser import parse
        snapshot=json.loads((ROOT/'script/fixtures/gmv_douyin.json').read_text(encoding='utf-8'))
        code=(ROOT/'ecom_forecast/api/gmv_rules/douyin.py').read_text(encoding='utf-8')
        run={'id':'douyin_run','source_id':'douyin_oct_nov','rule_key':'douyin','status':'success','result':parse(snapshot,code,2026)}
        g.put('run',run['id'],run)
        response=f.read(run['id'])
        channels=['抖音自播','抖音商城','抖音达播']
        self.assertEqual(list(response['values']['orders']),channels+['抖音精选联盟订单','派样'])
        self.assertIsNone(response['values']['orders']['派样']['2026-10-01'])
        values=response['values']
        values['aov']['抖音自播']['2026-10-01']=100
        values['orders']['抖音商城']['2026-10-01']='2,597'
        values['orders']['抖音达播']['2026-10-01']=200
        values['orders']['抖音精选联盟订单']['2026-10-01']='100'
        values['orders']['派样']['2026-10-01']='1,234'
        first=f.save(run['id'],{'values':values,'base_version':None})
        orders=g.get('forecast_input',first['version'])['effective_orders']
        live=next(r for r in orders if r['channel']=='抖音自播' and r['period']=='2026-10-01')
        self.assertEqual(live['amount'],34371)
        anchor=next(r for r in orders if r['channel']=='抖音达播' and r['period']=='2026-10-01')
        self.assertEqual(anchor['amount'],300)
        self.assertEqual(anchor['origin'],'sum_components')
        self.assertEqual({r['channel'] for r in orders},set(channels+['派样']))
        self.assertEqual(next(r['amount'] for r in orders if r['channel']=='派样' and r['period']=='2026-10-01'),1234)
        values=first['values'];values['orders']['抖音商城']['2026-10-01']=0
        second=f.save(run['id'],{'values':values,'base_version':first['version']})
        self.assertEqual(second['values']['orders']['派样']['2026-10-01'],1234)
        self.assertEqual(second['values']['orders']['抖音商城']['2026-10-01'],0)
        self.assertEqual(f.read(run['id'],first['version'])['values']['orders']['抖音商城']['2026-10-01'],2597)
        with self.assertRaises(ValueError):f.read(self.run['id'],first['version'])
        values=second['values'];values['orders']['货架-常规']={}
        with self.assertRaises(ValueError):f.save(run['id'],{'values':values,'base_version':second['version']})

    def test_legacy_douyin_aov_and_new_component_are_preserved(self):
        from api.services.gmv_douyin_parser import CHANNELS as DY_CHANNELS
        run={'id':'dy_legacy','source_id':'dy_legacy','rule_key':'douyin','status':'success',
             'result':{'parser':'douyin','records':[{'channel':c,'kind':'day','period':'2026-10-01','amount':10000} for c in DY_CHANNELS]}}
        g.put('run',run['id'],run)
        old={'id':'legacy','source_id':run['source_id'],'run_id':run['id'],'created_at':'2026-01-01',
             'values':{'aov':{c:{'2026-10-01':100} for c in DY_CHANNELS},'orders':{c:{'2026-10-01':None} for c in DY_CHANNELS}}}
        g.put('forecast_input','legacy',old)
        response=f.read(run['id'])
        self.assertEqual(response['values']['orders']['抖音达播']['2026-10-01'],100)
        self.assertNotIn('抖音精选联盟订单',g.get('forecast_input','legacy')['values']['orders'])
        response['values']['orders']['抖音精选联盟订单']['2026-10-01']=50
        saved=f.save(run['id'],{'values':response['values'],'base_version':'legacy'})
        components=f.read(run['id'])['values']['orders']
        self.assertEqual(components['抖音精选联盟订单']['2026-10-01'],50)
        saved['values']['orders']['派样']['2026-10-01']=42
        saved=f.save(run['id'],{'values':saved['values'],'base_version':saved['version']})
        legacy_client=copy.deepcopy(saved['values']);del legacy_client['orders']['抖音精选联盟订单'];del legacy_client['orders']['派样']
        saved2=f.save(run['id'],{'values':legacy_client,'base_version':saved['version']})
        self.assertEqual(saved2['values']['orders']['抖音精选联盟订单']['2026-10-01'],50)
        self.assertEqual(saved2['values']['orders']['派样']['2026-10-01'],42)
        actual=g.get('forecast_input',saved2['version'])['effective_orders']
        self.assertEqual(next(r['amount'] for r in actual if r['channel']=='抖音达播'),150)

    def test_baby_five_channel_forecast_history_and_isolation(self):
        from api.services.gmv_parser import parse
        from api.services.gmv_douyin_baby_parser import CHANNELS as BABY_CHANNELS
        snapshot=json.loads((ROOT/'script/fixtures/gmv_douyin_baby.json').read_text(encoding='utf-8'))
        code=(ROOT/'ecom_forecast/api/gmv_rules/douyin_baby.py').read_text(encoding='utf-8')
        run={'id':'baby','source_id':'baby','rule_key':'douyin_baby','status':'success','result':parse(snapshot,code,2026)}
        g.put('run','baby',run)
        response=f.read('baby')
        self.assertEqual(list(response['values']['orders']),BABY_CHANNELS+['PDD'])
        self.assertEqual(list(response['values']['aov']),BABY_CHANNELS)
        day='2026-10-01';values=response['values']
        values['aov']['自播官旗'][day]=100
        values['orders']['商城'][day]='2,597'
        values['orders']['抖音达播'][day]=200
        values['orders']['PDD'][day]='1,234'
        saved=f.save('baby',{'values':values,'base_version':None})
        effective=g.get('forecast_input',saved['version'])['effective_orders']
        first={r['channel']:r['amount'] for r in effective if r['period']==day}
        self.assertEqual(first['自播官旗'],1100)
        self.assertEqual(first['商城'],2597)
        self.assertEqual(first['抖音达播'],200)
        self.assertEqual(set(first),set(BABY_CHANNELS+['PDD']))
        self.assertEqual(first['PDD'],1234)
        saved['values']['orders']['商城'][day]=0
        second=f.save('baby',{'values':saved['values'],'base_version':saved['version']})
        self.assertEqual(second['values']['orders']['商城'][day],0)
        self.assertEqual(f.read('baby',saved['version'])['values']['orders']['商城'][day],2597)
        legacy=copy.deepcopy(second['values']);del legacy['orders']['PDD']
        second=f.save('baby',{'values':legacy,'base_version':second['version']})
        self.assertEqual(second['values']['orders']['PDD'][day],1234)
        with self.assertRaises(ValueError):f.read(self.run['id'],saved['version'])
        second['values']['orders']['抖音精选联盟订单']={day:999}
        with self.assertRaises(ValueError):f.save('baby',{'values':second['values'],'base_version':second['version']})


if __name__ == '__main__': unittest.main(verbosity=2)
