<template>
  <el-dialog v-model="visible" append-to-body :title="`${channelName} · 手动批量填报`" width="94%" top="3vh" destroy-on-close :close-on-click-modal="false">
    <div class="date-controls">
      <el-radio-group v-model="mode"><el-radio-button value="follow">跟随天猫日期</el-radio-button><el-radio-button value="custom">自定义日期</el-radio-button></el-radio-group>
      <el-select v-if="mode === 'follow'" v-model="tmallSourceId" placeholder="选择天猫规划" style="width:300px">
        <el-option v-for="source in tmallSources" :key="source.id" :label="source.name" :value="source.id"/>
      </el-select>
      <el-date-picker v-else v-model="customRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期"/>
      <span>{{ dates.length ? `${dates[0]} 至 ${dates.at(-1)} · ${dates.length}天` : '请选择有效日期范围' }}</span>
    </div>
    <el-alert type="info" :closable="false" title="按日期从左到右填充；支持空格、换行及制表符分隔，支持2,597这样的千位分隔数字。合计实时累加输入行已填写的数据；全部空白时显示—，填写0时显示0。"/>
    <div class="bulk-box">
      <div class="date-controls">
        <el-select v-model="bulkRow" style="width:150px"><el-option v-for="name in inputs" :key="name" :label="name" :value="name"/></el-select>
        <span>从</span><el-select v-model="bulkStart" placeholder="起始日期" style="width:160px"><el-option v-for="d in dates" :key="d" :label="d" :value="d"/></el-select><span>开始填充</span>
        <el-button type="primary" :disabled="!bulkTokens.length || !!bulkError" @click="applyBulk">填充到表格</el-button>
        <span>{{ bulkTokens.length }}个值 / 起始日期后可填{{ remaining }}天</span>
      </div>
      <el-input v-model="bulkText" type="textarea" :rows="3" placeholder="例如：2,597 2,597，也可以每行一个数字；用 — 或 - 表示未填写。"/>
      <p v-if="bulkError" class="error">{{ bulkError }}</p>
    </div>
    <div class="manual-scroll" tabindex="0" aria-label="手动单量填报表，可横向滚动">
      <table class="manual-table">
        <thead><tr><th>指标</th><th v-for="day in dates" :key="day">{{ day }}</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.channel" :class="{total: row.total}">
          <th scope="row">{{ row.channel }}</th>
          <td v-for="day in dates" :key="day"><b v-if="row.total">{{ dailyTotal(day) == null ? '—' : dailyTotal(day).toLocaleString('zh-CN') }}</b><input v-else :value="displayInput(values[row.channel][day])" @input="event => values[row.channel][day] = event.target.value" @blur="normalizeCell(row.channel, day)" :aria-label="`${day} ${row.channel}`" placeholder="未填写" inputmode="numeric"/></td>
        </tr></tbody>
      </table>
    </div>
    <p v-if="cellError" class="error">{{ cellError }}</p>
    <p class="hint">更改日期范围会保留重叠日期的填报值。保存后保留历史版本；跟随模式会随所选天猫规划的日期范围更新，新日期保持未填写。</p>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="!dates.length || !!cellError" :loading="saving" @click="save">保存填报</el-button></template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { gmvApi } from '../api'
