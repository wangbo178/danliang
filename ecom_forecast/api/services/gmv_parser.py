"""纯解析与独立复核。规则只能是 RULE = Python 字面量，不执行生成代码。"""
import ast
import calendar
import re
from decimal import Decimal, InvalidOperation

CHANNELS = ['货架-常规', '货架-CPS', '百补', '达播-超头', '达播-二梯队']
SCHEDULE_ALIASES = {'官方节奏': ['官方节奏'], '店铺节奏': ['店铺节奏', '店铺力度']}
SALES_FILING = '渠道目标-销售（备案版）'


def section_label(value):
    return re.sub(r'\s+', '', str(value)).replace('（', '(').replace('）', ')')


def load_rule(source):
    if not isinstance(source, str) or len(source) > 40000:
        raise ValueError('规则代码长度非法')
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        raise ValueError('只允许单个 RULE 字面量赋值，禁止执行任意代码')
    node = tree.body[0]
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.targets[0].id != 'RULE':
        raise ValueError('必须定义 RULE')
    rule = ast.literal_eval(node.value)
    if isinstance(rule, dict) and rule.get('parser') == 'vip':
        from api.services.gmv_vip_parser import validate_rule
        return validate_rule(rule)
    if isinstance(rule, dict) and rule.get('parser') == 'douyin_baby':
        from api.services.gmv_douyin_baby_parser import validate_rule
        return validate_rule(rule)
    if isinstance(rule, dict) and rule.get('parser') == 'douyin':
        from api.services.gmv_douyin_parser import validate_rule
        return validate_rule(rule)
    if not isinstance(rule, dict) or set(rule) - {'section', 'end_section', 'channels', 'schedule_labels', 'schedule_aliases', 'header_row', 'row_overrides', 'unit_multiplier', 'blank_policy'}:
        raise ValueError('规则结构非法')
    if set(rule.get('channels', {})) != set(CHANNELS):
        raise ValueError('必须包含固定的五个目标渠道')
    for aliases in rule['channels'].values():
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError('渠道别名非法')
    filing = section_label(rule.get('section')) == section_label(SALES_FILING)
    if filing:
        if rule.get('end_section') != '渠道目标-GMV':
            raise ValueError('备案销售区域必须以渠道目标-GMV为结束边界，禁止混入后续区域')
    elif rule.get('section') not in {'渠道目标-GMV', '渠道GMV'} or rule.get('end_section') not in {'核心指标', 'sheet_end'}:
        raise ValueError('目标区域仅支持渠道目标-GMV、渠道GMV或渠道目标-销售（备案版）')
    if rule.get('schedule_labels') != ['官方节奏', '店铺节奏']:
        raise ValueError('必须保留官方和店铺节奏')
    if rule.get('unit_multiplier', 1) not in (1, 1000, 10000, 100000000):
        raise ValueError('单位必须是元、千元、万元或亿元')
    if rule.get('blank_policy', 'error') not in {'error', 'preserve'}:
        raise ValueError('blank_policy仅支持error或preserve；preserve保留空值为null，禁止补0')
    if not isinstance(rule.get('row_overrides', {}), dict):
        raise ValueError('行覆盖必须是字典')
    if any(k not in CHANNELS or type(v) is not int or v < 1 for k, v in rule.get('row_overrides', {}).items()):
        raise ValueError('row_overrides只能将五个渠道名称映射到正整数行号，不能设置区域边界')
    aliases = rule.get('schedule_aliases', {})
    if not isinstance(aliases, dict) or any(k not in SCHEDULE_ALIASES or not isinstance(v, list) or not v or
            any(a not in SCHEDULE_ALIASES[k] for a in v) for k, v in aliases.items()):
        raise ValueError('schedule_aliases仅支持官方节奏→官方节奏、店铺节奏→店铺节奏/店铺力度')
    return rule


