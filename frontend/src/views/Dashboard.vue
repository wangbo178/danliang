<template>
  <div>
    <h1 class="page-title"><el-icon class="page-title-icon"><DataLine /></el-icon>数据看板</h1>
    <p class="page-subtitle">汇总历史数据、预测规模和模型健康度，快速进入日常运营流程。</p>

    <el-alert v-if="loadError" :title="loadError" type="error" show-icon class="dashboard-alert">
      <template #default><el-button size="small" @click="loadData">重新加载</el-button></template>
    </el-alert>

    <!-- KPI 卡片行 -->
    <el-row :gutter="20" style="margin-bottom:20px">
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          label="渠道订单总量"
          :value="overview.channel_orders"
          :icon="Box"
          color="#2563eb"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          label="数据时间范围"
          :value="dateRange"
          :icon="Calendar"
          color="#f79009"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          label="预测总单量"
          :value="summary.total_predicted_orders"
          :icon="TrendCharts"
          color="#12b76a"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <MetricCard
          :label="summary.wape_method === 'overall' ? '整体 WAPE' : '历史平均 WAPE'"
          :value="summary.overall_wape ?? summary.avg_wape"
          unit="%"
          :icon="DataAnalysis"
          color="#0e9384"
          :loading="loading"
          :sub="summary.avg_bias != null ? `Bias: ${summary.avg_bias}%` : ''"
        />
      </el-col>
    </el-row>

    <!-- 核心经营趋势 -->
    <el-row :gutter="20" class="trend-row">
      <el-col :xs="24" :xl="12">
        <div class="card trend-card" v-loading="warehouseLoading">
          <div class="card-header dashboard-card-header">
            <div>
              <span class="card-title">逻辑仓每日订单量（付款日）</span>
              <div class="chart-subtitle">源库最新历史 · 按逻辑仓拆分</div>
            </div>
            <el-radio-group v-model="warehouseDays" size="small" @change="loadWarehouseTrend">
              <el-radio-button :value="7">近7天</el-radio-button>
              <el-radio-button :value="30">近30天</el-radio-button>
            </el-radio-group>
          </div>
          <el-empty v-if="!warehouseLoading && !warehouseTrend.dates.length" description="暂无逻辑仓订单数据" />
          <v-chart v-else :option="warehouseTrendOption" class="trend-chart" autoresize />
        </div>
      </el-col>
      <el-col :xs="24" :xl="12">
        <div class="card trend-card" v-loading="aovLoading">
          <div class="card-header dashboard-card-header aov-header">
            <div>
              <span class="card-title">平均客单价趋势</span>
              <div class="chart-subtitle">
                源库最新历史 · 周期加权平均
                <strong>{{ formatCurrency(aovTrend.average_aov) }}</strong>
              </div>
            </div>
            <div class="trend-filters">
              <el-select v-model="aovFilters.shop_id" clearable filterable placeholder="全部店铺" size="small" @change="loadAovTrend">
                <el-option
                  v-for="shop in aovTrend.filters.shops"
                  :key="shop.shop_id"
                  :label="shop.name === shop.shop_id ? shop.shop_id : `${shop.name}（${shop.shop_id}）`"
                  :value="shop.shop_id"
                />
              </el-select>
              <el-select v-model="aovFilters.channel" clearable filterable placeholder="全部渠道" size="small" @change="loadAovTrend">
                <el-option v-for="channel in aovTrend.filters.channels" :key="channel" :label="channel" :value="channel" />
              </el-select>
              <el-radio-group v-model="aovDays" size="small" @change="loadAovTrend">
                <el-radio-button :value="7">近7天</el-radio-button>
                <el-radio-button :value="30">近30天</el-radio-button>
              </el-radio-group>
            </div>
          </div>
          <el-empty v-if="!aovLoading && !hasAovData" description="当前筛选范围暂无付款金额数据" />
          <v-chart v-else :option="aovTrendOption" class="trend-chart" autoresize />
        </div>
      </el-col>
    </el-row>

    <!-- 历史回测验证 -->
    <div class="card backtest-card" v-loading="backtestLoading">
      <div class="card-header dashboard-card-header">
        <div>
          <span class="card-title">历史回测：预测与实发差距</span>
          <div class="chart-subtitle">最新模型 {{ backtestData.job_id || '—' }} · 正数表示预测偏高，负数表示预测偏低</div>
        </div>
        <el-radio-group v-model="backtestDays" size="small" @change="loadBacktest">
          <el-radio-button :value="7">近7天</el-radio-button>
          <el-radio-button :value="30">近30天</el-radio-button>
        </el-radio-group>
      </div>

      <el-empty
        v-if="!backtestLoading && !backtestData.dates.length"
        :description="backtestData.requires_retrain ? '当前模型尚无逐日回测明细，请重新训练一次模型' : '暂无历史回测数据'"
      />
      <el-row v-else :gutter="20" class="backtest-grid">
        <el-col :xs="24" :xl="12">
          <div class="backtest-panel">
            <div class="backtest-panel-title">总体差距</div>
            <div class="backtest-kpis">
              <span>实发 <b>{{ formatOrders(backtestData.total.summary?.actual) }}</b></span>
              <span>预测 <b>{{ formatOrders(backtestData.total.summary?.predicted) }}</b></span>
              <span>累计差值 <b :class="gapClass(backtestData.total.summary?.gap)">{{ formatGap(backtestData.total.summary?.gap) }}</b></span>
              <span>WAPE <b>{{ formatPercent(backtestData.total.summary?.wape) }}</b></span>
            </div>
            <v-chart :option="totalBacktestOption" class="backtest-chart" autoresize />
          </div>
        </el-col>
        <el-col :xs="24" :xl="12">
          <div class="backtest-panel">
            <div class="backtest-panel-heading">
              <div class="backtest-panel-title">分仓差距</div>
              <el-select v-model="selectedWarehouse" filterable placeholder="选择仓库" size="small">
                <el-option v-for="warehouse in backtestData.warehouse_ids" :key="warehouse" :label="warehouse" :value="warehouse" />
              </el-select>
            </div>
            <div class="backtest-kpis">
              <span>实发 <b>{{ formatOrders(selectedWarehouseData.summary?.actual) }}</b></span>
              <span>预测 <b>{{ formatOrders(selectedWarehouseData.summary?.predicted) }}</b></span>
              <span>累计差值 <b :class="gapClass(selectedWarehouseData.summary?.gap)">{{ formatGap(selectedWarehouseData.summary?.gap) }}</b></span>
              <span>WAPE <b>{{ formatPercent(selectedWarehouseData.summary?.wape) }}</b></span>
            </div>
            <v-chart v-if="selectedWarehouse" :option="warehouseBacktestOption" class="backtest-chart" autoresize />
            <el-empty v-else description="暂无分仓回测数据" :image-size="70" />
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 趋势图 + 渠道占比 -->
    <el-row :gutter="20" class="secondary-row">
      <el-col :xs="24" :lg="16">
        <div class="card">
          <div class="card-header">
            <span class="card-title">渠道订单规模</span>
            <el-button text size="small" :loading="loading" @click="loadData">刷新</el-button>
          </div>
          <v-chart :option="trendOption" style="height:320px" autoresize />
        </div>
      </el-col>
      <el-col :xs="24" :lg="8">
        <div class="card">
          <div class="card-header">
            <span class="card-title">渠道订单占比</span>
            <span class="chart-caption">TOP 8 + 其他</span>
          </div>
          <v-chart :option="pieOption" style="height:320px" autoresize />
        </div>
      </el-col>
    </el-row>

    <!-- 快速入口 -->
    <el-row :gutter="20" style="margin-top:20px">
      <el-col :span="24">
        <div class="card">
          <div class="card-header">
            <span class="card-title">快速入口</span>
          </div>
          <el-row :gutter="12">
            <el-col v-for="item in quickLinks" :key="item.path" :xs="24" :sm="12" :lg="6">
              <button class="quick-link" type="button" @click="$router.push(item.path)">
                <span class="quick-icon"><el-icon><component :is="item.icon" /></el-icon></span>
                <div>
                  <div class="quick-title">{{ item.title }}</div>
                  <div class="quick-desc">{{ item.desc }}</div>
                </div>
              </button>
            </el-col>
          </el-row>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import { dataApi, resultsApi } from '../api/index.js'