import { parseQuantityToken, normalizeQuantity, sumQuantities } from '../utils/gmvQuantity.mjs'
const props = defineProps({ sources: Array, runs: Array })
const emit = defineEmits(['saved'])
const profile = ref('tmall_baby_manual')
const profiles = {
  other_manual: {name: '其它渠道', inputs: ['预计单量', '活动单量'], total: '合计', defaults: {'预计单量': 100}, save: 'saveOtherChannel'},
  ali_health_manual: {name: '阿里健康渠道', inputs: ['预计单量', '活动单量'], total: '合计', defaults: {'预计单量': 1000}, save: 'saveAliHealthChannel'},
  pdd_manual: {name: '拼多多店铺', inputs: ['预计单量', '手工增加单量'], total: '合计', defaults: {}, save: 'savePddChannel'},
  tmall_baby_manual: {name: '天猫宝贝', inputs: ['预估单量', '直播单量', '预售单量'], total: '合计总单量', defaults: {}, save: 'saveTmallBaby'},
  private_manual: {name: '私密渠道', inputs: ['预计单量', '活动单量'], total: '合计', defaults: {'预计单量': 200}, save: 'savePrivateChannel'},
  ka_manual: {name: 'KA-京东-快手-小红书', inputs: ['预计单量', '活动单量'], total: '合计', defaults: {'预计单量': 1500}, save: 'saveKaChannel'},
}
const profileConfig = computed(() => profiles[profile.value])
const channelName = computed(() => profileConfig.value.name)
const inputs = reactive(['预估单量', '直播单量', '预售单量'])
const rows = computed(() => [...inputs.map(channel => ({channel})), {channel: profileConfig.value.total, total: true}])
function defaultValue(name) { return profileConfig.value.defaults[name] ?? null }
const visible = ref(false), saving = ref(false), mode = ref('follow'), tmallSourceId = ref(''), customRange = ref([])
const values = ref(Object.fromEntries(inputs.map(name => [name, {}])))
const bulkRow = ref(inputs[0]), bulkStart = ref(''), bulkText = ref(''), baseRunId = ref(null)
const tmallSources = computed(() => (props.sources || []).filter(s => s.rule_key === 'tmall'))
const dates = computed(() => {
  if (mode.value === 'follow') {
    const run = (props.runs || []).find(r => r.source_id === tmallSourceId.value && r.status === 'success')
    return [...new Set(run?.day_periods || (run?.result?.records || []).filter(r => r.kind === 'day').map(r => r.period))].sort()
  }
  if (!customRange.value || customRange.value.length !== 2) return []
  const start = Date.parse(`${customRange.value[0]}T00:00:00Z`), end = Date.parse(`${customRange.value[1]}T00:00:00Z`)
  const count = (end - start) / 86400000 + 1
  return count >= 1 && count <= 366 ? Array.from({length: count}, (_, i) => new Date(start + i * 86400000).toISOString().slice(0, 10)) : []
})
watch(dates, current => {
  for (const name of inputs) for (const day of current) if (!(day in values.value[name])) values.value[name][day] = defaultValue(name)
  if (!current.includes(bulkStart.value)) bulkStart.value = current[0] || ''
})
const bulkTokens = computed(() => bulkText.value.trim() ? bulkText.value.trim().split(/\s+/u) : [])
const bulkValues = computed(() => bulkTokens.value.map(parseQuantityToken))
const remaining = computed(() => bulkStart.value && dates.value.includes(bulkStart.value) ? dates.value.length - dates.value.indexOf(bulkStart.value) : 0)
const bulkError = computed(() => {
  if (bulkValues.value.some(Number.isNaN)) return '只能输入0至1万亿的整数，支持2,597这样的千位分隔；用 — 或 - 表示未填写。'
  if (bulkTokens.value.length > remaining.value) return '数值个数超出可填日期，请调整起始日期或日期范围。'
  return ''
})
const cellError = computed(() => {
  for (const name of inputs) for (const day of dates.value) if (Number.isNaN(normalizeQuantity(values.value[name][day]))) return `${day} ${name}：请输入有效的非负整数，可使用千位逗号。`
  return ''
})
function displayInput(value) { return typeof value === 'number' ? value.toLocaleString('zh-CN') : value ?? '' }
function normalizeCell(name, day) { const number = normalizeQuantity(values.value[name][day]); if (!Number.isNaN(number)) values.value[name][day] = number }
function dailyTotal(day) { const total = sumQuantities(inputs.map(name => values.value[name][day])); return Number.isNaN(total) ? null : total }
async function applyBulk() {
  if (bulkError.value || !bulkTokens.value.length) return
  const start = dates.value.indexOf(bulkStart.value), tokens = bulkTokens.value
  if (tokens.some((_, i) => values.value[bulkRow.value][dates.value[start+i]] != null)) {
    try { await ElMessageBox.confirm(`将覆盖${bulkRow.value}中对应日期的已有数值。`, '确认批量填充') } catch { return }
  }
  bulkValues.value.forEach((value, i) => { values.value[bulkRow.value][dates.value[start+i]] = value })
  ElMessage.success(`已填充${tokens.length}天，请确认表格后保存`)
  bulkText.value = ''
}
function open(run, kind = 'tmall_baby_manual') {
  profile.value = kind
  inputs.splice(0, inputs.length, ...profiles[kind].inputs)
  const settings = run?.result?.manual_settings || {}
  baseRunId.value = run?.id || null
  values.value = Object.fromEntries(inputs.map(name => [name, {}]))
  for (const row of run?.result?.quantity_rows || []) if (inputs.includes(row.channel)) for (const r of row.records) if (r.kind === 'day') values.value[row.channel][r.period] = r.amount
  mode.value = settings.mode || 'follow'
  tmallSourceId.value = settings.tmall_source_id || tmallSources.value[0]?.id || ''
  customRange.value = settings.start_date ? [settings.start_date, settings.end_date] : []
  bulkText.value = ''; bulkRow.value = inputs[0]; bulkStart.value = ''
  for (const name of inputs) for (const day of dates.value) if (!(day in values.value[name])) values.value[name][day] = defaultValue(name)
  bulkStart.value = dates.value[0] || ''
  visible.value = true
}
async function save() {
  if (!dates.value.length || cellError.value) return
  saving.value = true
  try {
    const payload = {mode: mode.value, tmall_source_id: tmallSourceId.value, start_date: dates.value[0], end_date: dates.value.at(-1), base_run_id: baseRunId.value,
      values: Object.fromEntries(inputs.map(name => [name, Object.fromEntries(dates.value.map(day => [day, normalizeQuantity(values.value[name][day])]))]))}
    const run = await gmvApi[profileConfig.value.save](payload)
    visible.value = false; emit('saved', run); ElMessage.success(`${channelName.value}单量已保存，历史版本已保留`)
  } finally { saving.value = false }
}
defineExpose({open})
</script>

<style scoped>
.date-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}.bulk-box{padding:16px;background:#f8fafc;border:1px solid #e4e7ec;border-radius:8px;margin:16px 0}.date-controls span,.hint{font-size:13px;color:#667085}.error{color:#d92d20;font-size:13px}.manual-scroll{max-height:340px;overflow:auto;border:1px solid #d0d5dd}.manual-table{border-spacing:0;width:max-content;min-width:100%;font-size:14px}.manual-table th,.manual-table td{box-sizing:border-box;min-width:160px;padding:8px 12px;text-align:center;white-space:nowrap;border-right:1px solid #e4e7ec;border-bottom:1px solid #e4e7ec}.manual-table thead th{position:sticky;top:0;background:#f5f7fa;z-index:2}.manual-table th:first-child{position:sticky;left:0;min-width:150px;background:#f5f7fa;z-index:1}.manual-table thead th:first-child{z-index:3}.manual-table input{box-sizing:border-box;width:130px;height:32px;border:1px solid #d0d5dd;border-radius:4px;text-align:center;font:inherit;color:#172033;background:#fff}.manual-table input:focus{outline:2px solid #93b4ef;outline-offset:0}.manual-table .total{background:#eff6ff;color:#1e3a5f}.el-alert{margin:12px 0}
</style>
