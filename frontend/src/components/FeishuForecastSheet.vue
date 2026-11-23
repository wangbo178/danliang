<template>
  <section class="forecast-workbook" ref="rootRef">
    <header class="workbook-header">
      <div class="workbook-title">
        <span class="sheet-icon">▦</span>
        <div>
          <h2>预测计算表格</h2>
          <p>按原手工表的“店铺分块 × 订单类型 × 日期”结构展示</p>
        </div>
      </div>
      <div class="header-actions">
        <span class="save-state" :class="{ dirty: drafts.size }">
          <i />{{ drafts.size ? `${drafts.size} 个明细值待保存` : '已保存' }}
        </span>
        <el-button :disabled="!drafts.size" @click="discardChanges">撤销未保存</el-button>
        <el-button type="primary" :loading="saving" :disabled="!drafts.size" @click="saveChanges">
          保存调整
        </el-button>
      </div>
    </header>

    <div class="workbook-toolbar">
      <div class="toolbar-left">
        <el-select
          v-model="shopFilter"
          clearable
          filterable
          placeholder="全部店铺（可搜索）"
          class="shop-filter"
        >
          <el-option
            v-for="item in shopOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-input v-model="keyword" clearable placeholder="搜索渠道名称或编码" class="search-input" />
        <el-select v-model="platformFilter" clearable placeholder="全部平台" class="platform-filter">
          <el-option v-for="item in platformOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </div>
      <div class="toolbar-right">
        <span>当前页批量增减</span>
        <el-input-number v-model="batchPercent" :min="-100" :max="1000" :step="5" controls-position="right" />
        <span>%</span>
        <el-button :disabled="!batchPercent || !editableRows.length" @click="applyBatchPercent">应用</el-button>
        <el-button :disabled="!visibleAdjustedCount" @click="restoreVisible">恢复模型值</el-button>
        <el-button circle title="刷新" :loading="loading" @click="loadSheet">↻</el-button>
      </div>
    </div>

    <div class="formula-bar" :class="{ active: selectedCell }">
      <span class="cell-address">{{ selectedAddress }}</span>
      <span v-if="selectedCell" class="selected-date">日期 {{ selectedDate }}</span>
      <span class="fx">fx</span>
      <template v-if="selectedCell">
        <input
          type="number"
          min="0"
          class="formula-value"
          :value="aggregateValue(selectedCell)"
          @change="onFormulaChange"
        />
        <span class="model-value">
          基础 {{ formatNumber(aggregateBase(selectedCell)) }}
          + 达人直播 {{ formatNumber(aggregateAnchor(selectedCell)) }}
          = 模型 {{ formatNumber(aggregateModel(selectedCell)) }}
        </span>
        <input
          type="text"
          maxlength="255"
          class="formula-note"
          placeholder="调整说明（将应用到该汇总格下的明细）"
          @change="onAggregateNoteChange"
        />
      </template>
      <span v-else class="formula-placeholder">选择一个日期单元格进行调整</span>
    </div>

    <div v-if="sheet.job_id" class="workbook-meta">
      <span>任务 {{ sheet.job_id.slice(0, 8) }}</span>
      <span>完成于 {{ formatDateTime(sheet.finished_at) }}</span>
      <span>{{ sheet.dates.length }} 天</span>
      <span>{{ filteredBlocks.length }} 个店铺分组</span>
      <span>{{ filteredRows.length }} 个店铺渠道组合</span>
      <span>{{ sheet.records.length.toLocaleString('zh-CN') }} 条预测明细</span>
      <span>达人直播加单 {{ formatNumber(sheetAnchorTotal) }}</span>
      <span class="legend adjusted"><i />人工调整</span>
      <span class="legend pending"><i />待保存</span>
    </div>

    <div ref="viewportRef" class="sheet-viewport" v-loading="loading">
      <table v-if="visibleBlocks.length" class="manual-sheet">
        <tbody v-for="block in visibleBlocks" :key="block.key" class="shop-block">
          <tr class="date-header-row">
            <th class="sticky shop-col">店铺</th>
            <th class="sticky channel-col">渠道</th>
            <th class="sticky source-col">定源</th>
            <th v-for="date in sheet.dates" :key="date" class="date-col">
              {{ date }}
            </th>
          </tr>
          <tr class="shop-name-row">
            <td class="sticky shop-col">{{ block.label }}</td>
            <td class="sticky channel-col">本页 {{ block.rows.length }} / 共 {{ block.totalRows }} 个组合</td>
            <td class="sticky source-col">{{ block.shops.size }} 个店铺</td>
            <td v-for="date in sheet.dates" :key="date" class="date-col"></td>
          </tr>
          <tr
            v-for="row in block.rows"
            :key="row.key"
            class="metric-row"
            :class="{ 'manual-row': row.manualOnly }"
          >
            <td class="sticky shop-col dimension-cell">
              <strong>{{ row.shopName }}</strong>
              <small v-if="row.shopName !== row.shopId">{{ row.shopId }}</small>
            </td>
            <td class="sticky channel-col dimension-cell">
              <strong>{{ row.channelName }}</strong>
              <small v-if="row.channelName !== row.channelId">{{ row.channelId }}</small>
            </td>
            <td class="sticky source-col" :class="{ 'manual-source': row.manualOnly }">
              {{ row.manualOnly ? '人工排期' : (rowHasAnchor(row) ? '基础+直播' : '模型预测') }}
            </td>
            <td
              v-for="(date, colIndex) in sheet.dates"
              :key="date"
              class="value-cell"
              :class="aggregateCellClasses(row.cells[date])"
              :title="aggregateBreakdownTitle(row.cells[date])"
              @click="selectAggregate(row.cells[date], row, date, colIndex)"
            >
              <template v-if="row.cells[date]">
                <input
                  type="number"
                  min="0"
                  step="1"
                  :data-row="rowFlatIndex(row)"
                  :data-col="colIndex"
                  :value="aggregateValue(row.cells[date])"
                  :aria-label="`${row.shopName} ${row.channelName} ${date}`"
                  @focus="selectAggregate(row.cells[date], row, date, colIndex)"
                  @change="onAggregateChange(row.cells[date], $event)"
                  @keydown.enter.prevent="focusNextRow(rowFlatIndex(row), colIndex)"
                  @paste="handlePaste($event, rowFlatIndex(row), colIndex)"
                />
                <button
                  v-if="aggregateIsAdjusted(row.cells[date])"
                  class="cell-reset"
                  type="button"
                  title="恢复模型值"
                  @click.stop="resetAggregate(row.cells[date])"
                >×</button>
              </template>
            </td>
          </tr>
          <tr class="block-total-row">
            <td class="sticky shop-col">预计总订单数</td>
            <td class="sticky channel-col">{{ block.label }}</td>
            <td class="sticky source-col">自动汇总</td>
            <td v-for="date in sheet.dates" :key="date" class="total-cell">
              {{ formatNumber(blockTotal(block, date)) }}
            </td>
          </tr>
          <tr class="spacer-row">
            <td class="sticky shop-col"></td>
            <td class="sticky channel-col"></td>
            <td class="sticky source-col"></td>
            <td v-for="date in sheet.dates" :key="date"></td>
          </tr>
        </tbody>
        <tfoot>
          <tr class="grand-total-row">
            <td class="sticky shop-col">当前页总单量</td>
            <td class="sticky channel-col">共 {{ editableRows.length }} 个组合</td>
            <td class="sticky source-col">最终预测</td>
            <td v-for="date in sheet.dates" :key="date">
              {{ formatNumber(grandTotal(date)) }}
            </td>
          </tr>
        </tfoot>
      </table>
      <el-empty v-else-if="!loading" :description="sheet.job_id ? '当前筛选条件下没有数据' : '暂无预测结果，请先完成模型训练'" />
    </div>

    <div v-if="filteredRows.length" class="sheet-pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[20, 40, 80]"
        :total="filteredRows.length"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="handlePageChange"
        @size-change="handlePageChange"
      />
    </div>

    <footer class="workbook-footnote">
      <span>可从 Excel 复制连续数字区域，选择起始格后直接粘贴。</span>
      <span>模型值 = 非达人基础单量 + 达人直播加单量。</span>
      <span>“人工排期”行来自未加入测算的渠道，默认 0 单且不计入 WAPE，可直接按运营排期填写。</span>
    </footer>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { resultsApi } from '../api/index.js'

