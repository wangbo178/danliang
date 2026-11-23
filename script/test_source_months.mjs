import assert from 'node:assert/strict'
import {inferSourceMonths} from '../frontend/src/utils/gmvSourceMonths.mjs'
for(const [name,months] of [['26年5+6月份',[5,6]],['2026年10+11月（抖音调整版）',[10,11]],['25年10+11月份',[10,11]],['26年9月',[9]],['7-8月',[7,8]],['1至3月',[1,2,3]],['5月、6月',[5,6]],['2026年',[]],['13月',[]],['12-2月',[]]])assert.deepEqual(inferSourceMonths(name),months)
console.log('规划名称自动提取数字月份、跨月范围及年份隔离验证通过')
