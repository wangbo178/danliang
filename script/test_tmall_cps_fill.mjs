import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applyCpsMonthlyAverages} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive} = createRequire(new URL('../frontend/package.json', import.meta.url))('vue')
const values = reactive({aov:{'货架-CPS':{'2026-10-01':100}},orders:{'货架-CPS':{'2026-10-01':999,'2026-10-03':777,'2026-12-01':12},'货架-常规':{'2026-10-01':888}}})
const rows=[{month:'2026-10',estimated_order_qty:1461},{month:'2026-11',estimated_order_qty:4166},{month:'2026-12',estimated_order_qty:null},{month:'2027-01',estimated_order_qty:0}]
const result=applyCpsMonthlyAverages(values,rows,['2026-10-01','2026-10-02','2026-11-01','2026-12-01','2027-01-01'])
assert.equal(result.applied,4)
assert.equal(result.skipped,1)
assert.equal(result.values.orders['货架-CPS']['2026-10-01'],1461)
assert.equal(result.values.orders['货架-CPS']['2026-10-02'],1461)
assert.equal(result.values.orders['货架-CPS']['2026-10-03'],777)
assert.equal(result.values.orders['货架-CPS']['2026-11-01'],4166)
assert.equal(result.values.orders['货架-CPS']['2026-12-01'],12)
assert.equal(result.values.orders['货架-CPS']['2027-01-01'],0)
assert.equal(values.orders['货架-CPS']['2026-10-01'],999)
assert.deepEqual(result.values.aov,values.aov)
assert.deepEqual(result.values.orders['货架-常规'],values.orders['货架-常规'])
console.log('CPS批量填充：跨月均值、日期范围、缺失跳过、有效0值、响应式表单及其它填报保留通过')
