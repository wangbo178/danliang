<template>
  <div>
    <h1 class="page-title"><el-icon class="page-title-icon"><TrendCharts /></el-icon>预测结果</h1>
    <p class="page-subtitle">查看最新任务的预测趋势、精度指标与分仓计划。趋势数据已在服务端聚合，明细按页加载。</p>

    <!-- KPI -->
    <el-row :gutter="20" style="margin-bottom:20px" v-if="summary.has_results">
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard label="预测总单量" :value="summary.total_predicted_orders" :icon="TrendCharts" color="#2563eb" :sub="predictionBreakdownText" :loading="loading" />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard label="预测天数" :value="summary.forecast_days" :icon="Calendar" color="#f79009" unit="天" :loading="loading" />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          :label="summary.wape_method === 'overall' ? '校准后整体 WAPE' : '历史平均 WAPE'"
          :value="summary.overall_wape ?? summary.avg_wape"
          :icon="DataAnalysis"
          color="#12b76a"
          unit="%"
          :sub="wapeComparisonText"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          label="平均 Bias"
          :value="summary.avg_bias"
          :icon="ScaleToOriginal"
          color="#0e9384"
          unit="%"
          :loading="loading"
        />
      </el-col>
    </el-row>

    <el-alert
      v-if="!summary.has_results && !loading"
      title="暂无预测结果，请先完成模型训练"
      type="info"
      show-icon
      style="margin-bottom:20px"
    />

    <el-tabs v-model="activeTab" @tab-change="handleTabChange">
      <!-- ── 可编辑预测排期表 ── -->
      <el-tab-pane label="预测排期表" name="sheet">
        <ForecastSheet @saved="handleForecastSaved" />
      </el-tab-pane>

      <!-- ── 预测趋势图 ── -->
      <el-tab-pane label="预测趋势" name="trend" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">渠道预测趋势</span>
            <div class="result-toolbar">
              <span v-if="summary.finished_at" class="result-freshness">更新于 {{ formatTime(summary.finished_at) }}</span>
              <el-select v-model="filterChannel" clearable placeholder="全部渠道" size="small" style="width:160px" @change="loadPredictions">
                <el-option v-for="c in channelOptions" :key="c" :label="c" :value="c" />
              </el-select>
              <el-button size="small" :loading="loadingTrend" @click="loadPredictions">刷新</el-button>
              <el-button class="btn-primary" size="small" type="primary" @click="resultsApi.exportExcel()">
                <el-icon><Download /></el-icon>导出 Excel
              </el-button>
            </div>
          </div>

          <el-skeleton v-if="loadingTrend" :rows="7" animated />
          <v-chart v-else-if="predictions.length" :option="trendOption" style="height:380px" autoresize />
          <el-empty v-else description="当前筛选条件下没有预测数据" />
        </div>
      </el-tab-pane>

      <!-- ── 评估指标 ── -->
      <el-tab-pane label="评估指标" name="metrics" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">渠道级评估指标（Holdout）</span>
          </div>

          <el-alert
            v-if="summary.anchor_wape != null"
            :title="`达人直播子模型：WAPE ${summary.anchor_wape.toFixed(1)}%，Bias ${summary.anchor_bias?.toFixed(1) ?? '—'}%，飞书目标基线 WAPE ${summary.anchor_target_wape?.toFixed(1) ?? '—'}%`"
            type="info"
            show-icon
            :closable="false"
            style="margin-bottom:16px"
          />

          <el-table :data="metrics" stripe border size="small" v-loading="loading">
            <el-table-column prop="group_key" label="渠道" />
            <el-table-column label="原始 WAPE" width="110" align="right">
              <template #default="{ row }">
                <span v-if="row.raw_wape != null">{{ row.raw_wape.toFixed(1) }}%</span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column width="120" align="right">
              <template #header>
                <el-tooltip placement="top" :show-after="200">
                  <template #content>
                    <div style="max-width:220px;line-height:1.6">
                      <b>WAPE（加权绝对百分比误差）</b><br/>
                      = Σ|实际-预测| / Σ实际 × 100%<br/>
                      对零值免疫，适合稀疏渠道。<br/>
                      <span style="color:#12b76a">优：&lt;15%</span>
                      <span style="color:#faad14">中：15-30%</span>
                      <span style="color:#f5222d">差：&gt;30%</span>
                    </div>
                  </template>
                  <span style="cursor:help;border-bottom:1px dashed #999">校准后 WAPE ℹ</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">
                <span :style="{ color: getMapeColor((row.wape ?? row.mae) / 100) }">
                  {{ (row.wape ?? row.mae) != null ? (row.wape ?? row.mae).toFixed(1) + '%' : '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="WAPE 改善" width="105" align="right">
              <template #default="{ row }">
                <span v-if="row.raw_wape != null && row.wape != null" :style="{ color: row.raw_wape >= row.wape ? '#12b76a' : '#f5222d' }">
                  {{ row.raw_wape >= row.wape ? '↓' : '↑' }}{{ Math.abs(row.raw_wape - row.wape).toFixed(1) }}pp
                </span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column prop="n_samples" label="样本数" width="90" align="right" />
            <el-table-column label="实际总量" width="120" align="right">
              <template #default="{ row }">
                {{ row.total_actual != null ? Math.round(row.total_actual).toLocaleString('zh-CN') : '—' }}
              </template>
            </el-table-column>
            <el-table-column label="原始 Bias" width="100" align="right">
              <template #default="{ row }">
                <span :style="{ color: getBiasColor(row.raw_bias) }">
                  {{ row.raw_bias != null ? (row.raw_bias * 100).toFixed(1) + '%' : '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column width="110" align="right">
              <template #header>
                <el-tooltip placement="top" :show-after="200">
                  <template #content>
                    <div style="max-width:220px;line-height:1.6">
                      <b>RMSE（均方根误差）</b><br/>
                      = √(Σ(实际-预测)² / n)<br/>
                      单位与订单量相同，对大偏差更敏感。<br/>
                      数值越小越好，无固定阈值。
                    </div>
                  </template>
                  <span style="cursor:help;border-bottom:1px dashed #999">RMSE ℹ</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">{{ row.rmse?.toFixed(1) ?? '—' }}</template>
            </el-table-column>
            <el-table-column width="100">
              <template #header>
                <el-tooltip placement="top" :show-after="200">
                  <template #content>
                    <div style="max-width:220px;line-height:1.6">
                      <b>MAPE（平均绝对百分比误差）</b><br/>
                      = mean(|实际-预测| / 实际) × 100%<br/>
                      仅对实际&gt;0的行计算，直观反映单条误差。<br/>
                      <span style="color:#12b76a">优：&lt;15%</span>
                      <span style="color:#faad14">中：15-30%</span>
                      <span style="color:#f5222d">差：&gt;30%</span>
                    </div>
                  </template>
                  <span style="cursor:help;border-bottom:1px dashed #999">MAPE ℹ</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">
                <span :style="{ color: getMapeColor(row.mape) }">
                  {{ row.mape != null ? (row.mape * 100).toFixed(1) + '%' : '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column width="100">
              <template #header>
                <el-tooltip placement="top" :show-after="200">
                  <template #content>
                    <div style="max-width:220px;line-height:1.6">
                      <b>Bias（系统性偏差）</b><br/>
                      = mean(预测 - 实际) / mean(|实际|) × 100%<br/>
                      正值 = 系统性高估，负值 = 系统性低估。<br/>
                      <span style="color:#12b76a">优：|Bias|&lt;10%</span>
                      <span style="color:#faad14">中：10-25%</span>
                      <span style="color:#f5222d">差：&gt;25%</span>
                    </div>
                  </template>
                  <span style="cursor:help;border-bottom:1px dashed #999">校准后 Bias ℹ</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">
                <span :style="{ color: getBiasColor(row.bias) }">
                  {{ row.bias != null ? (row.bias * 100).toFixed(1) + '%' : '—' }}
                </span>
              </template>
            </el-table-column>
          </el-table>

          <!-- 指标说明卡片 -->
          <el-collapse style="margin-top:16px">
            <el-collapse-item title="指标说明">
              <el-row :gutter="16" style="padding:4px 0">
                <el-col :span="12" v-for="m in metricDefs" :key="m.name" style="margin-bottom:12px">
                  <div style="display:flex;gap:10px;align-items:flex-start">
                    <el-tag :color="m.color" style="color:#fff;flex-shrink:0;font-weight:600">{{ m.name }}</el-tag>
                    <div style="font-size:13px;color:#555;line-height:1.7">
                      <div>{{ m.desc }}</div>
                      <div style="color:#888;font-size:12px;margin-top:2px">{{ m.formula }}</div>
                    </div>
                  </div>
                </el-col>
              </el-row>
            </el-collapse-item>
          </el-collapse>

          <!-- 评估指标条形图 -->
          <v-chart v-if="metrics.length" :option="metricsOption" style="height:320px;margin-top:20px" autoresize />
        </div>
      </el-tab-pane>

      <!-- ── 分仓统计 ── -->
      <el-tab-pane label="分仓统计" name="warehouse" lazy>
        <!-- 汇总排名 -->
        <div class="card">
          <div class="card-header">
            <span class="card-title">各仓预测总单量排名</span>
            <span style="font-size:13px;color:#999">共 {{ warehouseStats.length }} 个仓</span>
          </div>
          <el-table :data="warehouseStats" stripe border size="small" v-loading="loadingWh" style="margin-bottom:20px">
            <el-table-column type="index" label="排名" width="60" align="center" />
            <el-table-column prop="warehouse_id" label="仓库" />
            <el-table-column label="预测总单量" align="right" sortable>
              <template #default="{ row }">
                <span style="font-weight:600;color:#2563eb">
                  {{ row.total_orders.toLocaleString('zh-CN') }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="占比" align="right" width="90">
              <template #default="{ row }">
                {{ whTotal > 0 ? ((row.total_orders / whTotal) * 100).toFixed(1) + '%' : '—' }}
              </template>
            </el-table-column>
          </el-table>
          <v-chart v-if="warehouseStats.length" :option="warehouseOption" style="height:280px" autoresize />
        </div>

        <!-- 按日趋势 -->
        <div class="card" style="margin-top:0">
          <div class="card-header">
            <span class="card-title">分仓每日预测单量</span>
            <span style="font-size:13px;color:#999">{{ whDaily.dates.length }} 天</span>
          </div>
          <v-chart v-if="whDaily.dates.length" :option="whDailyOption" style="height:320px" autoresize />
          <el-empty v-else description="暂无按日数据" />

          <!-- 日期 × 仓库 透视表 -->
          <div v-if="whDaily.dates.length" style="margin-top:20px;overflow-x:auto">
            <table class="wh-pivot-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th v-for="wh in whDaily.warehouses" :key="wh">{{ wh }}</th>
                  <th>合计</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(d, i) in whDaily.dates" :key="d">
                  <td>{{ d }}</td>
                  <td v-for="wh in whDaily.warehouses" :key="wh" class="num">
                    {{ (whDaily.series[wh]?.[i] ?? 0).toLocaleString('zh-CN') }}
                  </td>
                  <td class="num total">{{ (whDaily.totals[i] ?? 0).toLocaleString('zh-CN') }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <td><b>合计</b></td>
                  <td v-for="wh in whDaily.warehouses" :key="wh" class="num total">
                    {{ (whDaily.series[wh]?.reduce((s,v)=>s+v,0) ?? 0).toLocaleString('zh-CN') }}
                  </td>
                  <td class="num total">{{ whDaily.totals.reduce((s,v)=>s+v,0).toLocaleString('zh-CN') }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </el-tab-pane>

      <!-- ── 明细数据 ── -->
      <el-tab-pane label="明细数据" name="detail" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">预测明细</span>
            <el-button class="btn-primary" size="small" type="primary" @click="resultsApi.exportExcel()">
              <el-icon><Download /></el-icon>导出 Excel
            </el-button>
          </div>

          <div class="detail-toolbar">
            <el-input
              v-model="detailKeyword"
              clearable
              placeholder="搜索店铺、渠道或平台"
              style="max-width:300px"
              @keyup.enter="searchDetails"
              @clear="searchDetails"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-button type="primary" plain @click="searchDetails">查询</el-button>
          </div>

          <el-table
            :data="detailPredictions"
            stripe border size="small"
            max-height="500"
            v-loading="loadingDetail"
            empty-text="暂无匹配数据"
          >
            <el-table-column prop="date" label="日期" width="120" sortable />
            <el-table-column prop="shop_id" label="店铺" width="120" />
            <el-table-column prop="channel" label="渠道" width="130" />
            <el-table-column prop="platform" label="平台" width="100" />
            <el-table-column label="非达人基础" width="120" sortable>
              <template #default="{ row }">
                {{ row.base_predicted_orders != null ? Math.round(row.base_predicted_orders).toLocaleString() : '—' }}
              </template>
            </el-table-column>
            <el-table-column label="达人直播加单" width="130" sortable>
              <template #default="{ row }">
                <span :style="{ color: row.anchor_predicted_orders > 0 ? '#7c3aed' : '' }">
                  {{ row.anchor_predicted_orders != null ? Math.round(row.anchor_predicted_orders).toLocaleString() : '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="最终模型单量" width="130" sortable>
              <template #default="{ row }">
                {{ row.predicted_orders != null ? Math.round(row.predicted_orders).toLocaleString() : '—' }}
              </template>
            </el-table-column>
            <el-table-column label="原始预测" width="110" sortable>
              <template #default="{ row }">
                {{ row.raw_predicted_orders != null ? Math.round(row.raw_predicted_orders).toLocaleString() : '—' }}
              </template>
            </el-table-column>
          </el-table>

          <div class="detail-footer">
            <span>共 {{ detailTotal.toLocaleString('zh-CN') }} 条</span>
            <el-pagination
              v-model:current-page="detailPage"
              v-model:page-size="detailPageSize"
              :total="detailTotal"
              :page-sizes="[20, 50, 100, 200]"
              layout="total, sizes, prev, pager, next"
              background
              @current-change="loadDetails"
              @size-change="handleDetailSizeChange"
            />
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import ForecastSheet from '../components/FeishuForecastSheet.vue'
import { resultsApi } from '../api/index.js'
import { TrendCharts, Calendar, DataAnalysis, ScaleToOriginal, Download } from '@element-plus/icons-vue'

const loading = ref(true)
const loadingTrend = ref(false)
const loadingDetail = ref(false)
const loadingWh = ref(false)
const activeTab = ref('sheet')
const filterChannel = ref('')
const predictions = ref([])
const channelOptions = ref([])
const detailPredictions = ref([])
const detailKeyword = ref('')
const detailPage = ref(1)
const detailPageSize = ref(50)
const detailTotal = ref(0)
const metrics = ref([])
const summary = ref({ has_results: false })
const warehouseStats = ref([])
const whDaily = ref({ dates: [], warehouses: [], series: {}, totals: [] })

const whTotal = computed(() =>
  warehouseStats.value.reduce((s, r) => s + r.total_orders, 0)
)

const wapeComparisonText = computed(() => {
  if (summary.value.raw_wape == null) return ''
  const improvement = summary.value.wape_improvement
  const suffix = improvement == null
    ? ''
    : improvement >= 0 ? `，改善 ${improvement.toFixed(1)}pp` : `，恶化 ${Math.abs(improvement).toFixed(1)}pp`
  return `原始 ${Number(summary.value.raw_wape).toFixed(1)}%${suffix}`
})

const predictionBreakdownText = computed(() => {
  if (summary.value.total_base_predicted_orders == null) return ''
  const base = Number(summary.value.total_base_predicted_orders).toLocaleString('zh-CN')
  const anchor = Number(summary.value.total_anchor_predicted_orders || 0).toLocaleString('zh-CN')
  const share = Number(summary.value.anchor_share || 0).toFixed(1)
  return `基础 ${base} + 达人直播 ${anchor}（${share}%）`
})

const CHART_COLORS = ['#2563eb','#0ea5e9','#12b76a','#f79009','#7c3aed','#14b8a6','#475467','#e11d48','#84cc16','#06b6d4']

const whDailyOption = computed(() => {
  const { dates, warehouses, series, totals } = whDaily.value
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const date = params[0].axisValue
        let html = `<b>${date}</b><br/>`
        params.forEach(p => {
          html += `${p.marker}${p.seriesName}：${p.value.toLocaleString('zh-CN')}<br/>`
        })
        const total = totals[dates.indexOf(date)] ?? 0
        html += `<b>合计：${total.toLocaleString('zh-CN')}</b>`
        return html
      },
    },
    legend: { data: warehouses, bottom: 0, textStyle: { color: '#666' } },
    grid: { left: 16, right: 16, top: 16, bottom: 50, containLabel: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 28 }],
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#666', fontSize: 11, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#666', formatter: v => v.toLocaleString('zh-CN') },
      splitLine: { lineStyle: { color: '#f0f0f0' } },
    },
    series: warehouses.map((wh, i) => ({
      name: wh,
      type: 'bar',
      stack: 'total',
      data: series[wh] || [],
      itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
      emphasis: { focus: 'series' },
    })),
  }
})

const warehouseOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (p) => `${p[0].name}<br/>预测单量：<b>${p[0].value.toLocaleString('zh-CN')}</b>`,
  },
  grid: { left: 16, right: 16, top: 16, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    data: warehouseStats.value.map(r => r.warehouse_id),
    axisLabel: { color: '#666', fontSize: 12 },
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#666', formatter: (v) => v.toLocaleString('zh-CN') },
    splitLine: { lineStyle: { color: '#f0f0f0' } },
  },
  series: [{
    type: 'bar',
    data: warehouseStats.value.map(r => r.total_orders),
    itemStyle: {
      borderRadius: [6, 6, 0, 0],
      color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: '#2563eb' }, { offset: 1, color: '#60a5fa' }] },
    },
    barMaxWidth: 50,
    label: {
      show: true,
      position: 'top',
      formatter: (p) => p.value.toLocaleString('zh-CN'),
      color: '#555',
      fontSize: 11,
    },
  }],
}))

