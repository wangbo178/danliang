import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {orderFloorPreview, applyOrderFloor} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive} = createRequire(new URL('../frontend/package.json', import.meta.url))('vue')
const values = reactive({aov: {'货架-常规': {'2026-10-01': 100}}, orders: {'货架-常规': {'2026-10-01': 10, '2026-10-02': 50, '2026-11-01': 1}, '百补': {'2026-10-01': 1}}})
const rows = reactive([{metric:'forecast_orders', targetChannel:'货架-常规', values: {
  '2026-10-01': {amount:10}, '2026-10-02': {amount:50}, '2026-10-03': {amount:100},
  '2026-10-04': {amount:null}, '2026-10-05': {amount:NaN}, '2026-10-06': {amount:0}, '2026-11-01': {amount:1}
}}])
const preview = orderFloorPreview(rows,'2026-10',50)
assert.deepEqual(preview.below.map(r=>r.date),['2026-10-01','2026-10-06'])
assert.equal(preview.missing.length,2)
const days = Object.keys(rows[0].values)
const result = applyOrderFloor(values,rows,'2026-10',50,days)
assert.equal(result.applied,2)
assert.equal(result.values.orders['货架-常规']['2026-10-01'],50)
assert.equal(result.values.orders['货架-常规']['2026-10-06'],50)
assert.equal(result.values.orders['货架-常规']['2026-10-02'],50)
assert.equal(result.values.orders['货架-常规']['2026-11-01'],1)
assert.equal(result.values.orders['货架-常规']['2026-10-04'],undefined)
assert.deepEqual(result.values.orders['百补'],values.orders['百补'])
assert.deepEqual(result.values.aov,values.aov)
assert.equal(values.orders['货架-常规']['2026-10-01'],10)
assert.equal(orderFloorPreview(rows,'2026-10',0).below.length,0)
assert.throws(()=>orderFloorPreview(rows,'2026-10',null))
assert.throws(()=>orderFloorPreview(rows,'2026-13',50))
console.log('预估单量底线：预览、月份边界、严格低于、缺失跳过、响应式填报及原值保留通过')
