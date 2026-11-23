"""唯品会横向GMV、预估单量及独立来源范围复核。"""
import calendar
import re
from datetime import date
from decimal import Decimal

from api.services.gmv_douyin_parser import source_rows

SCHEDULES = ['主活动节奏', '辅助活动节奏', '我司力度']


def validate_rule(rule):
    if set(rule) != {'parser', 'header_row', 'label_column', 'gmv_label', 'quantity_label', 'schedule_labels', 'unit_multiplier'}:
        raise ValueError('唯品会规则字段不正确')
    if rule['header_row'] is not None and (type(rule['header_row']) is not int or rule['header_row'] < 1):
        raise ValueError('日期表头行无效')
    if not re.fullmatch(r'[A-Z]{1,3}', str(rule['label_column'])) or rule['unit_multiplier'] != 1:
        raise ValueError('唯品会按元和单读取原值，标签列需有效')
    if not all(isinstance(rule[k], str) and rule[k].strip() for k in ['gmv_label', 'quantity_label']) or rule['schedule_labels'] != SCHEDULES:
        raise ValueError('唯品会指标标签或节奏类型无效')
    return rule


def layout(rows, year, override=None):
    def day(value):
        match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', str(value).strip())
        return date(year, int(match[1]), int(match[2])).isoformat() if match else None
    candidates = [r for r, cs in rows.items() if sum(day(c['value']) is not None for c in cs.values()) >= 20]
    header = override or (candidates[0] if len(candidates) == 1 else None)
    if header not in rows:
        raise ValueError('唯品会日期表头缺失或不唯一')
    dates = {col: day(c['value']) for col, c in rows[header].items() if day(c['value'])}
    if len(dates) != len(set(dates.values())) or not dates:
        raise ValueError('唯品会日期重复或缺失')
    for month in {d[:7] for d in dates.values()}:
        if sum(d.startswith(month) for d in dates.values()) != calendar.monthrange(year, int(month[5:]))[1]:
            raise ValueError(f'{month} 日期覆盖不完整')
    years = {r: int(str(c['value']).strip()[1:]) + 2000 for r, cs in rows.items() for c in cs.values() if re.fullmatch(r'Y\d{2}', str(c['value']).strip())}
    starts = [r for r, y in years.items() if y == year]
    if len(starts) != 1:
        raise ValueError('唯品会目标年度区域无法独立定位')
    start = starts[0]
    end = min((r for r in years if r > start), default=max(rows) + 1)
    totals = [col for col, c in rows[header].items() if str(c['value']).strip() == '合计']
    if len(totals) != 1:
        raise ValueError('唯品会原表合计列缺失或不唯一')
    return header, dates, totals[0], start, end


def find_row(rows, label, column, start=1, end=None):
    hits = [r for r, cs in rows.items() if start <= r < (end or max(rows) + 1) and str(cs.get(column, {}).get('value', '')).strip() == label]
    if len(hits) != 1:
        raise ValueError(f'{label} 行缺失或不唯一')
    return hits[0]


def metric_records(rows, row, dates, total_col, channel, quantity=False):
    from api.services.gmv_parser import number
    records = []
    for kind, cols in [('day', dates), ('summary', {total_col: '原表合计'})]:
        for col, period in cols.items():
            cell = rows[row].get(col)
            if not cell:
                raise ValueError(f'{channel} {period} 缺失')
            value = number(cell['value'])
            if quantity and value != value.to_integral_value():
                raise ValueError('预估单量需为非负整数')
            records.append({'channel': channel, 'kind': kind, 'period': period, 'amount': float(value),
                            'cell': cell['ref'], 'raw': cell['value'], 'source_cells': [cell['ref']]})
    for month in sorted({d[:7] for d in dates.values()}):
        daily = [r for r in records if r['kind'] == 'day' and r['period'].startswith(month)]
        refs = [r['cell'] for r in daily]
        records.append({'channel': channel, 'kind': 'summary', 'period': f'{int(month[5:])}月合计',
                        'amount': float(sum((number(r['raw']) for r in daily), Decimal(0))),
                        'cell': ','.join(refs), 'raw': '逐日合计', 'source_cells': refs, 'derived': True})
    return records


