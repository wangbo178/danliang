// 客单价由调用方提供，不能以实际达成或其他渠道数值静默代替预期口径。
export function buildTmallForecastRows(records, channels, prices = {}, orderOverrides = {}) {
  return buildChannelForecastRows(records, channels, prices, orderOverrides, {includeSampling: true, aovLabel: '客单价'})
}

export function buildChannelForecastRows(records, channels, prices = {}, orderOverrides = {}, {includeSampling = false, aovLabel = '预估客单', splitDouyinAnchors = channels.includes('抖音自播') && channels.includes('抖音商城')} = {}) {
  const rows = channels.flatMap(channel => {
    const targets = records.filter(record => record.kind === 'day' && record.channel === channel)
    const gmv = {channel, kind: 'target', values: Object.fromEntries(targets.map(record => [record.period, record]))}
    if (channel === '抖音达播' && splitDouyinAnchors) {
      const components = [['抖音精选联盟订单', '抖音精选联盟订单'], ['抖音达播', '达播单量']].map(([key, label]) => ({
        channel: label, targetChannel: key, kind: 'target', metric: 'forecast_orders', excludeFromOrderTotal: true,
        values: Object.fromEntries(targets.map(record => {
          const raw = orderOverrides[key]?.[record.period]
          const amount = Number.isSafeInteger(raw) && raw >= 0 ? raw : null
          return [record.period, {amount, manual: amount != null, evidence: '独立填报单量，计入达播汇总单量'}]
        }))
      }))
      const total = {channel: '达播汇总单量', targetChannel: channel, kind: 'target', metric: 'forecast_orders', editable: false,
        values: Object.fromEntries(targets.map(record => [record.period, {
          amount: sumKnown(components.map(row => row.values[record.period]?.amount)),
          evidence: '抖音精选联盟订单＋达播单量；未填写项不参与统计，底部总单量只计入此汇总一次'
        }]))}
      return [gmv, ...components, total]
    }
    const aov = {channel: aovLabel, targetChannel: channel, kind: 'target', metric: 'forecast_aov', values: {}}
    const orders = {channel: '预估单量', targetChannel: channel, kind: 'target', metric: 'forecast_orders', values: {}}
    for (const record of targets) {
      const price = prices[channel]?.[record.period]
      const value = typeof price?.amount === 'number' && price.amount > 0 && Number.isFinite(price.amount) ? price.amount : null
      aov.values[record.period] = {amount: value, evidence: price?.evidence || '尚未设置该渠道当天的预期客单价'}
      const override = orderOverrides[channel]?.[record.period]
      const manual = typeof override === 'number' && Number.isSafeInteger(override) && override >= 0
      const quantity = manual ? override : record.amount == null ? null : record.amount === 0 ? 0 : value != null ? Math.round(record.amount / value) : null
      orders.values[record.period] = {amount: quantity, manual, evidence: manual ? '手动填写的预估单量，优先于GMV/客单价计算值；清空手填值可恢复自动计算' : record.amount == null ? '原表未提供预期GMV，无法计算预估单量' : quantity == null ? '客单价缺失，无法计算预估单量' : record.amount === 0 ? '预期GMV为0，预估单量为0' : `预期GMV ${record.amount}元 ÷ 客单价 ${value}元；四舍五入到整数单`}
    }
    return [gmv, aov, orders]
  })
  const days = [...new Set(records.filter(record => record.kind === 'day').map(record => record.period))].sort()
  if (includeSampling || splitDouyinAnchors) rows.push({channel: '派样单量', targetChannel: '派样', kind: 'target', metric: 'forecast_orders', values: Object.fromEntries(days.map(day => {
    const raw = orderOverrides['派样']?.[day]
    const amount = Number.isSafeInteger(raw) && raw >= 0 ? raw : null
    return [day, {amount, manual: amount != null, evidence: includeSampling ? '独立填报的派样单量；可按上月派样平均单量一键填充' : '独立填报的派样单量，计入底部预估单量合计'}]
  }))})
  if (channels.includes('自播官旗') && channels.includes('DP合计')) rows.push({channel: 'PDD单量', targetChannel: 'PDD', kind: 'target', metric: 'forecast_orders', values: Object.fromEntries(days.map(day => {
    const raw = orderOverrides.PDD?.[day]
    const amount = Number.isSafeInteger(raw) && raw >= 0 ? raw : null
    return [day, {amount, manual: amount != null, evidence: '独立填报的PDD单量，计入底部预估单量合计'}]
  }))})
  return rows
}

