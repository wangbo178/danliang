<template>
  <el-dialog v-model="visible" append-to-body title="预估单量底线调整 · 货架-常规" width="850px" top="5vh" destroy-on-close>
    <div class="floor-toolbar"><span>调整月份</span><el-select v-model="month" @change="load" style="width:160px"><el-option v-for="item in months" :key="item" :label="item" :value="item"/></el-select><el-button :loading="loading" @click="load">重新查询</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-loading="loading">
      <template v-if="data && !loading">
        <el-empty v-if="data.minimum_order_qty == null" :description="`${data.history_month}没有有效的货架实际单量，无法设置底线`"/>
        <template v-else>
          <p>上个月 <b>{{ data.history_month }}</b> 的货架实际最低单量：<b>{{ format(data.minimum_order_qty) }}单</b></p>
          <el-table :data="data.minimum_days" border max-height="180">
            <el-table-column prop="date" label="上月最低单量日期" align="center"/>
            <el-table-column label="实际达成单量" align="center"><template #default="scope">{{ format(scope.row.order_qty) }}</template></el-table-column>
            <el-table-column label="当日实际达成GMV（元）" align="center"><template #default="scope">{{ format(scope.row.payment_amount) }}</template></el-table-column>
          </el-table>
          <p class="hint">上月{{ data.valid_actual_days }}天有有效实绩；{{ data.missing_actual_days }}天缺少有效单量，未按0单计入。实绩使用支付日汇总表的货架单量与实付金额。</p>
          <p><b>{{ month }}</b> 在已有有效预估单量的日期中，共 <b>{{ preview.below.length }}天</b>低于底线，更新后均为 <b>{{ format(data.minimum_order_qty) }}单</b>。</p>
          <el-table v-if="preview.below.length" :data="preview.below" border max-height="300">
            <el-table-column prop="date" label="本月日期" align="center"/>
            <el-table-column label="当前预估单量" align="center"><template #default="scope">{{ format(scope.row.order_qty) }}</template></el-table-column>
            <el-table-column label="调整后预估单量" align="center"><template #default="scope">{{ format(scope.row.updated_order_qty) }}</template></el-table-column>
          </el-table>
          <el-empty v-else description="本月没有低于底线的预估单量，无需调整"/>
          <p class="hint">按当前编辑表格中的预估单量比较，包含尚未保存的修改。没有预估值的日期不纳入统计和调整；已有0单预估值的日期参与比较。更新填入编辑表格，点击“保存填报”后生效。</p>
        </template>
      </template>
    </div>
    <template #footer><el-button @click="visible = false">暂不更新</el-button><el-button type="primary" :disabled="loading || !data || !preview.below.length" @click="apply">按上月最低单量更新（{{ preview.below.length }}天）</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
import {orderFloorPreview} from '../utils/tmallForecast.mjs'
const props = defineProps({run: Object, rows: {type: Array, default: () => []}})
const emit = defineEmits(['apply'])
const visible = ref(false), loading = ref(false), error = ref(''), month = ref(''), data = ref(null)
let request = 0
const months = computed(() => [...new Set((props.run?.result?.records || []).filter(r => r.kind === 'day').map(r => r.period.slice(0,7)))].sort())
const preview = computed(() => data.value?.minimum_order_qty != null ? orderFloorPreview(props.rows, month.value, data.value.minimum_order_qty) : {below: [], missing: []})
const formatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2})
const format = value => value == null ? '—' : formatter.format(value)
async function load() {
  const id = ++request, runId = props.run?.id, selected = month.value
  if (!runId || !selected) return
  loading.value = true; error.value = ''; data.value = null
  try {
    const result = await gmvApi.forecastOrderFloor(runId, selected)
    if (id === request && props.run?.id === runId) data.value = result
  } catch (e) { if (id === request) error.value = e.response?.data?.detail || e.message || '底线查询失败，请重试' }
  finally { if (id === request) loading.value = false }
}
function open(preferredDate) {
  const preferred = preferredDate?.slice(0,7)
  month.value = months.value.includes(preferred) ? preferred : months.value[0]
  visible.value = true; load()
}
function apply() {
  if (loading.value || !preview.value.below.length || data.value?.run_id !== props.run?.id || data.value?.month !== month.value) return
  emit('apply', {run_id: data.value.run_id, month: month.value, minimum: data.value.minimum_order_qty})
  visible.value = false
}
defineExpose({open})
</script>

<style scoped>
.floor-toolbar{display:flex;align-items:center;gap:12px;margin-bottom:16px}p{font-size:14px;line-height:1.8}.hint{font-size:12px;color:#667085}
</style>
