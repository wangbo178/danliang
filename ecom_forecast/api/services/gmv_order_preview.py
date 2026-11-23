"""只读订单大表；店铺行单独定义，引用当前规划及最新保存的预估单量。"""
import calendar
import re
from datetime import date,timedelta

from api.services import expected_gmv as g
from api.services.gmv_forecast_inputs import read,effective_orders

TMALL_ROWS = [
    ('货架订单数',['货架-常规','百补']),
    ('CPS订单数',['货架-CPS']),
    ('派样订单数',['派样']),
    ('直播订单数',['达播-超头','达播-二梯队']),
]


def known_sum(values):
    known=[value for value in values if value is not None]
    return sum(known) if known else None


def layout(run):
    """每个店铺独立定义订单行；仅合计底层输入，不重复计算展示汇总行。"""
    result=run.get('result',{});parser=result.get('parser') or run.get('rule_key')
    if parser=='tmall':
        return TMALL_ROWS,True
    if parser=='douyin':
        return [('货架单量',['抖音自播']),
                ('抖音精选联盟',['抖音精选联盟订单']),
                ('直播单量',['达播单量']),('派样单量',['派样'])],True
    if parser=='douyin_baby':
        from api.services.gmv_douyin_baby_parser import CHANNELS
        return [(channel+'订单数',[channel]) for channel in CHANNELS]+[('PDD单量',['PDD'])],True
    quantity_rows=result.get('quantity_rows',[])
    if quantity_rows:
        return [(row['channel'],[row['channel']]) for row in quantity_rows if row.get('metric')!='total_orders'],False
    if result.get('estimated_orders'):
        return [('预估单量',['预估单量'])],False
    return [],False


def daily_records(run):
    result=run.get('result',{})
    if result.get('quantity_rows'):
        return [r for row in result['quantity_rows'] if row.get('metric')!='total_orders' for r in row['records'] if r['kind']=='day']
    records=result.get('estimated_orders') or result.get('records',[])
    return [r for r in records if r['kind']=='day']


def preview(month=None,start_date=None,end_date=None):
    with g.read_batch():
        runs=[];seen=set();shops={}
        for run in g.all_items('run'):
            if run.get('status')!='success' or run['source_id'] in seen:
                continue
            seen.add(run['source_id'])
            rows,forecast=layout(run)
            if not rows:
                continue
            shop=run.get('source',{}).get('shop') or run.get('shop') or run['source_id']
            if shop=='薇诺娜天猫旗舰店':shop='天猫旗舰店'
            runs.append(run);shops.setdefault(shop,[]).append(run)
        available=sorted({r['period'][:7] for run in runs for r in daily_records(run)})
        if not available:
            return {'months':[],'selected_month':None,'start_date':None,'end_date':None,'dates':[],'groups':[],'totals':{},'read_only':True}
        if start_date is not None or end_date is not None:
            try:
                start,end=date.fromisoformat(start_date),date.fromisoformat(end_date)
            except (ValueError,TypeError):
                raise ValueError('请选择有效的开始和结束日期')
            selected_month=None
        else:
            selected_month=month or available[-1]
            if not re.fullmatch(r'\d{4}-\d{2}',selected_month) or selected_month not in available:
                raise ValueError('请选择有预估规划的月份')
            year,number=map(int,selected_month.split('-'))
            start=date(year,number,1);end=date(year,number,calendar.monthrange(year,number)[1])
        if start>end or (end-start).days>365:
            raise ValueError('日期范围需在一年内且开始日期不晚于结束日期')
        dates=[(start+timedelta(days=n)).isoformat() for n in range((end-start).days+1)]
        selected=set(dates);groups=[]
        order=['天猫旗舰店','抖音店铺','抖音宝贝','唯品会','专柜商城','天猫宝贝','私密渠道','KA-京东-快手-小红书','拼多多店铺','阿里健康渠道','其它渠道']
        for shop in sorted(shops,key=lambda name:(order.index(name) if name in order else len(order),name)):
            shop_runs=shops[shop];chosen={}
            # 同一店铺同一天跨来源重叠时只采用最新成功规划；不同店铺分别选取。
            for run in shop_runs:
                for day in sorted(selected & {r['period'] for r in daily_records(run)}):
                    chosen.setdefault(day,run)
            inputs={};order_maps={}
            for run in {run['id']:run for run in chosen.values()}.values():
                if layout(run)[1]:
                    value=read(run['id']);inputs[run['id']]=value
                    records=effective_orders(run,value['values'])
                else:
                    inputs[run['id']]={'version':run['id']}
                    records=daily_records(run)
                order_maps[run['id']]={(r['channel'],r['period']):r['amount'] for r in records}
                if (run.get('result',{}).get('parser') or run.get('rule_key'))=='douyin':
                    # effective_orders 的抖音达播已经是联盟＋直播；明细引用独立填报值。
                    for key,target in [('抖音精选联盟订单','抖音精选联盟订单'),('达播单量','抖音达播')]:
                        for day,amount in inputs[run['id']]['values']['orders'][target].items():
                            order_maps[run['id']][key,day]=amount
            rows=[]
            for label,channels in layout(shop_runs[0])[0]:
                cells={}
                for day in dates:
                    run=chosen.get(day)
                    values=[order_maps[run['id']].get((channel,day)) if run else None for channel in channels]
                    cells[day]={'amount':known_sum(values),'missing_channels':[channel for channel,value in zip(channels,values) if value is None],
                                'run_id':run['id'] if run else None,'input_version':inputs[run['id']]['version'] if run else None,
                                'source_name':run.get('source',{}).get('name') if run else None}
                is_douyin=(shop_runs[0].get('result',{}).get('parser') or shop_runs[0].get('rule_key'))=='douyin'
                rows.append({'label':label,'channels':channels,'values':cells,
                             'source_label':'抖音自播 → 预估单量（手填优先，否则按GMV÷预估客单计算）' if is_douyin and label=='货架单量' else None,
                             'exclude_from_total':False})
            totals={day:known_sum([row['values'][day]['amount'] for row in rows if not row['exclude_from_total']]) for day in dates}
            groups.append({'name':shop,'rows':rows,'totals':totals})
        totals={day:known_sum([group['totals'][day] for group in groups]) for day in dates}
        return {'months':available,'selected_month':selected_month,'start_date':start.isoformat(),'end_date':end.isoformat(),
                'dates':dates,'groups':groups,'totals':totals,'read_only':True,
                'definition':'天猫货架＝常规＋百补，直播＝超头＋二达；抖音精选联盟与直播分别列出，仅在底部汇总一次。各店铺引用最新保存的预估单量，手动渠道及导入渠道引用已保存的单量。缺失项不补0。'}