export function forecastNumber(value, metric) {
  if (value == null || (typeof value === 'string' && ['', '-', '—'].includes(value.trim()))) return null
  const text = String(value).trim()
  if (!/^(?:\d+|\d{1,3}(?:[,，]\d{3})+)(?:\.\d{1,2})?$/.test(text)) return NaN
  const number = Number(text.replace(/[,，]/g, ''))
  if (!Number.isFinite(number) || number > 1000000000000 || (metric === 'aov' ? number <= 0 : !Number.isSafeInteger(number))) return NaN
  return number
}

export function priceRecords(values) {
  return Object.fromEntries(Object.entries(values.aov || {}).map(([channel, dates]) => [channel,
    Object.fromEntries(Object.entries(dates).map(([day, raw]) => [day, {amount: forecastNumber(raw, 'aov'), evidence: '手动填写的预期客单价'}]))]))
}

export function orderRecords(values) {
  return Object.fromEntries(Object.entries(values.orders || {}).map(([channel, dates]) => [channel,
    Object.fromEntries(Object.entries(dates).map(([day, raw]) => [day, forecastNumber(raw, 'orders')]))]))
}

// 先校验全部结果再返回，避免批量减法中途失败却已经改动部分日期。
export function applyForecastBulk(values, rows, {channels, metric, dates, operation, text}) {
  if (!['aov', 'orders'].includes(metric) || !dates.length || !channels.length) throw new Error('请选择渠道、指标和有效日期区间')
  const tokens = String(text || '').trim().split(/\s+/u).filter(Boolean)
  const paste = operation === 'paste'
  if (!['set', 'add', 'subtract', 'paste', 'clear'].includes(operation)) throw new Error('不支持的批量操作')
  if (operation !== 'clear' && (!tokens.length || (!paste && tokens.length !== 1) || (paste && tokens.length > dates.length))) throw new Error(paste ? '粘贴的数值数量超出日期区间或内容为空' : '统一设值、加减操作请输入一个数值')
  const additions = tokens.map(value => forecastNumber(value, metric))
  if (additions.some(Number.isNaN) || (['add', 'subtract'].includes(operation) && additions[0] == null)) throw new Error('数值格式无效；客单价需大于0、最多两位小数，单量需为非负整数')
  const updates = []
  for (const channel of channels) {
    const row = rows.find(row => row.targetChannel === channel && row.metric === (metric === 'aov' ? 'forecast_aov' : 'forecast_orders'))
    for (const [index, day] of dates.entries()) {
      if (paste && index >= tokens.length) break
      let result = operation === 'clear' ? null : additions[paste ? index : 0]
      if (operation === 'add' || operation === 'subtract') {
        const base = row?.values[day]?.amount
        if (typeof base !== 'number' || !Number.isFinite(base)) throw new Error(`${channel} ${day}没有原值，请先设值后再加减`)
        const sign = operation === 'add' ? 1 : -1
        result = metric === 'aov' ? (Math.round(base * 100) + sign * Math.round(result * 100)) / 100 : base + sign * result
        if (Number.isNaN(forecastNumber(result, metric))) throw new Error(`${channel} ${day}调整后超出有效范围，未修改任何数据`)
      }
      updates.push({channel, day, result})
    }
  }
  const next = {aov: {}, orders: {}}
  for (const key of ['aov', 'orders']) for (const [channel, dates] of Object.entries(values[key] || {})) next[key][channel] = {...dates}
  for (const {channel, day, result} of updates) {
    next[metric][channel] ||= {}
    next[metric][channel][day] = result
  }
  return next
}

export function forecastOrderTotal(rows, period) {
  return sumKnown(rows.filter(row => row.kind === 'target' && row.metric === 'forecast_orders' && !row.excludeFromOrderTotal).map(row => row.values[period]?.amount))
}