const emit = defineEmits(['saved'])
const rootRef = ref(null)
const viewportRef = ref(null)
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const shopFilter = ref('')
const platformFilter = ref('')
const batchPercent = ref(0)
const currentPage = ref(1)
const pageSize = ref(40)
const drafts = reactive(new Map())
const selectedCell = ref(null)
const selectedAddress = ref('—')
const selectedDate = ref('')
const sheet = ref({ job_id: null, finished_at: null, dates: [], records: [] })

const PLATFORM_NAMES = {
  tianmao: '天猫全店', DY: '抖音全店', dy: '抖音全店',
  pdd: '拼多多全店', kuaishou: '快手全店', jingdong: '京东',
  xiaohongshu: '小红书', xianxia: '专柜', wph: '唯品会',
  weixin: '微信渠道', dw: '得物', gm: '国贸', wd: '微店',
  ziyou: '自有平台', none: '其他渠道', nan: '未归类渠道',
}
const PLATFORM_ORDER = [
  'tianmao', 'DY', 'dy', 'pdd', 'kuaishou', 'jingdong', 'xiaohongshu',
  'xianxia', 'wph', 'weixin', 'dw', 'gm', 'wd', 'ziyou', 'none', 'nan',
]
function platformLabel(platform) {
  return PLATFORM_NAMES[platform] || platform || '未归类渠道'
}

