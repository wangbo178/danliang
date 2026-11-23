<template>
  <div class="gmv-native-scroll" tabindex="0" aria-label="渠道目标表，可横向滚动">
    <table class="gmv-native-table">
      <thead><tr><th class="metric-column">指标 / 渠道</th><th v-for="period in periods" :key="period">{{ period }}</th></tr></thead>
      <tbody>
        <tr v-for="row in displayRows" :key="row.key" :class="[row.kind + '-row', { 'achievement-row': row.metric === 'achievement_rate' }]">
          <th class="metric-column" scope="row"><div class="metric-label"><span class="metric-badge" :class="row.kind">{{ row.kind === 'target' ? '预期' : '实际' }}</span><span>{{ row.kind === 'actual' ? row.channel.replace(/^实际/, '') : row.channel }}</span></div></th>
          <td v-for="cell in row.cells" :key="cell.period" :title="cell.title"><template v-if="editable && row.editable !== false && ['forecast_aov', 'forecast_orders'].includes(row.metric)"><input class="forecast-input" :value="editValue(row, cell.period)" :aria-label="`${row.targetChannel} ${cell.period} ${row.channel}`" :placeholder="row.metric === 'forecast_orders' && cell.text !== '—' ? `自动 ${cell.text}` : '未填写'" inputmode="decimal" @input="event => emit('cell-input', {channel: row.targetChannel, metric: row.metric, period: cell.period, value: event.target.value})"/><small v-if="row.metric === 'forecast_orders'" class="cell-origin">{{ ['派样','PDD','抖音精选联盟订单','抖音达播'].includes(row.targetChannel) ? (row.values[cell.period]?.manual ? '已填报' : '未填写') : row.values[cell.period]?.manual ? '手填覆盖' : '自动计算' }}</small></template><span v-else class="gmv-value" :class="cell.trend?.strong ? `strong-${cell.trend.direction}` : ''">{{ cell.text }}</span><small v-if="cell.trend" class="gmv-trend" :class="cell.trend.strong ? `strong-${cell.trend.direction}` : 'trend-muted'">{{ cell.trend.label }}</small></td>
        </tr>
      </tbody>
      <tfoot><tr><td v-for="(cell, index) in summaries" :key="index" :class="{'metric-column': index === 0}"><RenderCell :node="cell"/></td></tr></tfoot>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { gmvDayTrend } from '../utils/gmvTrend.mjs'
const props = defineProps({rows: Array, periods: Array, summaries: Array, format: Function, evidence: Function, showTrend: Boolean, editable: Boolean, editValue: Function})
const emit = defineEmits(['cell-input'])
const RenderCell = props => props.node
RenderCell.props = ['node']
const displayRows = computed(() => props.rows.map((row, index) => ({...row, key: `${row.kind}-${row.channel}-${index}`,
  cells: props.periods.map((period, index) => {
    const previousPeriod = props.periods[index - 1]
    const trend = props.showTrend && row.kind === 'target' && !row.metric
      ? gmvDayTrend(row.values[previousPeriod]?.amount, row.values[period]?.amount, previousPeriod, period) : null
    return {period, text: props.format(row, row.values[period]?.amount), trend,
      title: [props.evidence(row.values[period]), trend?.title].filter(Boolean).join('\n')}
  })})))
</script>

<style scoped>
.gmv-native-scroll{max-height:530px;overflow:auto;margin-top:16px;border:1px solid #d0d5dd;position:relative;isolation:isolate}.gmv-native-table{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0;font-size:14px;font-variant-numeric:tabular-nums}.gmv-native-table th,.gmv-native-table td{box-sizing:border-box;min-width:150px;height:44px;padding:10px 12px;text-align:center;white-space:nowrap;border-right:1px solid #e4e7ec;border-bottom:1px solid #e4e7ec}.gmv-native-table .metric-column{position:sticky;left:0;min-width:210px;width:210px;z-index:2;background:inherit}.gmv-native-table thead th{position:sticky;top:0;z-index:3;background:#f5f7fa;color:#606266}.gmv-native-table thead .metric-column{z-index:4;background:#f5f7fa}.target-row{background:#1e3a5f;color:white;font-weight:700}.target-row th,.target-row td{border-color:#496382}.target-row:hover{background:#294e7b}.actual-row{background:#fff7e6;color:#713f12;font-weight:500}.actual-row th,.actual-row td{border-color:#ecd6ae}.actual-row:hover{background:#ffedc2}.actual-row th{box-shadow:inset 4px 0 #d97706}.achievement-row{background:#ffedc2;font-weight:700}.metric-label{display:grid;grid-template-columns:44px minmax(0,1fr);align-items:center;gap:10px;padding-left:8px;text-align:left}.metric-badge{box-sizing:border-box;width:44px;font-size:11px;line-height:20px;padding:0 7px;border-radius:4px;font-weight:700;text-align:center}.metric-badge.target{color:#fff;background:#38649a;border:1px solid #8caed6}.metric-badge.actual{color:#fff;background:#92400e;border:1px solid #92400e}.gmv-native-table tfoot td{position:sticky;bottom:0;z-index:3;padding:0;background:#eff6ff;color:#1e3a5f;font-weight:700}.gmv-native-table tfoot .metric-column{z-index:4;background:#eff6ff}.gmv-native-table :deep(.summary-item){height:36px;line-height:36px;padding:0 8px;border-top:1px solid #d6e1ef}.gmv-native-table :deep(.summary-actual){background:#ffedc2;color:#713f12;border-top-color:#e6c995}.gmv-native-table tfoot .metric-column :deep(.summary-item){text-align:left;padding-left:20px}
</style>
<style scoped>
.gmv-value{display:block}.gmv-trend{display:block;margin-top:4px;font-size:11px;line-height:16px;font-weight:600}.trend-muted{color:#d5e0f0}.strong-up{color:#ffb4ad;font-weight:800}.strong-down{color:#78e8bb;font-weight:800}
.forecast-input{box-sizing:border-box;width:125px;height:30px;border:1px solid #8caed6;border-radius:4px;text-align:center;background:#fff;color:#172033;font:inherit;font-weight:500}.forecast-input:focus{outline:2px solid #93c5fd}.forecast-input::placeholder{color:#667085;font-size:12px}.cell-origin{display:block;margin-top:3px;font-size:10px;color:#d5e0f0}
</style>