import { DataLine, Box, Calendar, TrendCharts, DataAnalysis, Upload, Setting, Cpu } from '@element-plus/icons-vue'

const loading = ref(true)
const warehouseLoading = ref(true)
const aovLoading = ref(true)
const backtestLoading = ref(true)
const loadError = ref('')
const overview = ref({ channel_orders: 0, channel_stats: [], date_range: {} })
const summary = ref({})
const warehouseDays = ref(7)
const aovDays = ref(7)
const backtestDays = ref(7)
const selectedWarehouse = ref('')
const warehouseTrend = ref({ dates: [], warehouses: [], series: {}, totals: [], batch_id: null })
const aovTrend = ref({
  series: [], average_aov: null,
  filters: { shops: [], channels: [] },
  batch_id: null,
})
const aovFilters = reactive({ shop_id: '', channel: '' })
const backtestData = ref({
  job_id: null, dates: [], total: { actual: [], predicted: [], gap: [], summary: {} },
  warehouses: {}, warehouse_ids: [], requires_retrain: false,
})
const CHART_COLORS = ['#2563eb', '#0ea5e9', '#12b76a', '#f79009', '#7c3aed', '#14b8a6', '#e11d48', '#84cc16']

const dateRange = computed(() => {
  const dr = overview.value.date_range || {}
  if (dr.min && dr.max) return `${dr.min} ~ ${dr.max}`
  return '暂无数据'
})

