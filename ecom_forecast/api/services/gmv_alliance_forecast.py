"""精选联盟上月均值及周末/国家假期实绩增幅，只生成预览，不写填报。"""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from api.services import query_source
from api.services.cn_rest_calendar import classify, SOURCES
from api.services.gmv_forecast_inputs import context
from api.services.gmv_history_reference import DOUYIN_STORES, DOUYIN_ACTUAL_SQL, actual_value

CHANNEL = '抖音精选联盟订单'
ORDER_TYPES = ('抖音-精选联盟（非派样）', '抖音-精选联盟（派样）')
SQL = DOUYIN_ACTUAL_SQL.replace('order_type = :order_type', 'order_type IN (:type0, :type1)')


def rounded(value, unit='.01'):
    return float(value.quantize(Decimal(unit), rounding=ROUND_HALF_UP)) if value is not None else None


def estimate(value):
    if value is None or not 0 <= value <= 10**12:
        return None
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def preview(run_id, months):
    run, _, days = context(run_id)
    if run.get('result', {}).get('parser') != 'douyin' or run.get('source', {}).get('shop', run.get('shop')) != '抖音店铺':
        raise ValueError('精选联盟填充仅支持抖音店铺')
    if not months or len(months) > 12 or any(month not in {day[:7] for day in days} for month in months):
        raise ValueError('请选择当前规划中的月份')
    rows, forecasts = [], []
    for month in sorted(set(months)):
        end = date.fromisoformat(month + '-01')
        start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        count = calendar.monthrange(start.year, start.month)[1]
        historical = {date(start.year, start.month, n).isoformat(): classify(date(start.year, start.month, n)) for n in range(1, count + 1)}
        target_days = {day: classify(day) for day in days if day.startswith(month + '-')}
        raw = query_source.rows(SQL, {'start': start.isoformat(), 'end': end.isoformat(),
                                     **{f'store{i}': name for i, name in enumerate(DOUYIN_STORES)},
                                     'type0': ORDER_TYPES[0], 'type1': ORDER_TYPES[1]})
        actuals = {}
        for row in raw:
            day = str(row['date'])[:10]
            quantity = actual_value(row, 'order_count', 'qty_rows')
            if day not in historical:
                continue
            if day in actuals:
                raise ValueError('精选联盟实绩日期重复，不能重复计入均值')
            if quantity is not None and 0 <= quantity <= 10**12 and quantity == quantity.to_integral_value():
                actuals[day] = quantity
        complete = len(actuals) == count
        ordinary = [qty for day, qty in actuals.items() if not historical[day]['special']]
        special = [qty for day, qty in actuals.items() if historical[day]['special']]
        total = sum(actuals.values(), Decimal(0))
        base = total / count if complete else None
        ordinary_mean = sum(ordinary) / len(ordinary) if ordinary else None
        special_mean = sum(special) / len(special) if special else None
        factor = special_mean / ordinary_mean if complete and ordinary_mean and special_mean is not None else Decimal(1) if complete and total == 0 else None
        special_qty = base * factor if base is not None and factor is not None else None
        rows.append({'month': month, 'history_month': start.strftime('%Y-%m'), 'calendar_days': count,
                     'valid_actual_days': len(actuals), 'missing_actual_days': count - len(actuals),
                     'total_order_qty': int(total), 'mean_order_qty': rounded(base),
                     'ordinary_days': len(ordinary), 'special_days': len(special),
                     'ordinary_mean': rounded(ordinary_mean), 'special_mean': rounded(special_mean),
                     'multiplier': rounded(factor, '.000001'), 'uplift_rate': rounded(factor - 1, '.000001') if factor is not None else None,
                     'ordinary_estimate': estimate(base),
                     'special_estimate': estimate(special_qty),
                     'status': 'incomplete_actuals' if not complete else 'no_special_factor' if factor is None else 'success'})
        for day, info in target_days.items():
            amount = special_qty if info['special'] else base
            forecasts.append({'date': day, 'special': info['special'], 'day_type': info['label'],
                              'history_month': start.strftime('%Y-%m'), 'amount': estimate(amount)})
    return {'run_id': run_id, 'channel': CHANNEL, 'rows': rows, 'forecasts': forecasts,
            'stores': list(DOUYIN_STORES), 'order_types': list(ORDER_TYPES), 'holiday_sources': SOURCES}
