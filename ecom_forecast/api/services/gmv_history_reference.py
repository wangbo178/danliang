"""匹配上月货架目标，无接近目标时回退去年同月；用支付日汇总表查询实绩。"""
import calendar
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from api.services import expected_gmv as g, query_source
from api.services.gmv_parser import number, tmall_boundary
from api.services.gmv_douyin_parser import verify as verify_douyin

TOLERANCE = Decimal('0.10')
ACTUAL_SQL = '''SELECT DATE(pay_date) AS date, COUNT(*) AS source_rows,
    COUNT(order_count) AS qty_rows, COUNT(pay_amount) AS amount_rows,
    SUM(order_count) AS order_count, SUM(pay_amount) AS pay_amount
FROM ads_ofs_order_daily_summary
WHERE pay_date >= :start AND pay_date < :end
    AND store_name = :store AND order_type = :order_type
GROUP BY DATE(pay_date) ORDER BY DATE(pay_date)'''
DOUYIN_STORES = ('抖音-薇诺娜官方旗舰店', '抖音-薇诺娜护肤旗舰店', '抖音-薇诺娜旗舰店')
DOUYIN_ACTUAL_SQL = ACTUAL_SQL.replace('store_name = :store', 'store_name IN (:store0, :store1, :store2)')


def reference_profile(run):
    if run.get('result', {}).get('parser') == 'douyin' and run.get('source', {}).get('shop', run.get('shop')) == '抖音店铺':
        return {'channel': '抖音自播', 'target_channel': '抖音自播', 'shop': '抖音店铺', 'fallback': False,
                'sql': DOUYIN_ACTUAL_SQL, 'params': {f'store{i}': name for i, name in enumerate(DOUYIN_STORES)},
                'stores': list(DOUYIN_STORES), 'target': '抖音自播GMV目标（当前与本年历史均取自播），实际货架平均单量填入抖音自播'}
    return {'channel': '货架-常规', 'target_channel': '货架-常规', 'shop': '天猫旗舰店', 'fallback': True, 'sql': ACTUAL_SQL,
            'params': {'store': '薇诺娜天猫旗舰店'}, 'stores': ['薇诺娜天猫旗舰店'],
            'target': run.get('snapshot', {}).get('target_section', '渠道目标-GMV') + ' / 货架-常规，直接复用主表解析结果'}


