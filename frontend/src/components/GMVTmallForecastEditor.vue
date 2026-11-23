<template>
  <el-dialog v-model="visible" append-to-body :title="`${run?.source?.shop || '店铺'} · ${isTmall ? '客单价' : '预估客单'}与预估单量填报`" width="96%" top="3vh" destroy-on-close :close-on-click-modal="false">
    <el-alert type="info" :closable="false" :title="`预估客单和预估单量均可编辑。未手填的单量按GMV÷预估客单计算；手填单量优先生效，清空可恢复自动计算。${isTmall ? '派样单量独立填写，已填数值计入单量合计。' : isDouyin ? '达播组改为独立填写精选联盟订单和达播单量，自动相加为达播汇总单量；派样单量独立填写并计入底部单量合计。' : isBaby ? 'PDD单量独立填写，计入底部单量合计。' : ''}`"/>
    <div class="forecast-controls">
      <el-select v-model="channel" style="width:170px"><el-option label="全部渠道" value="all"/><el-option v-for="name in fillChannels" :key="name" :label="isDouyin && name === '抖音达播' ? '达播单量' : isBaby && name === 'PDD' ? 'PDD单量' : name" :value="name"/></el-select>
      <el-select v-model="metric" style="width:140px"><el-option :label="isTmall ? '客单价（元）' : '预估客单（元）'" value="aov"/><el-option label="预估单量（单）" value="orders"/></el-select>
      <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期"/>
      <el-select v-model="operation" style="width:145px"><el-option label="统一设值" value="set"/><el-option label="批量加上" value="add"/><el-option label="批量减去" value="subtract"/><el-option label="按序粘贴" value="paste"/><el-option label="清空 / 恢复自动" value="clear"/></el-select>
      <el-button type="primary" :disabled="!!cellError || !!rangeError" @click="apply">应用到表格</el-button>
    </div>
    <el-input v-if="operation !== 'clear'" v-model="text" type="textarea" :rows="2" :placeholder="operation === 'paste' ? '按起始日期顺序粘贴，支持空格、换行、制表符和千位逗号，例如：2,597 2,597。用—清空对应值。' : '输入一个数值，例如：100。统一设值将填充整个区间；加减操作针对各日期当前值。'"/>
    <p class="hint">{{ rangeDays.length }}天 · 客单价最多两位小数，预估单量为整数。单量加减以当前显示值为基数，包含自动计算值；无原值的日期需先设值。</p>
    <p class="forecast-totals">所选日期范围 · GMV合计：<b>{{ format(rangeTotals.gmv) }}元</b> · 预估单量合计：<b>{{ format(rangeTotals.orders) }}单</b>（{{ isBaby ? '含PDD；' : isTmall || isDouyin ? '含派样；' : '' }}未填写项不参与统计）</p>
    <p v-if="cellError || rangeError" class="error">{{ cellError || rangeError }}</p>
    <div class="history-controls"><el-button v-if="isTmall" :disabled="saving || !!cellError || !!rangeError" @click="historyTwoPanel.open(rangeDays)">历史参考二</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError || !!rangeError" @click="specialShelfPanel.open(rangeDays)">特殊货架数据填充</el-button><el-button v-if="isBaby" :disabled="saving || !!cellError || !!rangeError" @click="babyAovPanel.open(rangeDays)">一键填充预估单量（除达播）</el-button><el-button v-if="isDouyin" :disabled="saving || !!cellError || !!rangeError" @click="alliancePanel.open(rangeDays)">一键填充精选联盟单量</el-button><el-button v-if="isTmall || isDouyin" @click="referencePanel.open(run, range?.[0])">{{ isTmall ? '历史参考（上月 / 去年同月）' : '自播历史参考（本年全部月份）' }}</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError" @click="floorPanel.open(range?.[0])">预估单量底线调整</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError || !!rangeError" @click="cpsPanel.open(rangeDays)">批量填充CPS预估单量</el-button><el-button v-if="isTmall || isDouyin" :disabled="saving || !!cellError || !!rangeError" @click="samplingPanel.open(rangeDays)">一键填充派样单量</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError" @click="superAnchorPanel.open()">一键填充双十一达播超头</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError" @click="secondAnchorPanel.open()">一键填充双十一二达</el-button><el-button v-if="isTmall" :disabled="saving || !!cellError" @click="sampleFestivalPanel.open()">一键预估小样节单量</el-button><span>填报历史</span><el-select v-model="version" :disabled="dirty || saving" placeholder="尚无保存记录" style="width:340px" @change="loadVersion"><el-option v-for="entry in history" :key="entry.id" :value="entry.id" :label="`${new Date(entry.created_at).toLocaleString('zh-CN')} · ${entry.id.slice(0,8)}`"/></el-select><span v-if="dirty">有未保存修改，保存或取消后可查看历史</span></div>
    <GMVTargetTable :rows="rows" :periods="days" :summaries="summaries" :format="(row, value) => isTmall && !row.metric ? formatGmv(value) : format(value)" :evidence="record => record?.evidence || record?.cell || '缺失'" show-trend editable :edit-value="editValue" @cell-input="editCell"/>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="!!cellError || !run?.id" :loading="saving" @click="save">保存填报</el-button></template>
  </el-dialog>
  <GMVSpecialShelfFill v-if="isTmall" ref="historyTwoPanel" kind="reference_two" :run="run" :forecast-rows="rows" @apply="useHistoryTwo"/>
  <GMVSpecialShelfFill v-if="isTmall" ref="specialShelfPanel" :run="run" @apply="useSpecialShelf"/>
  <GMVBabyAovFill v-if="isBaby" ref="babyAovPanel" :run="run" @apply="useBabyAov"/>
  <GMVAllianceFill v-if="isDouyin" ref="alliancePanel" :run="run" @apply="useAlliance"/>
  <GMVHistoryReference v-if="isTmall || isDouyin" ref="referencePanel" @use-averages="useAverages"/>
  <GMVOrderFloor v-if="isTmall" ref="floorPanel" :run="run" :rows="rows" @apply="useFloor"/>
  <GMVCpsFill v-if="isTmall" ref="cpsPanel" :run="run" @apply="useCps"/>
  <GMVCpsFill v-if="isTmall || isDouyin" ref="samplingPanel" kind="sampling" :run="run" @apply="useSampling"/>
  <GMVSuperAnchorFill v-if="isTmall" ref="superAnchorPanel" :run="run" :rows="rows" @apply="useSuperAnchor"/>
  <GMVSuperAnchorFill v-if="isTmall" ref="secondAnchorPanel" kind="second" :run="run" :rows="rows" @apply="useSecondAnchor"/>
  <GMVSampleFestival v-if="isTmall" ref="sampleFestivalPanel" :run="run" :rows="rows" @apply="useSampleFestival"/>
