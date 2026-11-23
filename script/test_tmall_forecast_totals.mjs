import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {buildTmallForecastRows,forecastGmvTotal,forecastOrderTotal,forecastTotals,orderRecords,priceRecords} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive,computed}=createRequire(new URL('../frontend/package.json',import.meta.url))('vue')
const dates=['2026-10-01','2026-10-02']
const records=[{channel:'货架-常规',kind:'day',period:dates[0],amount:10000.1},{channel:'达播-超头',kind:'day',period:dates[0],amount:null},{channel:'货架-常规',kind:'day',period:dates[1],amount:20000.2},{channel:'达播-超头',kind:'day',period:dates[1],amount:0}]
const values=reactive({aov:{'货架-常规':{[dates[0]]:100}},orders:{'货架-常规':{},'达播-超头':{},'派样':{[dates[0]]:10}}})
const rows=computed(()=>buildTmallForecastRows(records,['货架-常规','达播-超头'],priceRecords(values),orderRecords(values)))
const totals=computed(()=>forecastTotals(rows.value,dates))
assert.equal(forecastGmvTotal(rows.value,dates[0]),10000.1)
assert.equal(forecastOrderTotal(rows.value,dates[0]),110)
assert.equal(forecastOrderTotal(rows.value,dates[1]),0)
assert.deepEqual(totals.value,{gmv:30000.3,orders:110})
values.orders['达播-超头'][dates[0]]=500
assert.equal(totals.value.orders,610)
values.orders['货架-常规'][dates[1]]=200
assert.equal(totals.value.orders,810)
assert.deepEqual(forecastTotals(rows.value,[dates[0],dates[0]]),{gmv:10000.1,orders:610})
assert.deepEqual(forecastTotals(rows.value,[]),{gmv:null,orders:null})
const actual={kind:'actual',values:{[dates[0]]:{amount:999999}}}
const aov={kind:'target',metric:'forecast_aov',values:{[dates[0]]:{amount:999}}}
assert.equal(forecastGmvTotal([...rows.value,actual,aov],dates[0]),10000.1)
assert.equal(forecastGmvTotal([{kind:'target',values:{[dates[0]]:{amount:null}}}],dates[0]),null)
assert.equal(forecastOrderTotal([{kind:'target',metric:'forecast_orders',values:{[dates[0]]:{amount:null}}}],dates[0]),null)
const fractional=[{kind:'target',values:{[dates[0]]:{amount:0.004},[dates[1]]:{amount:0.004}}}]
assert.equal(forecastTotals(fractional,dates).gmv,0.01)
console.log('合计：跨渠道/日期/区间、缺失跳过、有效0、派样、排除客单价和实际行、原值精度及编辑实时重算通过')
