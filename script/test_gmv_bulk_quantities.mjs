import assert from 'node:assert/strict'
import { parseQuantityToken, normalizeQuantity, sumQuantities } from '../frontend/src/utils/gmvQuantity.mjs'

for (const input of ['2,597\n2,597', '2,597 2,597', '2，597\t2597', '2,597\r\n2,597']) {
  assert.deepEqual(input.trim().split(/\s+/u).map(parseQuantityToken), [2597, 2597])
}
assert.equal(parseQuantityToken('1,000,000'), 1000000)
assert.equal(parseQuantityToken('0'), 0)
assert.equal(parseQuantityToken('1,000,000,000,000'), 1000000000000)
assert.equal(parseQuantityToken('—'), null)
assert.equal(parseQuantityToken('-'), null)
for (const token of ['2,59', '25,97', '2,,597', ',2597', '2597,', '-1', '1.5', '1e3', 'Infinity', '1000000000001']) {
  assert.ok(Number.isNaN(parseQuantityToken(token)), token)
}
console.log('千位分隔、空格/换行、零值、空白及非法数值检查通过')
assert.equal(sumQuantities(['2,597', null, undefined]), 2597)
assert.equal(sumQuantities(['2,597', '1,000', 100]), 3697)
assert.equal(sumQuantities(['2,598', '1,000', 100]), 3698)
assert.equal(sumQuantities([null, '', undefined]), null)
assert.equal(sumQuantities([0, null, null]), 0)
assert.ok(Number.isNaN(sumQuantities(['2,59', 1, 2])))
assert.equal(normalizeQuantity('2，597'), 2597)