// ── 趋势图 ──────────────────────────────────────────────────────────────
const trendOption = computed(() => {
  const data = filterChannel.value
    ? predictions.value.filter(p => p.channel === filterChannel.value)
    : predictions.value

  // 按渠道分组
  const grouped = {}
  for (const p of data) {
    if (!grouped[p.channel]) grouped[p.channel] = {}
    grouped[p.channel][p.date] = (grouped[p.channel][p.date] || 0) + (p.predicted_orders || 0)
  }

  const allDates = [...new Set(data.map(p => p.date))].sort()
  const channels = Object.keys(grouped)
  const colors = ['#2563eb', '#0ea5e9', '#12b76a', '#f79009', '#7c3aed', '#14b8a6', '#475467', '#e11d48']

  // 每日汇总
  const totals = allDates.map(d =>
    +(channels.reduce((s, ch) => s + (grouped[ch][d] || 0), 0).toFixed(1))
  )
  const rawTotals = allDates.map(d => +data
    .filter(p => p.date === d)
    .reduce((sum, p) => sum + (p.raw_predicted_orders ?? p.predicted_orders ?? 0), 0)
    .toFixed(1))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v != null ? v.toFixed(1) : '—'),
    },
    legend: { data: [...channels, '校准后汇总', '原始汇总'], bottom: 0, textStyle: { color: '#666' } },
    grid: { left: 16, right: 16, top: 16, bottom: 50, containLabel: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 30 }],
    xAxis: {
      type: 'category',
      data: allDates,
      axisLabel: { color: '#666', fontSize: 11, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#666', formatter: (v) => v.toFixed(1) },
      splitLine: { lineStyle: { color: '#f0f0f0' } },
    },
    series: [
      ...channels.map((ch, i) => ({
        name: ch,
        type: 'line',
        smooth: true,
        data: allDates.map(d => grouped[ch][d] != null ? +grouped[ch][d].toFixed(1) : 0),
        itemStyle: { color: colors[i % colors.length] },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 4,
      })),
      {
        name: '校准后汇总',
        type: 'line',
        smooth: true,
        data: totals,
        itemStyle: { color: '#333' },
        lineStyle: { width: 3, type: 'dashed' },
        symbol: 'circle',
        symbolSize: 5,
        z: 10,
      },
      {
        name: '原始汇总',
        type: 'line',
        smooth: true,
        data: rawTotals,
        itemStyle: { color: '#98a2b3' },
        lineStyle: { width: 2, type: 'dotted' },
        symbol: 'none',
        z: 9,
      },
    ],
  }
})

