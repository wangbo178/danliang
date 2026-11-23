<template>
  <el-dialog v-model="visible" append-to-body title="一键预估小样节单量" width="920px" top="5vh" destroy-on-close>
    <div class="controls"><span>填充日期（手动指定）</span><el-date-picker v-model="fillDate" type="date" value-format="YYYY-MM-DD" :disabled-date="disabledDate" placeholder="请选择规划内的日期"/><span>附近日期 T+</span><el-input-number v-model="nearbyDays" :min="0" :max="30" :precision="0" style="width:125px" @change="load"/><el-button :loading="loading" @click="load">重新计算</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-loading="loading" element-loading-text="正在识别历年10月小样节并按年汇总…">
      <template v-if="data && !loading">
        <p>识别口径：往年10月，<b>单日订单量＞100,000单</b>且<b>当天客单价＜10元</b>判定为首日；<template v-if="data.definition.nearby_days">首日之后T+1至T+{{ data.definition.nearby_days }}日，若<b>单量＞40,000且客单价＜10元</b>也计入。附近日期可跨至11月，不从新增日期继续递推；</template><template v-else>不追加附近日期；</template>仅查询薇诺娜天猫旗舰店的达播、李佳琦实绩，排除{{ data.target_year }}年。</p>
        <el-empty v-if="data.status === 'no_samples'" description="历史数据中没有符合条件的小样节日期，未生成填充值"/>
        <el-alert v-if="data.status === 'out_of_range'" type="error" title="计算结果超出可填报单量范围，无法填充" :closable="false"/>
        <template v-if="data.yearly_rows.length">
          <el-table :data="data.yearly_rows" border max-height="250"><el-table-column prop="year" label="年份" align="center"/><el-table-column prop="seed_days" label="首日天数" align="center"/><el-table-column prop="nearby_days" label="附近日期天数" align="center"/><el-table-column prop="sample_days" label="合计天数（去重）" align="center"/><el-table-column label="当年小样节合计单量" align="center"><template #default="s">{{ format(s.row.order_qty) }}</template></el-table-column></el-table>
          <p><b>{{ format(data.statistics.total_order_qty) }}单 ÷ {{ data.statistics.year_count }}年＝{{ format(data.statistics.mean_order_qty) }}单</b>，四舍五入后填入 <b>{{ format(data.statistics.estimated_order_qty) }}单</b>。</p>
          <p class="hint">先将每年首日及附近符合条件日期去重后的订单量相加，再把各年总量相加÷参与年份数。没有识别到小样节的年份不纳入分母。</p>
          <el-collapse><el-collapse-item title="查看识别出的小样节每日明细" name="daily"><el-table :data="data.daily_rows" border max-height="250"><el-table-column prop="date" label="历史日期" align="center"/><el-table-column label="入选依据" align="center"><template #default="s">{{ s.row.inclusion_type === 'seed' ? '首日＞10万' : `T+${s.row.offset_days}＞4万` }}</template></el-table-column><el-table-column label="实际单量" align="center"><template #default="s">{{ format(s.row.order_qty) }}</template></el-table-column><el-table-column label="实际GMV（元）" align="center"><template #default="s">{{ format(s.row.payment_amount) }}</template></el-table-column><el-table-column label="客单价（元）" align="center"><template #default="s"><span :title="String(s.row.aov)">{{ aovFormat.format(s.row.aov) }}</span></template></el-table-column></el-table></el-collapse-item></el-collapse>
        </template>
        <p v-if="data.unmatched_years.length" class="hint">{{ data.unmatched_years.join('、') }}年没有符合条件的小样节日期，不计入参与年份数。</p>
        <p v-if="data.invalid_actual_days" class="hint">{{ data.invalid_actual_days }}天实绩字段缺失或无效，未参与识别。</p>
        <p v-if="fillDate" class="hint">将填入{{ fillDate }}的“达播-超头预估单量”，原值为{{ format(currentOrder) }}单。应用后点击“保存填报”生效。</p>
      </template>
    </div>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="loading || !canApply" @click="apply">填入指定日期的超头预估单量</el-button></template>
  </el-dialog>
</template>

<script setup>
import {computed, ref} from 'vue'
import {gmvApi} from '../api'
const props=defineProps({run:Object,rows:{type:Array,default:()=>[]}})
const emit=defineEmits(['apply'])
const visible=ref(false),loading=ref(false),error=ref(''),data=ref(null),fillDate=ref(''),nearbyDays=ref(1)
let request=0
const days=computed(()=>[...new Set((props.run?.result?.records || []).filter(r=>r.kind==='day').map(r=>r.period))])
const canApply=computed(()=>data.value?.status==='success' && data.value.statistics.estimated_order_qty!=null && days.value.includes(fillDate.value))
const currentOrder=computed(()=>props.rows.find(row=>row.targetChannel==='达播-超头' && row.metric==='forecast_orders')?.values[fillDate.value]?.amount)
const formatter=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2})
const aovFormat=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:6})
const format=value=>value==null?'—':formatter.format(value)
function disabledDate(value) {
  const date=`${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`
  return !days.value.includes(date)
}
async function load() {
  const id=++request,runId=props.run?.id
  loading.value=true;error.value='';data.value=null
  try {const result=await gmvApi.forecastSampleFestival(runId,nearbyDays.value ?? 1);if(id===request && props.run?.id===runId)data.value=result}
  catch(e){if(id===request)error.value=e.response?.data?.detail || e.message || '小样节单量计算失败'}
  finally{if(id===request)loading.value=false}
}
function open(){fillDate.value='';nearbyDays.value=1;visible.value=true;load()}
function apply(){if(loading.value || !canApply.value || data.value.run_id!==props.run?.id)return;emit('apply',{...data.value,fill_date:fillDate.value});visible.value=false}
defineExpose({open})
</script>

<style scoped>
.controls{display:flex;align-items:center;gap:12px;margin-bottom:16px}p{font-size:14px;line-height:1.8}.hint{font-size:12px;color:#667085}
</style>
