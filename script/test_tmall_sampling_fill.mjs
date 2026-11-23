import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applySamplingMonthlyAverages,buildTmallForecastRows,forecastOrderTotal,orderRecords} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive} = createRequire(new URL('../frontend/package.json', import.meta.url))('vue')
const values=reactive({aov:{},orders:{'派样':{'2026-10-01':5,'2026-10-03':6},'货架-CPS':{'2026-10-01':1461}}})
const result=applySamplingMonthlyAverages(values,[{month:'2026-10',estimated_order_qty:7631},{month:'2026-11',estimated_order_qty:11926},{month:'2026-12',estimated_order_qty:null}],['2026-10-01','2026-10-02','2026-11-01','2026-12-01'])
assert.equal(result.applied,3);assert.equal(result.skipped,1)
assert.equal(result.values.orders['派样']['2026-10-01'],7631)
assert.equal(result.values.orders['派样']['2026-10-03'],6)
assert.equal(result.values.orders['派样']['2026-11-01'],11926)
assert.deepEqual(result.values.orders['货架-CPS'],values.orders['货架-CPS'])
assert.equal(values.orders['派样']['2026-10-01'],5)
const rows=buildTmallForecastRows([{kind:'day',channel:'货架-CPS',period:'2026-10-01',amount:0}],['货架-CPS'],{},orderRecords(result.values))
assert.equal(rows.filter(r=>r.targetChannel==='派样').length,1)
assert.equal(rows.at(-1).values['2026-10-01'].amount,7631)
assert.equal(forecastOrderTotal(rows,'2026-10-01'),9092)
console.log('派样独立行、跨月填充、范围限制、响应式表单、CPS保留及单量合计验证通过')
