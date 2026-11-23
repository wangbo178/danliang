"""天猫宝贝按日手动单量，保留空白，实时累计已填单量。"""
import re
from datetime import date, timedelta

INPUTS = ['预估单量', '直播单量', '预售单量']
TOTAL = '合计总单量'
SOURCE_ID = 'tmall_baby_manual'

PROFILES = {
    'other_manual': {'shop': '其它渠道', 'inputs': ['预计单量', '活动单量'], 'total': '合计', 'defaults': {'预计单量': 100}},
    'ali_health_manual': {'shop': '阿里健康渠道', 'inputs': ['预计单量', '活动单量'], 'total': '合计', 'defaults': {'预计单量': 1000}},
    'pdd_manual': {'shop': '拼多多店铺', 'inputs': ['预计单量', '手工增加单量'], 'total': '合计', 'defaults': {}},
    'tmall_baby_manual': {'shop': '天猫宝贝', 'inputs': INPUTS, 'total': TOTAL, 'defaults': {}},
    'private_manual': {'shop': '私密渠道', 'inputs': ['预计单量', '活动单量'], 'total': '合计', 'defaults': {'预计单量': 200}},
    'ka_manual': {'shop': 'KA-京东-快手-小红书', 'inputs': ['预计单量', '活动单量'], 'total': '合计', 'defaults': {'预计单量': 1500}},
}


def normalize_quantity(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in {'', '-', '—'}:
            return None
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:[,，]\d{3})+)', value):
            raise ValueError('单量格式无效，支持2,597这样的千位分隔数字')
        value = int(value.replace(',', '').replace('，', ''))
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError('单量只能填写0至1万亿的整数')
    return value


def resolve_dates(payload, sources, runs):
    mode = payload.get('mode', 'follow')
    follow_run = None
    if mode == 'follow':
        source_id = payload.get('tmall_source_id')
        source = next((s for s in sources if s['id'] == source_id and s.get('rule_key') == 'tmall'), None)
        follow_run = next((r for r in runs if r['source_id'] == source_id and r.get('status') == 'success'), None)
        if not source or not follow_run:
            raise ValueError('请选择已成功解析的天猫规划日期范围')
        dates = sorted({r['period'] for r in follow_run['result']['records'] if r['kind'] == 'day'})
    elif mode == 'custom':
        try:
            start, end = date.fromisoformat(payload['start_date']), date.fromisoformat(payload['end_date'])
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('请选择有效的开始和结束日期') from exc
        count = (end - start).days + 1
        if not 1 <= count <= 366:
            raise ValueError('自定义日期范围需为1至366天')
        dates = [(start + timedelta(days=i)).isoformat() for i in range(count)]
    else:
        raise ValueError('日期模式必须是跟随天猫或自定义')
    if not 1 <= len(dates) <= 366:
        raise ValueError('日期范围为空或超过366天')
    return dates, follow_run


def build(payload, dates, follow_run, profile='tmall_baby_manual'):
    config = PROFILES[profile]
    inputs, total = config['inputs'], config['total']
    total_row = len(inputs) + 2
    values = payload.get('values')
    if not isinstance(values, dict) or set(values) != set(inputs):
        raise ValueError('需提供该渠道的全部输入指标，合计自动计算')
    allowed = set(dates)
    cells = [{'row': 1, 'col': 'A', 'ref': 'A1', 'value': '日期', 'formula': None}]
    for i, name in enumerate(inputs + [total], 2):
        cells.append({'row': i, 'col': 'A', 'ref': f'A{i}', 'value': name, 'formula': None})
    from openpyxl.utils import get_column_letter
    daily = {name: [] for name in inputs + [total]}
    normalized = {}
    for name in inputs:
        if not isinstance(values[name], dict) or set(values[name]) - allowed:
            raise ValueError(f'{name} 包含日期范围之外的数据，请重新确认范围')
        normalized[name] = {day: normalize_quantity(value) for day, value in values[name].items()}
    for index, day in enumerate(dates, 2):
        column = get_column_letter(index)
        cells.append({'row': 1, 'col': column, 'ref': f'{column}1', 'value': day, 'formula': None})
        amounts = [normalized[name].get(day, config['defaults'].get(name)) for name in inputs]
        filled = [value for value in amounts if value is not None]
        amounts.append(sum(filled) if filled else None)
        for row, (name, value) in enumerate(zip(inputs + [total], amounts), 2):
            ref = f'{column}{row}'
            refs = [ref] if row < total_row else [f'{column}{r}' for r in range(2, total_row)]
            daily[name].append({'channel': name, 'kind': 'day', 'period': day, 'amount': value,
                                'raw': value, 'cell': ref, 'source_cells': refs, 'derived': row == total_row,
                                'evidence': '实时累加输入项已填写的数值，未填写项不参与' if row == total_row else f'手动填报 · {day} · {name}'})
            if value is not None:
                cells.append({'row': row, 'col': column, 'ref': ref, 'value': value,
                              'formula': f'=SUM({column}2:{column}{total_row-1})' if row == total_row else None})
    rows = []
    for name in inputs + [total]:
        records = daily[name].copy()
        for month in sorted({d[:7] for d in dates}) + ['全部日期']:
            subset = daily[name] if month == '全部日期' else [r for r in daily[name] if r['period'].startswith(month)]
            filled = [r for r in subset if r['amount'] is not None]
            amount = sum(r['amount'] for r in filled) if filled else None
            refs = list(dict.fromkeys(ref for r in subset for ref in r['source_cells']))
            records.append({'channel': name, 'kind': 'summary', 'period': '范围合计' if month == '全部日期' else f'{month}合计',
                            'amount': amount, 'raw': '逐日合计', 'cell': ','.join(refs), 'source_cells': refs, 'derived': True,
                            'evidence': f'{len(subset)}天中已填写{len(filled)}天；按已填写数值累计'})
        rows.append({'channel': name, 'metric': 'total_orders' if name == total else name, 'records': records})
    settings = {k: payload.get(k) for k in ['mode', 'tmall_source_id']}
    settings['mode'] = payload.get('mode', 'follow')
    settings.update(start_date=dates[0], end_date=dates[-1], follow_run_id=follow_run['id'] if follow_run else None)
    snapshot = {'source_type': 'manual', 'title': config['shop'] + '手动单量', 'cells': cells,
                'range': f'A1:{get_column_letter(len(dates) + 1)}{total_row}'}
    result = {'parser': profile, 'quantity_only': True, 'channel_order': [], 'records': [],
              'quantity_rows': rows, 'estimated_orders': [], 'schedules': [], 'header_row': 1, 'section_rows': [2, total_row + 1],
              'summary_label': '月度 / 范围汇总', 'manual_settings': settings, 'totals_calculation': 'available_values_v2',
              'conversion_note': '单位：单。合计实时累加输入行已填写的数值，填写0表示确认为零；月度及范围汇总按已填数据累计。' + ''.join(f'{name}默认每天{value}单，可手动修改。' for name, value in config['defaults'].items())}
    return snapshot, result
