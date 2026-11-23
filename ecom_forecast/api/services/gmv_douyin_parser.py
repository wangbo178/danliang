"""抖音日期纵向表的独立解析器及范围复核。"""
import calendar
import re
from datetime import date
from decimal import Decimal

CHANNELS = ['抖音自播', '抖音商城', '抖音达播']
SOURCE_LABELS = {'抖音自播': '抖音自播', '抖音商城': '商城汇总', '抖音达播': '抖音达播'}
CANONICAL_ALIASES = {channel: [label] for channel, label in SOURCE_LABELS.items()}
CANONICAL_ALIASES['抖音商城'].append('商城')
SCHEDULE_COLS = {'date': 'M', 'type': 'L', 'anchor': 'K', 'gmv_wan': 'N'}
SCHEDULE_LABELS = {'date': '意向合作档期', 'type': '合作类型', 'anchor': '达人昵称', 'gmv_wan': '预估支付GMV（万）'}


def validate_rule(rule):
    if set(rule) != {'parser', 'header_row', 'date_column', 'channels', 'gmv_divisor', 'schedule_columns'} or set(rule.get('channels', {})) != set(CHANNELS):
        raise ValueError('抖音规则字段或渠道集合不正确')
    if rule['header_row'] is not None and (type(rule['header_row']) is not int or rule['header_row'] < 1):
        raise ValueError('表头行必须为空或正整数')
    if not re.fullmatch(r'[A-Z]{1,3}', str(rule['date_column'])):
        raise ValueError('日期列无效')
    if not isinstance(rule['gmv_divisor'], (int, float)) or not 0 < rule['gmv_divisor'] <= 1:
        raise ValueError('纯销转GMV系数无效')
    for aliases in rule['channels'].values():
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError('抖音渠道别名无效')
    if not isinstance(rule['schedule_columns'], dict) or set(rule['schedule_columns']) != set(SCHEDULE_COLS) or not all(re.fullmatch(r'[A-Z]{1,3}', str(v)) for v in rule['schedule_columns'].values()) or len(set(rule['schedule_columns'].values())) != 4:
        raise ValueError('达人排期列映射无效')
    return rule


