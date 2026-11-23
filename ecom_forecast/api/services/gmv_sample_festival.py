"""按历年10月识别小样节，先按年汇总单量，再计算参与年份平均值。"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from api.services import query_source
from api.services.gmv_forecast_inputs import context
from api.services.gmv_parser import number

HISTORY_SQL = '''SELECT DATE(order_create_time) AS date, COUNT(*) AS source_rows,
    COUNT(order_qty) AS qty_rows, COUNT(order_pay_amount) AS amount_rows,
    SUM(order_qty) AS order_qty, SUM(order_pay_amount) AS payment_amount
FROM dwh_db_order_summary
WHERE channel_type = :channel AND channel_name = :anchor
    AND shop_name = :shop
    AND order_create_time < :end
    AND (MONTH(order_create_time) = 10 OR (MONTH(order_create_time) = 11 AND DAYOFMONTH(order_create_time) <= :nearby_days))
GROUP BY DATE(order_create_time) ORDER BY DATE(order_create_time)'''


def validate_nearby_days(nearby_days):
    if type(nearby_days) is not int or not 0 <= nearby_days <= 30:
        raise ValueError('附近日期范围必须是0至30天，默认T+1')


def summarize(raw, target_year, nearby_days=1):
    validate_nearby_days(nearby_days)
    yearly, matched, invalid_days, available_years, valid = {}, [], 0, set(), {}
    for row in raw:
        try:
            day = date.fromisoformat(str(row['date'])[:10])
        except (ValueError, KeyError):
            invalid_days += 1
            continue
        if day.year >= target_year or (day.month != 10 and not (day.month == 11 and day.day <= nearby_days)):
            continue
        if day.month == 10:
            available_years.add(day.year)
        if not row.get('source_rows') or row.get('qty_rows') != row['source_rows'] or row.get('amount_rows') != row['source_rows']:
            invalid_days += 1
            continue
        try:
            qty, paid = number(row['order_qty']), number(row['payment_amount'])
        except (ValueError, KeyError):
            invalid_days += 1
            continue
        if qty != qty.to_integral_value():
            invalid_days += 1
            continue
        valid[day] = (qty, paid)
    seeds = sorted(day for day, (qty, paid) in valid.items() if day.month == 10 and qty > 100000 and paid < qty * 10)
    included = {}
    for seed in seeds:
        included.setdefault(seed, set()).add(seed)
        # 始终相对首日计算，不从T+1继续递推，也不跳过缺失日去找下一条记录。
        for offset in range(1, nearby_days + 1):
            nearby = seed + timedelta(days=offset)
            qty, paid = valid.get(nearby, (Decimal(0), Decimal(0)))
            if qty > 40000 and paid < qty * 10:
                included.setdefault(nearby, set()).add(seed)
    for day in sorted(included):
        qty, paid = valid[day]
        amount = int(qty)
        is_seed = day in seeds
        yearly.setdefault(day.year, {'year': day.year, 'order_qty': 0, 'sample_days': 0, 'seed_days': 0, 'nearby_days': 0})
        yearly[day.year]['order_qty'] += amount
        yearly[day.year]['sample_days'] += 1
        yearly[day.year]['seed_days' if is_seed else 'nearby_days'] += 1
        matched.append({'date': day.isoformat(), 'year': day.year, 'order_qty': amount,
                        'payment_amount': float(paid), 'aov': float(paid / qty),
                        'inclusion_type': 'seed' if is_seed else 'nearby',
                        'seed_dates': [seed.isoformat() for seed in sorted(included[day])],
                        'offset_days': 0 if is_seed else min((day-seed).days for seed in included[day])})
    years = sorted(yearly)
    total = sum(row['order_qty'] for row in yearly.values())
    average = Decimal(total) / len(years) if years else None
    rounded = int(average.quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if average is not None else None
    in_range = rounded is not None and rounded <= 10**12
    return {'status': 'success' if in_range else 'no_samples' if not years else 'out_of_range',
            'yearly_rows': [yearly[year] for year in years], 'daily_rows': sorted(matched, key=lambda row: row['date']),
            'statistics': {'year_count': len(years), 'matched_days': len(matched), 'seed_days': len(seeds),
                           'nearby_days': len(matched)-len(seeds), 'total_order_qty': total,
                           'mean_order_qty': float(average.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if average is not None else None,
                           'estimated_order_qty': rounded if in_range else None},
            'available_years': sorted(available_years), 'unmatched_years': sorted(available_years - set(years)),
            'invalid_actual_days': invalid_days}


def preview(run_id, nearby_days=1):
    validate_nearby_days(nearby_days)
    run, _, days = context(run_id)
    if run.get('rule_key') != 'tmall' or run.get('source', {}).get('shop', run.get('shop')) not in {'天猫旗舰店', '薇诺娜天猫旗舰店'}:
        raise ValueError('仅天猫旗舰店支持小样节单量预估')
    raw = query_source.rows(HISTORY_SQL, {'channel': '达播', 'anchor': '李佳琦', 'shop': '薇诺娜天猫旗舰店', 'end': f'{run["year"]}-01-01', 'nearby_days': nearby_days})
    return {'run_id': run_id, 'channel': '达播-超头', 'target_year': run['year'], 'allowed_dates': days,
            'definition': {'channel_type': '达播', 'channel_name': '李佳琦', 'shop_name': '薇诺娜天猫旗舰店', 'order_threshold': 100000,
                           'aov_threshold': 10, 'month': 10, 'nearby_order_threshold': 40000,
                           'nearby_days': nearby_days, 'recursive': False, 'exclude_target_year': True,
                           'year_count': '仅统计识别到小样节的年份',
                           'actual_filters': '仅薇诺娜天猫旗舰店，不附加平台、订单状态过滤'},
            **summarize(raw, run['year'], nearby_days)}
