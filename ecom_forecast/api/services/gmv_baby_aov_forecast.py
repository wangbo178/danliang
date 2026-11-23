"""宝贝店按上月加权客单价预估四渠道单量，仅返回预览。"""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from api.services import query_source
from api.services.gmv_forecast_inputs import context
from api.services.gmv_history_reference import actual_value

CHANNELS = ['自播官旗', '商城', 'KOC', 'DP合计']
STORES = ('抖音-薇诺娜宝贝专卖店', '抖音-薇诺娜宝贝母婴旗舰店')
SQL = '''SELECT DATE(pay_date) AS date, COUNT(*) AS source_rows,
    COUNT(order_count) AS qty_rows, COUNT(pay_amount) AS amount_rows,
    SUM(order_count) AS order_count, SUM(pay_amount) AS pay_amount
FROM ads_ofs_order_daily_summary
WHERE store_name IN (:store0, :store1)
    AND pay_date >= :start AND pay_date < :end
GROUP BY DATE(pay_date) ORDER BY DATE(pay_date)'''


def preview(run_id, months):
    run, _, days = context(run_id)
    if run.get('result', {}).get('parser') != 'douyin_baby':
        raise ValueError('该填充仅支持抖音宝贝')
    if not months or len(months) > 12 or any(month not in {day[:7] for day in days} for month in months):
        raise ValueError('请选择当前规划中的月份')
    rows, forecasts = [], []
    for month in sorted(set(months)):
        end = date.fromisoformat(month + '-01')
        start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        raw = query_source.rows(SQL, {'store0':STORES[0], 'store1':STORES[1], 'start':start.isoformat(), 'end':end.isoformat()})
        actuals = {}
        for row in raw:
            day = str(row['date'])[:10]
            if not start.isoformat() <= day < end.isoformat():
                continue
            qty = actual_value(row, 'order_count', 'qty_rows')
            paid = actual_value(row, 'pay_amount', 'amount_rows')
            if qty is None or paid is None or qty < 0 or paid < 0 or qty != qty.to_integral_value() or qty > 10**12:
                continue
            if day in actuals:
                raise ValueError('宝贝上月实绩日期重复')
            actuals[day] = (qty, paid)
        count = calendar.monthrange(start.year,start.month)[1]
        qty = sum((value[0] for value in actuals.values()), Decimal(0))
        paid = sum((value[1] for value in actuals.values()), Decimal(0))
        complete = len(actuals) == count
        aov = (paid / qty).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if complete and qty > 0 and paid > 0 else None
        if aov is not None and not 0 < aov <= 10**12:
            aov = None
        rows.append({'month':month, 'history_month':start.strftime('%Y-%m'), 'calendar_days':count,
                     'valid_actual_days':len(actuals), 'total_order_qty':int(qty), 'total_pay_amount':float(paid),
                     'mean_aov':float(aov) if aov is not None else None,
                     'status':'incomplete_actuals' if not complete else 'invalid_aov' if aov is None else 'success'})
        for rec in run['result']['records']:
            if rec['kind'] != 'day' or rec['channel'] not in CHANNELS or not rec['period'].startswith(month+'-'):
                continue
            amount = None
            if aov is not None and rec['amount'] is not None:
                target = Decimal(str(rec['amount']))
                value = (target/aov).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
                if value.is_finite() and 0 <= value <= 10**12:
                    amount = int(value)
            forecasts.append({'date':rec['period'], 'channel':rec['channel'], 'gmv':rec['amount'],
                              'aov':float(aov) if aov is not None else None, 'amount':amount})
    return {'run_id':run_id, 'channels':CHANNELS, 'stores':list(STORES), 'rows':rows, 'forecasts':forecasts,
            'definition':'仅指定两家抖音宝贝店铺；上月总实付金额÷总单量，保留两位小数；目标GMV÷该客单价，四舍五入取整。'}
