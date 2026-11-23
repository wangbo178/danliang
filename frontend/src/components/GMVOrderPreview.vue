<template>
  <el-dialog v-model="visible" append-to-body title="预览总订单数据" width="97%" top="3vh" destroy-on-close>
    <el-config-provider :locale="zhCn">
      <div class="preview-toolbar">
        <el-radio-group v-model="mode" @change="refresh"><el-radio-button value="orders">订单数据</el-radio-button><el-radio-button value="warehouse">订单分仓</el-radio-button></el-radio-group>
        <span>月份</span><el-select v-model="month" placeholder="选择月份" style="width:180px" @change="selectMonth"><el-option v-for="m in data?.months || []" :key="m" :value="m" :label="monthLabel(m)"/></el-select>
        <span>日期范围</span><el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" format="YYYY年MM月DD日" start-placeholder="开始日期" end-placeholder="结束日期" @change="selectRange"/>
        <el-button :loading="loading" @click="refresh">刷新预览</el-button><el-tag type="info">只读 · 引用已保存的预估单量</el-tag>
      </div>
    </el-config-provider>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <p class="definition">{{ data?.definition }}</p>
    <template v-if="mode === 'warehouse' && data?.profiles">
      <p class="definition">分仓参考期：{{ data.reference_start }} 至 {{ data.reference_end }}（最近7天，不含今天）</p>
      <el-alert v-for="warning in data.warnings" :key="warning" type="warning" :title="warning" :closable="false" show-icon/>
      <el-collapse><el-collapse-item title="查看渠道分仓占比与计算依据" name="ratios">
        <div class="ratio-scroll"><table class="ratio-table"><thead><tr><th>渠道</th><th v-for="warehouse in warehouses" :key="warehouse">{{ warehouse }}</th><th>计算依据</th></tr></thead><tbody><tr v-for="profile in data.profiles" :key="profile.name"><th>{{ profile.name }}</th><td v-for="warehouse in warehouses" :key="warehouse">{{ percent(profile.ratios[warehouse]) }}</td><td :title="profile.valid_days.join('、')">{{ profile.definition }}{{ profile.valid_days.length ? `（${profile.valid_days.length}天有效）` : '' }}</td></tr></tbody></table></div>
      </el-collapse-item></el-collapse>
    </template>
    <div v-loading="loading" element-loading-text="正在汇总预估订单…" class="preview-scroll">
      <table class="preview-table" aria-label="所有渠道预估订单总表">
        <thead><tr><th class="name-column">渠道名称</th><th v-for="day in data?.dates || []" :key="day">{{ day }}</th><th class="range-total">区间合计</th></tr></thead>
        <tbody v-for="group in data?.groups || []" :key="group.name">
          <tr class="shop-row"><th class="name-column" scope="row">{{ group.name }}</th><td v-for="day in data.dates" :key="day"></td><td></td></tr>
          <tr v-for="row in group.rows" :key="row.label" class="order-row"><th class="name-column item-name" scope="row">{{ row.label }}</th><td v-for="day in data.dates" :key="day" :title="evidence(row,day)">{{ format(row.values[day]?.amount) }}</td><td>{{ format(sum(data.dates.map(day=>row.values[day]?.amount))) }}</td></tr>
          <tr class="shop-subtotal"><th class="name-column item-name" scope="row">汇总单量</th><td v-for="day in data.dates" :key="day">{{ format(group.totals[day]) }}</td><td>{{ format(sum(Object.values(group.totals))) }}</td></tr>
        </tbody>
        <tbody v-if="mode === 'warehouse' && data?.warehouse_totals" class="warehouse-summary">
          <tr class="shop-row"><th class="name-column">各仓总单量</th><td v-for="day in data.dates" :key="day"></td><td></td></tr>
          <tr v-for="warehouse in warehouses" :key="warehouse" class="order-row"><th class="name-column item-name">{{ warehouse }}</th><td v-for="day in data.dates" :key="day">{{ format(data.warehouse_totals[warehouse][day]) }}</td><td>{{ format(sum(Object.values(data.warehouse_totals[warehouse]))) }}</td></tr>
          <tr v-if="Object.values(data.unallocated_totals).some(value=>value != null)" class="shop-subtotal"><th class="name-column">待分仓单量</th><td v-for="day in data.dates" :key="day">{{ format(data.unallocated_totals[day]) }}</td><td>{{ format(sum(Object.values(data.unallocated_totals))) }}</td></tr>
        </tbody>
        <tfoot v-if="data?.dates?.length"><tr><th class="name-column">{{ mode === 'warehouse' ? '已分仓合计' : '全部渠道合计' }}</th><td v-for="day in data.dates" :key="day">{{ format(data.totals[day]) }}</td><td>{{ format(sum(Object.values(data.totals))) }}</td></tr></tfoot>
      </table>
      <el-empty v-if="!loading && !data?.groups?.length" description="暂无可预览的预估单量"/>
    </div>
    <p class="definition">单位：单。“—”表示未填写或缺少分仓依据。{{ mode === 'warehouse' ? '各渠道按汇总预估单量分仓；各仓总单量为所有渠道分仓结果之和，待分仓单量不计入已分仓合计。' : '所有店铺共用日期列，店铺汇总行和明细行不重复累加；天猫旗舰店按货架、CPS、派样、直播四类订单展示。' }}</p>
    <template #footer><el-button @click="visible=false">关闭预览</el-button></template>
  </el-dialog>
