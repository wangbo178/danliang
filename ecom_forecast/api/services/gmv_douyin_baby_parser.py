"""宝贝抖音销售GMV横向表及独立范围审查。"""
import calendar
import re
from datetime import date, timedelta
from decimal import Decimal

from api.services.gmv_douyin_parser import source_rows

CHANNELS = ['自播官旗', '商城', 'KOC', 'DP合计', '抖音达播']


def validate_rule(rule):
    if set(rule) != {'parser', 'header_row', 'label_column', 'channels', 'schedule_rows', 'unit_multiplier'} or set(rule.get('channels', {})) != set(CHANNELS):
        raise ValueError('抖音宝贝规则字段或渠道集合不正确')
    if rule['header_row'] is not None and (type(rule['header_row']) is not int or rule['header_row'] < 1):
        raise ValueError('日期表头行无效')
    if not re.fullmatch(r'[A-Z]{1,3}', str(rule['label_column'])) or rule['unit_multiplier'] != 1:
        raise ValueError('销售GMV必须按原值读取，标签列需为有效列号')
    for aliases in rule['channels'].values():
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError('宝贝渠道别名无效')
    if not isinstance(rule['schedule_rows'], dict) or set(rule['schedule_rows']) != {'level', 'theme'} or not all(type(n) is int and n > 0 for n in rule['schedule_rows'].values()) or len(set(rule['schedule_rows'].values())) != 2:
        raise ValueError('营销等级和主题行映射无效')
    return rule


def layout(rows, year, header_override=None):
    def day(value):
        match = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', str(value).strip())
        return date(year, int(match[1]), int(match[2])).isoformat() if match else None
    headers = [r for r, cs in rows.items() if sum(day(c['value']) is not None for c in cs.values()) >= 20]
    header = header_override or (headers[0] if len(headers) == 1 else None)
    if header not in rows:
        raise ValueError('宝贝日期表头缺失或不唯一')
    dates = {col: day(c['value']) for col, c in rows[header].items() if day(c['value'])}
    if not dates:
        raise ValueError('日期列缺失')
    if len(dates) != len(set(dates.values())):
        raise ValueError('日期列重复')
    for month in {d[:7] for d in dates.values()}:
        if sum(d.startswith(month) for d in dates.values()) != calendar.monthrange(year, int(month[5:]))[1]:
            raise ValueError(f'{month} 日期覆盖不完整')
    ends = [r for r, cs in rows.items() if r > header and any(str(c['value']).strip() == '单品合计' for c in cs.values())]
    if not ends:
        raise ValueError('主销售区域结束边界缺失')
    end = min(ends)
    total_headers = [r for r, cs in rows.items() if r < header and any(str(c['value']).strip() == '端口' for c in cs.values())]
    if len(total_headers) != 1:
        raise ValueError('宝贝汇总表头缺失或不唯一')
    summaries = {col: str(c['value']).strip() for col, c in rows[total_headers[0]].items() if '目标合计' in str(c['value'])}
    if not summaries:
        raise ValueError('原表汇总列缺失')
    return header, dates, summaries, end


def schedules(rows, dates, mapping):
    groups = []
    for col, day in dates.items():
        level = rows.get(mapping['level'], {}).get(col)
        theme = rows.get(mapping['theme'], {}).get(col)
        if not level or not theme:
            raise ValueError(f'{day} 营销等级或主题缺失')
        text = f'活动等级：{str(level["value"]).strip()}\n营销主题：{str(theme["value"]).strip()}'
        refs = [level['ref'], theme['ref']]
        if groups and groups[-1]['text'] == text and date.fromisoformat(groups[-1]['end_date']) + timedelta(days=1) == date.fromisoformat(day):
            groups[-1]['end_date'] = day
            groups[-1]['date'] = f'{groups[-1]["start_date"]}—{day}'
            groups[-1]['source_cells'].extend(refs)
        else:
            groups.append({'type': '营销节奏', 'date': day, 'start_date': day, 'end_date': day, 'subtype': str(level['value']).strip(),
                           'text': text, 'cell': level['ref'], 'source_cells': refs})
    return groups


