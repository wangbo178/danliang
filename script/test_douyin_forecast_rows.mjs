import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {buildChannelForecastRows,forecastTotals,applyForecastBulk,priceRecords,orderRecords} from '../frontend/src/utils/tmallForecast.mjs'
const {reactive,computed}=createRequire(new URL('../frontend/package.json',import.meta.url))('vue')
const channels=['抖音自播','抖音商城','抖音达播'],days=['2026-10-01','2026-10-02']
const records=channels.flatMap(channel=>days.map(period=>({channel,kind:'day',period,amount:10000})))
const values=reactive({aov:Object.fromEntries(channels.map(c=>[c,{}])),orders:Object.fromEntries(channels.map(c=>[c,{}]))})
const rows=computed(()=>buildChannelForecastRows(records,channels,priceRecords(values),orderRecords(values)))
assert.equal(rows.value.length,11)
for(let i=0;i<2;i++)assert.deepEqual(rows.value.slice(i*3,i*3+3).map(r=>r.channel),[channels[i],'预估客单','预估单量'])
assert.deepEqual(rows.value.slice(6).map(r=>r.channel),['抖音达播','抖音精选联盟订单','达播单量','达播汇总单量','派样单量'])
assert.equal(rows.value.find(r=>r.channel==='达播汇总单量').editable,false)
assert.equal(rows.value.some(r=>r.targetChannel==='派样'),true)
values.aov['抖音自播'][days[0]]=100
assert.equal(rows.value[2].values[days[0]].amount,100)
values.orders['抖音自播'][days[0]]=123
values.aov['抖音自播'][days[0]]=200
assert.equal(rows.value[2].values[days[0]].amount,123)
values.orders['抖音自播'][days[0]]=null
assert.equal(rows.value[2].values[days[0]].amount,50)
const bulk=applyForecastBulk(values,rows.value,{channels,metric:'orders',dates:days,operation:'set',text:'2,597'})
const filled=buildChannelForecastRows(records,channels,priceRecords(bulk),orderRecords(bulk))
assert.deepEqual(forecastTotals(filled,days),{gmv:60000,orders:15582})
values.orders['抖音精选联盟订单']={[days[0]]:100,[days[1]]:null}
values.orders['抖音达播'][days[0]]=200
assert.equal(rows.value.find(r=>r.channel==='达播汇总单量').values[days[0]].amount,300)
const allBulk=applyForecastBulk(values,rows.value,{channels:[...channels,'抖音精选联盟订单'],metric:'orders',dates:days,operation:'set',text:'2,597'})
assert.deepEqual(forecastTotals(buildChannelForecastRows(records,channels,priceRecords(allBulk),orderRecords(allBulk)),days),{gmv:60000,orders:20776})
assert.equal(rows.value.filter(r=>r.metric==='forecast_aov'&&r.targetChannel==='抖音达播').length,0)
console.log('抖音达播拆分、独立填写、自动汇总及总计不重复验证通过')

values.orders['派样']={[days[0]]:1000,[days[1]]:0}
assert.equal(rows.value.find(r=>r.channel==='派样单量').values[days[0]].amount,1000)
assert.equal(rows.value.find(r=>r.channel==='达播汇总单量').values[days[0]].amount,300)
assert.equal(forecastTotals(rows.value,[days[0]]).orders,1350)
const sampled=applyForecastBulk(values,rows.value,{channels:['派样'],metric:'orders',dates:days,operation:'set',text:'2,597'})
assert.equal(sampled.orders['派样'][days[0]],2597)
assert.equal(sampled.orders['抖音达播'][days[0]],200)