</template>
<script setup>
import {ref} from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import {gmvApi} from '../api'
const visible=ref(false),loading=ref(false),error=ref(''),data=ref(null),month=ref(''),range=ref([]),mode=ref('orders')
const warehouses=['昆明仓','湖州仓','武汉仓','上海仓']
const percent=value=>value==null?'—':`${(value*100).toFixed(2)}%`
const names=['一','二','三','四','五','六','七','八','九','十','十一','十二']
let request=0
const format=value=>value==null?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(value)
const monthLabel=value=>`${value.slice(0,4)}年${names[Number(value.slice(5))-1]}月`
function sum(values){const known=values.filter(v=>typeof v==='number'&&Number.isFinite(v));return known.length?known.reduce((a,b)=>a+b,0):null}
function evidence(row,day){const cell=row.values[day];return [`来源：${row.source_label || row.channels.join('＋')}`,cell?.source_name||'该日无规划',cell?.input_version?`填报版本 ${cell.input_version}`:'尚无保存的填报版本',cell?.missing_channels?.length?`未填写：${cell.missing_channels.join('、')}`:''].filter(Boolean).join('\n')}
async function load(params={}){
  const id=++request;loading.value=true;error.value='';data.value=null
  try{const result=await (mode.value==='warehouse'?gmvApi.orderWarehousePreview:gmvApi.orderPreview)(params);if(id!==request)return;data.value=result;month.value=result.selected_month||'';range.value=result.start_date?[result.start_date,result.end_date]:[]}
  catch(e){if(id===request)error.value=e.response?.data?.detail||e.message||'总订单预览加载失败'}
  finally{if(id===request)loading.value=false}
}
function selectMonth(){load({month:month.value})}
function selectRange(){if(range.value?.length===2)load({start_date:range.value[0],end_date:range.value[1]});else load()}
function refresh(){if(month.value)load({month:month.value});else if(range.value?.length===2)selectRange();else load()}
function open(){visible.value=true;mode.value='orders';load()}
defineExpose({open})
</script>
<style scoped>
.ratio-scroll{overflow:auto}.ratio-table{border-collapse:collapse;width:100%;font-size:13px}.ratio-table th,.ratio-table td{padding:8px 12px;text-align:center;border:1px solid #d0d5dd}.ratio-table thead{background:#eff6ff}
.shop-subtotal{background:#dbeafe;color:#1e3a5f;font-weight:800}
.preview-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}.definition{font-size:12px;color:#667085;line-height:1.8;margin:10px 0}.preview-scroll{overflow:auto;max-height:70vh;border:1px solid #d0d5dd}.preview-table{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0;font-size:14px;font-variant-numeric:tabular-nums}.preview-table th,.preview-table td{min-width:145px;height:46px;padding:10px 12px;text-align:center;white-space:nowrap;border-right:1px solid #496382;border-bottom:1px solid #496382}.preview-table .name-column{position:sticky;left:0;min-width:200px;width:200px;text-align:left;padding-left:20px;z-index:2;background:inherit}.preview-table thead th{position:sticky;top:0;background:#f5f7fa;color:#475467;z-index:3}.preview-table thead .name-column{z-index:4}.shop-row{background:#294e7b;color:white;font-weight:800}.order-row{background:#1e3a5f;color:white;font-weight:600}.preview-table .item-name{padding-left:34px}.preview-table tfoot th,.preview-table tfoot td{position:sticky;bottom:0;background:#eff6ff;color:#1e3a5f;font-weight:700;z-index:3}.preview-table tfoot .name-column{z-index:4}
</style>