def as_date(value):
    match = re.fullmatch(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', str(value).strip())
    return date(*map(int, match.groups())).isoformat() if match else None


def source_rows(snapshot):
    rows = {}
    for cell in snapshot['cells']:
        rows.setdefault(cell['row'], {})[cell['col']] = cell
    return rows


def col_number(col):
    value = 0
    for char in col:
        value = value * 26 + ord(char) - ord('A') + 1
    return value


def source_header(rows):
    headers = [r for r, cells in rows.items() if any(str(c['value']).strip() == '日期' for c in cells.values())]
    if not headers:
        # 年初明细表省略“日期”标题，但保留完整渠道表头；禁止从数据行猜表头。
        headers = [r for r, cells in rows.items() if r <= 2 and all(
            any(str(c['value']).strip() in aliases for c in cells.values()) for aliases in CANONICAL_ALIASES.values())]
    if len(headers) != 1:
        raise ValueError('抖音表头缺失或不唯一')
    return headers[0]


def source_date_column(rows, header):
    columns = [col for col, cell in rows[header].items() if str(cell['value']).strip() == '日期']
    if not columns:
        columns = sorted({col for row, cells in rows.items() if row > header
                          for col, cell in cells.items() if as_date(cell['value'])})
    if len(columns) != 1:
        raise ValueError('日期列无法唯一定位')
    return columns[0]


def channel_columns(rows, header, labels):
    """重复标签仅可由明确的原值/GMV分区消歧，不能随意取最左列。"""
    columns = {}
    titles = [cell for row, cells in rows.items() if row < header for cell in cells.values()]
    starts = [col_number(c['col']) for c in titles if str(c['value']).strip() == '纯销维度'
              or re.match(r'^销售(?:目标)?\s*(?:如果|若|如需|如按)\s*GMV', str(c['value']).strip(), re.I)]
    gmv_starts = [col_number(c['col']) for c in titles if re.fullmatch(r'GMV(?:维度|目标)?', str(c['value']).strip(), re.I)]
    end = min((c for c in gmv_starts if len(starts) == 1 and c > starts[0]), default=None)
    for channel, aliases in labels.items():
        found = [col for col, cell in rows[header].items() if str(cell['value']).strip() in aliases]
        if end is not None:
            found = [col for col in found if starts[0] <= col_number(col) < end]
        if len(found) != 1:
            raise ValueError(f'{channel} 列缺失或不唯一，需明确原值与GMV分区')
        columns[channel] = found[0]
    return columns


def schedule_columns_from_source(rows, header):
    labels = {key: [col for col, cell in rows[header].items() if str(cell['value']).strip() == label]
              for key, label in SCHEDULE_LABELS.items()}
    if not any(labels.values()):
        return None
    if not all(len(cols) == 1 for cols in labels.values()):
        raise ValueError('达人排期表头缺失或重复，不能将GMV数据列当作排期')
    return {key: cols[0] for key, cols in labels.items()}


def conversion_from_source(snapshot):
    text = ' '.join(str(c['value']) for c in snapshot['cells'] if c['row'] <= 2)
    expressions = []
    if '纯销维度' in text:
        expressions += re.findall(r'GMV\s*[=＝]\s*纯销\s*[/÷／]\s*(0\.\d+|1(?:\.0+)?)(?![\d.])', text, re.I)
    # 运营9月表的明确说明：“销售 如果gmv维度/0.7”，不能从裸数字猜系数。
    expressions += re.findall(r'销售(?:目标)?\s*(?:如果|若|如需|如按)\s*GMV\s*维度\s*[/÷／]\s*(0\.\d+|1(?:\.0+)?)(?![\d.])', text, re.I)
    divisors = {Decimal(value) for value in expressions}
    if not divisors:
        raise ValueError('抖音原表缺少纯销转GMV口径，不能猜测换算系数')
    if len(divisors) != 1 or not 0 < next(iter(divisors)) <= 1:
        raise ValueError('抖音原表GMV换算说明存在冲突或系数无效，请确认来源口径')
    return next(iter(divisors))


def amount_divisor(snapshot):
    mode = snapshot.get('amount_mode', 'source_conversion')
    if mode == 'source_value':
        return Decimal(1)
    if mode != 'source_conversion':
        raise ValueError('抖音来源金额读取口径无效')
    return conversion_from_source(snapshot)


def daily_rows(rows, date_col, header, year):
    dated = [(row, as_date(cs[date_col]['value'])) for row, cs in sorted(rows.items()) if row > header and date_col in cs and as_date(cs[date_col]['value'])]
    if not dated or len({d for _, d in dated}) != len(dated):
        raise ValueError('抖音日期为空或重复')
    if any(int(d[:4]) != year for _, d in dated):
        raise ValueError('来源日期年份与配置不一致')
    for month in {d[:7] for _, d in dated}:
        days = {d for _, d in dated if d.startswith(month)}
        if len(days) != calendar.monthrange(year, int(month[5:]))[1]:
            raise ValueError(f'{month} 日期读取不完整')
    return dated


def schedule_record(rows, row, cols):
    from api.services.gmv_parser import number
    cs = rows[row]
    if cols['anchor'] not in cs:
        return None
    required = [cs.get(cols[k]) for k in ['date', 'type', 'anchor', 'gmv_wan']]
    if not all(required):
        raise ValueError(f'达人排期第{row}行字段缺失')
    day, kind, anchor, paid = required
    amount = number(paid['value']) * 10000
    return {'type': '达人意向排期', 'date': str(day['value']).strip(), 'subtype': str(kind['value']).strip(),
            'text': f'达人：{anchor["value"]}\n意向档期：{day["value"]}\n合作类型：{kind["value"]}\n预估支付GMV：{paid["value"]}万元',
            'cell': day['ref'], 'source_cells': [c['ref'] for c in required], 'estimated_payment_gmv': float(amount)}


def parse(snapshot, rule, year):
    from api.services.gmv_parser import number
    validate_rule(rule)
    rows = source_rows(snapshot)
    source_divisor = amount_divisor(snapshot)
    basis = '纯销' if any('纯销维度' in str(c['value']) for c in snapshot['cells'] if c['row'] <= 2) else '销售'
    original_values = snapshot.get('amount_mode') == 'source_value'
    divisor = source_divisor if original_values else Decimal(str(rule['gmv_divisor']))
    if divisor != source_divisor:
        raise ValueError('规则GMV换算系数与原表说明不一致')
    header = rule['header_row'] or source_header(rows)
    if header not in rows:
        raise ValueError('抖音表头缺失或不唯一')
    dated = daily_rows(rows, rule['date_column'], header, year)
    columns = channel_columns(rows, header, rule['channels'])
    records = []
    for channel, col in columns.items():
        daily = []
        for row, day in dated:
            cell = rows[row].get(col)
            if not cell:
                raise ValueError(f'{channel} {day} 原始纯销值缺失')
            raw = number(cell['value'])
            rec = {'channel': channel, 'kind': 'day', 'period': day, 'amount': float(raw / divisor),
                   'cell': cell['ref'], 'raw': cell['value'], 'source_cells': [cell['ref']], 'conversion': '按来源设置读取原值' if original_values else f'原表{basis}÷{divisor}'}
            records.append(rec)
            daily.append(rec)
        groups = [(f'{int(month[5:])}月合计', [r for r in daily if r['period'].startswith(month)]) for month in sorted({d[:7] for _, d in dated})]
        groups.append(('总计', daily))
        for period, subset in groups:
            refs = [r['cell'] for r in subset]
            total_raw = sum((number(r['raw']) for r in subset), Decimal(0))
            records.append({'channel': channel, 'kind': 'summary', 'period': period, 'amount': float(total_raw / divisor),
                            'cell': f'{refs[0]}:{refs[-1]}', 'raw': str(total_raw), 'source_cells': refs, 'derived': True, 'conversion': '原表每日原值合计' if original_values else f'每日{basis}合计÷{divisor}'})
    schedules = []
    schedule_cols = schedule_columns_from_source(rows, header)
    if schedule_cols and rule['schedule_columns'] != schedule_cols:
        raise ValueError('达人排期规则与原表表头不符，请分别映射档期、合作类型、达人昵称和支付GMV')
    for row in sorted(rows):
        if row > header and schedule_cols:
            record = schedule_record(rows, row, schedule_cols)
            if record:
                schedules.append(record)
    return {'parser': 'douyin', 'records': records, 'schedules': schedules, 'channel_order': CHANNELS,
            'section_rows': [dated[0][0], dated[-1][0] + 1], 'header_row': header, 'unit_multiplier': float(1 / divisor),
            'amount_mode': snapshot.get('amount_mode', 'source_conversion'),
            'conversion_note': '按用户确认的来源口径直接读取原表数值，不执行表头换算；月合计及总计由日目标汇总。' if original_values else f'原表为{basis}维度，按表头说明：预期GMV＝{basis}÷{divisor}；月合计及总计由日目标汇总。',
            'summary_label': '区间汇总', 'schedule_label': '达人意向排期',
            'schedule_note': '原表仅提供达人意向排期，保留复合/待定档期原文；未提供官方节奏和店铺节奏。' if schedule_cols else '原表未提供达人排期、官方节奏和店铺节奏；不将右侧GMV列作为排期读取。',
            'actuals_status': 'pending_sql'}


def verify(snapshot, result, year):
    """独立按固定业务标签定位，不信任候选规则的列别名、日期列和排期列。"""
    from api.services.gmv_parser import number
    errors, mappings = [], []
    rows = source_rows(snapshot)
    try:
        divisor = amount_divisor(snapshot)
        header = source_header(rows)
        dated = daily_rows(rows, source_date_column(rows, header), header, year)
        if result.get('header_row') != header or result.get('section_rows') != [dated[0][0], dated[-1][0] + 1] or result.get('channel_order') != CHANNELS:
            errors.append('抖音数据区域或渠道集合与原表不符')
        expected = {}
        canonical_columns = channel_columns(rows, header, CANONICAL_ALIASES)
        for channel, label in SOURCE_LABELS.items():
            col = canonical_columns[channel]
            values = []
            for row, day in dated:
                c = rows[row].get(col)
                if not c:
                    raise ValueError(f'{col}{row} 源值缺失')
                expected[(channel, 'day', day)] = ([c['ref']], number(c['value']) / divisor)
                values.append((day, c))
            for month in sorted({day[:7] for day, _ in values}):
                subset = [c for day, c in values if day.startswith(month)]
                expected[(channel, 'summary', f'{int(month[5:])}月合计')] = ([c['ref'] for c in subset], sum((number(c['value']) for c in subset), Decimal(0)) / divisor)
            expected[(channel, 'summary', '总计')] = ([c['ref'] for _, c in values], sum((number(c['value']) for _, c in values), Decimal(0)) / divisor)
            mappings.append({'channel': channel, 'column': col, 'cells': [c['ref'] for _, c in values]})
        seen = set()
        for rec in result.get('records', []):
            key = (rec.get('channel'), rec.get('kind'), rec.get('period'))
            evidence = expected.get(key)
            if key in seen or evidence is None:
                errors.append(f'抖音目标多读或重复：{key}')
            elif evidence[0] != rec.get('source_cells') or abs(evidence[1] - Decimal(str(rec['amount']))) > Decimal('.01'):
                errors.append(f'抖音读取范围或GMV换算错误：{key}')
            elif rec.get('cell') != (evidence[0][0] if rec['kind'] == 'day' else f'{evidence[0][0]}:{evidence[0][-1]}'):
                errors.append(f'抖音来源坐标不符：{key}')
            seen.add(key)
        if seen != set(expected):
            errors.append('抖音目标存在漏读或多读')
        schedule_cols = schedule_columns_from_source(rows, header)
        schedules = [schedule_record(rows, row, schedule_cols) for row in sorted(rows) if row > header] if schedule_cols else []
        if result.get('schedules') != [r for r in schedules if r]:
            errors.append('达人排期范围或源值不正确')
    except (ValueError, KeyError) as exc:
        errors.append(str(exc))
    return {'passed': not errors, 'scope': 'source_ranges', 'errors': errors, 'mappings': mappings,
            'cell_checks': len(result.get('records', [])), 'schedule_checks': len(result.get('schedules', []))}
