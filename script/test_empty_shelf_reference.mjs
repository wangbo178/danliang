import assert from 'node:assert/strict'
import {applyEmptyShelfReference,buildTmallForecastRows,priceRecords,orderRecords} from '../frontend/src/utils/tmallForecast.mjs'
const days=['2026-10-01','2026-10-02','2026-10-03','2026-10-04']
const values={aov:{'货架-常规':{'2026-10-03':10}},orders:{'货架-常规':{'2026-10-01':null,'2026-10-02':0,'2026-10-03':null,'2026-10-04':25},'货架-CPS':{'2026-10-01':99}}}
const records=days.map(period=>({period,channel:'货架-常规',kind:'day',amount:100}))
const rows=buildTmallForecastRows(records,['货架-常规'],priceRecords(values),orderRecords(values))
const response={channel:'货架-常规',rows:days.map((date,index)=>({date,mean_order_qty:999,estimated_order_qty:150+index}))}
const result=applyEmptyShelfReference(values,rows,response,days)
assert.equal(result.applied,2);assert.equal(result.skipped,2)
assert.equal(result.values.orders['货架-常规'][days[0]],150)
assert.equal(result.values.orders['货架-常规'][days[1]],151)
assert.equal(result.values.orders['货架-常规'][days[2]],null) // 自动算出的10单属于已有非零值，不能覆盖。
assert.equal(result.values.orders['货架-常规'][days[3]],25)
assert.deepEqual(result.values.aov,values.aov)
assert.deepEqual(result.values.orders['货架-CPS'],values.orders['货架-CPS'])
assert.equal(values.orders['货架-常规'][days[0]],null)
const missing=applyEmptyShelfReference(values,rows,{channel:'货架-常规',rows:[{date:days[0],estimated_order_qty:null}]},days)
assert.equal(missing.values.orders['货架-常规'][days[0]],null)
assert.throws(()=>applyEmptyShelfReference(values,[],response,days))
console.log('历史参考二：空值零值填充、自动计算非零值保护、原值及其他渠道保留通过')
