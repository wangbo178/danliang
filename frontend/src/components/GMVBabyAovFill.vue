<template>
  <el-dialog v-model="visible" append-to-body title="一键填充预估单量（除达播）" width="94%" top="5vh" destroy-on-close>
    <p>{{ dates[0] }} ～ {{ dates.at(-1) }}；填入自播官旗、商城、KOC、DP合计，达播保持原值。</p>
    <el-alert type="info" :closable="false" title="月均客单价＝上月总实付金额÷上月总单量，保留两位小数；预估单量＝各渠道当天目标GMV÷该客单价，四舍五入取整。跨月分别使用各自上月客单价。"/>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <div v-loading="loading" element-loading-text="正在查询抖音宝贝上月客单价…">
      <el-table :data="data?.rows || []" border>
        <el-table-column prop="month" label="填充月份" align="center"/>
        <el-table-column prop="history_month" label="参考月份" align="center"/>
        <el-table-column label="实绩覆盖" align="center"><template #default="s">{{ s.row.valid_actual_days }}/{{ s.row.calendar_days }}天</template></el-table-column>
        <el-table-column label="上月总单量" align="center"><template #default="s">{{ format(s.row.total_order_qty) }}</template></el-table-column>
        <el-table-column label="上月实付金额" align="center"><template #default="s">{{ format(s.row.total_pay_amount) }}</template></el-table-column>
        <el-table-column label="月均客单价" align="center"><template #default="s">{{ format(s.row.mean_aov) }}</template></el-table-column>
      </el-table>
      <el-table :data="selected" border max-height="300" style="margin-top:16px">
        <el-table-column prop="date" label="日期" align="center"/>
        <el-table-column prop="channel" label="渠道" align="center"/>
        <el-table-column label="目标GMV" align="center"><template #default="s">{{ format(s.row.gmv) }}</template></el-table-column>
        <el-table-column label="填入客单价" align="center"><template #default="s">{{ format(s.row.aov) }}</template></el-table-column>
        <el-table-column label="填入单量" align="center"><template #default="s">{{ s.row.amount == null ? '跳过' : format(s.row.amount) }}</template></el-table-column>
      </el-table>
    </div>
    <p class="hint">仅查询“抖音-薇诺娜宝贝专卖店”和“抖音-薇诺娜宝贝母婴旗舰店”。上月数据不完整或客单价无效时跳过，保留原值。</p>
    <p class="hint">应用会覆盖所选日期四个渠道的预估客单和预估单量；点击“保存填报”后生效。</p>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="loading || !available" @click="apply">应用到{{ available }}项</el-button></template>
  </el-dialog>
</template>
<script setup>
import {computed,ref} from 'vue'
import {gmvApi} from '../api'
const props=defineProps({run:Object}), emit=defineEmits(['apply'])
const visible=ref(false),loading=ref(false),error=ref(''),data=ref(null),dates=ref([])
let request=0
const selected=computed(()=>(data.value?.forecasts || []).filter(row=>dates.value.includes(row.date)))
const available=computed(()=>selected.value.filter(row=>row.amount!=null).length)
const format=value=>value==null?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value)
async function open(selectedDates){
  const id=++request,runId=props.run?.id
  dates.value=[...new Set(selectedDates)].sort();visible.value=true;loading.value=true;data.value=null;error.value=''
  try{const result=await gmvApi.forecastBabyAov(runId,[...new Set(dates.value.map(day=>day.slice(0,7)))]);if(id===request&&props.run?.id===runId)data.value=result}
  catch(e){if(id===request)error.value=e.response?.data?.detail||e.message||'宝贝客单价查询失败'}
  finally{if(id===request)loading.value=false}
}
function apply(){if(loading.value||!available.value||data.value?.run_id!==props.run?.id)return;emit('apply',{...data.value,dates:[...dates.value]});visible.value=false}
defineExpose({open})
</script>
<style scoped>p{font-size:14px;line-height:1.8}.hint{font-size:12px;color:#667085}</style>