const hasAovData = computed(() => aovTrend.value.series.some(item => item.avg_order_value != null))

function shortDate(value) {
  return value?.slice(5) || value
}

function formatCurrency(value) {
  return value == null
    ? '暂无数据'
    : `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatOrders(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

function formatGap(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toLocaleString('zh-CN')}`
}

function formatPercent(value) {
  return value == null ? '—' : `${Number(value).toFixed(1)}%`
}

function gapClass(value) {
  const number = Number(value || 0)
  return number > 0 ? 'gap-over' : number < 0 ? 'gap-under' : ''
}

const selectedWarehouseData = computed(() => (
  backtestData.value.warehouses[selectedWarehouse.value]
  || { actual: [], predicted: [], gap: [], summary: {} }
))

function createBacktestOption(seriesData) {
  const dates = backtestData.value.dates || []
  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const gap = Number(seriesData.gap?.[index] || 0)
        return `${dates[index] || ''}<br/>实发：<b>${formatOrders(seriesData.actual?.[index])}</b><br/>预测：<b>${formatOrders(seriesData.predicted?.[index])}</b><br/>差值：<b>${formatGap(gap)}</b>`
      },
    },
    legend: { top: 0, data: ['实发单量', '预测单量', '预测差值'] },
    grid: { left: 18, right: 18, top: 42, bottom: 20, containLabel: true },
    xAxis: {
      type: 'category', data: dates.map(shortDate),
      axisLine: { lineStyle: { color: '#e4e7ec' } },
      axisLabel: { color: '#667085' },
    },
    yAxis: [
      {
        type: 'value', name: '单量', minInterval: 1,
        splitLine: { lineStyle: { color: '#f2f4f7' } },
        axisLabel: { color: '#667085' },
      },
      {
        type: 'value', name: '差值', minInterval: 1,
        splitLine: { show: false },
        axisLabel: { color: '#98a2b3' },
      },
    ],
    series: [
      {
        name: '实发单量', type: 'line', smooth: true,
        data: seriesData.actual || [], symbolSize: 7,
        lineStyle: { color: '#12b76a', width: 3 }, itemStyle: { color: '#12b76a' },
        label: {
          show: backtestDays.value === 7, position: 'top', color: '#027a48', fontSize: 10,
          formatter: ({ value }) => formatOrders(value),
        },
      },
      {
        name: '预测单量', type: 'line', smooth: true,
        data: seriesData.predicted || [], symbolSize: 7,
        lineStyle: { color: '#2563eb', width: 3 }, itemStyle: { color: '#2563eb' },
        label: {
          show: backtestDays.value === 7, position: 'bottom', color: '#175cd3', fontSize: 10,
          formatter: ({ value }) => formatOrders(value),
        },
      },
      {
        name: '预测差值', type: 'bar', yAxisIndex: 1,
        data: seriesData.gap || [], barMaxWidth: 24,
        itemStyle: { color: ({ value }) => Number(value) >= 0 ? '#f79009' : '#f04438', opacity: .72 },
        label: {
          show: backtestDays.value === 7, position: 'top',
          color: '#667085', fontSize: 10, formatter: ({ value }) => formatGap(value),
        },
      },
    ],
  }
}