def read_schedules(rows, dates, labels, column, start, end):
    schedules = []
    for label in labels:
        row = find_row(rows, label, column, start, end)
        for col, day in dates.items():
            cell = rows[row].get(col)
            if cell and str(cell['value']).strip():
                schedules.append({'type': label, 'date': day, 'subtype': '', 'text': str(cell['value']).strip(),
                                  'cell': cell['ref'], 'source_cells': [cell['ref']]})
    return schedules


def parse(snapshot, rule, year):
    validate_rule(rule)
    rows = source_rows(snapshot)
    header, dates, total, start, end = layout(rows, year, rule['header_row'])
    gmv = find_row(rows, rule['gmv_label'], rule['label_column'], start, end)
    qty = find_row(rows, rule['quantity_label'], 'A')
    records = metric_records(rows, gmv, dates, total, '唯品会')
    quantities = metric_records(rows, qty, dates, total, '预估单量', True)
    source_total = next(r['amount'] for r in quantities if r['period'] == '原表合计')
    daily_total = sum(r['amount'] for r in quantities if r['kind'] == 'day')
    note = 'GMV按原表GMV目标（元）读取；预估单量按当年预估单量行读取，月合计由逐日数据相加。'
    if source_total != daily_total:
        note += f'预估单量原表合计{source_total:,.0f}单，逐日合计{daily_total:,.0f}单，相差{source_total-daily_total:,.0f}单；保留原值。'
    return {'parser': 'vip', 'channel_order': ['唯品会'], 'records': records, 'estimated_orders': quantities,
            'schedules': read_schedules(rows, dates, rule['schedule_labels'], rule['label_column'], start, end),
            'header_row': header, 'section_rows': [start, end], 'unit_multiplier': 1,
            'summary_label': '原表 / 月度汇总', 'schedule_label': '营销节奏', 'conversion_note': note,
            'schedule_note': '保留当年主活动、辅助活动及我司力度原文与坐标；日期为原文所在列，活动准确起止以原文为准。'}


def verify(snapshot, result, year):
    # 按固定业务标签与当年边界独立定位，不能使用候选规则别名或行覆盖。
    errors, mappings = [], []
    try:
        rows = source_rows(snapshot)
        header, dates, total, start, end = layout(rows, year)
        if result.get('header_row') != header or result.get('section_rows') != [start, end] or result.get('channel_order') != ['唯品会'] or result.get('unit_multiplier') != 1:
            errors.append('唯品会年度、日期、渠道区域或单位不符')
        for key, name, label, column, quantity in [('records', '唯品会', 'GMV目标', 'B', False), ('estimated_orders', '预估单量', f'{year % 100}年预估单量', 'A', True)]:
            row = find_row(rows, label, column, start if not quantity else 1, end if not quantity else None)
            expected = metric_records(rows, row, dates, total, name, quantity)
            if result.get(key) != expected:
                errors.append(f'{name} 来源范围、原值、汇总或日期覆盖不符')
            mappings.append({'channel': name, 'row': row, 'cells': list(dict.fromkeys(ref for r in expected for ref in r['source_cells']))})
        if result.get('schedules') != read_schedules(rows, dates, SCHEDULES, 'B', start, end):
            errors.append('唯品会活动范围或原文不符，禁止混入历史年度')
    except (ValueError, KeyError) as exc:
        errors.append(str(exc))
    return {'passed': not errors, 'scope': 'source_ranges', 'errors': errors, 'mappings': mappings,
            'cell_checks': len(result.get('records', [])) + len(result.get('estimated_orders', [])), 'schedule_checks': len(result.get('schedules', []))}
