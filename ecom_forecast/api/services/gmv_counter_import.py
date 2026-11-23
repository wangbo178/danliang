"""专柜本地Excel单量导入，不依赖飞书或AI规则。"""
import hashlib
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import PureWindowsPath
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook


def read_excel(payload, filename):
    if not filename.lower().endswith('.xlsx') or not payload or len(payload) > 10 * 1024 * 1024:
        raise ValueError('请选择不超过10MB的xlsx专柜预估单量表')
    # 原文件含百万行空白格式，按实际有值的XML单元格确定边界，避免遍历空白样式。
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError('Excel展开后过大')
            last = 1
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            for name in archive.namelist():
                if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                    root = ET.fromstring(archive.read(name))
                    for cell in root.findall('.//s:sheetData/s:row/s:c', ns):
                        if any(cell.find('s:' + tag, ns) is not None for tag in ['v', 'is', 'f']):
                            last = max(last, int(re.search(r'\d+$', cell.attrib['r'])[0]))
            if last > 20000:
                raise ValueError('预估表数据超过20000行')
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
    except (BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise ValueError('无法读取Excel文件') from exc
    try:
        matches = []
        for sheet in workbook:
            header = list(sheet.iter_rows(min_row=1, max_row=1, values_only=True))[0]
            dates = [(i, re.fullmatch(r'(20\d{2})年日期', str(v).strip())) for i, v in enumerate(header)]
            dates = [(i, m) for i, m in dates if m]
            quantities = [i for i, v in enumerate(header) if re.fullmatch(r'20\d{2}年分日预估订单数', str(v).strip())]
            if len(dates) == 1 and len(quantities) == 1:
                dc, match = dates[0]
                qc = quantities[0]
                year = int(match[1])
                if str(header[qc]).strip() != f'{year}年分日预估订单数':
                    raise ValueError('日期与预估订单数年份不一致')
                matches.append((sheet, dc, qc, year))
        if len(matches) != 1:
            raise ValueError('需唯一的“年份日期”和“年份分日预估订单数”工作表')
        sheet, dc, qc, year = matches[0]
        snapshot_cells, daily = [], []
        from openpyxl.utils import get_column_letter
        for row_index, row in enumerate(sheet.iter_rows(max_row=last), 1):
            for cell in row:
                if cell.value is not None:
                    value = cell.value.isoformat() if isinstance(cell.value, (date, datetime)) else cell.value
                    snapshot_cells.append({'row': cell.row, 'col': get_column_letter(cell.column), 'ref': cell.coordinate, 'value': value, 'formula': None})
            if row_index == 1:
                continue
            dcell, qcell = row[dc], row[qc]
            date_ref, qty_ref = f'{get_column_letter(dc + 1)}{row_index}', f'{get_column_letter(qc + 1)}{row_index}'
            if dcell.value is None and qcell.value is None:
                continue
            value = dcell.value
            if isinstance(value, datetime):
                day = value.date()
            elif isinstance(value, date):
                day = value
            else:
                try:
                    day = date.fromisoformat(str(value).strip())
                except ValueError as exc:
                    raise ValueError(f'{date_ref} 日期无效') from exc
            if day.year != year:
                raise ValueError(f'{date_ref} 混入其他年度日期')
            from api.services.gmv_parser import number
            quantity = number(qcell.value)
            if quantity != quantity.to_integral_value():
                raise ValueError(f'{qty_ref} 预估单量必须为非负整数')
            daily.append({'channel': '预估单量', 'kind': 'day', 'period': day.isoformat(), 'amount': int(quantity),
                          'cell': qty_ref, 'raw': qcell.value, 'source_cells': [date_ref, qty_ref]})
        daily.sort(key=lambda r: r['period'])
        if not daily or len(daily) != len({r['period'] for r in daily}):
            raise ValueError('预估单量为空或日期重复')
        if len(daily) != (date.fromisoformat(daily[-1]['period']) - date.fromisoformat(daily[0]['period'])).days + 1:
            raise ValueError('日期不连续，请检查是否漏行')
        summaries = []
        for month in sorted({r['period'][:7] for r in daily}) + ['全部日期']:
            records = daily if month == '全部日期' else [r for r in daily if r['period'].startswith(month)]
            refs = [ref for r in records for ref in r['source_cells']]
            summaries.append({'channel': '预估单量', 'kind': 'summary', 'period': '导入合计' if month == '全部日期' else f'{int(month[5:])}月合计',
                              'amount': sum(r['amount'] for r in records), 'raw': '逐日合计', 'derived': True,
                              'cell': ','.join(refs), 'source_cells': refs})
        filename = PureWindowsPath(filename).name
        snapshot = {'cells': snapshot_cells, 'title': filename, 'sheet_name': sheet.title,
                    'source_type': 'excel', 'file_sha256': hashlib.sha256(payload).hexdigest(), 'range': f'A1:{get_column_letter(sheet.max_column)}{last}'}
        result = {'parser': 'counter_excel', 'quantity_only': True, 'channel_order': [], 'records': [],
                  'estimated_orders': daily + summaries, 'schedules': [], 'header_row': 1, 'section_rows': [2, last + 1],
                  'summary_label': '月度 / 导入汇总', 'conversion_note': f'本地Excel导入：{sheet.title}，直接读取分日预估订单数（单），共{len(daily)}天；{daily[0]["period"]}至{daily[-1]["period"]}。'}
        return year, snapshot, result
    finally:
        workbook.close()
