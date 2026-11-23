import assert from 'node:assert/strict'
import {buildChannelForecastRows,forecastTotals,applyForecastBulk,priceRecords,orderRecords} from '../frontend/src/utils/tmallForecast.mjs'
const channels=['自播官旗','商城','KOC','DP合计','抖音达播'],days=['2026-10-01','2026-10-02']
const records=channels.flatMap(channel=>days.map(period=>({channel,kind:'day',period,amount:10000})))
const values={aov:Object.fromEntries(channels.map(c=>[c,Object.fromEntries(days.map(day=>[day,100]))])),orders:Object.fromEntries(channels.map(c=>[c,{}]))}
const rows=buildChannelForecastRows(records,channels,priceRecords(values),orderRecords(values))
assert.equal(rows.length,16)
assert.equal(rows.at(-1).channel,'PDD单量')
for(let i=0;i<channels.length;i++)assert.deepEqual(rows.slice(i*3,i*3+3).map(r=>r.channel),[channels[i],'预估客单','预估单量'])
assert.equal(rows.some(r=>['派样','抖音精选联盟订单'].includes(r.targetChannel)),false)
assert.equal(rows.some(r=>r.channel==='达播汇总单量'),false)
assert.deepEqual(forecastTotals(rows,days),{gmv:100000,orders:1000})
const bulk=applyForecastBulk(values,rows,{channels,metric:'orders',dates:days,operation:'set',text:'2,597'})
assert.deepEqual(forecastTotals(buildChannelForecastRows(records,channels,priceRecords(bulk),orderRecords(bulk)),days),{gmv:100000,orders:25970})
assert.equal(values.orders['自播官旗'][days[0]],undefined)
const pdd=applyForecastBulk(bulk,rows,{channels:['PDD'],metric:'orders',dates:days,operation:'set',text:'1,234'})
assert.deepEqual(forecastTotals(buildChannelForecastRows(records,channels,priceRecords(pdd),orderRecords(pdd)),days),{gmv:100000,orders:28438})
assert.equal(pdd.orders['抖音达播'][days[0]],2597)
console.log('宝贝五渠道预估客单/单量、批量填写、合计与成人店隔离验证通过')
