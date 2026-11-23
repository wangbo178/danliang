import assert from 'node:assert/strict'
import { readFile, writeFile, unlink } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { performance } from 'node:perf_hooks'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const {parse, compileScript} = require('@vue/compiler-sfc')
const {createSSRApp, h} = require('vue')
const {renderToString} = require('@vue/server-renderer')
const source = await readFile(new URL('../frontend/src/components/GMVTargetTable.vue', import.meta.url), 'utf8')
const {descriptor} = parse(source)
const compiled = compileScript(descriptor, {id: 'gmv-table-test', inlineTemplate: true})
const temporary = new URL('../frontend/.gmv-native-test.mjs', import.meta.url)
try {
  await writeFile(temporary, compiled.content.replace("'../utils/gmvTrend.mjs'", "'./src/utils/gmvTrend.mjs'"))
  const Table = (await import(temporary.href + '?test=' + Date.now())).default
  const periods = Array.from({length: 61}, (_, i) => `day-${i}`)
  const rows = Array.from({length: 25}, (_, index) => ({channel: `channel-${index}`, kind: index % 5 === 0 ? 'target' : 'actual',
    values: Object.fromEntries(periods.map(period => [period, {amount: index * 1000, evidence: '原始坐标'}]))}))
  const summaries = [h('div', '合计'), ...periods.map(() => h('div', '10,000'))]
  const start = performance.now()
  const html = await renderToString(createSSRApp({render: () => h(Table, {rows, periods, summaries, format: (_, value) => String(value), evidence: value => value.evidence})}))
  assert.equal((html.match(/<tbody>/g) || []).length, 1)
  assert.equal((html.match(/title="原始坐标"/g) || []).length, 25 * 61)
  assert.ok(html.includes('合计'))
  assert.ok(html.includes('day-60'))
  assert.ok(html.includes('target-row'))
  assert.ok(html.includes('actual-row'))
  assert.equal(html.includes('el-tooltip'), false)
  const trendPeriods = ['2026-11-01', '2026-11-02']
  const trendRows = [
    {channel: '预期增长', kind: 'target', values: {'2026-11-01': {amount: 100}, '2026-11-02': {amount: 120}}},
    {channel: '预期下降', kind: 'target', values: {'2026-11-01': {amount: 100}, '2026-11-02': {amount: 60}}},
    {channel: '实际GMV', kind: 'actual', values: {'2026-11-01': {amount: 100}, '2026-11-02': {amount: 150}}},
    {channel: '客单价', kind: 'target', metric: 'forecast_aov', values: {'2026-11-01': {amount: 100}, '2026-11-02': {amount: 200}}},
  ]
  const trendHtml = await renderToString(createSSRApp({render: () => h(Table, {rows: trendRows, periods: trendPeriods, summaries: [], format: (_, value) => String(value), evidence: () => '', showTrend: true})}))
  assert.ok(trendHtml.includes('strong-up'))
  assert.ok(trendHtml.includes('strong-down'))
  assert.ok(trendHtml.includes('↑ 20.0%'))
  assert.ok(trendHtml.includes('↓ 40.0%'))
  assert.equal(trendHtml.includes('↑ 50.0%'), false)
  assert.equal(trendHtml.includes('↑ 100.0%'), false)
  const editableRows = [
    {channel: '货架', kind: 'target', values: {'2026-11-01': {amount: 10000}}},
    {channel: '客单价', targetChannel: '货架', metric: 'forecast_aov', kind: 'target', values: {'2026-11-01': {amount: 100}}},
    {channel: '预估单量', targetChannel: '货架', metric: 'forecast_orders', kind: 'target', values: {'2026-11-01': {amount: 100, manual: false}}},
  ]
  const editableHtml = await renderToString(createSSRApp({render: () => h(Table, {rows: editableRows, periods: ['2026-11-01'], summaries: [], format: (_, value) => String(value), evidence: () => '', editable: true, editValue: row => row.metric === 'forecast_aov' ? '100' : ''})}))
  assert.equal((editableHtml.match(/<input/g) || []).length, 2)
  assert.ok(editableHtml.includes('货架 2026-11-01 客单价'))
  assert.ok(editableHtml.includes('自动 100'))
  assert.ok(editableHtml.includes('自动计算'))
  console.log(`1525个单元格、全部日期、预期/实际与汇总渲染通过，SSR ${Math.round(performance.now()-start)}ms（非浏览器耗时）`)
} finally { await unlink(temporary).catch(() => {}) }
