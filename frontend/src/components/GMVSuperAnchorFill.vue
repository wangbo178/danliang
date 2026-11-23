<template>
  <el-dialog v-model="visible" append-to-body :title="`一键填充双十一${label} · 算法1`" width="94%" top="4vh" destroy-on-close>
    <div class="controls"><span>填充范围</span><el-select v-model="mode" @change="load" style="width:310px"><el-option label="只填尾款日期（触发当天及后5天）" value="tail_only"/><el-option label="从首次触发日连续填至11月14日" value="through_1114"/></el-select><el-button :loading="loading" @click="load">重新计算</el-button></div>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <div v-loading="loading" :element-loading-text="`正在核验去年目标并查询${label}实绩…`">
      <template v-if="data && !loading">
        <el-alert v-if="data.status !== 'success'" type="info" :title="data.message" :closable="false"/>
        <p>今年触发日期：{{ data.trigger_dates.join('、') || '无' }}；动态开始日期：{{ data.start_date || '—' }}；最晚填充日期：{{ data.end_date }}。</p>
        <p>去年触发日期：{{ data.history_trigger_dates.join('、') || '无' }}。10—11月{{ label }}目标严格高于{{ second ? '800万元' : '2亿元' }}为触发日，触发当天及后5天为尾款日期，重叠日期只计一次。</p>
        <template v-if="data.statistics">
          <div class="stats"><span>历史参考 <b>{{ data.statistics.reference_days }}天</b></span><span>有效逐日参考 <b>{{ data.statistics.valid_reference_days }}天</b></span><span>可填入 <b>{{ data.statistics.fillable_days }}天</b></span><span>跳过 <b>{{ data.statistics.skipped_days }}天</b></span></div>
          <p>按触发日期顺序配对尾款阶段，今年阶段第N天对应去年同一阶段第N天。去年每天分别计算：达成率＝当天实际GMV÷当天目标GMV；客单价＝当天实际GMV÷当天实际单量。今年每天预估单量＝当天目标GMV÷去年对应日客单价×去年对应日达成率，计算后四舍五入为整数，不使用汇总参数。</p>
        </template>
        <el-collapse><el-collapse-item title="查看去年逐日参考目标及实绩明细" name="history"><el-table :data="data.history_rows" border max-height="240"><el-table-column prop="date" label="日期" align="center"/><el-table-column label="GMV目标" align="center"><template #default="s">{{ format(s.row.target_gmv) }}</template></el-table-column><el-table-column label="实际单量" align="center"><template #default="s">{{ format(s.row.actual_order_qty) }}</template></el-table-column><el-table-column label="实际GMV" align="center"><template #default="s">{{ format(s.row.actual_gmv) }}</template></el-table-column><el-table-column label="达成率" align="center"><template #default="s">{{ percent(s.row.achievement_rate) }}</template></el-table-column><el-table-column label="客单价" align="center"><template #default="s">{{ format(s.row.aov) }}</template></el-table-column></el-table></el-collapse-item></el-collapse>
        <el-table v-if="data.rows.length" :data="data.rows" border max-height="340"><el-table-column prop="date" label="今年日期" align="center"/><el-table-column prop="history_date" label="去年对应日期" align="center"/><el-table-column label="阶段天数" align="center"><template #default="s">第{{ s.row.phase_day }}天</template></el-table-column><el-table-column :label="`${label}目标`" align="center"><template #default="s">{{ format(s.row.target_gmv) }}</template></el-table-column><el-table-column label="去年对应日达成率" align="center"><template #default="s">{{ percent(s.row.achievement_rate) }}</template></el-table-column><el-table-column label="去年对应日客单价" align="center"><template #default="s">{{ format(s.row.aov) }}</template></el-table-column><el-table-column label="原预估单量" align="center"><template #default="s">{{ format(currentOrder(s.row.date)) }}</template></el-table-column><el-table-column label="拟填入预估单量" align="center"><template #default="s"><span :title="s.row.skipped_reason">{{ s.row.estimated_order_qty == null ? '跳过：' + s.row.skipped_reason : format(s.row.estimated_order_qty) }}</span></template></el-table-column></el-table>
        <p class="hint">实绩按提供的SQL口径：达播，channel_name {{ second ? '<>' : '=' }} 李佳琦，仅查询薇诺娜天猫旗舰店，不附加平台、订单状态过滤。只改{{ targetChannel }}预估单量，目标为空或去年对应日缺少有效参考的日期单独跳过；应用后点击“保存填报”生效。</p>
      </template>
    </div>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="loading || !availableCount" @click="apply">填入{{ availableCount }}天{{ label }}预估单量</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
const props = defineProps({run: Object, rows: {type: Array, default: () => []}, kind: {type: String, default: 'super'}})
const second=computed(()=>props.kind==='second')
const label=computed(()=>second.value?'二达':'超头')
const targetChannel=computed(()=>second.value?'达播-二梯队':'达播-超头')
const emit = defineEmits(['apply'])
const visible=ref(false), loading=ref(false), error=ref(''), mode=ref('tail_only'), data=ref(null)
let request=0
const availableCount=computed(()=>data.value?.status==='success' ? data.value.rows.filter(row=>row.estimated_order_qty!=null).length : 0)
const formatter=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2})
const format=value=>value==null?'—':formatter.format(value)
const percent=value=>value==null?'—':`${(value*100).toFixed(2)}%`
const currentOrder=day=>props.rows.find(row=>row.targetChannel===targetChannel.value && row.metric==='forecast_orders')?.values[day]?.amount
async function load() {
  const id=++request, runId=props.run?.id
  loading.value=true;error.value='';data.value=null
  try { const result=await (second.value ? gmvApi.forecastSecondAnchor : gmvApi.forecastSuperAnchor)(runId,mode.value); if(id===request && props.run?.id===runId)data.value=result }
  catch(e) { if(id===request)error.value=e.response?.data?.detail || e.message || `${label.value}预估计算失败` }
  finally { if(id===request)loading.value=false }
}
function open() { visible.value=true;load() }
function apply() {
  if(loading.value || !availableCount.value || data.value?.run_id!==props.run?.id)return
  emit('apply',data.value);visible.value=false
}
defineExpose({open})
</script>

<style scoped>
.controls,.stats{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:16px}.stats{padding:16px;background:#eff6ff}.stats span{display:flex;flex-direction:column;gap:6px}p{font-size:13px;line-height:1.8}.hint{color:#667085;font-size:12px}
</style>
