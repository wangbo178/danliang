<template>
  <el-dialog v-model="visible" append-to-body :title="`${referenceChannel}历史参考`" width="92%" top="6vh" destroy-on-close :close-on-click-modal="!applying" :close-on-press-escape="!applying" :show-close="!applying">
    <div class="reference-toolbar">
      <span>当前规划日期</span><el-select v-model="targetDate" filterable style="width:420px;max-width:100%" @change="load"><el-option v-for="option in targets" :key="option.date" :value="option.date" :label="`${option.date} · GMV ${format(option.gmv)}${option.eligible_count != null ? ` · ${option.eligible_count}条匹配${option.eligible_count ? `（${option.reference_month}）` : ''}` : ''}`"/></el-select>
      <el-button :loading="loading" @click="load">重新查询</el-button>
      <el-button :disabled="loading || applying || data?.statistics?.mean_order_qty == null" @click="useCurrent">{{ referenceChannel === '抖音自播' ? '填入当天自播预估单量' : '使用当天平均单量' }}</el-button>
      <el-button type="primary" :loading="applying" :disabled="loading" @click="useAll">{{ referenceChannel === '抖音自播' ? '批量填入自播预估单量' : '一键使用所有日期平均单量' }}</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-loading="loading" element-loading-text="正在匹配历史GMV目标及查询实绩…" class="reference-body">
      <template v-if="data">
        <p>参考月份：<b>{{ data.history_month }}</b>（{{ data.reference_source === 'current_year' ? '当年全部月份，预测日前的历史' : data.reference_source === 'previous_year' ? '去年同月' : '上个月' }}） · 当前{{ data.target_channel || data.channel }}GMV目标：<b>{{ format(data.target_gmv) }}元</b> · 匹配范围：{{ format(data.lower_gmv) }}～{{ format(data.upper_gmv) }}元（±10%）</p>
        <el-alert type="info" :closable="false" :title="referenceDescription"/>
        <p class="hint">当前目标来源：{{ data.definition.target }}</p><p v-if="data.reference_source === 'current_year'" class="hint">可用历史月份：{{ data.available_history_months?.join('、') || '暂无' }}；仅统计 {{ data.history_end_exclusive }} 之前的日期，当天及未来日期不计入。</p>
        <p v-if="data.fallback_used" class="hint">{{ data.previous_month }}未找到接近的目标GMV，已继续查找去年同月 {{ data.last_year_month }}。</p>
        <div class="reference-statistics">
          <div><span>匹配目标日期</span><b>{{ data.statistics.matched_count }}天</b></div>
          <div><span>有实际单量的样本</span><b>{{ data.statistics.sample_count }}天</b></div>
          <div><span>单量中位数</span><b>{{ format(data.statistics.median_order_qty) }}<small>单</small></b></div>
          <div><span>平均单量</span><b>{{ format(data.statistics.mean_order_qty) }}<small>单</small></b></div>
        </div>
        <p v-if="data.statistics.missing_actual_count" class="hint">{{ data.statistics.missing_actual_count }}个匹配日期缺少有效实绩单量，未计入中位数和平均数；实际为0单的日期会计入。</p>
        <el-empty v-if="!data.rows.length" :description="emptyMessage"/>
        <div v-else class="reference-scroll"><table>
          <thead><tr><th>参考日期</th><th>{{ data.target_channel || referenceChannel }}GMV目标（元）</th><th>与当前目标差值</th><th>实际达成GMV（元）</th><th>实际达成单量</th><th>实际客单价（元）</th><th>统计样本</th></tr></thead>
          <tbody><tr v-for="row in data.rows" :key="row.date"><td>{{ row.date }}</td><td :title="`目标来源：${row.source_cells.join('、')}；解析版本 ${row.run_id}`">{{ format(row.target_gmv) }}</td><td>{{ row.difference_rate > 0 ? '+' : '' }}{{ (row.difference_rate * 100).toFixed(2) }}%</td><td>{{ format(row.payment_amount) }}</td><td>{{ format(row.order_qty) }}</td><td>{{ format(row.avg_order_value) }}</td><td>{{ row.included ? '计入' : '无有效单量' }}</td></tr></tbody>
        </table></div>
        <p class="hint">平均单量四舍五入为整数，填入{{ referenceChannel }}预估单量；没有平均数的日期保留原值。批量操作覆盖本次规划的所有日期，填入后点击“保存填报”生效。</p>
      </template>
    </div>
    <template #footer><el-button :disabled="applying" @click="visible = false">关闭参考</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
