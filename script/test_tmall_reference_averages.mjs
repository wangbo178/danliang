import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {applyReferenceAverages} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive} = createRequire(new URL('../frontend/package.json', import.meta.url))('vue')
const initial = {aov: {'货架-常规': {'2026-10-01': 100}}, orders: {'货架-常规': {'2026-10-01': 999, '2026-10-02': 88}, '百补': {'2026-10-01': 12}}}
const result = applyReferenceAverages(initial, [
 {date:'2026-10-01',mean_order_qty:2597.5}, {date:'2026-10-02',mean_order_qty:null},
 {date:'2026-10-03',mean_order_qty:0}, {date:'2026-10-04',mean_order_qty:NaN},
 {date:'2027-01-01',mean_order_qty:123}], ['2026-10-01','2026-10-02','2026-10-03','2026-10-04'])
assert.equal(result.applied,2); assert.equal(result.skipped,3)
assert.equal(result.values.orders['货架-常规']['2026-10-01'],2598)
assert.equal(result.values.orders['货架-常规']['2026-10-02'],88)
assert.equal(result.values.orders['货架-常规']['2026-10-03'],0)
assert.equal(initial.orders['货架-常规']['2026-10-01'],999)
assert.deepEqual(result.values.aov,initial.aov)
assert.deepEqual(result.values.orders['百补'],initial.orders['百补'])
const reactiveValues = reactive(initial)
const reactiveResult = applyReferenceAverages(reactiveValues, [{date:'2026-10-01',mean_order_qty:2597.5}], ['2026-10-01'])
assert.equal(reactiveResult.values.orders['货架-常规']['2026-10-01'],2598)
assert.equal(reactiveValues.orders['货架-常规']['2026-10-01'],999)
reactiveResult.values.aov['货架-常规']['2026-10-01'] = 200
assert.equal(reactiveValues.aov['货架-常规']['2026-10-01'],100)
const douyin = {aov:{'抖音自播':{'2026-11-01':50}}, orders:{'抖音自播':{'2026-11-01':99,'2026-11-02':88},'抖音商城':{'2026-11-01':123}}}
const dyResult = applyReferenceAverages(douyin, [{date:'2026-11-01',mean_order_qty:100.5},{date:'2026-11-02',mean_order_qty:null}], ['2026-11-01','2026-11-02'], '抖音自播')
assert.equal(dyResult.values.orders['抖音自播']['2026-11-01'],101)
assert.equal(dyResult.values.orders['抖音自播']['2026-11-02'],88)
assert.deepEqual(dyResult.values.orders['抖音商城'],douyin.orders['抖音商城'])
assert.deepEqual(dyResult.values.aov,douyin.aov)
assert.equal(douyin.orders['抖音自播']['2026-11-01'],99)
assert.throws(()=>applyReferenceAverages(douyin,[],[], '抖音商城'))
assert.throws(()=>applyReferenceAverages(douyin,[],[], '货架-常规'))
console.log('平均单量填入：整数舍入、跳过缺失、有效零值、日期边界及其它填报保留通过')