def tmall_boundary(snapshot):
    """宿主从源表独立确认GMV块，优先当年目标区域，不能由候选行号放宽。"""
    cells = snapshot['cells']
    if snapshot.get('target_section') == SALES_FILING:
        starts = [c['row'] for c in cells if section_label(c['value']) == section_label(SALES_FILING)]
        if len(starts) != 1:
            raise ValueError('渠道目标-销售（备案版）区域缺失或不唯一，禁止回退到GMV区域')
        ends = [c['row'] for c in cells if c['row'] > starts[0] and str(c['value']).strip() == '渠道目标-GMV']
        if not ends:
            raise ValueError('备案销售区域结束边界缺失，请扩大读取范围')
        return starts[0], min(ends)
    starts = [c['row'] for c in cells if str(c['value']).strip() == '渠道目标-GMV']
    if not starts:
        starts = [c['row'] for c in cells if str(c['value']).strip() == '渠道GMV']
    if len(starts) != 1:
        raise ValueError('原表 GMV 区域不能唯一定位')
    start = starts[0]
    ends = [c['row'] for c in cells if c['row'] > start and str(c['value']).strip() == '核心指标']
    if ends:
        return start, min(ends)
    # 独立复制表没有“核心指标”，必须读取到真实工作表末尾，并以最后一个渠道行收尾。
    bounds = snapshot.get('sheet_bounds', {})
    last = bounds.get('row_count')
    if not last or snapshot.get('read_end_row', 0) < last:
        raise ValueError('GMV 区域边界缺失；请扩大读取范围至完整工作表')
    labels = [c for c in cells if c['row'] == last and str(c['value']).strip() in {'达播-二梯队', '达播二梯队'}]
    sections = [c for c in cells if str(c['value']).strip() in {'渠道销售', '渠道纯销', '渠道目标-销售', '渠道目标-纯销'}]
    if len(labels) != 1 or sections or last <= start:
        raise ValueError('无法独立确认完整GMV表的结束边界，禁止把读取范围末尾当成区域末尾')
    return start, last + 1


def schedule_stop(cells, anchor, start):
    if anchor['col'] != 'A':
        return anchor['row'] + 1
    return min([c['row'] for c in cells if anchor['row'] < c['row'] < start and
                (c['col'] == 'A' or str(c['value']).strip() in SCHEDULE_ALIASES['店铺节奏'])] + [start])


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('GMV 缺失')
    text = str(value).strip().replace(',', '').replace('，', '').replace('¥', '').replace('￥', '')
    factor = Decimal(1)
    for suffix, multiple in [('亿元', 100000000), ('万元', 10000), ('万', 10000), ('元', 1)]:
        if text.endswith(suffix):
            text, factor = text[:-len(suffix)], Decimal(multiple)
            break
    try:
        val = Decimal(text) * factor
    except InvalidOperation:
        raise ValueError(f'无效 GMV：{str(value)[:40]}')
    if not val.is_finite() or val < 0:
        raise ValueError('GMV 必须是非负有限数字')
    return val


def normalize(raw):
    if raw.get('has_more') or not raw.get('ranges'):
        raise ValueError('飞书返回不完整或带截断警告，禁止解析')
    cells, blank_cells = [], []
    for block in raw['ranges']:
        if block.get('truncated'):
            raise ValueError('单元格读取被截断')
        if len(block.get('row_indices', [])) != len(block.get('cells', [])):
            raise ValueError('飞书行索引与数据不匹配')
        for ri, row in zip(block['row_indices'], block['cells']):
            if len(block.get('col_indices', [])) != len(row):
                raise ValueError('飞书列索引与数据不匹配')
            for col, cell in zip(block['col_indices'], row):
                if cell.get('value') is not None:
                    cells.append({'row': ri, 'col': col, 'ref': f'{col}{ri}', 'value': cell['value'], 'formula': cell.get('formula')})
                else:
                    blank_cells.append(f'{col}{ri}')
    return {'cells': cells, 'blank_cells': blank_cells, 'revision': raw.get('revision')}