function sumKnown(values) {
  const known = values.filter(value => typeof value === 'number' && Number.isFinite(value) && value >= 0)
  return known.length ? known.reduce((sum, value) => sum + value, 0) : null
}

export function forecastGmvTotal(rows, period) {
  const total = sumKnown(rows.filter(row => row.kind === 'target' && !row.metric).map(row => row.values[period]?.amount))
  return total == null ? null : Number(total.toFixed(2))
}

export function forecastTotals(rows, periods) {
  const unique = [...new Set(periods)]
  // 全范围使用原始金额相加后统一取两位小数，避免逐日取整后再次汇总产生误差。
  const gmv = sumKnown(rows.filter(row => row.kind === 'target' && !row.metric)
    .flatMap(row => unique.map(period => row.values[period]?.amount)))
  return {gmv: gmv == null ? null : Number(gmv.toFixed(2)), orders: sumKnown(unique.map(period => forecastOrderTotal(rows, period)))}
}

export function applyBabyAovForecast(values, result, days) {
  const allowed = new Set(days), channels = new Set(['自播官旗','商城','KOC','DP合计']), seen = new Set()
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, dates]) => [channel, {...dates}]))]))
  let applied = 0, skipped = 0
  for (const row of result.forecasts || []) {
    if (!allowed.has(row.date) || !channels.has(row.channel)) continue
    const key = `${row.channel}:${row.date}`
    if (seen.has(key)) throw new Error('宝贝预估填充日期重复')
    seen.add(key)
    if (!Number.isSafeInteger(row.amount) || row.amount < 0 || row.amount > 1e12 || !Number.isFinite(row.aov) || row.aov <= 0 || row.aov > 1e12) { skipped++; continue }
    next.aov[row.channel] ||= {}; next.orders[row.channel] ||= {}
    next.aov[row.channel][row.date] = row.aov
    next.orders[row.channel][row.date] = row.amount
    applied++
  }
  return {values: next, applied, skipped}
}

export function applyAllianceForecast(values, result, days) {
  if (result.channel !== '抖音精选联盟订单') throw new Error('精选联盟填充渠道不正确')
  const allowed = new Set(days), seen = new Set()
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, dates]) => [channel, {...dates}]))]))
  next.orders['抖音精选联盟订单'] ||= {}
  let applied = 0, skipped = 0
  for (const row of result.forecasts || []) {
    if (!allowed.has(row.date)) continue
    if (seen.has(row.date)) throw new Error('精选联盟填充日期重复')
    seen.add(row.date)
    if (!Number.isSafeInteger(row.amount) || row.amount < 0 || row.amount > 1e12) { skipped++; continue }
    next.orders['抖音精选联盟订单'][row.date] = row.amount
    applied++
  }
  return {values: next, applied, skipped}
}

export function applyEmptyShelfReference(values, rows, result, days) {
  if (result.channel !== '货架-常规') throw new Error('历史参考二只支持货架-常规')
  const current = rows.find(row => row.targetChannel === '货架-常规' && row.metric === 'forecast_orders')
  if (!current) throw new Error('货架预估数据尚未加载，请重新打开填报')
  const eligible = days.filter(day => current?.values[day]?.amount == null || current.values[day].amount === 0)
  return applyReferenceAverages(values, result.rows.map(row => ({date: row.date, mean_order_qty: row.estimated_order_qty})), eligible, '货架-常规')
}

export function applyReferenceAverages(values, rows, days, channel = '货架-常规') {
  if (!['货架-常规', '抖音自播'].includes(channel) || !values.orders?.[channel]) throw new Error('历史参考渠道与当前规划不匹配')
  const allowed = new Set(days)
  // Vue响应式对象是Proxy，structuredClone不能复制；逐层展开数值表以保留其它填报。
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, dates]) => [channel, {...dates}]))]))
  let applied = 0, skipped = 0
  for (const row of rows) {
    const mean = row.mean_order_qty
    if (!allowed.has(row.date) || typeof mean !== 'number' || !Number.isFinite(mean) || mean < 0 || mean > 1e12) {
      skipped++; continue
    }
    next.orders[channel][row.date] = Math.round(mean)
    applied++
  }
  return {values: next, applied, skipped}
}

