"""按用户指定口径只读查询天猫及抖音实绩，不写入解析快照。"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from api.services import query_source

DOUYIN_SHOPS = ['抖音-薇诺娜官方旗舰店', '抖音-薇诺娜旗舰店', '抖音-薇诺娜护肤旗舰店', '抖音-薇诺娜护肤旗舰店2']
DOUYIN_BABY_SHOPS = ['抖音-薇诺娜宝贝母婴旗舰店', '抖音-薇诺娜宝贝专卖店', '']
DOUYIN_CHANNELS = {
    '抖音自播': ['自营直播间'],
    '抖音商城': ['自营其他', '自营短视频'],
    '抖音达播': ['达人短视频', '达人直播间', '达人其他'],
}
DOUYIN_SQL = '''SELECT DATE(order_create_time) AS date, COUNT(*) AS source_rows,
    COUNT(order_qty) AS qty_rows, COUNT(order_pay_amount) AS amount_rows,
    SUM(order_qty) AS order_qty, SUM(order_pay_amount) AS payment_amount
FROM dwh_db_order_summary WHERE order_status = :status
    AND shop_name IN (:shop0, :shop1, :shop2, :shop3) AND {channel_filter}
    AND order_create_time >= :start AND order_create_time < :end
GROUP BY DATE(order_create_time) ORDER BY DATE(order_create_time)'''

SHELF_SQL = '''SELECT DATE(order_create_time) AS date, COUNT(*) AS source_rows,
    COUNT(order_qty) AS qty_rows, COUNT(order_pay_amount) AS amount_rows,
    SUM(order_qty) AS order_qty, SUM(order_pay_amount) AS payment_amount
FROM dwh_db_order_summary
WHERE order_status = :status AND platform IN ('TB','TM')
    AND shop_name = :shop AND {channel_filter} {anchor_filter}
    AND order_create_time >= :start AND order_create_time < :end
GROUP BY DATE(order_create_time) ORDER BY DATE(order_create_time)'''


def aggregate(records):
    if not records:
        return {'order_qty': None, 'payment_amount': None, 'avg_order_value': None, 'days_with_data': 0, 'last_date': None}
    qty_complete = all(r['order_qty'] is not None and r['qty_rows'] == r['source_rows'] for r in records)
    amount_complete = all(r['payment_amount'] is not None and r['amount_rows'] == r['source_rows'] for r in records)
    qty = sum((Decimal(str(r['order_qty'])) for r in records), Decimal(0)) if qty_complete else None
    paid = sum((Decimal(str(r['payment_amount'])) for r in records), Decimal(0)) if amount_complete else None
    average = paid / qty if qty and paid is not None else None
    return {'order_qty': float(qty) if qty is not None else None,
            'payment_amount': float(paid.quantize(Decimal('.01'))) if paid is not None else None,
            'avg_order_value': float(average.quantize(Decimal('.01'))) if average is not None else None,
            'days_with_data': len(records), 'last_date': max(str(r['date'])[:10] for r in records)}


def build_periods(raw, records, year):
    dates = sorted({r['period'] for r in records if r['kind'] == 'day'})
    daily = {str(r['date'])[:10]: r for r in raw}
    periods = {d: aggregate([daily[d]] if d in daily else []) for d in dates}
    for period in dict.fromkeys(r['period'] for r in records if r['kind'] == 'summary'):
        month = re.fullmatch(r'(\d{1,2})月\s*(?:ttl|(?:目标)?合计)', period, re.I)
        campaign = re.search(r'双11[（(](\d+)\.(\d+)-(\d+)\.(\d+)[）)]', period)
        subset = None
        if month:
            subset = [daily[d] for d in dates if d in daily and int(d[5:7]) == int(month[1])]
        elif re.fullmatch(r'(?:\d+\+\d+月\s*)?(?:TTL|(?:目标)?合计|总计)', period, re.I):
            subset = [daily[d] for d in dates if d in daily]
        elif campaign:
            start = date(year, int(campaign[1]), int(campaign[2])).isoformat()
            end = date(year, int(campaign[3]), int(campaign[4])).isoformat()
            subset = [daily[d] for d in dates if d in daily and start <= d <= end]
        if subset is not None:
            periods[period] = aggregate(subset)
    return periods


def channel_actuals(run, channel):
    if channel not in {'店铺', '百亿补贴', 'CPS', '达播-超头', '达播-二梯队'}:
        raise ValueError('未配置该渠道的实绩读取口径')
    source = run.get('source', {})
    if run.get('rule_key') != 'tmall' or source.get('shop', run.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
        return {'supported': False, 'periods': {}}
    records = run.get('result', {}).get('records', [])
    dates = sorted({r['period'] for r in records if r['kind'] == 'day'})
    if not dates:
        return {'supported': True, 'periods': {}}
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    anchor_filter = ''
    channel_filter = 'SUBSTR(channel_type, 1, 3) = :channel' if channel == 'CPS' else 'channel_type = :channel'
    params = {'status': '900', 'shop': '薇诺娜天猫旗舰店', 'channel': channel, 'start': first.isoformat(), 'end': (last + timedelta(days=1)).isoformat()}
    if channel in {'达播-超头', '达播-二梯队'}:
        params.update(channel='达播', super_anchor='李佳琦')
        anchor_filter = 'AND channel_name = :super_anchor' if channel == '达播-超头' else 'AND (channel_name <> :super_anchor OR channel_name IS NULL)'
    raw = query_source.rows(SHELF_SQL.format(anchor_filter=anchor_filter, channel_filter=channel_filter), params)
    periods = build_periods(raw, records, run['year'])
    return {'supported': True, 'periods': periods, 'queried_at': datetime.now(timezone.utc).isoformat(),
            'definition': {'shop': params['shop'], 'status': params['status'], 'platforms': ['TB', 'TM'], 'channel_type': params['channel'], 'channel_match': 'prefix3' if channel == 'CPS' else 'exact', 'anchor_group': channel if anchor_filter else None}}


def shelf_actuals(run):
    return channel_actuals(run, '店铺')


def tmall_actuals(run):
    shelf = shelf_actuals(run)
    if not shelf['supported']:
        return shelf
    subsidy = channel_actuals(run, '百亿补贴')
    cps = channel_actuals(run, 'CPS')
    top = channel_actuals(run, '达播-超头')
    second = channel_actuals(run, '达播-二梯队')
    return {**shelf, 'channels': {'货架-常规': shelf, '货架-CPS': cps, '百补': subsidy, '达播-超头': top, '达播-二梯队': second}}


def douyin_actuals(run):
    source = run.get('source', {})
    if run.get('rule_key') != 'douyin' or source.get('shop', run.get('shop')) not in {'抖音店铺', '薇诺娜抖音店铺'}:
        return {'supported': False, 'periods': {}}
    records = run.get('result', {}).get('records', [])
    dates = sorted({r['period'] for r in records if r['kind'] == 'day'})
    channels = {}
    for target, source_channels in DOUYIN_CHANNELS.items():
        raw = []
        if dates:
            params = {'status': '900', 'start': dates[0], 'end': (date.fromisoformat(dates[-1]) + timedelta(days=1)).isoformat(), **{f'shop{i}': shop for i, shop in enumerate(DOUYIN_SHOPS)}, **{f'channel{i}': channel for i, channel in enumerate(source_channels)}}
            placeholders = ', '.join(f':channel{i}' for i in range(len(source_channels)))
            raw = query_source.rows(DOUYIN_SQL.format(channel_filter=f'channel_type IN ({placeholders})'), params)
        channels[target] = {'supported': True, 'periods': build_periods(raw, records, run['year']),
                            'definition': {'shops': DOUYIN_SHOPS, 'status': '900', 'channel_types': source_channels}}
    return {'supported': True, 'provider': 'douyin', 'channels': channels,
            'pending_channels': [], 'queried_at': datetime.now(timezone.utc).isoformat()}


def store_actuals(run):
    if run.get('result', {}).get('quantity_only'):
        return {'supported': False, 'channels': {}}
    if run.get('result', {}).get('parser') == 'vip' or run.get('rule_key') == 'vip':
        return {'supported': False, 'provider': 'vip', 'channels': {}, 'pending_channels': ['唯品会']}
    if run.get('result', {}).get('parser') == 'douyin_baby' or run.get('rule_key') == 'douyin_baby':
        return douyin_baby_actuals(run)
    return douyin_actuals(run) if run.get('result', {}).get('parser') == 'douyin' or run.get('rule_key') == 'douyin' else tmall_actuals(run)


def douyin_baby_actuals(run):
    records = run.get('result', {}).get('records', [])
    dates = sorted({r['period'] for r in records if r['kind'] == 'day'})
    channels = {}
    for target, channel in {'自播官旗': '自营直播间', '商城': '自营其他', 'KOC': '达人短视频', '抖音达播': '达人直播间'}.items():
        raw = []
        if dates:
            params = {'status': '900', 'start': dates[0],
                      'end': (date.fromisoformat(dates[-1]) + timedelta(days=1)).isoformat(),
                      **{f'shop{i}': shop for i, shop in enumerate(DOUYIN_BABY_SHOPS)},
                      'channel': channel}
            sql = DOUYIN_SQL.replace(':shop0, :shop1, :shop2, :shop3', ':shop0, :shop1, :shop2')
            raw = query_source.rows(sql.format(channel_filter='channel_type = :channel'), params)
        channels[target] = {'supported': True, 'periods': build_periods(raw, records, run['year']),
                            'definition': {'shops': DOUYIN_BABY_SHOPS, 'status': '900', 'channel_types': [channel]}}
    return {'supported': True, 'provider': 'douyin_baby', 'channels': channels,
            'pending_channels': [c for c in run.get('result', {}).get('channel_order', []) if c not in channels],
            'queried_at': datetime.now(timezone.utc).isoformat()}
