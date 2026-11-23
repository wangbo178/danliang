<template>
  <el-dialog v-model="visible" append-to-body :title="sampling ? '一键填充派样单量' : '批量填充CPS预估单量'" width="880px" destroy-on-close>
    <p>填充日期：<b>{{ dates[0] }} ～ {{ dates.at(-1) }}</b>，共{{ dates.length }}天；填入{{ sampling ? '派样单量' : '货架-CPS预估单量' }}。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-loading="loading" :element-loading-text="`正在查询上月整月${sampling ? '派样' : 'CPS'}单量…`">
      <el-table :data="data?.rows || []" border>
        <el-table-column prop="month" label="填充月份" align="center"/>
        <el-table-column prop="history_month" label="参考月份" align="center"/>
        <el-table-column label="实绩覆盖" align="center"><template #default="scope">{{ scope.row.valid_actual_days }}/{{ scope.row.calendar_days }}天</template></el-table-column>
        <el-table-column label="上月合计单量" align="center"><template #default="scope">{{ format(scope.row.total_order_qty) }}</template></el-table-column>
        <el-table-column label="日均单量" align="center"><template #default="scope">{{ format(scope.row.mean_order_qty) }}</template></el-table-column>
        <el-table-column label="每日填入单量" align="center"><template #default="scope">{{ format(scope.row.estimated_order_qty) }}</template></el-table-column>
        <el-table-column label="填充天数" align="center"><template #default="scope">{{ scope.row.estimated_order_qty == null ? '跳过' : dates.filter(day => day.startsWith(scope.row.month + '-')).length }}</template></el-table-column>
      </el-table>
    </div>
    <p class="hint">查询{{ props.run?.result?.parser === 'douyin' ? '指定三家抖音店铺（官方旗舰店、护肤旗舰店、旗舰店）' : '薇诺娜天猫旗舰店' }}、order_type＝{{ sampling ? '派样' : '天猫官旗CPS' }}，先汇总每日单量，再按上月整月合计÷自然天数计算日均单量，四舍五入为整数。跨月分别使用各自上个月的均值。</p>
    <p v-if="data?.rows.some(row => row.estimated_order_qty == null)" class="hint">部分参考月份缺少完整实绩，无法计算整月均值，对应日期将跳过并保留原值。</p>
    <p class="hint">应用将覆盖所选日期已有的{{ sampling ? '派样单量' : 'CPS预估单量' }}，点击“保存填报”后生效。</p>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="loading || !availableCount" @click="apply">应用到{{ availableCount }}天</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
const props = defineProps({run: Object, kind: {type: String, default: 'cps'}})
const sampling = computed(() => props.kind === 'sampling')
const emit = defineEmits(['apply'])
const visible = ref(false), loading = ref(false), error = ref(''), data = ref(null), dates = ref([])
let request = 0
const availableCount = computed(() => dates.value.filter(day => data.value?.rows.some(row => row.month === day.slice(0,7) && row.estimated_order_qty != null)).length)
const formatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2})
const format = value => value == null ? '—' : formatter.format(value)
async function open(selectedDates) {
  const id = ++request, runId = props.run?.id
  dates.value = [...new Set(selectedDates)].sort()
  visible.value = true; loading.value = true; data.value = null; error.value = ''
  try {
    const months = [...new Set(dates.value.map(day => day.slice(0,7)))]
    const result = await (sampling.value ? gmvApi.forecastSamplingAverages : gmvApi.forecastCpsAverages)(runId, months)
    if (id === request && props.run?.id === runId) data.value = result
  } catch (e) { if (id === request) error.value = e.response?.data?.detail || e.message || `${sampling.value ? '派样' : 'CPS'}平均单量查询失败，请重试` }
  finally { if (id === request) loading.value = false }
}
function apply() {
  if (loading.value || !availableCount.value || data.value?.run_id !== props.run?.id) return
  emit('apply', {...data.value, dates: [...dates.value]})
  visible.value = false
}
defineExpose({open})
</script>

<style scoped>
p{font-size:14px;line-height:1.8}.hint{font-size:12px;color:#667085}
</style>
