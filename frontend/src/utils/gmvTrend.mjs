export function gmvDayTrend(previous, current, previousDate, currentDate, threshold = 0.2) {
  if (![previous, current].every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0)) return null
  if (!/^\d{4}-\d{2}-\d{2}$/.test(previousDate || '') || !/^\d{4}-\d{2}-\d{2}$/.test(currentDate || '')) return null
  if (Date.parse(`${currentDate}T00:00:00Z`) - Date.parse(`${previousDate}T00:00:00Z`) !== 86400000) return null
  if (current === previous) return {direction: 'flat', strong: false, label: '持平', title: `相较${previousDate}的预期GMV持平`}
  const direction = current > previous ? 'up' : 'down'
  if (previous === 0) return {direction, strong: true, label: '↑ 从0增长', title: `相较${previousDate}从0增长至${current}元，基数为0不计算百分比`}
  const rate = Math.abs((current - previous) / previous)
  const percentage = rate < 0.001 ? '<0.1%' : `${(rate * 100).toFixed(1)}%`
  return {direction, strong: rate >= threshold, label: `${direction === 'up' ? '↑' : '↓'} ${percentage}`,
    title: `相较${previousDate}的预期GMV${direction === 'up' ? '增长' : '下降'}${percentage}（前一天${previous}元），涨跌达到${threshold * 100}%高亮金额`}
}