const totalBacktestOption = computed(() => createBacktestOption(backtestData.value.total))
const warehouseBacktestOption = computed(() => createBacktestOption(selectedWarehouseData.value))

const warehouseTrendOption = computed(() => {
  const { dates, warehouses, series, totals } = warehouseTrend.value
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { type: 'scroll', top: 2, right: 8, data: [...warehouses, '每日合计'] },
    grid: { left: 20, right: 18, top: 48, bottom: 18, containLabel: true },
    xAxis: {
      type: 'category', data: dates.map(shortDate),
      axisLine: { lineStyle: { color: '#e4e7ec' } },
      axisLabel: { color: '#667085' },
    },
    yAxis: {
      type: 'value', name: '发货单量', minInterval: 1,
      axisLabel: { color: '#667085' },
      splitLine: { lineStyle: { color: '#f2f4f7' } },
    },
    series: [
      ...warehouses.map((warehouse, index) => ({
        name: warehouse, type: 'bar', stack: 'warehouse',
        data: series[warehouse] || [],
        itemStyle: { color: CHART_COLORS[index % CHART_COLORS.length] },
        barMaxWidth: 36,
      })),
      {
        name: '每日合计', type: 'line', data: totals, smooth: true,
        symbolSize: 7, z: 10,
        lineStyle: { color: '#344054', width: 3 },
        itemStyle: { color: '#344054' },
        label: {
          show: warehouseDays.value === 7,
          position: 'top',
          distance: 8,
          color: '#344054',
          fontSize: 11,
          fontWeight: 600,
          formatter: ({ value }) => Number(value || 0).toLocaleString('zh-CN'),
        },
      },
    ],
  }
})

const aovTrendOption = computed(() => {
  const rows = aovTrend.value.series || []
  const average = aovTrend.value.average_aov
  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const row = rows[params?.[0]?.dataIndex] || {}
        return `${row.date || ''}<br/>客单价：<b>${formatCurrency(row.avg_order_value)}</b><br/>订单量：${Number(row.order_count || 0).toLocaleString('zh-CN')}<br/>付款金额：${formatCurrency(row.payment_amount)}`
      },
    },
    grid: { left: 20, right: 20, top: 36, bottom: 18, containLabel: true },
    xAxis: {
      type: 'category', boundaryGap: false, data: rows.map(item => shortDate(item.date)),
      axisLine: { lineStyle: { color: '#e4e7ec' } },
      axisLabel: { color: '#667085' },
    },
    yAxis: {
      type: 'value', name: '客单价（元）', scale: true,
      axisLabel: { color: '#667085', formatter: value => `¥${value}` },
      splitLine: { lineStyle: { color: '#f2f4f7' } },
    },
    series: [{
      name: '客单价', type: 'line', smooth: true, connectNulls: false,
      data: rows.map(item => item.avg_order_value),
      symbol: 'circle', symbolSize: 8,
      lineStyle: { color: '#7c3aed', width: 3 },
      itemStyle: { color: '#7c3aed', borderColor: '#fff', borderWidth: 2 },
      areaStyle: { color: 'rgba(124, 58, 237, .10)' },
      label: {
        show: aovDays.value === 7,
        position: 'top',
        distance: 8,
        color: '#6941c6',
        fontSize: 11,
        fontWeight: 600,
        formatter: ({ value }) => value == null ? '' : `¥${Number(value).toFixed(2)}`,
      },
      markLine: average == null ? undefined : {
        symbol: 'none',
        label: { formatter: `周期平均 ${formatCurrency(average)}`, color: '#6941c6' },
        lineStyle: { color: '#9e77ed', type: 'dashed', width: 2 },
        data: [{ yAxis: average }],
      },
    }],
  }
})

