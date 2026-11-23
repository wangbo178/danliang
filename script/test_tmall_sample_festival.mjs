import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applySampleFestival} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive}=createRequire(new URL('../frontend/package.json',import.meta.url))('vue')
const values=reactive({aov:{'达播-超头':{'2026-10-05':100}},orders:{'达播-超头':{'2026-10-05':10,'2026-10-06':20},'派样':{'2026-10-05':7631}}})
const response={status:'success',statistics:{estimated_order_qty:350002},allowed_dates:['2026-10-05','2026-10-06']}
const result=applySampleFestival(values,response,'2026-10-05',response.allowed_dates)
assert.equal(result.applied,1)
assert.equal(result.values.orders['达播-超头']['2026-10-05'],350002)
assert.equal(result.values.orders['达播-超头']['2026-10-06'],20)
assert.equal(values.orders['达播-超头']['2026-10-05'],10)
assert.deepEqual(result.values.orders['派样'],values.orders['派样'])
assert.deepEqual(result.values.aov,values.aov)
assert.throws(()=>applySampleFestival(values,response,'2026-11-01',response.allowed_dates))
assert.throws(()=>applySampleFestival(values,{...response,status:'no_samples'},'2026-10-05',response.allowed_dates))
assert.throws(()=>applySampleFestival(values,response,'',response.allowed_dates))
console.log('小样节：手选单日期、响应式填入、边界拦截、其它日期渠道保留及缺少历史拒绝验证通过')