// ── 评估指标图 ──────────────────────────────────────────────────────────
const metricsOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: { left: 16, right: 16, top: 16, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    data: metrics.value.map(m => m.group_key),
    axisLabel: { color: '#666', fontSize: 12 },
  },
  yAxis: [
    { type: 'value', name: 'WAPE %', axisLabel: { color: '#666', formatter: (v) => `${v.toFixed(0)}%` } },
  ],
  series: [
    {
      name: '原始 WAPE',
      type: 'bar',
      data: metrics.value.map(m => m.raw_wape),
      itemStyle: { color: '#cbd5e1', borderRadius: [5, 5, 0, 0] },
      barMaxWidth: 28,
    },
    {
      name: '校准后 WAPE',
      type: 'bar',
      data: metrics.value.map(m => m.wape ?? m.mae),
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: '#2563eb' }, { offset: 1, color: '#60a5fa' }] },
      },
      barMaxWidth: 40,
      label: {
        show: true,
        position: 'top',
        formatter: (p) => p.value != null ? `${p.value.toFixed(1)}%` : '',
        color: '#666',
        fontSize: 11,
      },
    },
  ],
}))

const metricDefs = [
  {
    name: 'WAPE',
    color: '#2563eb',
    desc: '加权绝对百分比误差，对零值免疫，适合稀疏渠道，是最推荐的整体精度指标。',
    formula: '= Σ|实际−预测| / Σ实际 × 100%，越小越好，目标 <15%',
  },
  {
    name: 'RMSE',
    color: '#f79009',
    desc: '均方根误差，单位与订单量相同，对大偏差惩罚更重，用于衡量极端误差风险。',
    formula: '= √mean((实际−预测)²)，越小越好，无固定阈值',
  },
  {
    name: 'MAPE',
    color: '#12b76a',
    desc: '平均绝对百分比误差，仅对实际>0的行计算，直观反映单日误差水平。',
    formula: '= mean(|实际−预测| / 实际) × 100%，越小越好，目标 <15%',
  },
  {
    name: 'Bias',
    color: '#0e9384',
    desc: '系统性偏差，正值代表模型长期高估，负值代表长期低估，是备货决策的关键参考。',
    formula: '= mean(预测−实际) / mean(|实际|) × 100%，目标 |Bias| <10%',
  },
]

