import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applySecondAnchorForecast,applySuperAnchorForecast} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive}=createRequire(new URL('../frontend/package.json',import.meta.url))('vue')
const values=reactive({aov:{'达播-二梯队':{'2026-10-20':100}},orders:{'达播-二梯队':{'2026-10-20':1,'2026-11-15':99},'达播-超头':{'2026-10-20':554531},'派样':{'2026-10-20':7631}}})
const response={channel:'达播-二梯队',calculation_version:'daily_v2',status:'success',mode:'tail_only',start_date:'2026-10-20',end_date:'2026-11-14',tail_dates:['2026-10-20','2026-10-21'],rows:[{date:'2026-10-20',estimated_order_qty:45312},{date:'2026-10-21',estimated_order_qty:null},{date:'2026-11-15',estimated_order_qty:10}]}
const result=applySecondAnchorForecast(values,response,['2026-10-20','2026-10-21','2026-11-15'])
assert.equal(result.applied,1);assert.equal(result.skipped,2)
assert.equal(result.values.orders['达播-二梯队']['2026-10-20'],45312)
assert.equal(result.values.orders['达播-二梯队']['2026-11-15'],99)
assert.equal(values.orders['达播-二梯队']['2026-10-20'],1)
assert.deepEqual(result.values.orders['达播-超头'],values.orders['达播-超头'])
assert.deepEqual(result.values.orders['派样'],values.orders['派样'])
assert.deepEqual(result.values.aov,values.aov)
assert.throws(()=>applySuperAnchorForecast(values,response,['2026-10-20']))
console.log('二达填充：独立目标渠道、响应式表单、缺失跳过、截止日期及超头/其它渠道保留通过')