def parse(snapshot, source, year):
    rule = load_rule(source)
    if rule.get('parser') == 'vip':
        from api.services.gmv_vip_parser import parse as parse_vip
        return parse_vip(snapshot, rule, year)
    if rule.get('parser') == 'douyin_baby':
        from api.services.gmv_douyin_baby_parser import parse as parse_baby
        return parse_baby(snapshot, rule, year)
    if rule.get('parser') == 'douyin':
        from api.services.gmv_douyin_parser import parse as parse_douyin
        return parse_douyin(snapshot, rule, year)
    cells = snapshot['cells']
    if snapshot.get('target_section') == SALES_FILING and section_label(rule['section']) != section_label(SALES_FILING):
        raise ValueError('当前来源已指定渠道目标-销售（备案版），该规则读取口径不匹配')
    rows = {}
    for c in cells:
        rows.setdefault(c['row'], {})[c['col']] = c
    starts = [c['row'] for c in cells if section_label(c['value']) == section_label(rule['section'])]
    if len(starts) != 1:
        raise ValueError('渠道目标-GMV 区域缺失或不唯一')
    start = starts[0]
    if rule['end_section'] == 'sheet_end':
        independent_start, end = tmall_boundary(snapshot)
        if independent_start != start:
            raise ValueError('候选区域不符合源表GMV口径')
    else:
        ends = [c['row'] for c in cells if c['row'] > start and str(c['value']).strip() == rule['end_section']]
        if not ends:
            raise ValueError('GMV 区域边界缺失；完整独立表可使用sheet_end，需读取到工作表末尾')
        end = min(ends)
    headers = [r for r in rows if r < start and any(str(c['value']).strip() == '日期' for c in rows[r].values())]
    header = rule.get('header_row') or (max(headers) if headers else None)
    if header not in rows or header >= start:
        raise ValueError('日期表头缺失')
    periods = {}
    for col, c in rows[header].items():
        text = str(c['value']).strip()
        m = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', text)
        if m:
            # 原表可能在月末保留下一月空白模板列；只有整个 GMV 区域均空才排除。
            if not any(col in cs for r, cs in rows.items() if start <= r < end):
                continue
            from datetime import date
            label = date(year, int(m[1]), int(m[2])).isoformat()
            periods[col] = ('day', label)
        elif re.search(r'TTL|ttl|合计|双11', text):
            periods[col] = ('summary', text)
    days = [label for kind, label in periods.values() if kind == 'day']
    if not days or len(days) != len(set(days)):
        raise ValueError('日期列为空或重复')
    for month in {d[:7] for d in days}:
        expected = calendar.monthrange(int(month[:4]), int(month[5:]))[1]
        if sum(d.startswith(month) for d in days) != expected:
            raise ValueError(f'{month} 日期覆盖不完整，请扩大读取范围')
    records = []
    for channel, aliases in rule['channels'].items():
        matches = [r for r, cs in rows.items() if start <= r < end and any(str(c['value']).strip() in aliases for c in cs.values())]
        override = rule.get('row_overrides', {}).get(channel)
        if override is not None:
            matches = [r for r in matches if r == override]
        if len(matches) != 1:
            raise ValueError(f'{channel} 目标行缺失或不唯一')
        for col, (kind, period) in periods.items():
            c = rows[matches[0]].get(col)
            ref = f'{col}{matches[0]}'
            if c is None and rule.get('blank_policy') == 'preserve' and ref in snapshot.get('blank_cells', []):
                records.append({'channel': channel, 'kind': kind, 'period': period, 'amount': None, 'cell': ref, 'raw': None})
                continue
            value = number(c['value'] if c else None) * rule.get('unit_multiplier', 1)
            records.append({'channel': channel, 'kind': kind, 'period': period, 'amount': float(value), 'cell': c['ref'], 'raw': c['value']})
    schedules = []
    for label in rule['schedule_labels']:
        aliases = rule.get('schedule_aliases', {}).get(label, [label])
        anchors = [c for c in cells if c['row'] < start and str(c['value']).strip() in aliases]
        if len(anchors) != 1:
            raise ValueError(f'{label} 区域缺失或不唯一')
        anchor = anchors[0]
        # 节奏位于 A 列时跨子行；位于 B 列时只取本行。
        stop = schedule_stop(cells, anchor, start)
        found = 0
        for row in range(anchor['row'], stop):
            for col, c in rows.get(row, {}).items():
                if col in periods and periods[col][0] == 'day':
                    text = str(c['value']).strip()
                    if text:
                        schedules.append({'type': label, 'date': periods[col][1], 'text': text, 'cell': c['ref'], 'subtype': rows[row].get('B', {}).get('value', '')})
                        found += 1
        if not found:
            raise ValueError(f'{label} 未提取到活动内容')
    result = {'records': records, 'schedules': schedules, 'section_rows': [start, end], 'header_row': header, 'unit_multiplier': rule.get('unit_multiplier', 1)}
    if rule.get('blank_policy') == 'preserve':
        result['blank_policy'] = 'preserve'
    if section_label(rule['section']) == section_label(SALES_FILING):
        result['target_section'] = SALES_FILING
    return result