</template>

<script setup>
import {computed, ref, h, watch} from 'vue'
import {ElMessage} from 'element-plus'
import {gmvApi} from '../api'
import GMVTargetTable from './GMVTargetTable.vue'
import GMVSpecialShelfFill from './GMVSpecialShelfFill.vue'
import GMVBabyAovFill from './GMVBabyAovFill.vue'
import GMVAllianceFill from './GMVAllianceFill.vue'
import GMVHistoryReference from './GMVHistoryReference.vue'
import GMVOrderFloor from './GMVOrderFloor.vue'
import GMVCpsFill from './GMVCpsFill.vue'
import GMVSuperAnchorFill from './GMVSuperAnchorFill.vue'
import GMVSampleFestival from './GMVSampleFestival.vue'
import {buildTmallForecastRows, buildChannelForecastRows, forecastNumber, priceRecords, orderRecords, applyForecastBulk, forecastOrderTotal, forecastGmvTotal, forecastTotals, applyReferenceAverages, applyEmptyShelfReference, applyAllianceForecast, applyBabyAovForecast, applyOrderFloor, applyCpsMonthlyAverages, applySamplingMonthlyAverages, applySuperAnchorForecast, applySecondAnchorForecast, applySampleFestival} from '../utils/tmallForecast.mjs'
const emit = defineEmits(['saved'])
const visible = ref(false), saving = ref(false), run = ref(null), values = ref({aov: {}, orders: {}})
const referencePanel = ref(null), alliancePanel = ref(null)
const specialShelfPanel = ref(null), historyTwoPanel = ref(null)
const floorPanel = ref(null), babyAovPanel = ref(null)
const cpsPanel = ref(null), samplingPanel = ref(null), superAnchorPanel = ref(null), secondAnchorPanel = ref(null), sampleFestivalPanel = ref(null)
const channel = ref('all'), metric = ref('aov'), range = ref([]), operation = ref('set'), text = ref('')
const history = ref([]), version = ref(null), baseVersion = ref(null), dirty = ref(false)
const channels = computed(() => [...new Set((run.value?.result?.records || []).map(r => r.channel))])
const isTmall = computed(() => run.value?.rule_key === 'tmall')
const isDouyin = computed(() => run.value?.result?.parser === 'douyin')
const isBaby = computed(() => run.value?.result?.parser === 'douyin_baby')
const priceChannels = computed(() => isDouyin.value ? channels.value.filter(name => name !== '抖音达播') : channels.value)
const orderChannels = computed(() => isTmall.value ? [...channels.value, '派样'] : isDouyin.value ? [...channels.value, '抖音精选联盟订单', '派样'] : isBaby.value ? [...channels.value, 'PDD'] : channels.value)
const fillChannels = computed(() => metric.value === 'orders' ? orderChannels.value : priceChannels.value)
watch(metric, value => { if (value === 'aov' && !priceChannels.value.includes(channel.value)) channel.value = 'all' })
const days = computed(() => [...new Set((run.value?.result?.records || []).filter(r=>r.kind==='day').map(r=>r.period))].sort())
const rangeDays = computed(() => range.value?.length === 2 ? days.value.filter(day=>day>=range.value[0]&&day<=range.value[1]) : [])
const rangeError = computed(() => range.value?.length !== 2 || !range.value.every(day=>days.value.includes(day)) || !rangeDays.value.length ? '请选择当前表格范围内的有效日期区间' : '')
const rows = computed(() => (isTmall.value ? buildTmallForecastRows : buildChannelForecastRows)(run.value?.result?.records || [], channels.value, priceRecords(values.value), orderRecords(values.value)))
const formatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2})
const format = value => value == null ? '—' : formatter.format(value)
const gmvFormatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 0})
const formatGmv = value => value == null ? '—' : gmvFormatter.format(value)
const cellError = computed(() => {
  for (const key of ['aov', 'orders']) for (const name of (key === 'orders' ? orderChannels.value : priceChannels.value)) for (const day of days.value)
    if (Number.isNaN(forecastNumber(values.value[key]?.[name]?.[day], key))) return `${name} ${day} ${key === 'aov' ? '客单价需大于0，最多两位小数' : '预估单量需为非负整数'}`
  return ''
})
const rangeTotals = computed(() => forecastTotals(rows.value, rangeDays.value))
const summaries = computed(() => [null, ...days.value].map(day => h('div', [h('div', {class:'summary-item'}, day ? format(forecastGmvTotal(rows.value, day)) : 'GMV合计'), h('div', {class:'summary-item'}, day ? format(forecastOrderTotal(rows.value, day)) : '预估单量合计')])))
function editValue(row, day) {
  const key = row.metric === 'forecast_aov' ? 'aov' : 'orders'
  const value = values.value[key]?.[row.targetChannel]?.[day]
  return typeof value === 'number' ? formatter.format(value) : value ?? ''
}
function editCell({channel, metric, period, value}) {
  const key = metric === 'forecast_aov' ? 'aov' : 'orders'
  values.value[key][channel] ||= {}
  values.value[key][channel][period] = value; dirty.value = true
}
function install(response) {
  values.value = JSON.parse(JSON.stringify(response.values))
  history.value = response.history; version.value = response.version; baseVersion.value = response.current_version
  dirty.value = false
}
function open(selectedRun, response) {
  run.value = selectedRun; install(response)
  channel.value = 'all'; metric.value = 'aov'; operation.value = 'set'; text.value = ''
  range.value = [days.value[0], days.value.at(-1)]; visible.value = true
}
async function loadVersion(id) { saving.value = true; try { install(await gmvApi.forecastInputs(run.value.id, id)) } catch {} finally { saving.value = false } }
function useHistoryTwo(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applyEmptyShelfReference(values.value, rows.value, result, result.dates.filter(day => days.value.includes(day)))
    values.value = applied.values
    if (applied.applied) dirty.value = true
    channel.value = '货架-常规'; metric.value = 'orders'
    ElMessage.success(`已填入${applied.applied}天空值或零值货架单量，跳过${applied.skipped}天；请检查后保存`)
  } catch (error) { ElMessage.warning(error.message) }
}
function useSpecialShelf(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id || result.channel !== '货架-常规') return
  const eligible = new Set((run.value.result.records || []).filter(r => r.kind === 'day' && r.channel === '货架-常规' && r.amount > 15000000).map(r => r.period))
  const dates = result.dates.filter(day => days.value.includes(day) && eligible.has(day))
  const applied = applyReferenceAverages(values.value, result.rows.map(row => ({...row, mean_order_qty: row.estimated_order_qty})), dates, '货架-常规')
  values.value = applied.values
  if (applied.applied) dirty.value = true
  channel.value = '货架-常规'; metric.value = 'orders'
  ElMessage.success(`已填入${applied.applied}天特殊货架单量，跳过${applied.skipped}天；请检查后保存`)
}
function useBabyAov(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applyBabyAovForecast(values.value, result, result.dates.filter(day => days.value.includes(day)))
    values.value = applied.values
    if (applied.applied) dirty.value = true
    channel.value = 'all'; metric.value = 'orders'
    ElMessage.success(`已填入${applied.applied}项宝贝预估单量，跳过${applied.skipped}项，达播保持原值；请检查后保存`)
  } catch (error) { ElMessage.warning(error.message) }
}
function useAlliance(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applyAllianceForecast(values.value, result, result.dates.filter(day => days.value.includes(day)))
    values.value = applied.values
    if (applied.applied) dirty.value = true
    channel.value = '抖音精选联盟订单'; metric.value = 'orders'
    ElMessage.success(`已填入${applied.applied}天精选联盟单量，跳过${applied.skipped}天，请检查后保存填报`)
  } catch (error) { ElMessage.warning(error.message) }
}
function useAverages(result) {
  if (!visible.value || saving.value || result.run_id !== run.value?.id) return
  const targetChannel = isDouyin.value ? '抖音自播' : '货架-常规'
  if (result.channel && result.channel !== targetChannel) return
  const applied = applyReferenceAverages(values.value, result.rows, days.value, targetChannel)
  values.value = applied.values
  if (applied.applied) dirty.value = true
  channel.value = targetChannel; metric.value = 'orders'
  ElMessage.success(`已填入${applied.applied}天${targetChannel}预估单量，跳过${applied.skipped}天；请检查后保存填报`)
}
function useFloor(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  const applied = applyOrderFloor(values.value, rows.value, result.month, result.minimum, days.value)
  values.value = applied.values
  if (applied.applied) dirty.value = true
  channel.value = '货架-常规'; metric.value = 'orders'
  range.value = [days.value.find(day => day.startsWith(result.month + '-')), days.value.findLast(day => day.startsWith(result.month + '-'))]
  ElMessage.success(`已将${applied.applied}天货架预估单量调整为${format(result.minimum)}单，请检查后保存填报`)
}
function useCps(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  const applied = applyCpsMonthlyAverages(values.value, result.rows, result.dates.filter(day => days.value.includes(day)))
  values.value = applied.values
  if (applied.applied) dirty.value = true
  channel.value = '货架-CPS'; metric.value = 'orders'
  ElMessage.success(`已填入${applied.applied}天CPS预估单量，跳过${applied.skipped}天，请检查后保存填报`)
}
function useSampling(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  const applied = applySamplingMonthlyAverages(values.value, result.rows, result.dates.filter(day => days.value.includes(day)))
  values.value = applied.values
  if (applied.applied) dirty.value = true
  channel.value = '派样'; metric.value = 'orders'
  ElMessage.success(`已填入${applied.applied}天派样单量，跳过${applied.skipped}天，请检查后保存填报`)
}
function useSuperAnchor(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applySuperAnchorForecast(values.value, result, days.value)
    values.value = applied.values
    if (applied.applied) dirty.value = true
    channel.value = '达播-超头'; metric.value = 'orders'
    ElMessage.success(`已填入${applied.applied}天超头预估单量，跳过${applied.skipped}天，请检查后保存填报`)
  } catch (error) { ElMessage.warning(error.message) }
}
function useSampleFestival(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applySampleFestival(values.value, result, result.fill_date, days.value)
    values.value = applied.values; dirty.value = true
    channel.value = '达播-超头'; metric.value = 'orders'
    ElMessage.success(`已填入${result.fill_date}超头预估单量${format(applied.amount)}单，请检查后保存填报`)
  } catch (error) { ElMessage.warning(error.message) }
}
function useSecondAnchor(result) {
  if (!visible.value || saving.value || cellError.value || result.run_id !== run.value?.id) return
  try {
    const applied = applySecondAnchorForecast(values.value, result, days.value)
    values.value = applied.values
    if (applied.applied) dirty.value = true
    channel.value = '达播-二梯队'; metric.value = 'orders'
    ElMessage.success(`已填入${applied.applied}天二达预估单量，跳过${applied.skipped}天，请检查后保存填报`)
  } catch (error) { ElMessage.warning(error.message) }
}
function apply() {
  if (cellError.value || rangeError.value) return
  try {
    values.value = applyForecastBulk(values.value, rows.value, {channels: channel.value === 'all' ? fillChannels.value : [channel.value], metric: metric.value, dates: rangeDays.value, operation: operation.value, text: text.value})
    dirty.value = true; ElMessage.success('已应用到表格，请检查后保存')
  } catch (error) { ElMessage.warning(error.message) }
}
async function save() {
  if (cellError.value) return
  saving.value = true
  try {
    const normalized = Object.fromEntries(['aov', 'orders'].map(key => [key, Object.fromEntries((key === 'orders' ? orderChannels.value : channels.value).map(name => [name, Object.fromEntries(days.value.map(day=>[day, forecastNumber(values.value[key]?.[name]?.[day],key)]))]))]))
    const response = await gmvApi.saveForecastInputs(run.value.id, {base_version: baseVersion.value, values: normalized})
    visible.value = false; emit('saved', response); ElMessage.success('客单价与预估单量已保存，历史版本已保留')
  } finally { saving.value = false }
}
defineExpose({open})
</script>

<style scoped>
.forecast-totals{padding:12px 16px;background:#eff6ff;color:#1e3a5f;border-radius:6px;font-size:14px}.forecast-controls,.history-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0}.hint,.history-controls{font-size:12px;color:#667085}.error{font-size:13px;color:#b42318}
</style>
