"""国务院公布的放假区间；本功能依用户要求把所有周六日视为特殊日。"""
from datetime import date

SOURCES = {
    2025: 'https://www.forestry.gov.cn/c/www/szxx/594663.jhtml',
    2026: 'https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html',
}
HOLIDAYS = {
    2025: [('元旦','01-01','01-01'),('春节','01-28','02-04'),('清明节','04-04','04-06'),
           ('劳动节','05-01','05-05'),('端午节','05-31','06-02'),('国庆节、中秋节','10-01','10-08')],
    2026: [('元旦','01-01','01-03'),('春节','02-15','02-23'),('清明节','04-04','04-06'),
           ('劳动节','05-01','05-05'),('端午节','06-19','06-21'),('中秋节','09-25','09-27'),('国庆节','10-01','10-07')],
}


def classify(day):
    day = date.fromisoformat(day) if isinstance(day, str) else day
    if day.year not in HOLIDAYS:
        raise ValueError(f'尚未配置{day.year}年官方放假安排，不能猜测特殊日期')
    holiday = next((name for name, start, end in HOLIDAYS[day.year] if start <= day.strftime('%m-%d') <= end), None)
    weekend = day.weekday() >= 5
    return {'special': weekend or bool(holiday), 'weekend': weekend, 'holiday': holiday,
            'label': '、'.join(([holiday] if holiday else []) + (['周末'] if weekend else [])) or '普通日'}