// 趋势图 option（渠道统计条形图）
const trendOption = computed(() => {
  const stats = overview.value.channel_stats || []
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 16, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: stats.map(s => s.channel),
      axisLabel: { color: '#666', fontSize: 12 },
      axisLine: { lineStyle: { color: '#eee' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#666', fontSize: 12 },
      splitLine: { lineStyle: { color: '#f0f0f0' } },
    },
    series: [{
      type: 'bar',
      data: stats.map(s => s.total_orders),
      label: {
        show: true,
        position: 'top',
        color: '#475467',
        fontSize: 11,
        formatter: ({ value }) => Number(value || 0).toLocaleString('zh-CN'),
      },
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: '#2563eb' },
            { offset: 1, color: '#60a5fa' },
          ],
        },
      },
      barMaxWidth: 50,
    }],
  }
})

// 饼图 option
const pieOption = computed(() => {
  const stats = [...(overview.value.channel_stats || [])]
    .filter(s => Number(s.total_orders) > 0)
    .sort((a, b) => Number(b.total_orders) - Number(a.total_orders))
  const primary = stats.slice(0, 8).map(s => ({
    name: s.channel,
    value: Number(s.total_orders) || 0,
  }))
  const otherValue = stats.slice(8).reduce((sum, s) => sum + (Number(s.total_orders) || 0), 0)
  const pieData = otherValue > 0
    ? [...primary, { name: `其他（${stats.length - 8}个渠道）`, value: otherValue }]
    : primary

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: ({ name, value, percent }) => `${name}<br/>订单量：<b>${Number(value).toLocaleString('zh-CN')}</b><br/>占比：${percent}%`,
    },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      right: 0,
      top: 'middle',
      height: 232,
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 10,
      pageIconSize: 10,
      pageTextStyle: { color: '#667085', fontSize: 11 },
      textStyle: { color: '#475467', fontSize: 11, width: 112, overflow: 'truncate' },
      tooltip: { show: true },
    },
    series: [{
      type: 'pie',
      radius: ['38%', '66%'],
      center: ['32%', '50%'],
      data: pieData,
      itemStyle: { borderRadius: 4, borderWidth: 2, borderColor: '#fff' },
      label: {
        show: true,
        color: '#475467',
        fontSize: 10,
        formatter: ({ name, value, percent }) => `${name}\n${Number(value).toLocaleString('zh-CN')}（${percent}%）`,
      },
      labelLine: { length: 8, length2: 6 },
      minShowLabelAngle: 4,
      emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
    }],
    color: ['#2563eb', '#0ea5e9', '#12b76a', '#f79009', '#7c3aed', '#667085', '#14b8a6', '#e11d48', '#84cc16'],
    media: [{
      query: { maxWidth: 480 },
      option: {
        legend: {
          orient: 'horizontal',
          left: 'center',
          right: 'auto',
          top: 'auto',
          bottom: 0,
          width: '92%',
          height: 42,
          itemGap: 8,
        },
        series: [{ center: ['50%', '40%'], radius: ['34%', '58%'] }],
      },
    }],
  }
})