const blocks = computed(() => {
  const map = new Map()
  for (const record of sheet.value.records) {
    const key = record.platform || 'nan'
    if (!map.has(key)) {
      map.set(key, {
        key,
        label: record.platform_name || platformLabel(key),
        shops: new Set(),
        rowMap: new Map(),
      })
    }
    const block = map.get(key)
    block.shops.add(record.shop_id)
    const shopLabel = record.shop_name || record.shop_id
    const channelLabel = record.channel_name || record.channel
    const manualOnly = record.include_in_forecast === false
    const rowKey = `${record.shop_id}\u0000${record.channel}`
    if (!block.rowMap.has(rowKey)) {
      block.rowMap.set(rowKey, {
        key: rowKey,
        label: `${shopLabel}｜${channelLabel}`,
        shopId: record.shop_id,
        shopName: shopLabel,
        channelId: record.channel,
        channelName: channelLabel,
        order: manualOnly ? 1 : 0,
        manualOnly,
        cells: {},
      })
    }
    const row = block.rowMap.get(rowKey)
    if (!row.cells[record.date]) row.cells[record.date] = { records: [] }
    row.cells[record.date].records.push(record)
  }
  const orderIndex = value => {
    const index = PLATFORM_ORDER.indexOf(value)
    return index === -1 ? 999 : index
  }
  return [...map.values()]
    .map(block => ({
      ...block,
      rows: [...block.rowMap.values()].sort(
        (a, b) => a.order - b.order || a.label.localeCompare(b.label, 'zh-CN'),
      ),
    }))
    .sort((a, b) => orderIndex(a.key) - orderIndex(b.key) || a.label.localeCompare(b.label, 'zh-CN'))
})

const filteredBlocks = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return blocks.value.flatMap(block => {
    if (platformFilter.value && block.key !== platformFilter.value) return []
    const rows = block.rows.filter(row => {
      if (shopFilter.value && row.shopId !== shopFilter.value) return false
      if (!q) return true
      return `${row.channelId} ${row.channelName}`.toLowerCase().includes(q)
    })
    return rows.length ? [{ ...block, rows }] : []
  })
})

