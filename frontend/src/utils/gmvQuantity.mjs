// 千位逗号属于一个数值，空格、换行和制表符由调用方分隔日期。
export function parseQuantityToken(token) {
  if (['-', '—'].includes(token)) return null
  if (!/^(?:\d+|\d{1,3}(?:[,，]\d{3})+)$/.test(token)) return NaN
  const value = Number(token.replace(/[,，]/g, ''))
  return Number.isSafeInteger(value) && value <= 1000000000000 ? value : NaN
}

export function normalizeQuantity(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return Number.isSafeInteger(value) && value >= 0 && value <= 1000000000000 ? value : NaN
  return typeof value === 'string' ? parseQuantityToken(value.trim()) : NaN
}

export function sumQuantities(values) {
  const numbers = values.map(normalizeQuantity)
  if (numbers.some(Number.isNaN)) return NaN
  const filled = numbers.filter(value => value != null)
  return filled.length ? filled.reduce((total, value) => total + value, 0) : null
}
