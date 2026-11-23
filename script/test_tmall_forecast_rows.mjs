import assert from 'node:assert/strict'
import {buildTmallForecastRows, forecastOrderTotal} from '../frontend/src/utils/tmallForecast.mjs'
const records = [
  {channel: '货架-常规', kind: 'day', period: '2026-11-01', amount: 259700, cell: 'H59'},
  {channel: '达播-超头', kind: 'day', period: '2026-11-01', amount: 0, cell: 'H63'},
  {channel: '货架-常规', kind: 'summary', period: 'TTL', amount: 999999},
]
const rows = buildTmallForecastRows(records, ['货架-常规', '达播-超头'], {'货架-常规': {'2026-11-01': {amount: 100}}})
assert.equal(rows.length, 7)
assert.deepEqual(rows.slice(0,3).map(row=>row.channel), ['货架-常规', '客单价', '预估单量'])
assert.equal(rows[2].values['2026-11-01'].amount, 2597)
assert.equal(rows[5].values['2026-11-01'].amount, 0)
assert.equal(forecastOrderTotal(rows, '2026-11-01'), 2597)
assert.equal(rows.some(row => row.kind === 'actual'), false)
assert.equal(rows.some(row => row.values.TTL), false)
assert.equal(rows.at(-1).channel,'派样单量')
assert.equal(rows.at(-1).values['2026-11-01'].amount,null)
const sampled = buildTmallForecastRows(records, ['货架-常规','达播-超头'], {'货架-常规':{'2026-11-01':{amount:100}}}, {'派样':{'2026-11-01':100}})
assert.equal(forecastOrderTotal(sampled,'2026-11-01'),2697)
const unknown = buildTmallForecastRows([{channel: '达播-超头', kind: 'day', period: '2025-10-01', amount: null}], ['达播-超头'], {'达播-超头': {'2025-10-01': {amount: 100}}})
assert.equal(unknown[2].values['2025-10-01'].amount, null)
assert.equal(forecastOrderTotal(unknown, '2025-10-01'), null)
for (const price of [undefined, {amount: 0}, {amount: -1}, {amount: Infinity}]) {
  const missing = buildTmallForecastRows(records, ['货架-常规'], {'货架-常规': {'2026-11-01': price}})
  assert.equal(missing[2].values['2026-11-01'].amount, null)
  assert.equal(forecastOrderTotal(missing, '2026-11-01'), null)
}
console.log('天猫预期三行结构、客单价换算、整数单量、缺失值、零目标及不含实际指标验证通过')
