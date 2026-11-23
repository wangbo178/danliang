# 规则代码使用受限 Python 字面量；不执行 import、函数或任意系统代码。
RULE = {
    'section': '渠道目标-销售（备案版）',
    'end_section': '渠道目标-GMV',
    'channels': {
        '货架-常规': ['货架-常规', '货架-常规GMV'],
        '货架-CPS': ['货架-CPS'],
        '百补': ['百补'],
        '达播-超头': ['达播-超头'],
        '达播-二梯队': ['达播-二梯队', '达播二梯队']
    },
    'schedule_labels': ['官方节奏', '店铺节奏'],
    'header_row': None,
    'row_overrides': {},
    'blank_policy': 'preserve',
    'unit_multiplier': 1
}
