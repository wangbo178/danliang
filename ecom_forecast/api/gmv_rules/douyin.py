# 抖音日期纵向表；金额按来源口径读取，达人排期按实际表头映射。
RULE = {
    'parser': 'douyin',
    'header_row': None,
    'date_column': 'A',
    'channels': {
        '抖音自播': ['抖音自播'],
        '抖音商城': ['商城汇总'],
        '抖音达播': ['抖音达播']
    },
    'gmv_divisor': 0.7,
    'schedule_columns': {'date': 'M', 'type': 'L', 'anchor': 'K', 'gmv_wan': 'N'}
}
