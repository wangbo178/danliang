"""高目标货架用接近的历史实际GMV匹配单量，只生成预览。"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from api.services import expected_gmv as g, query_source
from api.services.gmv_history_reference import shelf_targets, ACTUAL_SQL, actual_value

THRESHOLD = Decimal('15000000')
TOLERANCE = Decimal('5000000')
SCOPES = {'since_2024_10', 'rolling_year', 'current_year', 'previous_month'}


def history_start(day, scope):
    if scope == 'since_2024_10':
        return date(2024,10,1)
    if scope == 'current_year':
        return day.replace(month=1, day=1)
    if scope == 'previous_month':
        return date(day.year-1,12,1) if day.month == 1 else date(day.year,day.month-1,1)
    try:
        return day.replace(year=day.year-1)
    except ValueError:
        return day.replace(year=day.year-1,day=28)


def history_end(day, scope):
    end = day.replace(day=1) if scope == 'previous_month' else day
    return min(end,date.today())


def preview(run_id, start_date=None, end_date=None, scope='since_2024_10', mode='special', selected_dates=None):
    run = g.get('run',run_id)
    if run.get('rule_key') != 'tmall':
        raise ValueError('特殊货架填充仅支持天猫旗舰店')
    if scope not in SCOPES:
        raise ValueError('历史范围无效')
    if mode not in {'special','reference_two'}:
        raise ValueError('货架历史匹配模式无效')
    targets = shelf_targets(run)
    if selected_dates is not None and (not selected_dates or len(selected_dates)>366 or any(day not in targets for day in selected_dates)):
        raise ValueError('参考日期必须在当前规划中')
    days = sorted(targets)
    start_date, end_date = start_date or days[0], end_date or days[-1]
    if start_date not in targets or end_date not in targets or start_date > end_date:
        raise ValueError('请选择当前规划内的有效日期范围')
    selected = [(day,value) for day,value in sorted(targets.items())
                if start_date <= day <= end_date and value['amount'] > (THRESHOLD if mode=='special' else 0)
                and (selected_dates is None or day in selected_dates)]
    result = {'run_id':run_id,'channel':'货架-常规','scope':scope,'mode':mode,'threshold':int(THRESHOLD) if mode=='special' else None,'tolerance':int(TOLERANCE) if mode=='special' else .1,
              'rows':[], 'available_count':0, 'skipped_count':0,
              'definition':'薇诺娜天猫旗舰店；支付日；ads_ofs_order_daily_summary，order_type=货架；实际达成GMV与当前目标差额不超过500万元的全部日期，按接近程度排序并取有效实际单量均值。'}
    if not selected:
        return result
    query_start = min(history_start(date.fromisoformat(day),scope) for day,_ in selected)
    query_end = max(history_end(date.fromisoformat(day),scope) for day,_ in selected)
    raw = query_source.rows(ACTUAL_SQL, {'start':query_start.isoformat(),'end':query_end.isoformat(),
                                       'store':'薇诺娜天猫旗舰店','order_type':'货架'}) if query_start < query_end else []
    actuals = {}
    for row in raw:
        day = str(row['date'])[:10]
        if not query_start.isoformat() <= day < query_end.isoformat():
            continue
        if day in actuals:
            raise ValueError('货架实际日期重复，不能重复统计')
        paid = actual_value(row,'pay_amount','amount_rows')
        qty = actual_value(row,'order_count','qty_rows')
        if paid is None or paid < 0:
            continue
        included = qty is not None and 0 <= qty <= 10**12 and qty == qty.to_integral_value()
        actuals[day] = {'paid':paid, 'qty':qty if included else None}
    result.update(query_start=query_start.isoformat(),query_end=query_end.isoformat())
    for day,value in selected:
        target = value['amount']
        start = history_start(date.fromisoformat(day),scope).isoformat()
        end = history_end(date.fromisoformat(day),scope).isoformat()
        matched = []
        nearest = None
        tolerance = TOLERANCE if mode=='special' else target*Decimal('.10')
        for old_day, actual in actuals.items():
            difference = actual['paid']-target
            if not start <= old_day < end:
                continue
            candidate = {'date':old_day,'actual_gmv':float(actual['paid']),'difference':float(difference),
                         'difference_rate':float(difference/target),
                         'order_qty':int(actual['qty']) if actual['qty'] is not None else None}
            if nearest is None or (abs(candidate['difference']),old_day) < (abs(nearest['difference']),nearest['date']):
                nearest = candidate
            if abs(difference) <= tolerance:
                matched.append({'date':old_day,'actual_gmv':float(actual['paid']),
                                'difference':float(difference),'order_qty':int(actual['qty']) if actual['qty'] is not None else None,
                                'difference_rate':float(difference/target),
                                'included':actual['qty'] is not None})
        matched.sort(key=lambda row:(abs(row['difference']),row['date']))
        quantities = [row['order_qty'] for row in matched if row['included']]
        mean = Decimal(sum(quantities))/len(quantities) if quantities else None
        result['rows'].append({'date':day,'target_gmv':float(target),'source_cells':value['source_cells'],
                               'history_start':start,'history_end_exclusive':end,'samples':matched,
                               'closest_date':matched[0]['date'] if matched else None,
                               'nearest_actual':nearest,
                               'sample_count':len(quantities),'matched_count':len(matched),
                               'mean_order_qty':float(mean.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) if mean is not None else None,
                               'estimated_order_qty':int(mean.quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if mean is not None else None})
    result['available_count'] = sum(row['estimated_order_qty'] is not None for row in result['rows'])
    result['skipped_count'] = len(result['rows'])-result['available_count']
    return result


def history_two(run_id, dates, scope='since_2024_10'):
    result = preview(run_id, scope=scope, mode='reference_two', selected_dates=dates)
    result['definition'] = '按货架历史实际GMV匹配当前目标，差额绝对值÷目标GMV≤10%；取有效实绩单量均值，仅供当前预估单量为空或0的日期填充。'
    return result