const platformOptions = computed(() => blocks.value.map(block => ({ value: block.key, label: block.label })))
const shopOptions = computed(() => {
  const shops = new Map()
  for (const record of sheet.value.records) {
    const shopId = String(record.shop_id || '').trim()
    if (!shopId || shops.has(shopId)) continue
    const shopName = String(record.shop_name || shopId).trim()
    shops.set(shopId, {
      value: shopId,
      label: shopName === shopId ? shopId : `${shopName}（${shopId}）`,
    })
  }
  return [...shops.values()].sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
})
const filteredRows = computed(() => filteredBlocks.value.flatMap(block => (
  block.rows.map(row => ({ block, row }))
)))
const visibleBlocks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const entries = filteredRows.value.slice(start, start + pageSize.value)
  const pageBlocks = []
  for (const { block, row } of entries) {
    let target = pageBlocks[pageBlocks.length - 1]
    if (!target || target.key !== block.key) {
      target = {
        ...block,
        rows: [],
        totalRows: block.rows.length,
      }
      pageBlocks.push(target)
    }
    target.rows.push(row)
  }
  return pageBlocks
})
const editableRows = computed(() => visibleBlocks.value.flatMap(block => block.rows))
const visibleAdjustedCount = computed(() => editableRows.value.reduce((sum, row) => (
  sum + Object.values(row.cells).filter(aggregateIsAdjusted).length
), 0))
const sheetAnchorTotal = computed(() => sheet.value.records.reduce(
  (sum, record) => sum + Number(record.anchor_predicted_orders || 0), 0,
))

watch([keyword, shopFilter, platformFilter, pageSize], () => {
  currentPage.value = 1
  selectedCell.value = null
  selectedAddress.value = '—'
  selectedDate.value = ''
})
watch(filteredRows, (value) => {
  const lastPage = Math.max(1, Math.ceil(value.length / pageSize.value))
  if (currentPage.value > lastPage) currentPage.value = lastPage
})

function rowFlatIndex(row) {
  return editableRows.value.indexOf(row)
}

function handlePageChange() {
  selectedCell.value = null
  selectedAddress.value = '—'
  selectedDate.value = ''
  requestAnimationFrame(() => {
    if (viewportRef.value) viewportRef.value.scrollTop = 0
  })
}

function recordValue(record) {
  const draft = drafts.get(record.id)
  if (draft) {
    return draft.value == null
      ? Math.round(Number(record.model_predicted_orders || 0))
      : draft.value
  }
  return Math.round(Number(
    record.manual_predicted_orders == null
      ? record.model_predicted_orders || 0
      : record.manual_predicted_orders,
  ))
}

function setRecordDraft(record, value, note = record.adjustment_note || '') {
  const normalized = value == null ? null : Math.max(0, Math.round(Number(value)))
  const original = record.manual_predicted_orders == null
    ? null
    : Math.round(Number(record.manual_predicted_orders))
  const normalizedNote = normalized == null ? '' : String(note || '').trim()
  if (normalized === original && normalizedNote === (record.adjustment_note || '')) {
    drafts.delete(record.id)
  } else {
    drafts.set(record.id, { value: normalized, note: normalizedNote })
  }
}

function aggregateValue(cell) {
  if (!cell) return 0
  return cell.records.reduce((sum, record) => sum + recordValue(record), 0)
}

function aggregateModel(cell) {
  if (!cell) return 0
  return cell.records.reduce((sum, record) => sum + Math.round(Number(record.model_predicted_orders || 0)), 0)
}

function aggregateBase(cell) {
  if (!cell) return 0
  return cell.records.reduce((sum, record) => sum + Number(record.base_predicted_orders || 0), 0)
}

function aggregateAnchor(cell) {
  if (!cell) return 0
  return cell.records.reduce((sum, record) => sum + Number(record.anchor_predicted_orders || 0), 0)
}