export function orderFloorPreview(rows, month, minimum) {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month) || !Number.isSafeInteger(minimum) || minimum < 0 || minimum > 1e12) {
    throw new Error('月份或上月最低单量无效')
  }
  const row = rows.find(item => item.targetChannel === '货架-常规' && item.metric === 'forecast_orders')
  const below = [], missing = []
  for (const [date, record] of Object.entries(row?.values || {}).sort(([a], [b]) => a.localeCompare(b))) {
    if (!date.startsWith(month + '-')) continue
    const amount = record?.amount
    if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) missing.push(date)
    else if (amount < minimum) below.push({date, order_qty: amount, updated_order_qty: minimum})
  }
  return {below, missing}
}

export function applyOrderFloor(values, rows, month, minimum, days) {
  const preview = orderFloorPreview(rows, month, minimum)
  const result = applyReferenceAverages(values, preview.below.map(row => ({date: row.date, mean_order_qty: minimum})), days)
  return {...result, preview}
}

export function applyCpsMonthlyAverages(values, rows, dates) {
  return applyMonthlyOrderAverages(values, rows, dates, '货架-CPS')
}

export function applySamplingMonthlyAverages(values, rows, dates) {
  return applyMonthlyOrderAverages(values, rows, dates, '派样')
}

export function applySuperAnchorForecast(values, response, days) {
  return applyAnchorForecast(values, response, days, '达播-超头')
}

export function applySecondAnchorForecast(values, response, days) {
  return applyAnchorForecast(values, response, days, '达播-二梯队')
}

function applyAnchorForecast(values, response, days, channel) {
  if ((response.channel || '达播-超头') !== channel) throw new Error('预估结果与填充渠道不一致，请重新计算')
  if (response.calculation_version !== 'daily_v2') throw new Error('请重新计算逐日达播预估后再填充')
  if (response.status !== 'success' || !response.start_date || !response.end_date) throw new Error('达播预估尚未通过核验')
  const allowed = new Set(days)
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, dates]) => [channel, {...dates}]))]))
  next.orders[channel] ||= {}
  const eligible = new Set(response.mode === 'tail_only' ? response.tail_dates : days)
  let applied = 0, skipped = 0
  for (const row of response.rows) {
    const amount = row.estimated_order_qty
    if (!allowed.has(row.date) || !eligible.has(row.date) || row.date < response.start_date || row.date > response.end_date || row.date.slice(5) > '11-14' || !Number.isSafeInteger(amount) || amount < 0 || amount > 1e12) {
      skipped++; continue
    }
    next.orders[channel][row.date] = amount
    applied++
  }
  return {values: next, applied, skipped}
}

function applyMonthlyOrderAverages(values, rows, dates, channel) {
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, days]) => [channel, {...days}]))]))
  const averages = new Map(rows.map(row => [row.month, row.estimated_order_qty]))
  next.orders[channel] ||= {}
  let applied = 0, skipped = 0
  for (const day of new Set(dates)) {
    const amount = averages.get(day.slice(0,7))
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isSafeInteger(amount) || amount < 0 || amount > 1e12) {
      skipped++; continue
    }
    next.orders[channel][day] = amount
    applied++
  }
  return {values: next, applied, skipped}
}

export function applySampleFestival(values, response, day, days) {
  const amount = response.statistics?.estimated_order_qty
  if (response.status !== 'success' || !days.includes(day) || !response.allowed_dates.includes(day) ||
      !Number.isSafeInteger(amount) || amount < 0 || amount > 1e12) throw new Error('请选择当前规划内的填充日期，并确认有有效小样节历史单量')
  const next = Object.fromEntries(['aov', 'orders'].map(metric => [metric,
    Object.fromEntries(Object.entries(values[metric] || {}).map(([channel, dates]) => [channel, {...dates}]))]))
  next.orders['达播-超头'] ||= {}
  next.orders['达播-超头'][day] = amount
  return {values: next, applied: 1, amount}
}