function getMapeColor(v) {
  if (v == null) return '#999'
  const pct = v * 100
  if (pct < 15) return '#12b76a'
  if (pct < 30) return '#faad14'
  return '#f5222d'
}

function getBiasColor(v) {
  if (v == null) return '#999'
  const pct = Math.abs(v * 100)
  if (pct < 10) return '#12b76a'
  if (pct < 25) return '#faad14'
  return '#f5222d'
}

async function loadPredictions() {
  loadingTrend.value = true
  const params = { view: 'trend' }
  if (filterChannel.value) params.channel = filterChannel.value
  try {
    const res = await resultsApi.getLatestPredictions(params)
    predictions.value = res.predictions || []
    channelOptions.value = res.channels || []
  } finally {
    loadingTrend.value = false
  }
}

async function loadDetails() {
  loadingDetail.value = true
  try {
    const res = await resultsApi.getLatestPredictions({
      view: 'detail',
      page: detailPage.value,
      page_size: detailPageSize.value,
      keyword: detailKeyword.value || undefined,
    })
    detailPredictions.value = res.predictions || []
    detailTotal.value = res.total || 0
  } finally {
    loadingDetail.value = false
  }
}

function searchDetails() {
  detailPage.value = 1
  loadDetails()
}

function handleDetailSizeChange() {
  detailPage.value = 1
  loadDetails()
}