function aggregateBreakdownTitle(cell) {
  if (!cell) return ''
  return `非达人基础 ${formatNumber(aggregateBase(cell))} + 达人直播 ${formatNumber(aggregateAnchor(cell))} = 模型 ${formatNumber(aggregateModel(cell))}`
}

function rowHasAnchor(row) {
  return Object.values(row.cells).some(cell => aggregateAnchor(cell) > 0)
}

function aggregateIsAdjusted(cell) {
  return Boolean(cell?.records.some(record => {
    const draft = drafts.get(record.id)
    return draft ? draft.value != null : record.manual_predicted_orders != null
  }))
}

function aggregateIsDirty(cell) {
  return Boolean(cell?.records.some(record => drafts.has(record.id)))
}

function aggregateCellClasses(cell) {
  return {
    adjusted: aggregateIsAdjusted(cell),
    dirty: aggregateIsDirty(cell),
    selected: selectedCell.value === cell,
    'has-anchor': aggregateAnchor(cell) > 0,
  }
}

function setAggregateValue(cell, targetValue, note) {
  if (!cell?.records.length) return
  if (targetValue == null) {
    cell.records.forEach(record => setRecordDraft(record, null, ''))
    return
  }
  const target = Math.max(0, Math.round(Number(targetValue)))
  const currentValues = cell.records.map(recordValue)
  const currentTotal = currentValues.reduce((sum, value) => sum + value, 0)
  let assigned = 0
  cell.records.forEach((record, index) => {
    let value
    if (index === cell.records.length - 1) {
      value = target - assigned
    } else if (currentTotal > 0) {
      value = Math.floor(target * currentValues[index] / currentTotal)
      assigned += value
    } else {
      value = Math.floor(target / cell.records.length)
      assigned += value
    }
    setRecordDraft(record, value, note == null ? (drafts.get(record.id)?.note || record.adjustment_note || '') : note)
  })
}

function selectAggregate(cell, row, date, colIndex) {
  if (!cell) return
  selectedCell.value = cell
  const rowIndex = rowFlatIndex(row)
  selectedAddress.value = `${columnLabel(colIndex + 3)}${rowIndex + 3} · ${row.shopName} / ${row.channelName}`
  selectedDate.value = date
}

function columnLabel(index) {
  let value = index + 1
  let label = ''
  while (value > 0) {
    value -= 1
    label = String.fromCharCode(65 + (value % 26)) + label
    value = Math.floor(value / 26)
  }
  return label
}

function onAggregateChange(cell, event) {
  const text = event.target.value.trim()
  if (!text) {
    setAggregateValue(cell, null)
  } else {
    const value = Number(text)
    if (!Number.isFinite(value) || value < 0) {
      event.target.value = aggregateValue(cell)
      ElMessage.warning('预测单量必须是大于或等于 0 的数字')
      return
    }
    setAggregateValue(cell, value)
  }
  event.target.value = aggregateValue(cell)
}

function onFormulaChange(event) {
  if (selectedCell.value) onAggregateChange(selectedCell.value, event)
}

function onAggregateNoteChange(event) {
  if (!selectedCell.value) return
  setAggregateValue(selectedCell.value, aggregateValue(selectedCell.value), event.target.value)
}

function resetAggregate(cell) {
  setAggregateValue(cell, null)
}

function blockTotal(block, date) {
  return block.rows.reduce((sum, row) => sum + aggregateValue(row.cells[date]), 0)
}

function grandTotal(date) {
  return visibleBlocks.value.reduce((sum, block) => sum + blockTotal(block, date), 0)
}

function formatNumber(value) {
  return Math.round(Number(value || 0)).toLocaleString('zh-CN')
}

function formatDateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function focusNextRow(rowIndex, colIndex) {
  const next = rootRef.value?.querySelector(`input[data-row="${rowIndex + 1}"][data-col="${colIndex}"]`)
  next?.focus()
  next?.select()
}