def shelf_targets(run):
    """使用主表同一解析结果，并核对源坐标仍位于渠道目标-GMV/货架-常规。"""
    if reference_profile(run)['channel'] == '抖音自播':
        if run.get('status') != 'success':
            raise ValueError('历史参考仅支持成功解析的规划')
        report = verify_douyin(run.get('snapshot', {}), run['result'], run['year'])
        if not report['passed']:
            raise ValueError('抖音自播历史目标与原表读取范围或口径不符：' + '; '.join(report['errors']))
        targets = {r['period']: {'amount': number(r['amount']), 'source_cells': r['source_cells']}
                   for r in run['result']['records'] if r['kind'] == 'day' and r['channel'] == '抖音自播'}
        if not targets:
            raise ValueError('主表未解析出抖音自播目标')
        return targets
    if run.get('rule_key') != 'tmall' or run.get('status') != 'success':
        raise ValueError('历史参考仅支持成功解析的天猫旗舰店')
    if run.get('source', {}).get('shop', run.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
        raise ValueError('当前来源不是天猫旗舰店')
    cells = run.get('snapshot', {}).get('cells', [])
    start, end = tmall_boundary(run['snapshot'])
    rows = {}
    lookup = {cell['ref']: cell for cell in cells}
    for cell in cells:
        rows.setdefault(cell['row'], {})[cell['col']] = cell
    header = max((r for r, cs in rows.items() if r < start and any(str(c['value']).strip() == '日期' for c in cs.values())), default=None)
    targets = {}
    for record in run['result']['records']:
        if record['kind'] != 'day' or record['channel'] != '货架-常规':
            continue
        source = lookup.get(record.get('cell'))
        if source is None or not start <= source['row'] < end:
            raise ValueError('货架目标源坐标不在渠道目标-GMV区域')
        label = str(rows[source['row']].get('B', {}).get('value', '')).strip()
        if label not in {'货架-常规', '货架-常规GMV'}:
            raise ValueError('货架目标必须与主表货架-常规渠道一致')
        raw_date = str(rows.get(header, {}).get(source['col'], {}).get('value', '')).strip()
        match = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', raw_date)
        if not match or date(run['year'], int(match[1]), int(match[2])).isoformat() != record['period']:
            raise ValueError('货架目标源日期与主表不一致')
        amount = number(record['amount'])
        if abs(number(source['value']) * run['result'].get('unit_multiplier', 1) - amount) > Decimal('.01'):
            raise ValueError('主表货架GMV与原表渠道目标-GMV源值不一致')
        if record['period'] in targets:
            raise ValueError('主表货架目标日期重复')
        targets[record['period']] = {'amount': amount, 'source_cells': [record['cell']]}
    if not targets:
        raise ValueError('主表未解析出渠道目标-GMV/货架-常规数据')
    return targets


def actual_value(row, key, count_key):
    if row is None or not row.get('source_rows') or row.get(count_key) != row['source_rows'] or row.get(key) is None:
        return None
    value = Decimal(str(row[key]))
    return value if value.is_finite() else None


def reference(run_id, target_date, _cache=None):
    cache = _cache if _cache is not None else {}
    if 'run' not in cache:
        cache['run'] = g.get('run', run_id)
        cache['current'] = shelf_targets(cache['run'])
    run, current = cache['run'], cache['current']
    profile = reference_profile(run)
    if target_date not in current:
        raise ValueError('请选择当前GMV规划中的日期')
    selected = date.fromisoformat(target_date)
    first = selected.replace(day=1)
    previous_year, previous_month = (first.year - 1, 12) if first.month == 1 else (first.year, first.month - 1)
    month = f'{previous_year}-{previous_month:02d}'
    last_year_month = f'{selected.year - 1}-{selected.month:02d}'
    target = current[target_date]['amount']
    lower, upper = target * (1 - TOLERANCE), target * (1 + TOLERANCE)
    response = {'target_date': target_date, 'target_gmv': float(target), 'channel': profile['channel'], 'target_channel': profile['target_channel'], 'shop': profile['shop'], 'fallback_enabled': profile['fallback'], 'history_month': month,
        'previous_month': month, 'last_year_month': last_year_month, 'reference_source': 'previous_month',
        'fallback_used': False, 'searched_months': [month],
        'tolerance': float(TOLERANCE), 'lower_gmv': float(lower), 'upper_gmv': float(upper), 'status': 'success',
        'target_source_cells': current[target_date]['source_cells'], 'rows': [],
        'targets': [{'date': day, 'gmv': float(value['amount'])} for day, value in sorted(current.items())],
        'statistics': {'matched_count': 0, 'sample_count': 0, 'missing_actual_count': 0, 'median_order_qty': None, 'mean_order_qty': None},
        'definition': {'target': profile['target'],
            'actual_table': 'ads_ofs_order_daily_summary', 'date_field': 'pay_date',
            'store_name': profile['stores'][0] if len(profile['stores']) == 1 else profile['stores'], 'order_type': '货架', 'aov': 'SUM(pay_amount) / SUM(order_count)'}}
    annual = profile['channel'] == '抖音自播'
    if annual:
        response.update(reference_source='current_year', history_month=f'{selected.year}年全年',
                        searched_months=[f'{selected.year}-{m:02d}' for m in range(1, 13)],
                        history_end_exclusive=min(target_date, date.today().isoformat()), available_history_months=[])
    if target == 0:
        response['status'] = 'zero_target'
        return response
    if 'runs' not in cache:
        cache['runs'] = g.all_items('run')
    runs = cache['runs']
    if annual and 'source_settings' not in cache:
        cache['source_settings'] = {s['id']: s for s in g.all_items('source')}
    history_cache = cache.setdefault('history', {})
    run_cache = cache.setdefault('targets', {})
    def history_for(history_month):
        if history_month in history_cache:
            return history_cache[history_month]
        history = {}
        year, month_number = map(int, history_month.split('-'))
        for old in runs:
            if old.get('status') != 'success':
                continue
            if profile['channel'] == '抖音自播':
                if reference_profile(old)['channel'] != '抖音自播':
                    continue
                settings = cache['source_settings'].get(old.get('source_id'), old.get('source', {}))
                if settings.get('history_enabled') is False:
                    continue
            elif old.get('rule_key') != 'tmall' or old.get('source', {}).get('shop', old.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
                continue
            if not any(r['kind'] == 'day' and r['period'].startswith(history_month) for r in old.get('result', {}).get('records', [])):
                continue
            if old['id'] not in run_cache:
                run_cache[old['id']] = shelf_targets(old)
            for day, value in run_cache[old['id']].items():
                if day.startswith(history_month) and day not in history:
                    history[day] = {**value, 'run_id': old['id']}
            if len(history) == calendar.monthrange(year, month_number)[1]:
                break
        history_cache[history_month] = history
        return history

    def matches(history, amount):
        return [(day, value) for day, value in history.items()
                if amount > 0 and amount * (1-TOLERANCE) <= value['amount'] <= amount * (1+TOLERANCE)]

    if annual:
        all_history = {day: value for history_month in response['searched_months']
                       for day, value in history_for(history_month).items()}
        history = {day: value for day, value in all_history.items() if day < response['history_end_exclusive']}
        response['available_history_months'] = sorted({day[:7] for day in history})
    else:
        history = history_for(month)
    matched = matches(history, target)
    if not matched and profile['fallback']:
        response.update(history_month=last_year_month, reference_source='previous_year', fallback_used=True,
                        searched_months=[month, last_year_month])
        fallback = history_for(last_year_month)
        matched = matches(fallback, target)
        if not matched:
            response['status'] = 'no_match' if history or fallback else 'no_history'
    elif not matched:
        response['status'] = 'no_match' if history else 'no_history'
    for option in response['targets']:
        if annual or option['date'][:7] == target_date[:7]:
            amount = current[option['date']]['amount']
            option_history = {day: value for day, value in all_history.items()
                              if day < min(option['date'], date.today().isoformat())} if annual else history
            options = matches(option_history, amount)
            use_fallback = not options and profile['fallback']
            option['reference_source'] = 'current_year' if annual else 'previous_year' if use_fallback else 'previous_month'
            option['reference_month'] = response['history_month'] if annual else last_year_month if use_fallback else month
            option['eligible_count'] = len(options or (matches(history_for(last_year_month), amount) if use_fallback else [])) if amount > 0 else 0
    matched.sort(key=lambda item: (abs(item[1]['amount'] - target), item[0]))
    response['statistics']['matched_count'] = len(matched)
    if not matched:
        return response
    if annual:
        start = date(selected.year, 1, 1)
        end = min(date(selected.year + 1, 1, 1), date.today())
    else:
        year, month_number = map(int, response['history_month'].split('-'))
        start = date(year, month_number, 1)
        end = date(year + 1, 1, 1) if month_number == 12 else date(year, month_number + 1, 1)
    actual_cache = cache.setdefault('actuals', {})
    if response['history_month'] not in actual_cache:
        actual_cache[response['history_month']] = query_source.rows(profile['sql'], {
            'start': start.isoformat(), 'end': end.isoformat(), **profile['params'], 'order_type': '货架'})
    raw = actual_cache[response['history_month']]
    actuals = {str(row['date'])[:10]: row for row in raw}
    quantities = []
    for day, value in matched:
        actual = actuals.get(day)
        quantity = actual_value(actual, 'order_count', 'qty_rows')
        paid = actual_value(actual, 'pay_amount', 'amount_rows')
        included = quantity is not None and quantity >= 0
        if included:
            quantities.append(quantity)
        aov = paid / quantity if paid is not None and quantity and quantity > 0 else None
        response['rows'].append({'date': day, 'target_gmv': float(value['amount']),
            'difference_rate': float((value['amount'] - target) / target), 'source_cells': value['source_cells'], 'run_id': value['run_id'],
            'order_qty': float(quantity) if quantity is not None else None, 'payment_amount': float(paid) if paid is not None else None,
            'avg_order_value': float(aov.quantize(Decimal('.01'))) if aov is not None else None, 'included': included})
    response['statistics'].update(sample_count=len(quantities), missing_actual_count=len(matched) - len(quantities),
        median_order_qty=float(median(quantities)) if quantities else None,
        mean_order_qty=float((sum(quantities) / len(quantities)).quantize(Decimal('.01'))) if quantities else None)
    return response


def reference_averages(run_id):
    """全部规划日期的平均单量建议；相同参考月份只查询一次实绩，不修改填报。"""
    run = g.get('run', run_id)
    current = shelf_targets(run)
    cache = {'run': run, 'current': current}
    rows = []
    for day in sorted(current):
        result = reference(run_id, day, cache)
        mean = result['statistics']['mean_order_qty']
        rows.append({'date': day, 'mean_order_qty': mean, 'history_month': result['history_month'],
                     'reference_source': result['reference_source'], 'sample_count': result['statistics']['sample_count'],
                     'status': result['status']})
    return {'run_id': run_id, 'channel': reference_profile(run)['channel'], 'rows': rows,
            'available_count': sum(row['mean_order_qty'] is not None for row in rows),
            'skipped_count': sum(row['mean_order_qty'] is None for row in rows)}


def previous_month_minimum(run_id, month):
    """读取上月货架实际最低单量及对应日GMV，不修改本月填报。"""
    run = g.get('run', run_id)
    if reference_profile(run)['channel'] != '货架-常规':
        raise ValueError('单量底线调整目前仅支持天猫旗舰店')
    targets = shelf_targets(run)
    if not re.fullmatch(r'\d{4}-\d{2}', month) or not any(day.startswith(month + '-') for day in targets):
        raise ValueError('请选择当前规划中的月份')
    end = date.fromisoformat(month + '-01')
    start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
    raw = query_source.rows(ACTUAL_SQL, {'start': start.isoformat(), 'end': end.isoformat(),
                                       'store': '薇诺娜天猫旗舰店', 'order_type': '货架'})
    days = []
    for row in raw:
        quantity = actual_value(row, 'order_count', 'qty_rows')
        if quantity is None or quantity < 0 or quantity != quantity.to_integral_value() or quantity > 10**12:
            continue
        paid = actual_value(row, 'pay_amount', 'amount_rows')
        days.append({'date': str(row['date'])[:10], 'order_qty': int(quantity),
                     'payment_amount': float(paid) if paid is not None else None})
    minimum = min((row['order_qty'] for row in days), default=None)
    return {'run_id': run_id, 'month': month, 'channel': '货架-常规', 'history_month': start.strftime('%Y-%m'),
            'minimum_order_qty': minimum, 'minimum_days': sorted(
                (row for row in days if row['order_qty'] == minimum), key=lambda row: row['date']),
            'valid_actual_days': len(days), 'missing_actual_days': calendar.monthrange(start.year, start.month)[1] - len(days),
            'status': 'success' if minimum is not None else 'no_actuals'}


def cps_monthly_averages(run_id, months):
    return monthly_order_averages(run_id, months, '货架-CPS')


def sampling_monthly_averages(run_id, months):
    return monthly_order_averages(run_id, months, '派样')


def monthly_order_averages(run_id, months, channel):
    """按整月日均实绩给出填充建议；缺少完整月份数据时不补零。"""
    from api.services.gmv_forecast_inputs import context
    run, _, days = context(run_id)
    tmall = run.get('rule_key') == 'tmall' and run.get('source', {}).get('shop', run.get('shop')) in {'天猫旗舰店', '薇诺娜天猫旗舰店'}
    douyin = channel == '派样' and run.get('result', {}).get('parser') == 'douyin' and run.get('source', {}).get('shop', run.get('shop')) == '抖音店铺'
    if not (tmall or douyin):
        raise ValueError('CPS仅支持天猫旗舰店；派样支持天猫旗舰店和抖音店铺')
    sql = DOUYIN_ACTUAL_SQL if douyin else ACTUAL_SQL
    stores = list(DOUYIN_STORES) if douyin else ['薇诺娜天猫旗舰店']
    store_params = {f'store{i}': name for i, name in enumerate(stores)} if douyin else {'store': stores[0]}
    order_type = {'货架-CPS': '天猫官旗CPS', '派样': '派样'}[channel]
    available = {day[:7] for day in days}
    if not months or len(months) > 12 or any(month not in available for month in months):
        raise ValueError('请选择当前规划中的月份')
    results = []
    for month in sorted(set(months)):
        end = date.fromisoformat(month + '-01')
        start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        raw = query_source.rows(sql, {'start': start.isoformat(), 'end': end.isoformat(),
                                    **store_params, 'order_type': order_type})
        quantities = []
        for row in raw:
            quantity = actual_value(row, 'order_count', 'qty_rows')
            if quantity is not None and 0 <= quantity <= 10**12 and quantity == quantity.to_integral_value():
                quantities.append(quantity)
        calendar_days = calendar.monthrange(start.year, start.month)[1]
        complete = len(quantities) == calendar_days
        total = sum(quantities, Decimal(0))
        average = total / calendar_days if complete else None
        results.append({'month': month, 'history_month': start.strftime('%Y-%m'),
                        'calendar_days': calendar_days, 'valid_actual_days': len(quantities),
                        'missing_actual_days': calendar_days - len(quantities), 'total_order_qty': int(total),
                        'mean_order_qty': float(average.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if average is not None else None,
                        'estimated_order_qty': int(average.quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if average is not None else None,
                        'status': 'success' if complete else 'incomplete_actuals'})
    return {'run_id': run_id, 'channel': channel, 'order_type': order_type, 'stores': stores, 'rows': results}
