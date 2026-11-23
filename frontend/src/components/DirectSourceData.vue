<template>
  <div class="source-data">
    <div class="card source-header">
      <div>
        <h2>数据库数据</h2>
        <p>直接查询源库当前数据，无需上传或选择批次。</p>
      </div>
      <el-button type="primary" :loading="refreshing" @click="refresh">刷新最新数据</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <div class="source-cards" v-loading="refreshing">
      <button v-for="item in sourceList" :key="item.kind" class="card source-card"
        :class="{ selected: kind === item.kind }" @click="kind = item.kind">
        <span>{{ labels[item.kind] }}</span>
        <strong>{{ Number(item.count || 0).toLocaleString('zh-CN') }}</strong>
        <small>{{ item.date_start || '—' }} 至 {{ item.date_end || '—' }}</small>
        <el-tag size="small" type="success">只读直连</el-tag>
      </button>
    </div>
    <div class="card">
      <div class="source-caption">
        <strong>{{ labels[kind] }}明细</strong>
        <span v-if="overview.queried_at">查询时间 {{ overview.queried_at.replace('T', ' ') }}</span>
      </div>
      <el-alert v-if="kind === 'warehouse'" type="info" show-icon :closable="false"
        title="库存地点按地名归并逻辑仓；源表无店铺维度，分仓使用整体历史占比。" />
      <el-alert v-if="kind === 'anchor'" type="info" show-icon :closable="false"
        title="当前仅按主播匹配历史。来源店铺名称仅作参考，不与系统店铺强行关联；训练时按场次去重。" />
      <div class="source-filters">
        <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD"
          range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" />
        <el-select v-if="kind === 'channel'" v-model="shop" filterable clearable placeholder="全部店铺" style="width:230px">
          <el-option v-for="item in shops" :key="item.shop_id" :value="item.shop_id"
            :label="`${item.name || item.shop_id}（${item.shop_id}）`" />
        </el-select>
        <el-input v-if="kind === 'channel'" v-model.trim="channel" clearable placeholder="渠道（精确名称）" style="width:180px" @keyup.enter="search" />
        <el-input v-if="kind === 'anchor'" v-model.trim="anchor" clearable placeholder="主播昵称（精确名称）" style="width:220px" @keyup.enter="search" />
        <el-button type="primary" :loading="loading" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
      <el-table :data="records" v-loading="loading" border stripe height="520" empty-text="当前条件下暂无源库数据">
        <el-table-column v-for="column in columns" :key="column" :prop="column"
          :label="columnLabels[column] || column" :min-width="column.includes('name') ? 190 : 140" show-overflow-tooltip />
      </el-table>
      <div class="source-pagination">
        <span>第 {{ history.length + 1 }} 页 · 本页 {{ records.length }} 条<span v-if="queryMs != null"> · 查询 {{ queryMs.toFixed(0) }} ms</span></span>
        <div>
          <el-button :disabled="loading || !history.length" @click="previous">上一页</el-button>
          <el-button :disabled="loading || !hasMore" @click="next">下一页</el-button>
        </div>
      </div>
    </div>
    <div v-if="kind === 'warehouse'" class="card">
      <div class="source-caption"><strong>库存地点与逻辑仓</strong><span>上海、武汉优先匹配，其余按城市或大区归并；未识别地点保留原名。</span></div>
      <el-table :data="overview.warehouse_mapping || []" stripe border max-height="320">
        <el-table-column prop="facility_name" label="源库存地点" min-width="280" />
        <el-table-column prop="warehouse_id" label="逻辑仓" min-width="150" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { dataApi } from '../api/index.js'

