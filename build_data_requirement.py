from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


OUT = Path("output") / "单量预测系统在线数据接入需求说明.docx"

BLACK = "000000"
NAVY = "1F4E78"
BLUE = "2F75B5"
PALE_BLUE = "EAF2F8"
PALE_GRAY = "F5F7F9"
MID_GRAY = "667085"
LIGHT_BORDER = "D9D9D9"
WHITE = "FFFFFF"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=100, bottom=90, end=100):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_borders(table, color=LIGHT_BORDER, size="6"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "single")
        tag.set(qn("w:sz"), size)
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), color)


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def set_cell_width(cell, width_cm):
    cell.width = Cm(width_cm)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(width_cm * 567)))
    tc_w.set(qn("w:type"), "dxa")


def set_run_font(run, name="Microsoft YaHei", size=10.5, bold=False, color=BLACK):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(paragraph, text, bold=False, color=BLACK, size=10.5):
    run = paragraph.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)
    return run


def add_body(doc, text, *, first_line=True, space_after=6, keep=False):
    p = doc.add_paragraph()
    p.style = doc.styles["Normal"]
    p.paragraph_format.first_line_indent = Cm(0.74) if first_line else Cm(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.35
    p.paragraph_format.keep_with_next = keep
    add_text(p, text)
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.25
    add_text(p, text)
    return p


def add_number(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.25
    add_text(p, text)
    return p


def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.page_break_before = False
    add_text(p, text, bold=True, size=15 if level == 1 else 12)
    return p


def remove_trailing_empty_paragraph(doc):
    if doc.paragraphs and not doc.paragraphs[-1].text.strip():
        paragraph = doc.paragraphs[-1]._element
        paragraph.getparent().remove(paragraph)


def add_table(doc, headers, rows, widths, *, font_size=9.0, header_fill=NAVY, alignments=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")
    set_table_borders(table)

    header = table.rows[0]
    set_repeat_table_header(header)
    prevent_row_split(header)
    for i, (cell, text) in enumerate(zip(header.cells, headers)):
        set_cell_width(cell, widths[i])
        set_cell_shading(cell, header_fill)
        set_cell_margins(cell, top=110, bottom=110)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        add_text(p, str(text), bold=True, color=WHITE, size=font_size)

    for ridx, row in enumerate(rows):
        cells = table.add_row().cells
        prevent_row_split(table.rows[-1])
        for i, (cell, value) in enumerate(zip(cells, row)):
            set_cell_width(cell, widths[i])
            set_cell_margins(cell)
            if ridx % 2 == 1:
                set_cell_shading(cell, PALE_BLUE)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.2
            alignment = alignments[i] if alignments else ("center" if i in (0, 1) else "left")
            p.alignment = {
                "left": WD_ALIGN_PARAGRAPH.LEFT,
                "center": WD_ALIGN_PARAGRAPH.CENTER,
                "right": WD_ALIGN_PARAGRAPH.RIGHT,
            }[alignment]
            add_text(p, str(value), size=font_size)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_dataset_fields(doc, title, purpose, key, fields, *, page_break=False, split_at=None, table_font_size=8.8):
    if page_break:
        remove_trailing_empty_paragraph(doc)
    title_paragraph = add_heading(doc, title, 2)
    if page_break:
        title_paragraph.paragraph_format.page_break_before = True
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.3
    add_text(p, "用途  ", bold=True)
    add_text(p, purpose)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(7)
    p.paragraph_format.line_spacing = 1.3
    add_text(p, "建议唯一键  ", bold=True)
    add_text(p, key)
    rows = []
    for name, type_req, definition, example in fields:
        rows.append([name, type_req, definition, example])
    chunks = [rows]
    if split_at and len(rows) > split_at:
        chunks = [rows[:split_at], rows[split_at:]]
    for index, chunk in enumerate(chunks):
        if index:
            remove_trailing_empty_paragraph(doc)
            continuation = add_heading(doc, f"{title} 续", 2)
            continuation.paragraph_format.page_break_before = True
        add_table(
            doc,
            ["字段", "类型和必填性", "业务定义和取值规则", "示例"],
            chunk,
            [3.2, 2.8, 8.1, 3.3],
            font_size=table_font_size,
            alignments=["left", "center", "left", "left"],
        )


def add_page_number(paragraph):
    run = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char)
    run._r.append(instr_text)
    run._r.append(fld_char2)
    set_run_font(run, size=8.5, color=MID_GRAY)


doc = Document()
section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(1.8)
section.bottom_margin = Cm(1.7)
section.left_margin = Cm(1.8)
section.right_margin = Cm(1.8)
section.header_distance = Cm(0.8)
section.footer_distance = Cm(0.8)

# Base styles
normal = doc.styles["Normal"]
normal.font.name = "Microsoft YaHei"
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
normal.font.size = Pt(10.5)
normal.font.color.rgb = RGBColor.from_string(BLACK)
normal.paragraph_format.line_spacing = 1.35

title_style = doc.styles["Title"]
title_style.font.name = "Microsoft YaHei"
title_style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
title_style.font.size = Pt(25)
title_style.font.bold = True
title_style.font.color.rgb = RGBColor.from_string(BLACK)
title_ppr = title_style.element.get_or_add_pPr()
title_border = title_ppr.find(qn("w:pBdr"))
if title_border is not None:
    title_ppr.remove(title_border)

for level, size, before, after in ((1, 15, 15, 7), (2, 12, 10, 5), (3, 10.5, 7, 3)):
    style = doc.styles[f"Heading {level}"]
    style.font.name = "Microsoft YaHei"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    style.font.size = Pt(size)
    style.font.bold = True
    style.font.color.rgb = RGBColor.from_string(BLACK)
    style.paragraph_format.space_before = Pt(before)
    style.paragraph_format.space_after = Pt(after)
    style.paragraph_format.keep_with_next = True

for style_name in ("List Bullet", "List Bullet 2", "List Number"):
    style = doc.styles[style_name]
    style.font.name = "Microsoft YaHei"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    style.font.size = Pt(10.5)

# Minimal header and footer
header_p = section.header.paragraphs[0]
header_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
header_p.paragraph_format.space_after = Pt(0)
add_text(header_p, "单量预测系统在线数据接入需求说明", size=8.5, color=MID_GRAY)

footer_p = section.footer.paragraphs[0]
footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer_p.paragraph_format.space_after = Pt(0)
add_text(footer_p, "数据需求评审稿  |  第 ", size=8.5, color=MID_GRAY)
add_page_number(footer_p)
add_text(footer_p, " 页", size=8.5, color=MID_GRAY)

# Cover
doc.add_paragraph().paragraph_format.space_after = Pt(80)
p = doc.add_paragraph(style="Title")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(16)
title_para_border = p._p.get_or_add_pPr().find(qn("w:pBdr"))
if title_para_border is not None:
    p._p.get_or_add_pPr().remove(title_para_border)
add_text(p, "单量预测系统在线数据接入需求说明", bold=True, size=25)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(58)
add_text(p, "用于向 IT 数据分析模块提出数据接口和指标口径需求", size=13, color=MID_GRAY)

meta_rows = [
    ["需求提出方", "单量预测系统项目组"],
    ["需求接收方", "IT 数据分析模块"],
    ["文档版本", "V1.0"],
    ["编制日期", "2026 年 10 月 9 日"],
    ["文档状态", "需求评审稿"],
]
table = doc.add_table(rows=0, cols=2)
table.alignment = WD_TABLE_ALIGNMENT.CENTER
table.autofit = False
for idx, row in enumerate(meta_rows):
    cells = table.add_row().cells
    set_cell_width(cells[0], 4.0)
    set_cell_width(cells[1], 8.6)
    set_cell_margins(cells[0], top=120, bottom=120)
    set_cell_margins(cells[1], top=120, bottom=120)
    cells[0].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    cells[1].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p0 = cells[0].paragraphs[0]
    p1 = cells[1].paragraphs[0]
    p0.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p1.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_text(p0, row[0], bold=True, color=MID_GRAY, size=10)
    add_text(p1, row[1], size=10)
set_table_borders(table, color=WHITE, size="0")

doc.add_page_break()

# Opening
add_heading(doc, "一 需求结论", 1)
add_body(
    doc,
    "请 IT 数据分析模块为单量预测系统提供标准化在线数据源，替代当前人工导入 Excel 的方式。建议优先建设只读数据仓库视图；如无法开放数据库访问，可提供等价的 REST API。接口需支持历史全量回补、按更新时间增量拉取、数据修订和逻辑删除，并统一店铺、平台、渠道、仓库及活动编码。",
)
add_body(
    doc,
    "一期必须上线渠道订单、分仓订单、主数据和活动日历。直播排期、达人直播实绩和预售历史列为一期增强项；联盟佣金及活跃达人数据可作为二期。数据接入完成后，系统应能每日自动更新训练数据，按日监控完整性和勾稽差异，并在大促期间提高关键数据的刷新频率。",
)

add_heading(doc, "二 背景与现状", 1)
add_body(
    doc,
    "当前单量预测系统通过文件上传写入 MySQL，再使用渠道订单训练渠道模型、使用分仓订单学习仓库路由，并结合直播、预售和活动信息修正预测。人工导入方式存在更新不及时、重复批次、名称映射和口径不一致等风险，且无法稳定支撑每日重训和大促期间的高频修正。",
)

snapshot_rows = [
    ["渠道订单", "198,682 条", "2023-06-01 至 2026-09-22", "当前核心训练数据"],
    ["分仓订单", "342,650 条", "2023-06-01 至 2026-09-22", "用于分仓比例学习和仓级评估"],
    ["直播排期", "16 条", "2026-10-06 至 2026-10-25", "仅覆盖部分未来排期"],
    ["达人直播实绩", "129,016 条", "2023-01-01 至 2026-09-22", "用于达人历史带货能力特征"],
    ["预售历史", "261 条", "2023-01-08 至 2026-05-28", "用于尾款日预测校准"],
    ["联盟佣金", "0 条", "暂无", "系统目前按缺省值处理"],
    ["主数据", "185 个店铺  38 个渠道  16 个仓库", "当前配置", "需改为统一编码在线同步"],
]
add_table(doc, ["数据对象", "当前规模", "当前覆盖", "系统用途"], snapshot_rows, [3.1, 3.4, 4.6, 6.3], font_size=9.0)
add_body(
    doc,
    "截至 2026 年 10 月 9 日，核心订单数据最新日期为 2026 年 9 月 22 日，距文档编制日存在 17 天数据滞后。在线化的首要目标是消除人工导入等待时间，并使数据刷新、补数和异常处理可监控。",
    first_line=False,
)

add_heading(doc, "三 业务目标与范围", 1)
goal_rows = [
    ["自动更新", "系统无需人工上传文件即可获得最新数据", "按约定频率自动拉取并落库，失败后自动重试并告警"],
    ["统一口径", "同一订单在渠道和分仓数据中可核对", "明确订单状态、业务日期、预售日期、派样和取消规则"],
    ["稳定映射", "店铺、渠道和仓库使用稳定 ID", "名称变化不改变 ID，主数据保留生效和失效时间"],
    ["可追溯", "每次同步可定位来源和变更", "保留 source_system、updated_at、is_deleted 和同步批次"],
    ["支持建模", "历史数据连续，未来事件提前可见", "至少覆盖 24 个月历史，活动和排期覆盖预测窗口"],
]
add_table(doc, ["目标", "业务结果", "验收方式"], goal_rows, [2.8, 6.2, 8.4], font_size=9.2)

scope_rows = [
    ["P0", "渠道订单日汇总", "日期 店铺 平台 渠道 派样 预售", "训练渠道模型和计算实际单量", "每日"],
    ["P0", "分仓订单日汇总", "日期 店铺 渠道 仓库 派样", "学习仓库路由和仓级评估", "每日"],
    ["P0", "店铺平台渠道仓库主数据", "业务编码及生效区间", "标准化映射和维度校验", "变更触发"],
    ["P0", "活动日历", "活动 店铺 日期阶段", "生成预热预售尾款和大促特征", "变更触发"],
    ["P1", "直播排期", "店铺 日期 场次 达人", "生成直播和预售排期特征", "30 分钟"],
    ["P1", "达人直播实绩", "店铺 日期 达人", "学习达人历史带货能力", "每日"],
    ["P1", "预售历史", "店铺 渠道 直播日 尾款日", "校准尾款期单量", "大促期间每小时"],
    ["P2", "联盟佣金月度数据", "店铺 月份", "联盟渠道特征", "每日或月度"],
]
add_heading(doc, "四 数据范围和优先级", 1)
add_table(doc, ["级别", "数据集", "数据粒度", "用途", "建议频率"], scope_rows, [1.3, 4.1, 4.5, 5.3, 2.2], font_size=8.6)
add_heading(doc, "五 数据明细", 1)
add_body(
    doc,
    "以下字段名为建议的标准输出名。IT 可保留内部物理字段名，但需提供一份固定的数据字典和映射关系。所有时间均使用 Asia Shanghai 时区，日期格式使用 YYYY-MM-DD，时间格式使用 ISO 8601。",
)

add_dataset_fields(
    doc,
    "渠道订单日汇总 P0",
    "作为渠道模型的实际单量。系统按店铺和渠道构造滞后、滚动、同比和活动特征。",
    "business_date + shop_id + platform_id + channel_id + is_sample + is_presale_order",
    [
        ("business_date", "date 必填", "有效订单归属日期。普通订单取支付完成日期；预售订单取尾款支付完成日期，不取定金日期。", "2026-10-08"),
        ("shop_id", "string 必填", "稳定店铺编码。不得用店铺展示名称代替；名称变化时编码不变。", "SHOP_TM_001"),
        ("platform_id", "string 必填", "稳定平台编码，必须能关联平台主数据。", "tmall"),
        ("channel_id", "string 必填", "稳定渠道编码，必须能关联渠道主数据及渠道类型。", "tmall_live"),
        ("order_count", "integer 必填", "当日有效销售订单数。按销售订单号去重计数，不使用商品件数或订单明细行数。", "12580"),
        ("is_sample", "boolean 必填", "是否派样单。派样单不删除，使用 0 和 1 分组输出。", "0"),
        ("is_presale_order", "boolean 必填", "是否预售尾款订单。仅最终支付尾款的订单记为 1。", "1"),
        ("source_system", "string 必填", "数据来源系统或主题域，用于问题追溯。", "OMS"),
        ("updated_at", "datetime 必填", "源数据最后更新时间，用于增量同步和修订识别。", "2026-10-09T06:10:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。历史汇总被撤销或重算时需返回删除记录。", "0"),
    ],
)

add_dataset_fields(
    doc,
    "分仓订单日汇总 P0",
    "学习各店铺和渠道的实际分仓比例，并评估仓级预测。",
    "business_date + shop_id + platform_id + channel_id + warehouse_id + is_sample",
    [
        ("business_date", "date 必填", "与渠道订单采用相同业务日期。若源系统只能提供入仓时间，应同时提供 paid_date 供勾稽。", "2026-10-08"),
        ("shop_id", "string 必填", "稳定店铺编码，与渠道订单一致。", "SHOP_TM_001"),
        ("platform_id", "string 必填", "稳定平台编码。", "tmall"),
        ("channel_id", "string 必填", "稳定渠道编码。", "tmall_live"),
        ("warehouse_id", "string 必填", "订单最终有效分配仓编码。改仓后需覆盖旧分配或通过逻辑删除撤销。", "WH_HZ_01"),
        ("order_count", "integer 必填", "成功分配并推送至该仓的有效销售订单数，按销售订单号去重。", "7340"),
        ("is_sample", "boolean 必填", "是否派样单，与渠道订单口径一致。", "0"),
        ("routing_status", "string 建议", "success failed pending。用于解释渠道订单与分仓订单差异。", "success"),
        ("source_system", "string 必填", "来源系统或主题域。", "OMS_WMS"),
        ("updated_at", "datetime 必填", "源数据最后更新时间。", "2026-10-09T06:15:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。", "0"),
    ],
    page_break=True,
)

add_dataset_fields(
    doc,
    "主数据 P0",
    "提供统一编码、显示名称、归属关系和有效期，避免依赖名称自动映射。",
    "各维度的业务 ID + effective_start_time",
    [
        ("platform", "维表 必填", "platform_id、platform_name、is_active、effective_start_time、effective_end_time。", "tmall  天猫"),
        ("shop", "维表 必填", "shop_id、shop_name、platform_id、is_active、生效失效时间。", "SHOP_TM_001"),
        ("channel", "维表 必填", "channel_id、channel_name、platform_id、channel_type。channel_type 仅允许 stable event affiliate。", "tmall_live  event"),
        ("warehouse", "维表 必填", "warehouse_id、warehouse_name、region、is_active、生效失效时间。", "WH_HZ_01  湖州仓"),
        ("shop warehouse binding", "关系表 建议", "店铺单仓或多仓绑定、绑定类型、生效日期。用于识别路由规则变化。", "SHOP_TM_001  multi"),
        ("alias mapping", "映射表 建议", "历史名称和外部编码到标准 ID 的映射。保留来源系统和有效期。", "薇诺娜天猫旗舰店"),
        ("updated_at", "datetime 必填", "主数据最后更新时间。", "2026-10-09T05:00:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。", "0"),
    ],
    page_break=True,
)

add_dataset_fields(
    doc,
    "活动日历 P0",
    "生成预热、预售、尾款、大促正式期和售后期特征，替代本地硬编码日历。",
    "campaign_id + shop_id + stage_type + start_time + end_time",
    [
        ("campaign_id", "string 必填", "活动唯一编码，同一活动跨阶段保持一致。", "CAMP_1111_2026"),
        ("campaign_name", "string 必填", "活动名称。", "2026 双十一"),
        ("campaign_type", "string 必填", "标准活动类型，如 38 618 1111 1212。", "1111"),
        ("shop_id", "string 必填", "参与活动的店铺。全店适用时可使用 ALL，并明确展开规则。", "SHOP_TM_001"),
        ("stage_type", "string 必填", "preheat presale final_payment campaign post_campaign。", "final_payment"),
        ("start_time", "datetime 必填", "阶段开始时间。", "2026-10-31T20:00:00+08:00"),
        ("end_time", "datetime 必填", "阶段结束时间。", "2026-11-03T23:59:59+08:00"),
        ("intensity", "decimal 建议", "活动强度 0 至 1。无统一规则时可留空，由模型按阶段计算。", "1.0"),
        ("updated_at", "datetime 必填", "排期最后更新时间。", "2026-10-01T10:00:00+08:00"),
        ("is_deleted", "boolean 必填", "取消活动或撤销阶段时返回 1。", "0"),
    ],
    page_break=True,
)

add_dataset_fields(
    doc,
    "直播排期 P1",
    "生成当天直播、直播滞后、主播档级和预售尾款关联特征。排期必须覆盖系统预测日期。",
    "schedule_id；若无场次 ID，使用 shop_id + event_date + event_type + anchor_id",
    [
        ("schedule_id", "string 必填", "直播场次或排期唯一编码。", "LIVE_20261008_001"),
        ("shop_id", "string 必填", "直播归属店铺。", "SHOP_DY_001"),
        ("channel_id", "string 建议", "直播归属渠道；同店有多个直播渠道时必填。", "douyin_live"),
        ("event_date", "date 必填", "普通直播取播出日；预售尾款事件取尾款日。", "2026-10-20"),
        ("event_type", "string 必填", "live 或 presale_payment。", "presale_payment"),
        ("broadcast_date", "date 条件必填", "event_type 为 presale_payment 时必填，表示对应定金场播出日。", "2026-10-08"),
        ("anchor_id", "string 建议", "稳定达人编码。不可仅依赖名称连接。", "ANCHOR_0188"),
        ("anchor_name", "string 建议", "达人展示名称，需与达人实绩保持一致。", "达人甲"),
        ("anchor_tier", "string 建议", "A B C。A 为头部，B 为腰部，C 为尾部。", "A"),
        ("expected_viewers", "integer 建议", "排期时预计观看人数。", "1200000"),
        ("updated_at", "datetime 必填", "排期最后更新时间。", "2026-10-07T18:20:00+08:00"),
        ("is_deleted", "boolean 必填", "取消场次时返回 1。", "0"),
    ],
    page_break=True,
    split_at=6,
)

add_dataset_fields(
    doc,
    "达人直播实绩 P1",
    "计算达人历史平均单量和近三场表现，辅助预测未来直播场次。",
    "live_date + shop_id + anchor_id；无 anchor_id 时临时使用 anchor_name",
    [
        ("live_date", "date 必填", "直播实际发生日期。", "2026-10-08"),
        ("shop_id", "string 必填", "直播归属店铺。", "SHOP_DY_001"),
        ("anchor_id", "string 建议", "稳定达人编码。", "ANCHOR_0188"),
        ("anchor_name", "string 必填", "达人名称，当前系统仍使用该字段与排期连接。", "达人甲"),
        ("order_count", "integer 必填", "该达人在该店铺当日实际带货订单数，按有效订单去重。", "48320"),
        ("actual_viewers", "integer 可选", "实际观看人数，用于后续替代预计观看人数。", "1680000"),
        ("source_system", "string 必填", "直播平台或数据中台来源。", "LIVE_DATA_MART"),
        ("updated_at", "datetime 必填", "实绩最后更新时间。", "2026-10-09T03:00:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。", "0"),
    ],
    page_break=True,
)

add_dataset_fields(
    doc,
    "预售历史 P1",
    "把定金场与尾款支付日关联，用于识别尾款爆发节奏并校准大促预测。",
    "shop_id + channel_id + broadcast_date + payment_date",
    [
        ("shop_id", "string 必填", "预售归属店铺。", "SHOP_TM_001"),
        ("channel_id", "string 必填", "预售归属的实际直播或活动渠道。", "tmall_live"),
        ("broadcast_date", "date 必填", "定金场或预售场直播日期。", "2026-10-15"),
        ("payment_date", "date 必填", "买家完成尾款支付的日期，也是模型使用的入仓需求日期。", "2026-10-20"),
        ("order_count", "integer 必填", "该直播日与尾款日组合对应的已付尾款订单数。", "428369"),
        ("campaign_type", "string 建议", "38 618 1111 1212 或其他标准活动类型。", "1111"),
        ("deposit_order_count", "integer 可选", "定金订单数。用于计算尾款转化率。", "510000"),
        ("updated_at", "datetime 必填", "数据最后更新时间。", "2026-10-21T02:00:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。", "0"),
    ],
    page_break=True,
)

add_dataset_fields(
    doc,
    "联盟佣金月度数据 P2",
    "为 CPS 和精选联盟等渠道提供佣金与活跃达人特征。",
    "shop_id + month",
    [
        ("shop_id", "string 必填", "联盟业务归属店铺。", "SHOP_DY_001"),
        ("month", "string 必填", "月份，格式 YYYY-MM。", "2026-10"),
        ("commission_rate", "decimal 必填", "有效佣金率，使用 0 至 1 小数，不使用百分号字符串。", "0.08"),
        ("active_affiliates", "integer 必填", "当月产生有效推广或有效订单的达人和推手去重数。", "410"),
        ("updated_at", "datetime 必填", "数据最后更新时间。", "2026-10-09T02:00:00+08:00"),
        ("is_deleted", "boolean 必填", "逻辑删除标识。", "0"),
    ],
    page_break=True,
)

add_heading(doc, "六 数据获取方式", 1)
add_heading(doc, "六一 推荐方式", 2)
add_body(
    doc,
    "优先由 IT 在数据仓库或 MySQL 分析库发布只读标准视图，预测系统使用独立只读账号定时拉取。视图只输出本需求需要的汇总字段，不包含客户姓名、手机号、地址、证件号等个人信息。若网络或权限策略不允许数据库直连，IT 可提供字段和口径完全一致的 REST API。",
)

method_rows = [
    ["只读数据库视图", "结构稳定 查询效率高 易于全量回补和批量校验", "需开通网络和只读账号", "推荐"],
    ["REST API", "权限边界清晰 适合跨网络区域", "需处理分页 限流 重试和大批量历史回补", "备选"],
    ["消息或 CDC", "实时性高", "建设和运维成本较高 当前日级预测不强制需要", "后续可选"],
]
add_table(doc, ["方式", "优点", "约束", "建议"], method_rows, [3.0, 6.1, 6.2, 2.1], font_size=9.0)

add_heading(doc, "六二 建议视图名称", 2)
view_rows = [
    ["ads_forecast_channel_order_di", "渠道订单日汇总"],
    ["ads_forecast_warehouse_order_di", "分仓订单日汇总"],
    ["dim_forecast_platform", "平台主数据"],
    ["dim_forecast_shop", "店铺主数据"],
    ["dim_forecast_channel", "渠道主数据"],
    ["dim_forecast_warehouse", "仓库主数据"],
    ["ads_forecast_campaign_calendar", "活动日历"],
    ["ads_forecast_live_schedule", "直播排期"],
    ["ads_forecast_anchor_live_order_di", "达人直播实绩"],
    ["ads_forecast_presale_order_di", "预售历史"],
    ["ads_forecast_affiliate_monthly", "联盟佣金月度数据"],
]
add_table(doc, ["建议名称", "内容"], view_rows, [8.6, 8.8], font_size=9.2, alignments=["left", "left"])

add_heading(doc, "六三 全量和增量同步", 2)
add_number(doc, "首次上线执行历史全量回补。订单和分仓数据至少回补最近 24 个月，建议从 2023 年 1 月 1 日开始，以覆盖同比、大促和达人历史。")
add_number(doc, "日常同步按 updated_at 增量拉取，并保存上次成功水位。增量窗口需允许等于水位，接收端按唯一键幂等更新。")
add_number(doc, "每天重新拉取最近 35 天数据，用于吸收取消、退款、改仓和迟到数据。大促预售数据建议重拉最近 90 天。")
add_number(doc, "源端删除记录不得物理消失，需通过 is_deleted 返回。接收端按唯一键软删除或撤销聚合。")
add_number(doc, "接口失败后自动重试三次；连续失败、数据量异常或水位不前进时发送告警。恢复后从最后成功水位继续。")

add_heading(doc, "六四 REST API 约定", 2)
api_rows = [
    ["请求参数", "start_date end_date updated_after cursor page_size"],
    ["分页", "使用稳定游标，不使用会因插入数据而漂移的页码"],
    ["返回元数据", "request_id data next_cursor has_more watermark generated_at"],
    ["编码", "UTF-8，日期和时间使用 ISO 8601，时区固定为 Asia Shanghai"],
    ["认证", "应用级 Token 或 OAuth2，凭证通过密钥管理配置，不写入代码"],
    ["限流", "IT 提供并发和每分钟请求上限；超限返回 429 并提供 Retry After"],
    ["错误响应", "统一 code message request_id，可区分参数错误、认证失败和服务异常"],
]
add_table(doc, ["项目", "要求"], api_rows, [3.5, 13.9], font_size=9.2, alignments=["center", "left"])

add_heading(doc, "七 指标口径", 1)
add_heading(doc, "七一 业务数据指标", 2)
business_metric_rows = [
    ["有效支付订单量", "去重后的有效销售订单数", "按 business_date shop_id channel_id 汇总", "渠道模型目标值"],
    ["派样订单量", "is_sample 等于 1 的有效订单数", "与普通订单分开统计", "计算派样比例和仓级需求"],
    ["预售尾款订单量", "is_presale_order 等于 1 且已完成尾款支付的订单数", "日期取尾款支付日", "大促尾款校准"],
    ["成功分仓订单量", "routing_status 为 success 的去重订单数", "按仓库汇总", "仓级预测实际值"],
    ["分仓成功率", "成功分仓订单量除以应分仓有效订单量", "分母须排除无需入仓的明确业务类型", "解释渠道与仓库差异"],
    ["仓库分配占比", "某仓有效订单量除以同店铺同渠道全仓订单量", "按稳定路由期计算", "仓库路由模型"],
    ["尾款转化率", "已付尾款订单量除以定金订单量", "按活动 店铺 渠道计算", "预售校准参数"],
]
add_table(doc, ["指标", "定义", "统计维度", "用途"], business_metric_rows, [3.2, 6.3, 4.4, 3.5], font_size=8.9)

add_heading(doc, "七二 模型评估指标", 2)
model_metric_rows = [
    ["WAPE", "Σ绝对值 预测减实际 ÷ Σ实际 × 100%", "主要精度指标，适合包含零值和量级差异较大的渠道", "整体小于等于 15%  大促小于等于 20%"],
    ["MAPE", "仅实际大于 0 的样本，平均 绝对值 预测减实际 ÷ 实际 × 100%", "补充观察单日相对误差", "整体小于等于 15%  大促小于等于 20%"],
    ["Bias", "平均 预测减实际 ÷ 平均绝对实际 × 100%", "正值表示高估，负值表示低估", "绝对值小于 5%"],
    ["MAE", "平均绝对误差", "以订单数表示平均偏差", "按渠道和仓库基线持续下降"],
    ["RMSE", "均方误差平方根", "对大误差更敏感", "按渠道和仓库基线持续下降"],
]
add_table(doc, ["指标", "公式口径", "解释", "建议目标"], model_metric_rows, [2.3, 6.4, 5.0, 3.7], font_size=8.7)
add_body(
    doc,
    "模型评估按最近 30 天留出集计算，并至少拆分全局、渠道、仓库、大促和非大促。目标值用于评审，最终阈值应根据历史基线和业务可接受误差确认。",
    first_line=False,
)

add_heading(doc, "七三 数据质量和服务指标", 2)
dq_rows = [
    ["及时性", "前一自然日订单和分仓数据按时到达", "每日 07:00 前可用率不低于 99%"],
    ["完整性", "必填字段非空记录数除以总记录数", "主键字段 100%  其他必填字段不低于 99.9%"],
    ["唯一性", "按约定唯一键检查重复", "重复率 0"],
    ["主数据映射率", "可关联标准店铺 渠道 仓库的记录数除以总记录数", "不低于 99.9%  未映射记录单独输出"],
    ["勾稽一致性", "同口径渠道有效订单量与成功分仓订单量核对", "扣除明确未路由和跨日差异后 未解释差异不高于 0.5%"],
    ["数值有效性", "订单量非负 佣金率在 0 至 1 日期关系有效", "非法值 0"],
    ["连续性", "有效业务组合应有连续日期，零单日也需输出 0 或由双方约定补零", "异常断档在当日告警"],
    ["接口可用性", "同步窗口内接口或视图可访问", "月度不低于 99.5%"],
]
add_table(doc, ["指标", "定义", "建议验收标准"], dq_rows, [3.0, 8.6, 5.8], font_size=8.9)

add_heading(doc, "八 刷新频率和时效", 1)
sla_rows = [
    ["渠道订单 分仓订单", "每日 06:30 前完成前一日初版", "每日 07:00 可用", "最近 35 天滚动修订"],
    ["主数据", "变更后 30 分钟内", "全天", "保留有效期和删除标识"],
    ["活动日历", "创建或变更后 30 分钟内", "至少提前覆盖未来 60 天", "取消活动需同步撤销"],
    ["直播排期", "每 30 分钟", "至少覆盖预测未来 30 天", "临时加场和取消需及时更新"],
    ["达人直播实绩", "每日 06:30 前", "T 加 1", "最近 7 天滚动修订"],
    ["预售历史", "大促期间每小时  其他时间每日", "大促期间 T 加 1 小时", "最近 90 天滚动修订"],
    ["联盟佣金", "每日或每月 2 日前", "按来源能力确认", "月度数据允许追溯修订"],
]
add_table(doc, ["数据集", "刷新频率", "时效要求", "修订窗口"], sla_rows, [4.0, 4.7, 4.1, 4.6], font_size=8.8)

add_heading(doc, "九 数据校验规则", 1)
validation_rows = [
    ["主键", "business_date shop_id channel_id 等关键字段不得为空"],
    ["日期", "预售 broadcast_date 不得晚于 payment_date；活动开始时间不得晚于结束时间"],
    ["数量", "order_count 为非负整数；发现负数需通过删除或修订记录处理"],
    ["布尔", "is_sample is_presale_order is_deleted 仅允许 0 或 1"],
    ["枚举", "channel_type 仅允许 stable event affiliate；event_type 仅允许 live presale_payment"],
    ["佣金", "commission_rate 在 0 和 1 之间"],
    ["关联", "shop_id platform_id channel_id warehouse_id 必须存在于有效主数据"],
    ["汇总", "同一唯一键只能有一条有效记录；发生修订时覆盖或撤销旧值"],
    ["勾稽", "每日输出渠道订单 分仓成功 未分仓 跨日差异和已解释异常"],
]
add_table(doc, ["规则类别", "校验要求"], validation_rows, [3.5, 13.9], font_size=9.2, alignments=["center", "left"])

add_heading(doc, "十 验收标准", 1)
acceptance_rows = [
    ["字段和口径", "IT 提供数据字典 源表说明 计算 SQL 或口径说明及样例数据", "评审通过"],
    ["历史全量", "回补范围覆盖约定起始日期，按日 店铺 渠道和仓库抽样核对", "抽样汇总差异为 0 或有书面解释"],
    ["增量同步", "新增 修订 删除三类变更均可被系统接收", "连续运行 10 个自然日无漏数和重复"],
    ["时效", "按第八章刷新频率提供数据", "达成约定 SLA"],
    ["数据质量", "完整性 唯一性 映射率 勾稽 数值和连续性", "达到第七章建议阈值或双方确认后的阈值"],
    ["故障恢复", "模拟接口超时 水位中断和补数", "恢复后可从最后成功水位续传，结果幂等"],
    ["安全", "只读权限 最小字段 网络白名单 凭证管理和访问日志", "通过 IT 安全检查"],
    ["模型回归", "使用在线数据完成一次训练 预测和最近 30 天回测", "记录 WAPE MAPE Bias MAE RMSE 并与导入方式结果解释差异"],
]
add_table(doc, ["验收项", "验收内容", "通过标准"], acceptance_rows, [3.1, 9.3, 5.0], font_size=8.8)

add_heading(doc, "十一 职责分工", 1)
owner_rows = [
    ["IT 数据分析模块", "确认源系统和口径，建设视图或 API，提供历史回补 增量字段 删除语义 SLA 监控和变更通知"],
    ["单量预测系统项目组", "开发拉取任务 水位管理 幂等落库 映射校验 告警和模型回归，维护消费端数据字典"],
    ["业务和运营", "确认有效订单 预售 派样 活动和渠道分类口径，处理未映射主数据和异常业务日"],
    ["双方共同", "完成全量勾稽 增量联调 压力测试 上线切换和并行验证"],
]
add_table(doc, ["责任方", "主要职责"], owner_rows, [4.2, 13.2], font_size=9.2, alignments=["center", "left"])

add_heading(doc, "十二 待 IT 确认事项", 1)
confirm_rows = [
    ["1", "渠道订单和分仓订单的权威源系统及表名", "待确认"],
    ["2", "有效订单是否回冲退款和取消，以及回冲截止时间", "待确认"],
    ["3", "是否具备稳定的 updated_at 和 is_deleted 字段", "待确认"],
    ["4", "渠道订单与分仓订单能否使用同一业务日期和订单范围", "待确认"],
    ["5", "店铺 渠道 仓库 达人是否已有统一业务编码", "待确认"],
    ["6", "直播排期和达人实绩的数据来源 更新频率和历史范围", "待确认"],
    ["7", "是否可提供定金订单数及定金场与尾款订单的关联", "待确认"],
    ["8", "联盟佣金率和活跃达人数量是否有稳定月度口径", "待确认"],
    ["9", "数据库视图和 REST API 两种接入方式的网络与权限可行性", "待确认"],
    ["10", "正式上线时间 联调环境和责任人", "待确认"],
]
add_table(doc, ["序号", "确认事项", "状态"], confirm_rows, [1.4, 13.7, 2.3], font_size=9.0)

add_heading(doc, "附录 一 最小上线范围", 1)
add_body(
    doc,
    "如需分阶段实施，第一阶段只建设以下六项即可完成从人工导入到每日在线更新的切换。其余数据可在不影响主流程的前提下逐步接入。",
)
for item in [
    "渠道订单日汇总",
    "分仓订单日汇总",
    "平台 店铺 渠道 仓库主数据",
    "活动日历",
    "updated_at 增量水位和 is_deleted 删除语义",
    "每日数据质量报告和同步失败告警",
]:
    add_bullet(doc, item)

add_heading(doc, "附录 二 口径说明摘要", 1)
summary_rows = [
    ["订单统计单位", "销售订单数，不是商品件数，不是明细行数"],
    ["有效订单", "已完成支付、非测试、非重复且日结时未被全额取消的销售订单"],
    ["退款和取消", "是否回冲历史由业务、财务和 IT 共同确认；各日期和平台必须使用同一规则"],
    ["普通订单日期", "支付完成日期"],
    ["预售订单日期", "尾款支付完成日期，不使用定金支付日期"],
    ["派样订单", "保留并通过 is_sample 单独标识"],
    ["分仓口径", "订单最终有效分配仓，改仓需修订历史"],
    ["零单日期", "建议输出 0；如源表不出零记录，接收端按有效主数据补零"],
    ["时间和时区", "Asia Shanghai，日期 YYYY-MM-DD，时间 ISO 8601"],
    ["历史范围", "至少 24 个月，建议从 2023 年 1 月 1 日开始"],
]
add_table(doc, ["项目", "统一口径"], summary_rows, [4.0, 13.4], font_size=9.2, alignments=["center", "left"])

# Document properties
doc.core_properties.title = "单量预测系统在线数据接入需求说明"
doc.core_properties.subject = "向 IT 数据分析模块提出在线数据接口和指标口径需求"
doc.core_properties.author = "单量预测系统项目组"
doc.core_properties.keywords = "单量预测 数据接入 数据需求 指标口径"

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print(str(OUT.resolve()))
