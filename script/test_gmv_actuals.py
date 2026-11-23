import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ecom_forecast'))
from api.services import gmv_actuals as actuals
from api.services import query_source


class ActualsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.connection = self.engine.connect()
        self.connection.execute(text('''CREATE TABLE dwh_db_order_summary (
            order_create_time TEXT, order_status TEXT, platform TEXT,
            shop_name TEXT, channel_type TEXT, order_qty INTEGER, order_pay_amount NUMERIC, channel_name TEXT)'''))
        self.run = {'rule_key': 'tmall', 'year': 2026, 'shop': '天猫旗舰店', 'source': {'shop': '天猫旗舰店'}, 'result': {'records': [
            {'kind': 'day', 'period': d} for d in ['2026-11-01', '2026-11-02', '2026-11-03']
        ] + [{'kind': 'summary', 'period': p} for p in ['11月ttl', 'TTL', '双11（11.1-11.1）']]}}

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def insert(self, day, qty, paid, status='900', platform='TB', shop='薇诺娜天猫旗舰店', channel='店铺', name=None):
        self.connection.execute(text('INSERT INTO dwh_db_order_summary VALUES (:day,:status,:platform,:shop,:channel,:qty,:paid,:name)'), locals())

    def test_filters_day_boundaries_and_weighted_aov(self):
        self.insert('2026-11-01 00:00:00', 100, 1000)
        self.insert('2026-11-01 23:59:59', 200, 4000, platform='TM')
        self.insert('2026-11-02 12:00:00', 0, 0)
        for overrides in [{'status': '999'}, {'platform': 'DY'}, {'shop': '其他店'}, {'channel': 'CPS'}]:
            self.insert('2026-11-01 12:00:00', 9999, 999999, **overrides)
        self.insert('2026-11-04 00:00:00', 9999, 999999)
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.shelf_actuals(self.run)['periods']
        self.assertEqual(result['2026-11-01']['order_qty'], 300)
        self.assertEqual(result['2026-11-01']['payment_amount'], 5000)
        self.assertEqual(result['2026-11-01']['avg_order_value'], 16.67)
        self.assertEqual(result['2026-11-02']['order_qty'], 0)
        self.assertIsNone(result['2026-11-02']['avg_order_value'])
        self.assertIsNone(result['2026-11-03']['order_qty'])
        self.assertEqual(result['11月ttl']['avg_order_value'], 16.67)
        self.assertEqual(result['TTL']['days_with_data'], 2)
        self.assertEqual(result['双11（11.1-11.1）']['days_with_data'], 1)

    def test_missing_source_amount_is_not_silently_zeroed(self):
        self.insert('2026-11-01 00:00:00', 100, 1000)
        self.insert('2026-11-01 12:00:00', 10, None)
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.shelf_actuals(self.run)['periods']['2026-11-01']
        self.assertEqual(result['order_qty'], 110)
        self.assertIsNone(result['payment_amount'])
        self.assertIsNone(result['avg_order_value'])

    def test_other_shop_does_not_read_tmall_data(self):
        self.run['source']['shop'] = '其他店'
        with patch.object(actuals.query_source, 'rows') as query:
            self.assertFalse(actuals.shelf_actuals(self.run)['supported'])
            query.assert_not_called()

    def test_subsidy_and_shelf_are_separate(self):
        self.insert('2026-11-01 00:00:00', 10, 1000)
        self.insert('2026-11-01 10:00:00', 20, 3000, channel='百亿补贴')
        self.insert('2026-11-01 23:59:59', 5, 1000, channel='百亿补贴', platform='TM')
        self.insert('2026-11-02 12:00:00', 5, 500, channel='百亿补贴')
        self.insert('2026-11-01 12:00:00', 999, 99999, channel='百亿补贴', status='999')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.tmall_actuals(self.run)
        shelf = result['channels']['货架-常规']['periods']['2026-11-01']
        subsidy = result['channels']['百补']['periods']
        self.assertEqual(shelf['order_qty'], 10)
        self.assertEqual(subsidy['2026-11-01']['order_qty'], 25)
        self.assertEqual(subsidy['2026-11-01']['payment_amount'], 4000)
        self.assertEqual(subsidy['2026-11-01']['avg_order_value'], 160)
        self.assertEqual(subsidy['11月ttl']['order_qty'], 30)
        self.assertEqual(subsidy['11月ttl']['avg_order_value'], 150)
        self.assertIsNone(subsidy['2026-11-03']['payment_amount'])

    def test_anchor_groups_are_exclusive_and_cover_other_names(self):
        self.insert('2026-11-01 00:00:00', 10, 1000, channel='达播', name='李佳琦')
        self.insert('2026-11-01 23:59:59', 20, 4000, channel='达播', name='李佳琦', platform='TM')
        self.insert('2026-11-01 12:00:00', 5, 500, channel='达播', name='其他主播')
        self.insert('2026-11-01 13:00:00', 2, 200, channel='达播', name=None)
        self.insert('2026-11-01 14:00:00', 3, 300, channel='达播', name='')
        self.insert('2026-11-01 15:00:00', 4, 400, channel='达播', name='李佳琦直播间')
        self.insert('2026-11-01 12:00:00', 999, 99999, channel='达播', name='李佳琦', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, channel='店铺', name='李佳琦')
        self.insert('2026-11-02 12:00:00', 5, 1000, channel='达播', name='其他主播')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.tmall_actuals(self.run)['channels']
        top, second = result['达播-超头']['periods'], result['达播-二梯队']['periods']
        self.assertEqual(top['2026-11-01']['order_qty'], 30)
        self.assertEqual(top['2026-11-01']['payment_amount'], 5000)
        self.assertEqual(top['2026-11-01']['avg_order_value'], 166.67)
        self.assertEqual(second['2026-11-01']['order_qty'], 14)
        self.assertEqual(second['2026-11-01']['payment_amount'], 1400)
        self.assertEqual(second['11月ttl']['order_qty'], 19)
        self.assertEqual(second['11月ttl']['avg_order_value'], 126.32)
        self.assertIsNone(top['2026-11-02']['order_qty'])

    def test_cps_matches_all_prefix_variants_only(self):
        for channel, qty, paid in [('CPS', 10, 1000), ('CPS达人', 20, 4000), ('CPS-联盟', 5, 500)]:
            self.insert('2026-11-01 23:59:59', qty, paid, channel=channel)
        for channel in ['非CPS', 'XCPS', 'CP', '店铺']:
            self.insert('2026-11-01 12:00:00', 999, 99999, channel=channel)
        self.insert('2026-11-01 12:00:00', 999, 99999, channel='CPS达人', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, channel='CPS达人', platform='DY')
        self.insert('2026-11-02 12:00:00', 5, 500, channel='CPS联盟')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.tmall_actuals(self.run)['channels']['货架-CPS']
        self.assertEqual(result['definition']['channel_match'], 'prefix3')
        self.assertEqual(result['periods']['2026-11-01']['order_qty'], 35)
        self.assertEqual(result['periods']['2026-11-01']['payment_amount'], 5500)
        self.assertEqual(result['periods']['2026-11-01']['avg_order_value'], 157.14)
        self.assertEqual(result['periods']['11月ttl']['order_qty'], 40)
        self.assertEqual(result['periods']['11月ttl']['avg_order_value'], 150)
        self.assertIsNone(result['periods']['2026-11-03']['order_qty'])

    def test_douyin_self_live_four_shops_and_no_added_platform_filter(self):
        run = {**self.run, 'rule_key': 'douyin', 'shop': '抖音店铺', 'source': {'shop': '抖音店铺'}, 'result': {**self.run['result'], 'parser': 'douyin'}}
        run['result']['records'] = [*run['result']['records'], {'kind': 'summary', 'period': '总计'}]
        for shop, qty, paid, platform in zip(actuals.DOUYIN_SHOPS, [10,20,5,15], [1000,4000,500,4500], ['DY','DOUYIN','其他','DY']):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel='自营直播间', platform=platform)
        self.insert('2026-11-02 12:00:00', 5, 1000, shop=actuals.DOUYIN_SHOPS[0], channel='自营直播间')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='自营直播间', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop='其他抖音店铺', channel='自营直播间')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='达播')
        self.insert('2026-11-04 00:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='自营直播间')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)) as query:
            result = actuals.store_actuals(run)
        self.assertEqual(query.call_count, 3)
        self.assertEqual(set(result['channels']), {'抖音自播', '抖音商城', '抖音达播'})
        self.assertEqual(result['pending_channels'], [])
        periods = result['channels']['抖音自播']['periods']
        self.assertEqual(periods['2026-11-01']['order_qty'], 50)
        self.assertEqual(periods['2026-11-01']['payment_amount'], 10000)
        self.assertEqual(periods['2026-11-01']['avg_order_value'], 200)
        self.assertEqual(periods['11月ttl']['order_qty'], 55)
        self.assertEqual(periods['总计']['avg_order_value'], 200)
        self.assertIsNone(periods['2026-11-03']['order_qty'])

    def test_douyin_does_not_query_for_unknown_shop(self):
        run = {**self.run, 'rule_key': 'douyin', 'source': {'shop': '其他抖音店铺'}, 'result': {'parser': 'douyin'}}
        with patch.object(actuals.query_source, 'rows') as query:
            self.assertFalse(actuals.store_actuals(run)['supported'])
            query.assert_not_called()

    def test_douyin_mall_combines_other_and_short_video_only(self):
        run = {**self.run, 'rule_key': 'douyin', 'source': {'shop': '抖音店铺'}, 'result': {**self.run['result'], 'parser': 'douyin'}}
        run['result']['records'] = [*run['result']['records'], {'kind': 'summary', 'period': '总计'}]
        for shop, qty, paid, channel in zip(actuals.DOUYIN_SHOPS, [10,20,5,15], [1000,4000,500,4500], ['自营其他','自营短视频','自营其他','自营短视频']):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel=channel)
        self.insert('2026-11-01 12:00:00', 5, 500, shop=actuals.DOUYIN_SHOPS[0], channel='自营直播间')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='自营其他', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop='其他抖音店铺', channel='自营短视频')
        self.insert('2026-11-02 12:00:00', 10, 1000, shop=actuals.DOUYIN_SHOPS[0], channel='自营短视频')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.store_actuals(run)['channels']
        mall = result['抖音商城']['periods']
        self.assertEqual(mall['2026-11-01']['order_qty'], 50)
        self.assertEqual(mall['2026-11-01']['payment_amount'], 10000)
        self.assertEqual(mall['2026-11-01']['avg_order_value'], 200)
        self.assertEqual(mall['11月ttl']['order_qty'], 60)
        self.assertEqual(mall['总计']['avg_order_value'], 183.33)
        self.assertEqual(result['抖音自播']['periods']['2026-11-01']['order_qty'], 5)
        self.assertIsNone(mall['2026-11-03']['order_qty'])

    def test_douyin_anchor_combines_three_types_without_self_or_mall(self):
        run = {**self.run, 'rule_key': 'douyin', 'source': {'shop': '抖音店铺'}, 'result': {**self.run['result'], 'parser': 'douyin'}}
        run['result']['records'] = [*run['result']['records'], {'kind': 'summary', 'period': '总计'}]
        for shop, qty, paid, channel in zip(actuals.DOUYIN_SHOPS, [10,20,5,15], [1000,4000,500,4500], ['达人短视频','达人直播间','达人其他','达人直播间']):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel=channel)
        for channel in ['自营直播间', '自营短视频', '自营其他', '达播']:
            self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel=channel)
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='达人其他', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop='其他抖音店铺', channel='达人直播间')
        self.insert('2026-11-02 12:00:00', 10, 1000, shop=actuals.DOUYIN_SHOPS[0], channel='达人短视频')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)):
            result = actuals.store_actuals(run)
        anchor = result['channels']['抖音达播']['periods']
        self.assertEqual(anchor['2026-11-01']['order_qty'], 50)
        self.assertEqual(anchor['2026-11-01']['payment_amount'], 10000)
        self.assertEqual(anchor['2026-11-01']['avg_order_value'], 200)
        self.assertEqual(anchor['11月ttl']['order_qty'], 60)
        self.assertEqual(anchor['总计']['avg_order_value'], 183.33)
        self.assertIsNone(anchor['2026-11-03']['order_qty'])
        self.assertEqual(result['pending_channels'], [])


    def test_baby_self_live_exact_shops_and_month_summaries(self):
        run = {'rule_key': 'douyin_baby', 'year': 2026, 'result': {
            'parser': 'douyin_baby', 'channel_order': ['自播官旗', '商城', 'KOC', 'DP合计', '抖音达播'],
            'records': [{'kind': 'day', 'period': d} for d in ['2026-10-31', '2026-11-01', '2026-11-02']]
            + [{'kind': 'summary', 'period': p} for p in ['10+11月目标合计', '10月目标合计', '11月目标合计']]}}
        for shop, qty, paid in zip(actuals.DOUYIN_BABY_SHOPS, [10, 20, 5], [1000, 4000, 500]):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel='自营直播间', platform='DY')
        self.insert('2026-10-31 00:00:00', 10, 1000, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='自营直播间')
        for overrides in [{'shop': actuals.DOUYIN_SHOPS[0]}, {'status': '999'}, {'channel': '达人其他'}]:
            params = {'shop': actuals.DOUYIN_BABY_SHOPS[0], 'channel': '自营直播间', **overrides}
            self.insert('2026-11-01 12:00:00', 999, 99999, **params)
        self.insert('2026-11-03 00:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='自营直播间')
        for shop, qty, paid in zip(actuals.DOUYIN_BABY_SHOPS, [2, 3, 1], [200, 600, 100]):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel='自营其他', platform='DY')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='自营其他')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='自营其他', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='自营短视频')
        for shop, qty, paid in zip(actuals.DOUYIN_BABY_SHOPS, [4, 6, 2], [400, 1200, 200]):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel='达人短视频', platform='DY')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='达人短视频')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='达人短视频', status='999')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='达人其他')
        for shop, qty, paid in zip(actuals.DOUYIN_BABY_SHOPS, [8, 12, 4], [800, 2400, 400]):
            self.insert('2026-11-01 23:59:59', qty, paid, shop=shop, channel='达人直播间', platform='DY')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_SHOPS[0], channel='达人直播间')
        self.insert('2026-11-01 12:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='达人直播间', status='999')
        self.insert('2026-11-03 00:00:00', 999, 99999, shop=actuals.DOUYIN_BABY_SHOPS[0], channel='达人直播间')
        original = query_source.rows
        with patch.object(actuals.query_source, 'rows', side_effect=lambda sql, params: original(sql, params, self.connection)) as query:
            result = actuals.store_actuals(run)
        self.assertEqual(query.call_count, 4)
        periods = result['channels']['自播官旗']['periods']
        self.assertEqual(periods['2026-11-01']['order_qty'], 35)
        self.assertEqual(periods['2026-11-01']['payment_amount'], 5500)
        self.assertEqual(periods['2026-11-01']['avg_order_value'], 157.14)
        self.assertEqual(periods['10月目标合计']['payment_amount'], 1000)
        self.assertEqual(periods['11月目标合计']['payment_amount'], 5500)
        self.assertEqual(periods['10+11月目标合计']['order_qty'], 45)
        self.assertIsNone(periods['2026-11-02']['order_qty'])
        self.assertEqual(result['pending_channels'], ['DP合计'])
        mall = result['channels']['商城']['periods']
        self.assertEqual(mall['2026-11-01']['order_qty'], 6)
        self.assertEqual(mall['2026-11-01']['payment_amount'], 900)
        self.assertEqual(mall['2026-11-01']['avg_order_value'], 150)
        self.assertEqual(mall['11月目标合计']['payment_amount'], 900)
        self.assertEqual(mall['10+11月目标合计']['order_qty'], 6)
        self.assertIsNone(mall['2026-11-02']['payment_amount'])
        koc = result['channels']['KOC']['periods']
        self.assertEqual(koc['2026-11-01']['order_qty'], 12)
        self.assertEqual(koc['2026-11-01']['payment_amount'], 1800)
        self.assertEqual(koc['2026-11-01']['avg_order_value'], 150)
        self.assertEqual(koc['11月目标合计']['payment_amount'], 1800)
        self.assertEqual(koc['10+11月目标合计']['order_qty'], 12)
        self.assertIsNone(koc['2026-11-02']['payment_amount'])
        anchor = result['channels']['抖音达播']['periods']
        self.assertEqual(anchor['2026-11-01']['order_qty'], 24)
        self.assertEqual(anchor['2026-11-01']['payment_amount'], 3600)
        self.assertEqual(anchor['2026-11-01']['avg_order_value'], 150)
        self.assertEqual(anchor['11月目标合计']['payment_amount'], 3600)
        self.assertEqual(anchor['10+11月目标合计']['order_qty'], 24)
        self.assertIsNone(anchor['2026-11-02']['payment_amount'])




if __name__ == '__main__':
    unittest.main(verbosity=2)