import {ElMessage} from 'element-plus'
const emit = defineEmits(['use-averages'])
const applying = ref(false)
const visible = ref(false), loading = ref(false), error = ref(''), data = ref(null), targets = ref([])
const runId = ref(''), targetDate = ref(''), referenceChannel = ref('货架-常规'), shop = ref('天猫旗舰店')
const referenceDescription = computed(() => referenceChannel.value === '抖音自播' ? '以当前日期自播目标GMV匹配本年已解析的全部月份自播目标GMV，取差值在±10%内的全部日期，再计算这些日期实际货架单量的平均值，填入自播预估单量；实绩按支付日汇总指定三家抖音店铺，order_type=货架。缺少实绩的日期不计入平均，0单计入。' : '优先匹配上个月，某天无±10%内目标时改查去年同月份。目标统一使用当前主表选定区域的货架-常规解析值；实绩按支付日、薇诺娜天猫旗舰店、order_type=货架汇总。全部符合±10%的日期按目标接近程度排序。')
let request = 0
const formatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2})
const format = value => value == null ? '—' : formatter.format(value)
const emptyMessage = computed(() => ({no_history: `未找到${data.value?.searched_months?.join('、')}的${shop.value}GMV规划，请先解析相应历史目标表`, no_match: referenceChannel.value === '抖音自播' ? '本年历史中没有目标差值在±10%内的日期，保留原有预估单量' : '上个月和去年同月份均没有目标差值在±10%内的日期，可选择其他规划日期查看', zero_target: '当前目标为0，无法按百分比匹配参考样本'})[data.value?.status] || '暂无匹配参考数据')
function useCurrent() {
  if (data.value?.statistics?.mean_order_qty == null) return
  emit('use-averages', {run_id: runId.value, channel: referenceChannel.value, rows: [{date: data.value.target_date, mean_order_qty: data.value.statistics.mean_order_qty}]})
  visible.value = false
}
async function useAll() {
  if (applying.value) return
  const selectedRun = runId.value
  applying.value = true; error.value = ''
  try {
    const result = await gmvApi.historyReferenceAverages(selectedRun)
    if (selectedRun !== runId.value) return
    if (!result.available_count) { ElMessage.info('所有日期均无有效平均单量，已跳过，原填报保留'); return }
    emit('use-averages', result)
    visible.value = false
  } catch (e) { error.value = e.response?.data?.detail || (e.code === 'ECONNABORTED' ? '平均单量查询超时，请重试' : e.message || '平均单量查询失败，未修改填报，请重试') }
  finally { applying.value = false }
}
async function load() {
  const id = ++request, selectedDate = targetDate.value
  loading.value = true; error.value = ''; data.value = null
  try {
    const result = await gmvApi.historyReference(runId.value, selectedDate)
    if (id === request) { data.value = result; targets.value = result.targets }
  } catch (e) { if (id === request) error.value = e.response?.data?.detail || '历史参考加载失败，请重试' }
  finally { if (id === request) loading.value = false }
}
function open(run, day) {
  runId.value = run.id
  referenceChannel.value = run.result.parser === 'douyin' ? '抖音自播' : '货架-常规'
  shop.value = run.source?.shop || run.shop || '店铺'
  const days = [...new Set(run.result.records.filter(r=>r.kind==='day').map(r=>r.period))].sort()
  targets.value = days.map(date=>({date, gmv: null}))
  targetDate.value = days.includes(day) ? day : days[0]
  visible.value = true; load()
}
defineExpose({open})
</script>

<style scoped>
.reference-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}.reference-body{min-height:180px}p,.hint{font-size:13px;color:#667085;line-height:1.7}.reference-statistics{display:flex;gap:32px;flex-wrap:wrap;padding:20px 0}.reference-statistics>div{display:flex;flex-direction:column;gap:8px}.reference-statistics span{font-size:12px;color:#667085}.reference-statistics b{font-size:24px;color:#1e3a5f}.reference-statistics small{font-size:12px;margin-left:5px}.reference-scroll{overflow:auto;max-height:360px;border:1px solid #e4e7ec}.reference-scroll table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px;white-space:nowrap}.reference-scroll th,.reference-scroll td{text-align:center;padding:12px;border-right:1px solid #e4e7ec;border-bottom:1px solid #e4e7ec}.reference-scroll th{position:sticky;top:0;background:#f5f7fa}.reference-scroll tbody tr:first-child{background:#eff6ff;font-weight:600}
</style>
