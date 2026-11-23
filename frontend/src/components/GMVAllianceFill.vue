<template>
  <el-dialog v-model="visible" append-to-body title="一键填充精选联盟单量" width="95%" top="5vh" destroy-on-close>
    <p>填充日期：{{ dates[0] }} ～ {{ dates.at(-1) }}，填入“抖音精选联盟订单”。</p>
    <el-alert type="info" :closable="false" title="普通日＝上月整月合计÷自然天数；特殊日＝整月日均×（上月特殊日日均÷上月普通日日均）。周六日或国家放假日期算特殊日，重合只调整一次；跨月分别计算。"/>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <div v-loading="loading" element-loading-text="正在查询精选联盟实绩并计算特殊日增幅…">
      <el-table :data="data?.rows || []" border>
        <el-table-column prop="month" label="填充月份" align="center"/>
        <el-table-column prop="history_month" label="参考月份" align="center"/>
        <el-table-column label="实绩覆盖" align="center"><template #default="s">{{ s.row.valid_actual_days }}/{{ s.row.calendar_days }}天</template></el-table-column>
        <el-table-column label="上月整月日均" align="center"><template #default="s">{{ format(s.row.mean_order_qty) }}</template></el-table-column>
        <el-table-column label="普通日样本 / 均值" align="center"><template #default="s">{{ s.row.ordinary_days }}天 / {{ format(s.row.ordinary_mean) }}</template></el-table-column>
        <el-table-column label="特殊日样本 / 均值" align="center"><template #default="s">{{ s.row.special_days }}天 / {{ format(s.row.special_mean) }}</template></el-table-column>
        <el-table-column label="特殊日增幅" align="center"><template #default="s">{{ s.row.uplift_rate == null ? '—' : `${s.row.uplift_rate >= 0 ? '+' : ''}${(s.row.uplift_rate * 100).toFixed(2)}%` }}</template></el-table-column>
        <el-table-column label="普通日填入" align="center"><template #default="s">{{ format(s.row.ordinary_estimate) }}</template></el-table-column>
        <el-table-column label="特殊日填入" align="center"><template #default="s">{{ format(s.row.special_estimate) }}</template></el-table-column>
      </el-table>
      <el-table :data="selectedForecasts" border max-height="280" class="daily-preview">
        <el-table-column prop="date" label="填充日期" align="center"/>
        <el-table-column prop="day_type" label="日期类型" align="center"/>
        <el-table-column prop="history_month" label="参考月份" align="center"/>
        <el-table-column label="填入单量" align="center"><template #default="s">{{ s.row.amount == null ? '跳过，保留原值' : format(s.row.amount) }}</template></el-table-column>
      </el-table>
    </div>
    <p class="hint">查询指定三家抖音店铺，合并“抖音-精选联盟（非派样）”及“抖音-精选联盟（派样）”，先汇总每天实绩。上月缺少完整数据时跳过对应月份；无法计算比例时只跳过特殊日。增幅按实绩计算，可能为负，不预设上涨比例。</p>
    <p class="hint">按你的规则，调休上班的周六日仍按周末处理。填入后达播汇总会自动重算，点击“保存填报”后生效。</p>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="loading || !available" @click="apply">应用到{{ available }}天</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
const props = defineProps({run: Object})
const emit = defineEmits(['apply'])
const visible = ref(false), loading = ref(false), error = ref(''), data = ref(null), dates = ref([])
let request = 0
const selectedForecasts = computed(() => (data.value?.forecasts || []).filter(row => dates.value.includes(row.date)))
const available = computed(() => selectedForecasts.value.filter(row => row.amount != null).length)
const format = value => value == null ? '—' : new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(value)
async function open(selectedDates) {
  const id = ++request, runId = props.run?.id
  dates.value = [...new Set(selectedDates)].sort()
  visible.value = true; loading.value = true; data.value = null; error.value = ''
  try {
    const result = await gmvApi.forecastAlliance(runId, [...new Set(dates.value.map(day => day.slice(0,7)))])
    if (id === request && props.run?.id === runId) data.value = result
  } catch (e) { if (id === request) error.value = e.response?.data?.detail || e.message || '精选联盟单量查询失败' }
  finally { if (id === request) loading.value = false }
}
function apply() {
  if (loading.value || !available.value || data.value?.run_id !== props.run?.id) return
  emit('apply', {...data.value, dates: [...dates.value]})
  visible.value = false
}
defineExpose({open})
</script>

<style scoped>
p{font-size:14px;line-height:1.8}.hint{font-size:12px;color:#667085}.daily-preview{margin-top:16px}
</style>