def parse(snapshot, rule, year):
    from api.services.gmv_parser import number
    validate_rule(rule)
    rows = source_rows(snapshot)
    header, dates, summary_cols, end = layout(rows, year, rule['header_row'])
    records = []
    target_rows = []
    for channel, aliases in rule['channels'].items():
        matches = [r for r, cs in rows.items() if header < r < end and rule['label_column'] in cs and str(cs[rule['label_column']]['value']).strip() in aliases]
        if len(matches) != 1:
            raise ValueError(f'{channel} 销售目标行缺失或不唯一')
        row = matches[0]
        target_rows.append(row)
        for kind, periods in [('day', dates), ('summary', summary_cols)]:
            for col, period in periods.items():
                cell = rows[row].get(col)
                if not cell:
                    raise ValueError(f'{channel} {period} GMV缺失')
                records.append({'channel': channel, 'kind': kind, 'period': period, 'amount': float(number(cell['value'])),
                                'cell': cell['ref'], 'raw': cell['value'], 'source_cells': [cell['ref']]})
    return {'parser': 'douyin_baby', 'channel_order': CHANNELS, 'records': records,
            'schedules': schedules(rows, dates, rule['schedule_rows']), 'header_row': header,
            'section_rows': [min(target_rows), end], 'unit_multiplier': 1,
            'conversion_note': '按原表销售GMV金额（元）直接展示；抖音合计不含快手达播，DP子项不重复累计。',
            'summary_label': '原表汇总', 'schedule_label': '营销节奏',
            'schedule_note': '按原表活动等级及营销主题合并连续日期，保留全部来源坐标；原表未区分官方与店铺节奏。',
            'actuals_profile': 'pending', 'actuals_status': 'pending_sql'}


def verify(snapshot, result, year):
    from api.services.gmv_parser import number
    errors, mappings = [], []
    rows = source_rows(snapshot)
    try:
        header, dates, summary_cols, end = layout(rows, year)
        # 独立按A列原业务标签定位，不使用候选规则别名或配置行。
        expected = {}
        found_rows = []
        for channel in CHANNELS:
            matched = [r for r, cs in rows.items() if header < r < end and str(cs.get('A', {}).get('value', '')).strip() == channel]
            if len(matched) != 1:
                raise ValueError(f'{channel} 原表行无法独立定位')
            row = matched[0]
            found_rows.append(row)
            refs = []
            for kind, periods in [('day', dates), ('summary', summary_cols)]:
                for col, period in periods.items():
                    cell = rows[row].get(col)
                    if not cell:
                        raise ValueError(f'{col}{row} 源GMV缺失')
                    expected[(channel, kind, period)] = cell
                    refs.append(cell['ref'])
            mappings.append({'channel': channel, 'row': row, 'cells': refs})
        if result.get('header_row') != header or result.get('section_rows') != [min(found_rows), end] or result.get('channel_order') != CHANNELS or result.get('unit_multiplier') != 1:
            errors.append('宝贝数据区域、渠道或单位不正确')
        seen = set()
        for rec in result.get('records', []):
            key = (rec.get('channel'), rec.get('kind'), rec.get('period'))
            source = expected.get(key)
            if key in seen or not source or rec.get('cell') != source['ref'] or rec.get('source_cells') != [source['ref']]:
                errors.append(f'宝贝读取范围错位或重复：{key}')
            elif abs(number(source['value']) - Decimal(str(rec['amount']))) > Decimal('.01'):
                errors.append(f'宝贝销售GMV源值不一致：{key}')
            seen.add(key)
        if seen != set(expected):
            errors.append('宝贝目标存在漏读或多读')
        if result.get('schedules') != schedules(rows, dates, {'level': header + 1, 'theme': header + 2}):
            errors.append('宝贝营销节奏范围或原文不符')
    except (ValueError, KeyError) as exc:
        errors.append(str(exc))
    return {'passed': not errors, 'scope': 'source_ranges', 'errors': errors, 'mappings': mappings,
            'cell_checks': len(result.get('records', [])), 'schedule_checks': len(result.get('schedules', []))}
