import assert from 'node:assert/strict'
import {applyAllianceForecast,buildChannelForecastRows,priceRecords,orderRecords,forecastOrderTotal} from '../frontend/src/utils/tmallForecast.mjs'
const day='2026-11-01', other='2026-11-02'
const values={aov:{'抖音自播':{[day]:100}},orders:{'抖音自播':{[day]:20},'抖音商城':{[day]:30},'抖音达播':{[day]:40},'抖音精选联盟订单':{[day]:99,[other]:88}}}
const result=applyAllianceForecast(values,{channel:'抖音精选联盟订单',forecasts:[{date:day,amount:100},{date:other,amount:null},{date:'2027-01-01',amount:999}]},[day,other])
assert.equal(result.applied,1); assert.equal(result.skipped,1)
assert.equal(result.values.orders['抖音精选联盟订单'][day],100)
assert.equal(result.values.orders['抖音精选联盟订单'][other],88)
assert.equal(values.orders['抖音精选联盟订单'][day],99)
assert.deepEqual(result.values.aov,values.aov)
assert.deepEqual(result.values.orders['抖音达播'],values.orders['抖音达播'])
const channels=['抖音自播','抖音商城','抖音达播']
const rows=buildChannelForecastRows(channels.map(channel=>({kind:'day',channel,period:day,amount:10000})),channels,priceRecords(result.values),orderRecords(result.values))
assert.equal(rows.find(r=>r.channel==='达播汇总单量').values[day].amount,140)
assert.equal(forecastOrderTotal(rows,day),190)
assert.throws(()=>applyAllianceForecast(values,{channel:'抖音自播'},[day]))
console.log('精选联盟填充、跳过缺失、日期范围、原填报保留及达播合计验证通过')
