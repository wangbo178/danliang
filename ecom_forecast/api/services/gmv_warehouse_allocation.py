"""按近七个完整自然日的日均仓占比，分配只读预估订单。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from api.services import query_source
from api.services.gmv_actuals import DOUYIN_SHOPS
from api.services.gmv_order_preview import preview, known_sum

WAREHOUSES = ['昆明仓', '湖州仓', '武汉仓', '上海仓']
FACILITIES = {'昆明线上2C发货仓':'昆明仓', '湖州线上2C发货仓':'湖州仓', '武汉线上2C发货仓':'武汉仓'}
PROFILES = {
    '天猫旗舰店':['薇诺娜天猫旗舰店'],
    '抖音店铺':DOUYIN_SHOPS,
    '阿里健康渠道':['阿里健康大药房医药连锁有限公司', '阿里健康大药房医药连锁有限公司（昆明贝泰妮）'],
}
FIXED = {'抖音宝贝':'上海仓', '唯品会':'武汉仓', '专柜商城':'湖州仓', '天猫宝贝':'上海仓',
         '私密渠道':'武汉仓', 'KA-京东-快手-小红书':'武汉仓', '拼多多店铺':'武汉仓'}
SQL = '''SELECT DATE(pay_date) AS date, facility_name, SUM(order_count) AS order_count
FROM ads_ofs_store_facility_order_daily_summary
WHERE pay_date >= :start AND pay_date < :end
AND store_name IN ({shops}) AND facility_name IN (:facility0, :facility1, :facility2)
GROUP BY DATE(pay_date), facility_name ORDER BY DATE(pay_date), facility_name'''


def average_shares(records, start, end):
    daily={}
    for row in records:
        day=str(row['date'])[:10];facility=row['facility_name'];raw=row['order_count']
        if not start<=day<end or facility not in FACILITIES or raw is None:
            continue
        amount=Decimal(str(raw))
        if not amount.is_finite() or amount<0:
            raise ValueError('分仓历史包含无效单量，请核对库存地点发货数据')
        values=daily.setdefault(day,{warehouse:Decimal(0) for warehouse in WAREHOUSES})
        values[FACILITIES[facility]]+=amount
    valid={day:values for day,values in daily.items() if sum(values.values())>0}
    if not valid:
        return None,[]
    ratios={warehouse:sum((values[warehouse]/sum(values.values()) for values in valid.values()),Decimal(0))/len(valid)
            for warehouse in WAREHOUSES}
    return ratios,sorted(valid)


def distribute(quantity, ratios):
    if quantity is None or ratios is None:
        return {warehouse:None for warehouse in WAREHOUSES}
    quantity=int(Decimal(str(quantity)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    total=sum(ratios.values())
    if total<=0:
        return {warehouse:None for warehouse in WAREHOUSES}
    exact={warehouse:Decimal(quantity)*ratios[warehouse]/total for warehouse in WAREHOUSES}
    allocated={warehouse:int(value.to_integral_value(rounding=ROUND_FLOOR)) for warehouse,value in exact.items()}
    # 最大余数法，保证整数单量守恒；同余数时按固定仓顺序分配。
    ranked=sorted(WAREHOUSES,key=lambda warehouse:-(exact[warehouse]-allocated[warehouse]))
    for warehouse in ranked[:quantity-sum(allocated.values())]:
        allocated[warehouse]+=1
    return allocated


def allocation(month=None,start_date=None,end_date=None,today=None):
    data=preview(month,start_date,end_date)
    today=today or datetime.now(timezone(timedelta(hours=8))).date()
    start=(today-timedelta(days=7)).isoformat();end=today.isoformat()
    profiles={};warnings=[]
    with query_source.source_connection() as connection:
        for name,shops in PROFILES.items():
            params={'start':start,'end':end,**{f'shop{i}':shop for i,shop in enumerate(shops)},
                    **{f'facility{i}':facility for i,facility in enumerate(FACILITIES)}}
            records=query_source.rows(SQL.format(shops=', '.join(f':shop{i}' for i in range(len(shops)))),params,connection)
            ratios,days=average_shares(records,start,end)
            profiles[name]={'ratios':ratios,'days':days}
            if len(days)<7:
                warnings.append(f'{name}最近7天有{len(days)}天有效发货数据'+('，按有效日期的日占比平均。' if days else '，暂不分仓，不使用其它渠道占比代替。'))
    groups=[];profiles_display=[]
    for group in data['groups']:
        name=group['name'];fixed=FIXED.get(name)
        profile_name=None if fixed else (name if name in PROFILES else '天猫旗舰店')
        profile=profiles.get(profile_name)
        ratios={warehouse:Decimal(int(warehouse==fixed)) for warehouse in WAREHOUSES} if fixed else profile['ratios']
        explanation=f'全部分配至{fixed}' if fixed else f'采用{profile_name}近7天每日仓占比的平均值'
        assigned={day:distribute(group['totals'][day],ratios) for day in data['dates']}
        rows=[]
        for warehouse in WAREHOUSES:
            rows.append({'label':warehouse,'channels':[warehouse],'source_label':explanation,
                         'values':{day:{'amount':assigned[day][warehouse],'source_name':explanation,'missing_channels':[]} for day in data['dates']}})
        totals={day:known_sum([assigned[day][warehouse] for warehouse in WAREHOUSES]) for day in data['dates']}
        groups.append({'name':name,'rows':rows,'totals':totals,'forecast_totals':group['totals']})
        profiles_display.append({'name':name,'definition':explanation,'valid_days':profile['days'] if profile else [],
                                 'ratios':{warehouse:float(ratios[warehouse]) if ratios else None for warehouse in WAREHOUSES}})
    warehouse_totals={warehouse:{day:known_sum([group['rows'][i]['values'][day]['amount'] for group in groups])
                                for day in data['dates']} for i,warehouse in enumerate(WAREHOUSES)}
    totals={day:known_sum([group['totals'][day] for group in groups]) for day in data['dates']}
    unresolved={day:known_sum([group['forecast_totals'][day] for group in groups
                             if group['forecast_totals'][day] is not None and group['totals'][day] is None]) for day in data['dates']}
    return {**data,'groups':groups,'totals':totals,'forecast_totals':data['totals'],'unallocated_totals':unresolved,
            'warehouse_totals':warehouse_totals,'profiles':profiles_display,'warnings':warnings,
            'reference_start':start,'reference_end':(today-timedelta(days=1)).isoformat(),
            'definition':'参考最近7个完整自然日（不含今天）；先计算每天各仓单量占比，再取有效日期的平均占比。无发货数据或总量为0的日期不参与平均。按整数分仓，尾差按最大余数分配，保证各仓合计与渠道预估单量一致。'}
