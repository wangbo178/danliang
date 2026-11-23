import assert from 'node:assert/strict'
import {applyBabyAovForecast,buildChannelForecastRows,priceRecords,orderRecords,forecastOrderTotal} from '../frontend/src/utils/tmallForecast.mjs'
const day='2026-10-01',channels=['自播官旗','商城','KOC','DP合计','抖音达播']
const values={aov:Object.fromEntries(channels.map(c=>[c,{[day]:10}])),orders:Object.fromEntries(channels.map(c=>[c,{[day]:99}]))}
const forecasts=channels.map(channel=>({date:day,channel,amount:100,aov:91}))
const result=applyBabyAovForecast(values,{forecasts},[day])
assert.equal(result.applied,4)
assert.equal(result.values.orders['抖音达播'][day],99)
assert.equal(result.values.aov['抖音达播'][day],10)
assert.equal(result.values.aov['自播官旗'][day],91)
assert.equal(result.values.orders['自播官旗'][day],100)
assert.equal(values.orders['自播官旗'][day],99)
const rows=buildChannelForecastRows(channels.map(channel=>({channel,kind:'day',period:day,amount:9100})),channels,priceRecords(result.values),orderRecords(result.values))
assert.equal(forecastOrderTotal(rows,day),499)
const missing=applyBabyAovForecast(values,{forecasts:[{date:day,channel:'商城',amount:null,aov:null}]},[day])
assert.equal(missing.skipped,1);assert.equal(missing.values.orders['商城'][day],99)
console.log('宝贝按月客单填充、达播保持原值、缺失跳过及单量合计验证通过')