function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function handleTabChange(name) {
  if (name === 'trend' && !predictions.value.length) loadPredictions()
  if (name === 'warehouse' && !warehouseStats.value.length) loadWarehouseStats()
  if (name === 'detail' && !detailPredictions.value.length) loadDetails()
}

async function handleForecastSaved() {
  const [sm] = await Promise.all([
    resultsApi.getSummary(),
    loadPredictions(),
  ])
  summary.value = sm
}

async function loadWarehouseStats() {
  loadingWh.value = true
  try {
    const [stats, daily] = await Promise.all([
      resultsApi.getWarehouseStats(),
      resultsApi.getWarehouseDaily(),
    ])
    warehouseStats.value = stats
    whDaily.value = daily
  } finally {
    loadingWh.value = false
  }
}

async function loadData() {
  loading.value = true
  try {
    const [sm, met] = await Promise.all([
      resultsApi.getSummary(),
      resultsApi.getMetrics({ level: 'channel' }),
    ])
    summary.value = sm
    metrics.value = met
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.result-toolbar, .detail-toolbar, .detail-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.result-freshness { color: #8a94a6; font-size: 12px; white-space: nowrap; }
.detail-toolbar { margin-bottom: 16px; }
.detail-footer { justify-content: space-between; margin-top: 14px; color: #8a94a6; font-size: 13px; }

.wh-pivot-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.wh-pivot-table th,
.wh-pivot-table td {
  border: 1px solid #eee;
  padding: 6px 10px;
  white-space: nowrap;
}
.wh-pivot-table th {
  background: #f7f8fa;
  color: #555;
  font-weight: 600;
  text-align: center;
}
.wh-pivot-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.wh-pivot-table td.total {
  font-weight: 600;
  color: #2563eb;
  background: #f5f4ff;
}
.wh-pivot-table tfoot td {
  background: #f0f0f0;
  font-weight: 700;
}
.wh-pivot-table tbody tr:hover td {
  background: #fafafa;
}

@media (max-width: 768px) {
  .result-toolbar { width: 100%; }
  .result-toolbar .el-select { flex: 1; min-width: 140px; }
  .detail-footer { align-items: flex-start; }
  .detail-footer .el-pagination { overflow-x: auto; max-width: 100%; padding-bottom: 4px; }
}
</style>
