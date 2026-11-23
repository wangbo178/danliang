import assert from 'node:assert/strict'
import {gmvDayTrend} from '../frontend/src/utils/gmvTrend.mjs'
const days = ['2026-10-31', '2026-11-01']
assert.deepEqual(gmvDayTrend(100, 120, ...days).direction, 'up')
assert.equal(gmvDayTrend(100, 120, ...days).strong, true)
assert.equal(gmvDayTrend(100, 119, ...days).strong, false)
assert.equal(gmvDayTrend(100, 80, ...days).strong, true)
assert.equal(gmvDayTrend(100, 80, ...days).label, '↓ 20.0%')
assert.equal(gmvDayTrend(100, 100, ...days).label, '持平')
assert.equal(gmvDayTrend(0, 100, ...days).label, '↑ 从0增长')
assert.equal(gmvDayTrend(100, 0, ...days).label, '↓ 100.0%')
assert.equal(gmvDayTrend(0, 0, ...days).label, '持平')
assert.equal(gmvDayTrend(100000, 100001, ...days).label, '↑ <0.1%')
for (const previous of [null, undefined, NaN, -1]) assert.equal(gmvDayTrend(previous, 100, ...days), null)
assert.equal(gmvDayTrend(100, 120, '10月ttl', '11月ttl'), null)
assert.equal(gmvDayTrend(100, 120, '2026-10-30', '2026-11-01'), null)
console.log('日环比涨跌、大幅波动阈值、跨月、零基数、缺失和非相邻日期验证通过')
