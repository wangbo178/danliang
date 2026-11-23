<template>
  <el-dialog v-model="visible" append-to-body :title="referenceTwo ? '历史参考二' : '特殊货架数据填充'" width="95%" top="5vh" destroy-on-close>
    <div class="toolbar"><span>{{ dates[0] }} ～ {{ dates.at(-1) }}</span><span>历史范围</span><el-select v-model="scope" style="width:230px" @change="load"><el-option label="2024-10-01起的历史实绩" value="since_2024_10"/><el-option label="预测日前一年" value="rolling_year"/><el-option label="本年预测日前" value="current_year"/><el-option label="上个月" value="previous_month"/></el-select><el-button :loading="loading" @click="load">重新计算</el-button></div>
    <el-alert type="info" :closable="false" :title="referenceTwo ? '只处理当前货架预估单量为空或0的日期。匹配历史实际达成GMV，差额绝对值÷当前目标GMV不超过10%；取有效实际单量均值。已有非零单量保持原值。' : '仅填充货架目标GMV高于1,500万元的日期。匹配历史货架实际达成GMV，与当前目标相差不超过500万元；全部匹配日期按接近程度排序，取有效实际单量的平均值。'"/>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <div v-loading="loading" element-loading-text="正在匹配特殊货架历史实绩…">
      <el-table :data="data?.rows || []" border row-key="date">
        <el-table-column type="expand"><template #default="s"><div class="sample-detail"><p>历史范围：{{ s.row.history_start }} 至 {{ s.row.history_end_exclusive }} 之前</p><el-table :data="s.row.samples" border max-height="300"><el-table-column prop="date" label="实际日期" align="center"/><el-table-column label="实际达成GMV" align="center"><template #default="a">{{ format(a.row.actual_gmv) }}</template></el-table-column><el-table-column label="与当前目标差额" align="center"><template #default="a">{{ format(a.row.difference) }}</template></el-table-column><el-table-column label="实际单量" align="center"><template #default="a">{{ format(a.row.order_qty) }}</template></el-table-column><el-table-column label="计入均值" align="center"><template #default="a">{{ a.row.included ? '是' : '缺少有效单量' }}</template></el-table-column></el-table></div></template></el-table-column>
        <el-table-column prop="date" :label="referenceTwo ? '待填充日期' : '特殊货架日期'" align="center"/>
        <el-table-column label="目标GMV" align="center"><template #default="s">{{ format(Math.round(s.row.target_gmv)) }}</template></el-table-column>
        <el-table-column prop="closest_date" label="最接近的实绩日期" align="center"/>
        <el-table-column label="匹配说明" align="center" min-width="220"><template #default="s"><span v-if="!s.row.matched_count && s.row.nearest_actual">最近实绩 {{ s.row.nearest_actual.date }}：{{ format(s.row.nearest_actual.actual_gmv) }}元，差 {{ format(Math.abs(s.row.nearest_actual.difference)) }}元<span v-if="referenceTwo">（{{ (Math.abs(s.row.nearest_actual.difference_rate)*100).toFixed(2) }}%）</span>，超过{{ referenceTwo ? '10%' : '500万元' }}，跳过</span><span v-else-if="!s.row.matched_count">历史范围内无有效实绩</span><span v-else-if="!s.row.sample_count">匹配日期缺少有效单量，跳过</span><span v-else>按有效单量均值填充</span></template></el-table-column>
        <el-table-column label="匹配 / 有效单量日期" align="center"><template #default="s">{{ s.row.matched_count }} / {{ s.row.sample_count }}天</template></el-table-column>
        <el-table-column label="平均单量" align="center"><template #default="s">{{ format(s.row.mean_order_qty) }}</template></el-table-column>
        <el-table-column label="填入单量" align="center"><template #default="s">{{ format(s.row.estimated_order_qty) }}</template></el-table-column>
        <el-table-column label="操作" align="center"><template #default="s"><el-button text :disabled="s.row.estimated_order_qty == null || loading" @click="apply([s.row])">填充此日</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="data && !data.rows.length" :description="referenceTwo ? '所选范围没有货架预估单量为空或0的日期，或目标GMV不大于0' : '所选范围没有货架目标高于1,500万元的日期'"/>
    </div>
    <p class="hint">展开每一行可查看全部参考日期、实际GMV和单量。只使用预测日之前且已结束日期的实绩；无匹配或缺少有效单量的日期跳过，实际0单计入均值。{{ referenceTwo ? '已有非零货架预估单量保持原值。' : '普通货架日期保持原值。' }}</p>
    <p class="hint">填入货架-常规预估单量，四舍五入为整数，点击“保存填报”后生效。</p>
    <template #footer><el-button @click="visible=false">取消</el-button><el-button type="primary" :disabled="loading || !data?.available_count" @click="apply(data.rows)">填充全部{{ data?.available_count || 0 }}个匹配日期</el-button></template>
  </el-dialog>
</template>
<script setup>
import {ref,computed} from 'vue'
import {gmvApi} from '../api'
const props=defineProps({run:Object,kind:{type:String,default:'special'},forecastRows:Array}),emit=defineEmits(['apply'])
const visible=ref(false),loading=ref(false),error=ref(''),data=ref(null),dates=ref([]),scope=ref('since_2024_10')
const referenceTwo=computed(()=>props.kind==='reference_two')
let request=0
const format=value=>value==null?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value)
async function load(){
  const id=++request,runId=props.run?.id
  loading.value=true;error.value='';data.value=null
  try{
    const row=props.forecastRows?.find(row=>row.targetChannel==='货架-常规'&&row.metric==='forecast_orders')
    const eligible=referenceTwo.value ? dates.value.filter(day=>row&&(row.values[day]?.amount==null||row.values[day].amount===0)) : dates.value
    if(referenceTwo.value&&!eligible.length){data.value={run_id:runId,channel:'货架-常规',rows:[],available_count:0};return}
    const result=await (referenceTwo.value ? gmvApi.historyReferenceTwo(runId,eligible,scope.value) : gmvApi.forecastSpecialShelf(runId,dates.value[0],dates.value.at(-1),scope.value))
    if(id===request&&props.run?.id===runId)data.value=result
  }
  catch(e){if(id===request)error.value=e.response?.data?.detail||e.message||'特殊货架查询失败'}
  finally{if(id===request)loading.value=false}
}
function open(selectedDates){dates.value=[...new Set(selectedDates)].sort();visible.value=true;load()}
function apply(rows){if(loading.value||data.value?.run_id!==props.run?.id)return;emit('apply',{...data.value,rows,dates:[...dates.value]});visible.value=false}
defineExpose({open})
</script>
<style scoped>.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px}.hint{font-size:12px;line-height:1.8;color:#667085}.sample-detail{padding:16px}</style>
