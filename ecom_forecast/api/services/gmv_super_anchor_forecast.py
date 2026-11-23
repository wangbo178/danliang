"""双十一超头及二达预估：动态识别尾款日期，用去年尾款实绩校准今年目标。"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from api.services import expected_gmv as g, query_source
from api.services.gmv_parser import verify, number

THRESHOLD = Decimal('200000000')
SECOND_THRESHOLD = Decimal('8000000')
ACTUAL_SQL = '''SELECT DATE(order_create_time) AS date, COUNT(*) AS source_rows,
    COUNT(order_qty) AS qty_rows, COUNT(order_pay_amount) AS amount_rows,
    SUM(order_qty) AS order_qty, CAST(SUM(order_pay_amount) AS SIGNED) AS payment_amount
FROM dwh_db_order_summary
WHERE channel_type = :channel AND channel_name = :anchor
    AND shop_name = :shop
    AND order_create_time >= :start AND order_create_time < :end
GROUP BY DATE(order_create_time) ORDER BY DATE(order_create_time)'''
SECOND_ACTUAL_SQL = ACTUAL_SQL.replace('channel_name = :anchor', 'channel_name <> :anchor')


def targets(run, channel='达播-超头'):
    if channel not in {'达播-超头', '达播-二梯队'}:
        raise ValueError('达播预估渠道无效')
    if run.get('rule_key') != 'tmall' or run.get('status') != 'success' or run.get('source', {}).get('shop', run.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
        raise ValueError('仅成功解析的天猫旗舰店支持双十一达播填充')
    report = verify(run['snapshot'], run['result'], run['year'])
    if not report['passed']:
        raise ValueError(channel + '目标源范围核验失败：' + '; '.join(report['errors'][:3]))
    values = {}
    for record in run['result']['records']:
        if record['channel'] == channel and record['kind'] == 'day':
            day = date.fromisoformat(record['period'])
            if day.year == run['year'] and day.month in (10, 11):
                if record['period'] in values:
                    raise ValueError(channel + '目标日期重复')
                values[record['period']] = {'amount': number(record['amount']) if record['amount'] is not None else None,
                                           'cell': record['cell'], 'run_id': run['id']}
    return values


def tail_dates(values, year, threshold=THRESHOLD):
    cap = date(year, 11, 14)
    triggers = sorted(day for day, value in values.items() if date(year, 10, 1) <= date.fromisoformat(day) <= cap
                      and value['amount'] is not None and value['amount'] > threshold)
    days = set()
    for trigger in triggers:
        first = date.fromisoformat(trigger)
        days.update((first + timedelta(days=offset)).isoformat() for offset in range(6)
                    if first + timedelta(days=offset) <= cap)
    return triggers, sorted(days)


def preview(run_id, mode='tail_only', channel='达播-超头'):
    if mode not in {'tail_only', 'through_1114'}:
        raise ValueError('填充日期模式无效')
    run = g.get('run', run_id)
    current = targets(run, channel)
    threshold = THRESHOLD if channel == '达播-超头' else SECOND_THRESHOLD
    year = run['year']
    triggers, tails = tail_dates(current, year, threshold)
    threshold_name = '2亿元' if channel == '达播-超头' else '800万元'
    response = {'run_id': run_id, 'channel': channel, 'timing_channel': channel, 'year': year, 'history_year': year - 1,
                'calculation_version': 'daily_v2', 'mode': mode, 'threshold': int(threshold), 'trigger_dates': triggers, 'tail_dates': tails,
                'start_date': triggers[0] if triggers else None, 'end_date': f'{year}-11-14',
                'history_trigger_dates': [], 'history_rows': [], 'rows': [], 'statistics': None,
                'status': 'no_trigger', 'message': f'10月至11月14日没有{channel}目标高于{threshold_name}的日期',
                'actual_definition': {'table': 'dwh_db_order_summary', 'channel_type': '达播', 'channel_name': '李佳琦',
                                      'shop_name': '薇诺娜天猫旗舰店',
                                      'channel_name_operator': '=' if channel == '达播-超头' else '<>',
                                      'filters': '仅薇诺娜天猫旗舰店，不附加平台或订单状态过滤'}}
    if not triggers:
        return response
    history = {}
    for old in g.all_items('run'):
        if old.get('year') != year - 1 or old.get('status') != 'success' or old.get('rule_key') != 'tmall':
            continue
        if old.get('source', {}).get('shop', old.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
            continue
        for day, value in targets(old, channel).items():
            history.setdefault(day, value)
    old_triggers, old_tails = tail_dates(history, year - 1, threshold)
    response['history_trigger_dates'] = old_triggers
    if not old_tails:
        response.update(status='no_history_trigger', message=f'去年没有已解析且目标高于{threshold_name}的{channel}日期，请先解析去年目标表')
        return response
    sql = ACTUAL_SQL if channel == '达播-超头' else SECOND_ACTUAL_SQL
    raw = query_source.rows(sql, {'channel': '达播', 'anchor': '李佳琦', 'shop': '薇诺娜天猫旗舰店',
                                       'start': f'{year-1}-10-01', 'end': f'{year-1}-11-15'})
    actuals = {str(row['date'])[:10]: row for row in raw}
    eligible = set(tails) if mode == 'tail_only' else {day for day in current if triggers[0] <= day <= f'{year}-11-14'}
    mappings = {}
    for day in sorted(eligible):
        # 同一轮触发日起的相对第N天，对应去年同一轮的第N天。
        phase = max(index for index, trigger in enumerate(triggers) if trigger <= day)
        offset = (date.fromisoformat(day) - date.fromisoformat(triggers[phase])).days
        old_day = (date.fromisoformat(old_triggers[phase]) + timedelta(days=offset)).isoformat() if phase < len(old_triggers) else None
        if old_day and old_day > f'{year-1}-11-14':
            old_day = None
        mappings[day] = (old_day, offset + 1)
    daily = {}
    for day in sorted({old_day for old_day, _ in mappings.values() if old_day}):
        value, actual = history.get(day), actuals.get(day)
        target = value['amount'] if value else None
        qty = paid = None
        reason = ''
        if actual and actual.get('source_rows') and actual.get('qty_rows') == actual['source_rows'] and actual.get('amount_rows') == actual['source_rows']:
            try:
                qty, paid = number(actual['order_qty']), number(actual['payment_amount'])
                if qty != qty.to_integral_value():
                    qty = paid = None
            except ValueError:
                qty = paid = None
        if target is None or target <= 0:
            reason = '去年对应日GMV目标缺失或不大于0'
        elif qty is None or paid is None:
            reason = '去年对应日实际单量或GMV缺失/无效'
        elif qty <= 0 or paid <= 0:
            reason = '去年对应日单量或GMV不大于0，无法计算有效客单价'
        rate = paid / target if not reason else None
        aov = paid / qty if not reason else None
        daily[day] = {'rate': rate, 'aov': aov, 'reason': reason}
        response['history_rows'].append({'date': day, 'target_gmv': float(target) if target is not None else None,
            'actual_order_qty': float(qty) if qty is not None else None, 'actual_gmv': float(paid) if paid is not None else None,
            'achievement_rate': float(rate) if rate is not None else None,
            'aov': float(aov) if aov is not None else None, 'included': not reason, 'skipped_reason': reason,
            'source_cell': value['cell'] if value else None, 'source_run_id': value['run_id'] if value else None})
    for day, (old_day, phase_day) in mappings.items():
        value = current.get(day)
        amount = value['amount'] if value else None
        reference = daily.get(old_day, {})
        reason = reference.get('reason', '') if old_day else '没有可对应的去年尾款阶段日期'
        if amount is None:
            reason = '今年对应日GMV目标为空'
        rate, aov = reference.get('rate'), reference.get('aov')
        quantity = amount / aov * rate if not reason and amount is not None and aov and rate is not None else None
        rounded = int(quantity.quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if quantity is not None else None
        if rounded is not None and rounded > 10**12:
            reason = '计算单量超出填报上限'
            rounded = None
        response['rows'].append({'date': day, 'target_gmv': float(amount) if amount is not None else None,
            'history_date': old_day, 'phase_day': phase_day,
            'achievement_rate': float(rate) if rate is not None else None, 'aov': float(aov) if aov is not None else None,
            'estimated_order_qty': rounded, 'skipped_reason': reason,
            'is_tail_day': day in tails, 'source_cell': value['cell'] if value else None})
    filled = sum(row['estimated_order_qty'] is not None for row in response['rows'])
    response['statistics'] = {'reference_days': len(daily), 'valid_reference_days': sum(value['rate'] is not None for value in daily.values()),
                              'fillable_days': filled, 'skipped_days': len(response['rows']) - filled}
    response.update(status='success' if filled else 'incomplete_history',
                    message='按去年对应日分别计算达成率及客单价，缺失日期单独跳过' if filled else '没有可计算的逐日历史参考，未生成填充值')
    return response
