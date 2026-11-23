# 宝贝店横向日销规划，销售GMV直接读取，独立于成人抖音纯销换算。
RULE = {
    'parser': 'douyin_baby',
    'header_row': None,
    'label_column': 'A',
    'channels': {
        '自播官旗': ['自播官旗'],
        '商城': ['商城'],
        'KOC': ['KOC'],
        'DP合计': ['DP合计'],
        '抖音达播': ['抖音达播']
    },
    'schedule_rows': {'level': 4, 'theme': 5},
    'unit_multiplier': 1
}