function handlePaste(event, startRow, startCol) {
  const text = event.clipboardData?.getData('text/plain') || ''
  const matrix = text.trim().split(/\r?\n/).map(line => line.split('\t'))
  if (matrix.length === 1 && matrix[0].length === 1) return
  event.preventDefault()
  let applied = 0
  matrix.forEach((values, rowOffset) => {
    const row = editableRows.value[startRow + rowOffset]
    if (!row) return
    values.forEach((rawValue, colOffset) => {
      const date = sheet.value.dates[startCol + colOffset]
      const cell = date ? row.cells[date] : null
      if (!cell) return
      const cleaned = rawValue.replace(/,/g, '').trim()
      if (!cleaned) {
        setAggregateValue(cell, null)
        applied += 1
        return
      }
      const value = Number(cleaned)
      if (Number.isFinite(value) && value >= 0) {
        setAggregateValue(cell, value)
        applied += 1
      }
    })
  })
  ElMessage.success(`已粘贴 ${applied} 个汇总单元格，请确认后保存`)
}

function applyBatchPercent() {
  const rate = Number(batchPercent.value) / 100
  if (!Number.isFinite(rate) || rate === 0) return
  let count = 0
  editableRows.value.forEach(row => Object.values(row.cells).forEach(cell => {
    setAggregateValue(cell, aggregateValue(cell) * (1 + rate))
    count += 1
  }))
  ElMessage.success(`已调整当前页 ${count} 个汇总单元格`)
}

