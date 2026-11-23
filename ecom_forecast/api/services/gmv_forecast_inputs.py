"""天猫及抖音GMV预估的客单价、单量覆盖值及独立填报历史。"""
import re
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def context(run_id):
    from api.services import expected_gmv as g
    run = g.get('run', run_id)
    is_douyin = run.get('result', {}).get('parser') == 'douyin' or run.get('rule_key') == 'douyin'
    is_baby = run.get('result', {}).get('parser') == 'douyin_baby' or run.get('rule_key') == 'douyin_baby'
    if (run.get('rule_key') != 'tmall' and not is_douyin and not is_baby) or run.get('status') != 'success':
        raise ValueError('仅已成功解析的天猫旗舰店、抖音店铺和抖音宝贝支持客单价和预估单量填报')
    if is_baby:
        from api.services.gmv_douyin_baby_parser import CHANNELS
    elif is_douyin:
        from api.services.gmv_douyin_parser import CHANNELS
    else:
        from api.services.gmv_parser import CHANNELS
    actual_channels = {r['channel'] for r in run['result']['records'] if r['kind'] == 'day'}
    if (is_douyin or is_baby) and actual_channels != set(CHANNELS):
        raise ValueError('规划渠道与店铺预估单量配置不匹配')
    days = sorted({r['period'] for r in run['result']['records'] if r['kind'] == 'day'})
    return run, CHANNELS, days


def metric_channels(run, channels, metric):
    if metric == 'orders' and (run.get('result', {}).get('parser') == 'douyin_baby' or run.get('rule_key') == 'douyin_baby'):
        return channels + ['PDD']
    if metric == 'orders' and (run.get('result', {}).get('parser') == 'douyin' or run.get('rule_key') == 'douyin'):
        return channels + ['抖音精选联盟订单', '派样']
    return channels + ['派样'] if metric == 'orders' and run.get('rule_key') == 'tmall' else channels


def number(value, metric):
    if value is None or (isinstance(value, str) and value.strip() in {'', '-', '—'}):
        return None
    if isinstance(value, bool):
        raise ValueError('数值格式无效')
    text = str(value).strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:[,，]\d{3})+)(?:\.\d{1,2})?', text):
        raise ValueError('请输入有效数值，支持千位逗号；客单价最多两位小数，单量必须为整数')
    try:
        amount = Decimal(text.replace(',', '').replace('，', ''))
    except InvalidOperation as exc:
        raise ValueError('数值格式无效') from exc
    if amount > 10**12 or (metric == 'aov' and amount <= 0) or (metric == 'orders' and amount != amount.to_integral_value()):
        raise ValueError('客单价需大于0且最多两位小数，单量需为非负整数，数值不能超过1万亿')
    return float(amount) if metric == 'aov' else int(amount)


def read(run_id, version=None):
    from api.services import expected_gmv as g
    run, channels, days = context(run_id)
    history = [v for v in g.all_items('forecast_input') if v['source_id'] == run['source_id']]
    selected = next((v for v in history if v['id'] == version), None) if version else (history[0] if history else None)
    if version and selected is None:
        raise ValueError('该填报版本不存在或属于其他店铺来源')
    stored = selected['values'] if selected else {}
    values = {metric: {channel: {day: stored.get(metric, {}).get(channel, {}).get(day) for day in days}
                       for channel in metric_channels(run, channels, metric)} for metric in ['aov', 'orders']}
    if '抖音精选联盟订单' in values['orders'] and stored and '抖音精选联盟订单' not in stored.get('orders', {}):
        # 兼容旧填报：保留原达播预估结果，不修改旧版本或把旧单量丢弃。
        for record in run['result']['records']:
            if record['kind'] == 'day' and record['channel'] == '抖音达播':
                day = record['period']
                price = values['aov']['抖音达播'][day]
                if values['orders']['抖音达播'][day] is None and record['amount'] is not None:
                    if record['amount'] == 0:
                        values['orders']['抖音达播'][day] = 0
                    elif price:
                        values['orders']['抖音达播'][day] = int((Decimal(str(record['amount'])) / Decimal(str(price))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {'run_id': run_id, 'source_id': run['source_id'], 'version': selected['id'] if selected else None,
            'current_version': history[0]['id'] if history else None, 'values': values,
            'history': [{'id': v['id'], 'created_at': v['created_at'], 'run_id': v['run_id']} for v in history]}


def effective_orders(run, values):
    records = []
    for target in run['result']['records']:
        if target['kind'] != 'day':
            continue
        channel, day = target['channel'], target['period']
        if channel == '抖音达播' and '抖音精选联盟订单' in values['orders']:
            components = [values['orders'][name][day] for name in ['抖音达播', '抖音精选联盟订单']]
            known = [value for value in components if value is not None]
            records.append({'channel': channel, 'period': day, 'amount': sum(known) if known else None, 'origin': 'sum_components'})
            continue
        price = values['aov'][channel][day]
        override = values['orders'][channel][day]
        if override is not None:
            quantity, origin = override, 'manual'
        elif target['amount'] is None:
            quantity, origin = None, 'missing_gmv'
        elif target['amount'] == 0:
            quantity, origin = 0, 'calculated'
        elif price:
            quantity = int((Decimal(str(target['amount'])) / Decimal(str(price))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            origin = 'calculated'
        else:
            quantity, origin = None, 'missing_aov'
        records.append({'channel': channel, 'period': day, 'amount': quantity, 'origin': origin})
    days = sorted({r['period'] for r in run['result']['records'] if r['kind'] == 'day'})
    for extra in ['派样', 'PDD']:
        if extra in values['orders']:
            records.extend({'channel': extra, 'period': day, 'amount': values['orders'][extra].get(day),
                            'origin': 'manual' if values['orders'][extra].get(day) is not None else 'missing_orders'} for day in days)
    return records


def save(run_id, payload):
    from api.services import expected_gmv as g
    run, channels, days = context(run_id)
    data = payload.get('values')
    if not isinstance(data, dict) or set(data) != {'aov', 'orders'}:
        raise ValueError('请提供客单价及预估单量的完整填报数据')
    values = {}
    for metric in ['aov', 'orders']:
        allowed_channels = metric_channels(run, channels, metric)
        if not isinstance(data[metric], dict) or set(data[metric]) - set(allowed_channels):
            raise ValueError('包含未配置的渠道')
        values[metric] = {}
        for channel in allowed_channels:
            source = data[metric].get(channel, {})
            if metric == 'orders' and channel in {'派样', '抖音精选联盟订单', 'PDD'} and channel not in data[metric]:
                # 旧页面未提交新增字段时保留已有派样填报，显式清空仍按None保存。
                source = read(run_id)['values']['orders'][channel]
            if not isinstance(source, dict) or set(source) - set(days):
                raise ValueError('填报日期超出当前解析版本范围')
            values[metric][channel] = {day: number(source.get(day), metric) for day in days}
    with g.LOCK:
        current = read(run_id)
        if payload.get('base_version') != current['current_version']:
            raise ValueError('填报数据已被更新，请重新加载后再保存')
        entry = {'id': uuid.uuid4().hex, 'source_id': run['source_id'], 'run_id': run_id,
                 'created_at': g.now(), 'values': values, 'effective_orders': effective_orders(run, values)}
        g.put('forecast_input', entry['id'], entry)
        return read(run_id)
