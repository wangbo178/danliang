import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applySuperAnchorForecast} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive}=createRequire(new URL('../frontend/package.json',import.meta.url))('vue')
const values=reactive({aov:{'达播-超头':{'2026-10-20':100}},orders:{'达播-超头':{'2026-10-19':9,'2026-10-20':1,'2026-11-15':99},'货架-CPS':{'2026-10-20':1461},'派样':{'2026-10-20':7631}}})
const response={status:'success',calculation_version:'daily_v2',mode:'tail_only',start_date:'2026-10-20',end_date:'2026-11-14',tail_dates:['2026-10-20','2026-10-21'],rows:[
 {date:'2026-10-19',estimated_order_qty:1},{date:'2026-10-20',estimated_order_qty:20000},{date:'2026-10-21',estimated_order_qty:null},
 {date:'2026-10-26',estimated_order_qty:123},{date:'2026-11-14',estimated_order_qty:0},{date:'2026-11-15',estimated_order_qty:1}]}
const days=response.rows.map(r=>r.date)
const result=applySuperAnchorForecast(values,response,days)
assert.equal(result.applied,1);assert.equal(result.skipped,5)
assert.equal(result.values.orders['达播-超头']['2026-10-20'],20000)
assert.equal(result.values.orders['达播-超头']['2026-10-19'],9)
assert.equal(result.values.orders['达播-超头']['2026-11-15'],99)
assert.equal(values.orders['达播-超头']['2026-10-20'],1)
assert.deepEqual(result.values.orders['货架-CPS'],values.orders['货架-CPS'])
assert.deepEqual(result.values.orders['派样'],values.orders['派样'])
assert.deepEqual(result.values.aov,values.aov)
const extended=applySuperAnchorForecast(values,{...response,mode:'through_1114'},days)
assert.equal(extended.applied,3)
assert.equal(extended.values.orders['达播-超头']['2026-11-14'],0)
assert.throws(()=>applySuperAnchorForecast(values,{...response,status:'incomplete_history'},days))
console.log('双十一超头：动态起止、尾款范围、11.14截止、零值/缺失、响应式填充和其它渠道保留通过')