async function restoreVisible() {
  try {
    await ElMessageBox.confirm(
      `将当前页 ${visibleAdjustedCount.value} 个人工汇总格恢复为模型值，之后仍需点击保存。`,
      '恢复当前页',
      { confirmButtonText: '恢复', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  editableRows.value.forEach(row => Object.values(row.cells).forEach(cell => {
    if (aggregateIsAdjusted(cell)) resetAggregate(cell)
  }))
}

function discardChanges() {
  drafts.clear()
  ElMessage.info('已撤销未保存的修改')
}

async function saveChanges() {
  if (!sheet.value.job_id || !drafts.size) return
  saving.value = true
  try {
    const edits = [...drafts.entries()].map(([prediction_id, draft]) => ({
      prediction_id,
      manual_predicted_orders: draft.value,
      adjustment_note: draft.note || null,
    }))
    const result = await resultsApi.saveForecastSheet({ job_id: sheet.value.job_id, edits })
    ElMessage.success(`已保存 ${result.updated} 条预测明细`)
    await loadSheet()
    emit('saved')
  } finally {
    saving.value = false
  }
}

async function loadSheet() {
  loading.value = true
  try {
    sheet.value = await resultsApi.getForecastSheet()
    drafts.clear()
    selectedCell.value = null
    selectedAddress.value = '—'
    selectedDate.value = ''
  } finally {
    loading.value = false
  }
}

function warnBeforeLeave(event) {
  if (!drafts.size) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  loadSheet()
  window.addEventListener('beforeunload', warnBeforeLeave)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnBeforeLeave))
onBeforeRouteLeave(async () => {
  if (!drafts.size) return true
  try {
    await ElMessageBox.confirm(
      `还有 ${drafts.size} 条明细调整未保存，离开后会丢失。`,
      '离开预测计算表？',
      { confirmButtonText: '放弃并离开', cancelButtonText: '继续编辑', type: 'warning' },
    )
    return true
  } catch {
    return false
  }
})
</script>

<style scoped>
.forecast-workbook { overflow: hidden; background: #fff; border: 1px solid #dfe3e8; border-radius: 10px; box-shadow: 0 1px 3px rgba(16,24,40,.05); }
.workbook-header { min-height: 76px; padding: 0 18px; display: flex; align-items: center; justify-content: space-between; gap: 18px; border-bottom: 1px solid #e6e8eb; }
.workbook-title, .header-actions, .toolbar-left, .toolbar-right { display: flex; align-items: center; gap: 10px; }
.sheet-icon { width: 36px; height: 36px; display: grid; place-items: center; border-radius: 8px; color: #fff; background: #3370ff; font-size: 23px; }
.workbook-title h2 { font-size: 18px; line-height: 1.4; }
.workbook-title p { color: #8f959e; font-size: 12px; }
.save-state { display: inline-flex; align-items: center; gap: 6px; color: #8f959e; font-size: 12px; }
.save-state i { width: 7px; height: 7px; border-radius: 50%; background: #34c759; }
.save-state.dirty { color: #b26a00; }
.save-state.dirty i { background: #f5a623; }

.workbook-toolbar { min-height: 56px; padding: 0 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid #e6e8eb; background: #fbfcfd; }
.shop-filter { width: 260px; }
.search-input { width: 190px; }
.platform-filter { width: 160px; }
.toolbar-right { color: #646a73; font-size: 12px; }
.toolbar-right :deep(.el-input-number) { width: 108px; }

.formula-bar { height: 42px; padding: 0 18px; display: flex; align-items: center; overflow-x: auto; border-bottom: 1px solid #dfe3e8; color: #646a73; font-size: 12px; white-space: nowrap; }
.cell-address { width: 230px; flex: 0 0 230px; overflow: hidden; color: #373c43; text-overflow: ellipsis; white-space: nowrap; }
.selected-date { flex: 0 0 auto; margin-right: 12px; padding: 4px 9px; border-radius: 6px; color: #175cd3; background: #eff8ff; font-size: 12px; font-weight: 650; }
.fx { width: 34px; border-left: 1px solid #e5e7eb; color: #8f959e; font-family: Georgia,serif; font-style: italic; font-weight: 700; text-align: center; }
.formula-value, .formula-note { height: 30px; border: 0; outline: 0; background: transparent; color: #1f2329; font: inherit; }
.formula-value { width: 120px; padding: 0 10px; border-left: 1px solid #e5e7eb; }
.formula-note { min-width: 220px; flex: 1; padding: 0 12px; border-left: 1px solid #e5e7eb; }
.model-value { padding: 0 12px; color: #8f959e; white-space: nowrap; }
.formula-placeholder { padding-left: 10px; color: #a8adb5; }

.workbook-meta { min-height: 36px; padding: 0 18px; display: flex; align-items: center; gap: 18px; overflow-x: auto; border-bottom: 1px solid #dfe3e8; background: #f7f8fa; color: #8f959e; font-size: 11px; white-space: nowrap; }
.legend { display: inline-flex; align-items: center; gap: 5px; }
.legend i { width: 9px; height: 9px; border-radius: 2px; }
.legend.adjusted i { background: #3370ff; }
.legend.pending i { background: #fff0b3; border: 1px solid #f5c451; }

.sheet-viewport { min-height: 380px; max-height: calc(100vh - 350px); overflow: auto; background: #fff; }
.manual-sheet { min-width: max-content; border-collapse: separate; border-spacing: 0; color: #1f2329; font-size: 13px; }
.manual-sheet th, .manual-sheet td { height: 31px; padding: 0; border-right: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb; background: #fff; }
.sticky { position: sticky; z-index: 5; }
.shop-col { left: 0; width: 220px; min-width: 220px; max-width: 220px; padding: 0 12px !important; text-align: left; }
.channel-col { left: 220px; width: 180px; min-width: 180px; max-width: 180px; padding: 0 12px !important; text-align: left; }
.source-col { left: 400px; width: 100px; min-width: 100px; max-width: 100px; padding: 0 8px !important; color: #646a73; text-align: center; box-shadow: 3px 0 4px rgba(16,24,40,.05); }
.date-col, .value-cell, .total-cell, .grand-total-row td:not(.shop-col):not(.channel-col):not(.source-col) { width: 112px; min-width: 112px; max-width: 112px; }
.date-header-row th { position: sticky; top: 0; z-index: 11; height: 34px; color: #c00000; background: #fff; box-shadow: 0 1px 0 #d0d5dd; font-size: 13px; font-weight: 500; text-align: center; white-space: nowrap; }
.date-header-row th.sticky { z-index: 14; }
.date-header-row .shop-col, .date-header-row .channel-col { color: #1f2329; font-size: 14px; }
.date-header-row .source-col { color: #1f2329; font-size: 14px; }
.shop-name-row td { height: 26px; }
.shop-name-row .shop-col { color: #ff0000; font-weight: 700; }
.shop-name-row .channel-col { color: #8f959e; font-size: 11px; }
.shop-name-row .source-col { color: #8f959e; font-size: 11px; }
.metric-row td { height: 42px; }
.dimension-cell { overflow: hidden; color: #1f2329; }
.dimension-cell strong, .dimension-cell small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dimension-cell strong { font-size: 13px; font-weight: 600; line-height: 18px; }
.dimension-cell small { color: #8f959e; font-size: 11px; line-height: 15px; }
.metric-shelf td { background: #eff2fa; }
.metric-shelf .sticky { background: #eff2fa; }
.manual-row td { background: #fff8e8; }
.manual-row .sticky { background: #fff8e8; }
.manual-source { color: #b54708; font-weight: 600; }
.value-cell { position: relative; }
.value-cell input { width: 100%; height: 41px; padding: 0 8px; border: 0; outline: 0; background: transparent; color: #1f2329; font-size: 13px; font-variant-numeric: tabular-nums; text-align: center; appearance: textfield; }
.value-cell input::-webkit-inner-spin-button { appearance: none; }
.value-cell:hover { background: #f4f7ff; }
.value-cell.has-anchor { box-shadow: inset 0 -2px 0 #7c3aed; }
.value-cell.has-anchor.selected { box-shadow: inset 0 0 0 2px #3370ff, inset 0 -4px 0 #7c3aed; }
.value-cell.selected { box-shadow: inset 0 0 0 2px #3370ff; z-index: 3; }
.value-cell.adjusted::before { content: ''; position: absolute; top: 0; right: 0; border-top: 8px solid #3370ff; border-left: 8px solid transparent; }
.value-cell.dirty { background: #fff8d8 !important; }
.value-cell.dirty::before { border-top-color: #f5a623; }
.cell-reset { position: absolute; right: 2px; bottom: 1px; display: none; width: 15px; height: 15px; border: 0; border-radius: 3px; color: #fff; background: #8f959e; cursor: pointer; line-height: 14px; }
.value-cell:hover .cell-reset { display: block; }
.block-total-row td { height: 32px; font-weight: 600; text-align: center; }
.block-total-row .shop-col, .block-total-row .channel-col { text-align: left; }
.block-total-row .total-cell { color: #1d4ed8; font-variant-numeric: tabular-nums; }
.spacer-row td { height: 18px; border-bottom: 0; background: #fff; }
.grand-total-row td { position: sticky; bottom: 0; z-index: 8; height: 34px; padding: 0 8px; background: #fff258; font-weight: 700; font-variant-numeric: tabular-nums; text-align: center; }
.grand-total-row .shop-col, .grand-total-row .channel-col { z-index: 10; text-align: left; }

.sheet-pagination { min-height: 52px; padding: 8px 18px; display: flex; align-items: center; justify-content: flex-end; border-top: 1px solid #eaedf0; background: #fff; }

.workbook-footnote { min-height: 42px; padding: 0 18px; display: flex; align-items: center; justify-content: space-between; gap: 18px; border-top: 1px solid #eaedf0; background: #fbfcfd; color: #8f959e; font-size: 11px; }

@media (max-width: 1000px) {
  .workbook-header, .workbook-toolbar { align-items: flex-start; flex-direction: column; padding-top: 12px; padding-bottom: 12px; }
  .header-actions, .toolbar-left, .toolbar-right { width: 100%; flex-wrap: wrap; }
  .shop-filter, .search-input { width: 100%; }
  .platform-filter { flex: 1; }
  .sheet-viewport { max-height: 58vh; }
  .workbook-footnote { align-items: flex-start; flex-direction: column; padding-top: 10px; padding-bottom: 10px; }
}
</style>