def verify(snapshot, result, year=None):
    """从原表独立重定位范围，不使用候选规则提供的渠道别名或行覆盖。"""
    if result.get('parser') == 'vip':
        from api.services.gmv_vip_parser import verify as verify_vip
        return verify_vip(snapshot, result, year)
    if result.get('parser') == 'douyin_baby':
        from api.services.gmv_douyin_baby_parser import verify as verify_baby
        return verify_baby(snapshot, result, year)
    if result.get('parser') == 'douyin':
        from api.services.gmv_douyin_parser import verify as verify_douyin
        return verify_douyin(snapshot, result, year)
    from datetime import date
    cells = snapshot['cells']
    lookup = {c['ref']: c for c in cells}
    rows = {}
    for c in cells:
        rows.setdefault(c['row'], {})[c['col']] = c
    errors, mappings = [], []
    def report():
        return {'passed': not errors, 'scope': 'source_ranges', 'errors': errors,
                'mappings': mappings, 'cell_checks': len(result.get('records', [])),
                'schedule_checks': len(result.get('schedules', []))}
    try:
        start, end = tmall_boundary(snapshot)
    except ValueError as exc:
        errors.append(str(exc))
        return report()
    headers = [r for r, cs in rows.items() if r < start and any(str(c['value']).strip() == '日期' for c in cs.values())]
    if not headers:
        errors.append('原表缺少日期表头')
        return report()
    header = max(headers)
    if result.get('section_rows') != [start, end] or result.get('header_row') != header:
        errors.append('代码定位的 GMV 区域或日期表头与原表不一致')
    if year is None:
        dates = [r.get('period', '') for r in result.get('records', []) if r.get('kind') == 'day']
        year = int(dates[0][:4]) if dates else None
    periods = {}
    active_months = set()
    for col, c in rows[header].items():
        m = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', str(c['value']).strip())
        if m and any(col in cs for r, cs in rows.items() if start <= r < end):
            active_months.add(int(m[1]))
    for col, c in rows[header].items():
        text = str(c['value']).strip()
        m = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', text)
        if m and int(m[1]) in active_months and year:
            periods[col] = ('day', date(year, int(m[1]), int(m[2])).isoformat())
        elif re.search(r'TTL|ttl|合计|双11', text):
            periods[col] = ('summary', text)
    for month in active_months:
        days = [period for kind, period in periods.values() if kind == 'day' and int(period[5:7]) == month]
        if len(set(days)) != calendar.monthrange(year, month)[1] or len(days) != len(set(days)):
            errors.append(f'原表 {month} 月日期列缺失或重复')
    if not periods or not active_months:
        errors.append('没有可核验的日期/汇总列')
    def label(value):
        return re.sub(r'[\s_－—–-]', '', str(value)).lower().replace('gmv', '')
    expected = {}
    for channel in CHANNELS:
        matched = [r for r, cs in rows.items() if start <= r < end and any(label(c['value']) == label(channel) for c in cs.values())]
        if len(matched) != 1:
            errors.append(f'{channel} 原表标签不能独立唯一定位')
            continue
        row = matched[0]
        refs = []
        for col, (kind, period) in periods.items():
            ref = f'{col}{row}'
            expected[(channel, kind, period)] = ref
            refs.append(ref)
        mappings.append({'channel': channel, 'row': row, 'header_row': header, 'cells': refs})
    seen = set()
    used_refs = set()
    for rec in result.get('records', []):
        key = (rec.get('channel'), rec.get('kind'), rec.get('period'))
        ref = rec.get('cell')
        if key in seen or ref in used_refs:
            errors.append(f'重复读取：{key}')
        seen.add(key)
        used_refs.add(ref)
        if expected.get(key) != ref:
            errors.append(f'渠道/日期读取范围错误：{key} 应读 {expected.get(key)}，实际 {ref}')
        c = lookup.get(ref)
        if not c:
            if not (result.get('blank_policy') == 'preserve' and ref in snapshot.get('blank_cells', [])
                    and rec.get('amount') is None and rec.get('raw') is None):
                errors.append(f'读取了原表不存在的单元格或填造空值：{ref}')
        else:
            try:
                if abs(number(c['value']) * result.get('unit_multiplier', 1) - Decimal(str(rec['amount']))) > Decimal('.01'):
                    errors.append(f'原值不一致：{ref}')
            except (ValueError, InvalidOperation):
                errors.append(f'源值或输出金额无效：{ref}')
    missing = set(expected) - seen
    if missing:
        errors.append(f'代码漏读 {len(missing)} 个渠道/日期单元格')
    # 从原表的官方/店铺标签独立重建活动范围，防止标签互换或遗漏整段。
    schedule_expected = {}
    for kind in ['官方节奏', '店铺节奏']:
        anchors = [c for c in cells if c['row'] < start and str(c['value']).strip() in SCHEDULE_ALIASES[kind]]
        if len(anchors) != 1:
            errors.append(f'{kind} 区域不能独立唯一定位')
            continue
        anchor = anchors[0]
        stop = schedule_stop(cells, anchor, start)
        for c in cells:
            period = periods.get(c['col'])
            if anchor['row'] <= c['row'] < stop and period and period[0] == 'day' and str(c['value']).strip():
                schedule_expected[(kind, c['ref'])] = (period[1], str(c['value']).strip(), rows[c['row']].get('B', {}).get('value', ''))
    schedule_seen = set()
    for rec in result.get('schedules', []):
        key = (rec.get('type'), rec.get('cell'))
        if key in schedule_seen or schedule_expected.get(key) != (rec.get('date'), rec.get('text'), rec.get('subtype')):
            errors.append(f'活动节奏读取范围或内容不符：{key}')
        schedule_seen.add(key)
    if set(schedule_expected) != schedule_seen:
        errors.append('官方/店铺节奏范围存在漏读或多读')
    if not schedule_expected:
        errors.append('未找到可核验的活动范围')
    return report()
