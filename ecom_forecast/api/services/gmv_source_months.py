"""从规划名称提取数字月份；年份不能当作月份。"""
import re


def infer_months(name):
    name = re.sub(r'\s+', '', str(name or ''))
    result = set()
    for group in re.findall(r'(?<!\d)(\d{1,2}(?:月?[+＋、,，/&和至到\-–—~～]\d{1,2})*)月', name):
        parts = re.split(r'[+＋、,，/&和]', group.replace('月',''))
        for part in parts:
            bounds = re.split(r'[至到\-–—~～]', part)
            numbers = [int(n) for n in bounds]
            if any(not 1 <= n <= 12 for n in numbers) or len(numbers) > 2:
                return []
            if len(numbers) == 2:
                if numbers[0] > numbers[1]:
                    return []
                result.update(range(numbers[0],numbers[1]+1))
            else:
                result.add(numbers[0])
    return sorted(result)


def resolve_months(source):
    months = source.get('months')
    if months is None:
        months = infer_months(source.get('name'))
    if not isinstance(months,list) or not months or any(type(n) is not int or not 1 <= n <= 12 for n in months):
        raise ValueError('请选择1—12的数字月份，可多选')
    return sorted(set(months))