const labels = { channel: '渠道订单', warehouse: '仓库订单', anchor: '抖音主播数据' }
const columnLabels = {
  date: '日期', shop_id: '店铺编码', shop_name: '店铺名称', platform: '平台', channel: '渠道',
  channel_name: '渠道描述', order_status: '订单状态', order_count: '订单量', payment_amount: '付款金额',
  facility_name: '库存地点', warehouse_id: '逻辑仓', anchor_name: '主播昵称', anchor_id: '主播抖音号',
  source_shop_name: '来源店铺名称（未关联）', source_shop_id: '来源店铺 ID', live_room_id: '直播间 ID',
  live_begin_time: '开播时间', live_end_time: '下播时间', statistics_date_type: '统计粒度',
  viewers: '观看人数', live_duration: '时长（分钟）', net_transaction_order_num: '净成交订单数',
  live_order_refund_num: '退款订单数',
}
const kind = ref('channel')
const overview = ref({})
const sourceList = computed(() => overview.value.sources || [])
const refreshing = ref(false)
const loading = ref(false)
const error = ref('')
const dates = ref(null)
const shop = ref('')
const shops = ref([])
const channel = ref('')
const anchor = ref('')
const records = ref([])
const columns = ref([])
const cursor = ref(null)
const nextCursor = ref(null)
const history = ref([])
const hasMore = ref(false)
const queryMs = ref(null)
let sequence = 0

function defaultDates() {
  const latest = sourceList.value.find(item => item.kind === kind.value)?.date_end
  if (!latest) return null
  const start = new Date(`${latest}T00:00:00`)
  start.setDate(start.getDate() - 29)
  return [`${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, '0')}-${String(start.getDate()).padStart(2, '0')}`, latest]
}
async function load() {
  const current = ++sequence
  loading.value = true
  error.value = ''
  try {
    const result = await dataApi.getRecords({
      data_type: kind.value, page_size: 50, after_id: cursor.value || undefined,
      shop_id: kind.value === 'channel' ? shop.value || undefined : undefined,
      channel: kind.value === 'channel' ? channel.value || undefined : undefined,
      anchor_name: kind.value === 'anchor' ? anchor.value || undefined : undefined,
      date_start: dates.value?.[0], date_end: dates.value?.[1],
    })
    if (current !== sequence) return
    records.value = result.records || []
    columns.value = (result.columns || []).filter(column => !['id', 'source_time'].includes(column))
    nextCursor.value = result.next_cursor
    hasMore.value = result.has_more
    queryMs.value = Number(result.query_ms)
  } catch (exc) {
    if (current !== sequence) return
    records.value = []
    hasMore.value = false
    error.value = exc.response?.data?.detail || '源库数据查询失败'
  } finally {
    if (current === sequence) loading.value = false
  }
}
function search() {
  history.value = []
  cursor.value = null
  return load()
}
function reset() {
  shop.value = channel.value = anchor.value = ''
  dates.value = defaultDates()
  return search()
}
function next() {
  history.value.push(cursor.value)
  cursor.value = nextCursor.value
  return load()
}
function previous() {
  cursor.value = history.value.pop()
  return load()
}
async function refresh() {
  refreshing.value = true
  error.value = ''
  try {
    overview.value = await dataApi.getOverview()
    dates.value = defaultDates()
    const options = await dataApi.getRecordOptions({ data_type: 'channel' })
    shops.value = options.shops || []
    await search()
  } catch (exc) {
    records.value = []
    hasMore.value = false
    error.value = exc.response?.data?.detail || '只读数据源暂时不可用'
  } finally {
    refreshing.value = false
  }
}
watch(kind, reset)
onMounted(refresh)
</script>

<style scoped>
.source-header,.source-caption,.source-pagination { display:flex; justify-content:space-between; align-items:center; gap:16px; }
.source-header h2 { margin:0 0 8px; font-size:19px; }
.source-header p,.source-caption span,.source-pagination>span { color:#7b8495; font-size:13px; margin:0; }
.source-cards { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
.source-card { text-align:left; cursor:pointer; color:#465066; border:1px solid #e5e9f1; font:inherit; }
.source-card.selected { border-color:#4080ff; background:#f5f8ff; }
.source-card strong { display:block; font-size:28px; margin:12px 0; color:#263d64; }
.source-card small { display:block; color:#7b8495; margin-bottom:10px; }
.source-caption { margin-bottom:16px; }
.source-filters { display:flex; flex-wrap:wrap; gap:12px; margin:18px 0; }
.source-pagination { margin-top:16px; }
@media(max-width:900px) { .source-cards { grid-template-columns:1fr; } .source-header,.source-caption { align-items:flex-start; flex-direction:column; } }
</style>