const quickLinks = [
  { path: '/data',     icon: Upload, title: '查看数据',  desc: '查询源库订单并维护排期' },
  { path: '/config',   icon: Setting, title: '配置管理',  desc: '维护平台渠道店铺' },
  { path: '/training', icon: Cpu, title: '开始训练',  desc: '启动 ML 模型训练' },
  { path: '/results',  icon: TrendCharts, title: '查看结果',  desc: '预测结果与评估指标' },
]

async function loadData() {
  loading.value = true
  loadError.value = ''
  try {
    const [ov, sm] = await Promise.all([
      dataApi.getOverview(),
      resultsApi.getSummary(),
    ])
    overview.value = ov
    summary.value = sm
  } catch {
    loadError.value = '看板数据加载失败，请检查 API 服务后重试。'
  } finally {
    loading.value = false
  }
}

async function loadWarehouseTrend() {
  warehouseLoading.value = true
  try {
    warehouseTrend.value = await dataApi.getWarehouseDaily({ recent_days: warehouseDays.value })
  } finally {
    warehouseLoading.value = false
  }
}

async function loadAovTrend() {
  aovLoading.value = true
  try {
    const response = await dataApi.getAovDaily({
      recent_days: aovDays.value,
      shop_id: aovFilters.shop_id || undefined,
      channel: aovFilters.channel || undefined,
    })
    aovTrend.value = response
  } finally {
    aovLoading.value = false
  }
}

async function loadBacktest() {
  backtestLoading.value = true
  try {
    const response = await resultsApi.getBacktestDaily({ days: backtestDays.value })
    backtestData.value = response
    if (!response.warehouse_ids?.includes(selectedWarehouse.value)) {
      selectedWarehouse.value = response.warehouse_ids?.[0] || ''
    }
  } finally {
    backtestLoading.value = false
  }
}

onMounted(() => Promise.allSettled([loadData(), loadWarehouseTrend(), loadAovTrend(), loadBacktest()]))
</script>

<style scoped>
.quick-link {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  border-radius: 12px;
  border: 1.5px solid #f0f0f0;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: all 0.2s;
}

.dashboard-alert { margin-bottom: 20px; }
.chart-caption { color: #98a2b3; font-size: 11px; letter-spacing: .3px; }
.trend-row { margin-bottom: 20px; }
.secondary-row { margin-bottom: 0; }
.trend-card { min-height: 410px; }
.trend-chart { height: 330px; }
.dashboard-card-header { gap: 16px; align-items: flex-start; }
.chart-subtitle { margin-top: 5px; color: #98a2b3; font-size: 12px; font-weight: 400; }
.chart-subtitle strong { color: #6941c6; font-size: 14px; margin-left: 4px; }
.trend-filters { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.trend-filters .el-select { width: 138px; }
.backtest-card { margin-bottom: 20px; min-height: 430px; }
.backtest-grid { margin-top: 18px; }
.backtest-panel { border: 1px solid #eaecf0; border-radius: 12px; padding: 16px; }
.backtest-panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.backtest-panel-heading .el-select { width: 160px; }
.backtest-panel-title { color: #344054; font-size: 14px; font-weight: 650; }
.backtest-kpis { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 12px; color: #667085; font-size: 12px; }
.backtest-kpis b { margin-left: 4px; color: #344054; font-size: 13px; }
.backtest-kpis .gap-over { color: #b54708; }
.backtest-kpis .gap-under { color: #d92d20; }
.backtest-chart { height: 320px; margin-top: 8px; }

@media (max-width: 991px) {
  .card { margin-top: 12px; }
  .trend-card { min-height: 380px; }
  .dashboard-card-header { flex-direction: column; }
  .trend-filters { width: 100%; justify-content: flex-start; }
  .trend-filters .el-select { width: 160px; }
  .backtest-panel { margin-top: 12px; }
}

.quick-link:hover {
  border-color: #93c5fd;
  background: #f8fbff;
  transform: translateY(-2px);
}

.quick-icon {
  width: 42px;
  height: 42px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 22px;
}

.quick-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a2e;
}

.quick-desc {
  font-size: 12px;
  color: #8c8c8c;
  margin-top: 2px;
}
</style>
