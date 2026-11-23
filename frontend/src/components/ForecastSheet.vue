<template>
  <section class="forecast-sheet-card" ref="rootRef">
    <div class="sheet-heading">
      <div>
        <div class="sheet-title-row">
          <span class="sheet-app-icon">▦</span>
          <div>
            <h2>预测排期表</h2>
            <p>模型结果已按店铺、渠道和日期展开，单击任意数字即可直接调整。</p>
          </div>
        </div>
      </div>
      <div class="sheet-actions">
        <span class="save-status" :class="{ dirty: dirtyCount }">
          <i />{{ dirtyCount ? `${dirtyCount} 处待保存` : '所有更改已保存' }}
        </span>
        <el-button :disabled="!dirtyCount" @click="discardChanges">撤销未保存</el-button>
        <el-button type="primary" :loading="saving" :disabled="!dirtyCount" @click="saveChanges">
          保存调整
        </el-button>
      </div>
    </div>

    <div class="sheet-toolbar">
      <div class="toolbar-group">
        <el-input v-model="keyword" clearable placeholder="搜索店铺 / 渠道" class="search-input" />
        <el-select v-model="platformFilter" clearable placeholder="全部平台" class="filter-select">
          <el-option v-for="item in sheet.platforms" :key="item" :label="item" :value="item" />
        </el-select>
        <el-select v-model="channelFilter" clearable placeholder="全部渠道" class="filter-select">
          <el-option v-for="item in sheet.channels" :key="item" :label="item" :value="item" />
        </el-select>
      </div>
      <div class="toolbar-group toolbar-right">
        <span class="toolbar-label">当前范围批量增减</span>
        <el-input-number v-model="batchPercent" :min="-100" :max="1000" :step="5" controls-position="right" />
        <span class="percent-sign">%</span>
        <el-button :disabled="!visibleRows.length || !batchPercent" @click="applyBatchPercent">应用</el-button>
        <el-button :disabled="!visibleAdjustedCount" @click="restoreVisible">恢复模型值</el-button>
        <el-button circle title="刷新" :loading="loading" @click="loadSheet">↻</el-button>
      </div>
    </div>

    <div class="formula-bar" :class="{ active: selectedCell }">
      <span class="cell-address">{{ selectedAddress }}</span>
      <span class="fx">fx</span>
      <template v-if="selectedCell">
        <input
          class="formula-value"
          type="number"
          min="0"
          :value="cellValue(selectedCell)"
          @change="onFormulaChange"
        />
        <span class="model-hint">模型值 {{ formatNumber(selectedCell.model_predicted_orders) }}</span>
        <input
          class="formula-note"
          type="text"
          maxlength="255"
          placeholder="添加调整说明（选填）"
          :value="cellNote(selectedCell)"
          @change="onNoteChange"
        />
      </template>
      <span v-else class="formula-placeholder">选择一个单元格查看模型值并填写调整说明</span>
    </div>

    <div v-if="sheet.job_id" class="sheet-meta">
      <span>任务 {{ sheet.job_id.slice(0, 8) }}</span>
      <span>训练完成于 {{ formatDateTime(sheet.finished_at) }}</span>
      <span>{{ visibleRows.length }} 个店铺渠道组合</span>
      <span>{{ visibleCellCount.toLocaleString('zh-CN') }} 个预测单元格</span>
      <span class="adjusted-legend"><i />蓝角表示人工调整</span>
      <span class="dirty-legend"><i />黄色表示待保存</span>
    </div>

    <div class="grid-shell" v-loading="loading">
      <table v-if="visibleRows.length" class="forecast-grid">
        <thead>
          <tr>
            <th class="sticky-col row-number-col">#</th>
            <th class="sticky-col platform-col">平台</th>
            <th class="sticky-col shop-col">店铺</th>
            <th class="sticky-col channel-col">渠道</th>
            <th
              v-for="date in sheet.dates"
              :key="date"
              class="date-col"
              :class="{ weekend: isWeekend(date) }"
            >
              <b>{{ formatShortDate(date) }}</b>
              <span>{{ formatWeekday(date) }}</span>
            </th>
            <th class="total-col">合计</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowIndex) in visibleRows" :key="row.key">
            <td class="sticky-col row-number-col row-number">{{ rowIndex + 1 }}</td>
            <td class="sticky-col platform-col dimension-cell">{{ row.platform || '—' }}</td>
            <td class="sticky-col shop-col dimension-cell shop-name">{{ row.shop_id }}</td>
            <td class="sticky-col channel-col dimension-cell">{{ row.channel }}</td>
            <td
              v-for="(date, colIndex) in sheet.dates"
              :key="date"
              class="value-cell"
              :class="cellClasses(row.cells[date], date)"
              @click="selectCell(row.cells[date], rowIndex, colIndex)"
            >
              <template v-if="row.cells[date]">
                <input
                  type="number"
                  min="0"
                  step="1"
                  :aria-label="`${row.shop_id} ${row.channel} ${date}预测单量`"
                  :data-row="rowIndex"
                  :data-col="colIndex"
                  :value="cellValue(row.cells[date])"
                  @focus="selectCell(row.cells[date], rowIndex, colIndex)"
                  @change="onCellChange(row.cells[date], $event)"
                  @keydown.enter.prevent="focusNextRow(rowIndex, colIndex)"
                  @paste="handlePaste($event, rowIndex, colIndex)"
                />
                <button
                  v-if="cellIsAdjusted(row.cells[date])"
                  type="button"
                  class="cell-reset"
                  title="恢复模型值"
                  @click.stop="resetCell(row.cells[date])"
                >×</button>
              </template>
              <span v-else class="empty-cell">—</span>
            </td>
            <td class="total-col row-total">{{ formatNumber(rowTotal(row)) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td class="sticky-col row-number-col"></td>
            <td class="sticky-col platform-col total-label" colspan="3">每日合计</td>
            <td v-for="date in sheet.dates" :key="date" class="date-total">
              {{ formatNumber(dateTotal(date)) }}
            </td>
            <td class="total-col grand-total">{{ formatNumber(grandTotal) }}</td>
          </tr>
        </tfoot>
      </table>
      <el-empty v-else-if="!loading" :description="sheet.job_id ? '当前筛选条件下没有数据' : '暂无预测结果，请先完成模型训练'" />
    </div>

    <div class="sheet-footnote">
      <span>提示：可从 Excel 复制一块数字区域，选中起始单元格后直接粘贴。</span>
      <span>人工调整只改变最终展示值，模型原值会完整保留。</span>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'
import { resultsApi } from '../api/index.js'

const emit = defineEmits(['saved'])

const rootRef = ref(null)
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const platformFilter = ref('')
const channelFilter = ref('')
const batchPercent = ref(0)
const sheet = ref({
  job_id: null,
  finished_at: null,
  dates: [],
  records: [],
  platforms: [],
  channels: [],
  adjusted_count: 0,
})
const drafts = reactive(new Map())
const selectedCell = ref(null)
const selectedPosition = ref({ row: -1, col: -1 })

const dirtyCount = computed(() => drafts.size)

const rows = computed(() => {
  const map = new Map()
  for (const cell of sheet.value.records) {
    const key = [cell.platform || '', cell.shop_id, cell.channel].join('\u0001')
    if (!map.has(key)) {
      map.set(key, {
        key,
        platform: cell.platform,
        shop_id: cell.shop_id,
        channel: cell.channel,
        cells: {},
      })
    }
    map.get(key).cells[cell.date] = cell
  }
  return [...map.values()]
})

const visibleRows = computed(() => {
  const q = keyword.value.trim().toLocaleLowerCase('zh-CN')
  return rows.value.filter((row) => {
    if (platformFilter.value && row.platform !== platformFilter.value) return false
    if (channelFilter.value && row.channel !== channelFilter.value) return false
    if (!q) return true
    return [row.platform, row.shop_id, row.channel]
      .some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(q))
  })
})

const visibleCellCount = computed(() => visibleRows.value.reduce(
  (total, row) => total + Object.keys(row.cells).length,
  0,
))

const visibleAdjustedCount = computed(() => visibleRows.value.reduce((total, row) => (
  total + Object.values(row.cells).filter(cellIsAdjusted).length
), 0))

const grandTotal = computed(() => visibleRows.value.reduce((sum, row) => sum + rowTotal(row), 0))

const selectedAddress = computed(() => {
  const { row, col } = selectedPosition.value
  if (row < 0 || col < 0) return '—'
  return `${columnLabel(col + 4)}${row + 2}`
})

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

function originalManualValue(cell) {
  return cell.manual_predicted_orders == null ? null : Math.round(Number(cell.manual_predicted_orders))
}

function cellValue(cell) {
  const draft = drafts.get(cell.id)
  if (draft) {
    return draft.value == null ? Math.round(Number(cell.model_predicted_orders || 0)) : draft.value
  }
  const value = cell.manual_predicted_orders == null
    ? cell.model_predicted_orders
    : cell.manual_predicted_orders
  return Math.round(Number(value || 0))
}

function cellNote(cell) {
  const draft = drafts.get(cell.id)
  return draft ? (draft.note || '') : (cell.adjustment_note || '')
}

function setDraft(cell, value, note = cellNote(cell)) {
  const normalizedValue = value == null ? null : Math.max(0, Math.round(Number(value)))
  const normalizedNote = String(note || '').trim()
  const originalValue = originalManualValue(cell)
  const originalNote = cell.adjustment_note || ''
  if (normalizedValue === originalValue && normalizedNote === originalNote) {
    drafts.delete(cell.id)
    return
  }
  drafts.set(cell.id, { value: normalizedValue, note: normalizedValue == null ? '' : normalizedNote })
}

function cellIsAdjusted(cell) {
  const draft = drafts.get(cell.id)
  return draft ? draft.value != null : cell.manual_predicted_orders != null
}

function cellClasses(cell, date) {
  if (!cell) return { weekend: isWeekend(date) }
  return {
    weekend: isWeekend(date),
    adjusted: cellIsAdjusted(cell),
    dirty: drafts.has(cell.id),
    selected: selectedCell.value?.id === cell.id,
  }
}

function selectCell(cell, row, col) {
  if (!cell) return
  selectedCell.value = cell
  selectedPosition.value = { row, col }
}

function onCellChange(cell, event) {
  const text = event.target.value.trim()
  if (text === '') {
    setDraft(cell, null, '')
  } else {
    const value = Number(text)
    if (!Number.isFinite(value) || value < 0) {
      event.target.value = cellValue(cell)
      ElMessage.warning('预测单量必须是大于或等于 0 的数字')
      return
    }
    setDraft(cell, value)
  }
  event.target.value = cellValue(cell)
}

function onFormulaChange(event) {
  if (!selectedCell.value) return
  onCellChange(selectedCell.value, event)
}

function onNoteChange(event) {
  if (!selectedCell.value) return
  const value = cellIsAdjusted(selectedCell.value) ? cellValue(selectedCell.value) : cellValue(selectedCell.value)
  setDraft(selectedCell.value, value, event.target.value)
}

function resetCell(cell) {
  setDraft(cell, null, '')
}

function rowTotal(row) {
  return sheet.value.dates.reduce((sum, date) => {
    const cell = row.cells[date]
    return sum + (cell ? cellValue(cell) : 0)
  }, 0)
}

function dateTotal(date) {
  return visibleRows.value.reduce((sum, row) => {
    const cell = row.cells[date]
    return sum + (cell ? cellValue(cell) : 0)
  }, 0)
}

function formatNumber(value) {
  return Math.round(Number(value || 0)).toLocaleString('zh-CN')
}

function dateObject(date) {
  return new Date(`${date}T00:00:00`)
}

function formatShortDate(date) {
  const d = dateObject(date)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function formatWeekday(date) {
  return new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(dateObject(date))
}

function isWeekend(date) {
  const day = dateObject(date).getDay()
  return day === 0 || day === 6
}

function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
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
    const row = visibleRows.value[startRow + rowOffset]
    if (!row) return
    values.forEach((textValue, colOffset) => {
      const date = sheet.value.dates[startCol + colOffset]
      const cell = date ? row.cells[date] : null
      if (!cell) return
      const cleaned = textValue.replace(/,/g, '').trim()
      if (cleaned === '') {
        setDraft(cell, null, '')
        applied += 1
        return
      }
      const value = Number(cleaned)
      if (Number.isFinite(value) && value >= 0) {
        setDraft(cell, value)
        applied += 1
      }
    })
  })
  ElMessage.success(`已粘贴 ${applied} 个单元格，请确认后保存`)
}

function applyBatchPercent() {
  const rate = Number(batchPercent.value) / 100
  if (!Number.isFinite(rate) || rate === 0) return
  let count = 0
  for (const row of visibleRows.value) {
    for (const cell of Object.values(row.cells)) {
      setDraft(cell, cellValue(cell) * (1 + rate))
      count += 1
    }
  }
  ElMessage.success(`已对当前范围 ${count} 个单元格调整 ${batchPercent.value}%`)
}

async function restoreVisible() {
  try {
    await ElMessageBox.confirm(
      `将当前筛选范围内 ${visibleAdjustedCount.value} 个人工值恢复为模型值，确认后仍需点击“保存调整”。`,
      '恢复当前范围',
      { confirmButtonText: '恢复', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  for (const row of visibleRows.value) {
    for (const cell of Object.values(row.cells)) {
      if (cellIsAdjusted(cell)) setDraft(cell, null, '')
    }
  }
}

function discardChanges() {
  drafts.clear()
  ElMessage.info('已撤销本次未保存的修改')
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
    ElMessage.success(`已保存 ${result.updated} 处调整`)
    await loadSheet()
    emit('saved')
  } finally {
    saving.value = false
  }
}

async function loadSheet() {
  loading.value = true
  try {
    const result = await resultsApi.getForecastSheet()
    sheet.value = result
    drafts.clear()
    selectedCell.value = null
    selectedPosition.value = { row: -1, col: -1 }
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
      `还有 ${drafts.size} 处调整未保存，离开后这些修改会丢失。`,
      '离开预测排期表？',
      { confirmButtonText: '放弃并离开', cancelButtonText: '继续编辑', type: 'warning' },
    )
    return true
  } catch {
    return false
  }
})

defineExpose({ reload: loadSheet })
</script>

<style scoped>
.forecast-sheet-card {
  overflow: hidden;
  background: #fff;
  border: 1px solid #dfe3e8;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(16, 24, 40, .05);
}

.sheet-heading,
.sheet-toolbar,
.formula-bar,
.sheet-meta,
.sheet-footnote { padding-left: 18px; padding-right: 18px; }

.sheet-heading {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid #eaedf0;
}

.sheet-title-row { display: flex; align-items: center; gap: 12px; }
.sheet-app-icon {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  background: #3370ff;
  font-size: 23px;
  box-shadow: 0 4px 10px rgba(51, 112, 255, .22);
}
.sheet-title-row h2 { font-size: 18px; line-height: 1.4; }
.sheet-title-row p { margin-top: 2px; color: #8f959e; font-size: 12px; }
.sheet-actions { display: flex; align-items: center; gap: 10px; }
.save-status { display: inline-flex; align-items: center; gap: 7px; margin-right: 4px; color: #8f959e; font-size: 12px; }
.save-status i { width: 7px; height: 7px; border-radius: 50%; background: #34c759; }
.save-status.dirty { color: #b26a00; }
.save-status.dirty i { background: #f5a623; }

.sheet-toolbar {
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid #eaedf0;
  background: #fbfcfd;
}
.toolbar-group { display: flex; align-items: center; gap: 8px; }
.toolbar-right { margin-left: auto; }
.search-input { width: 210px; }
.filter-select { width: 130px; }
.toolbar-label, .percent-sign { color: #646a73; font-size: 12px; white-space: nowrap; }
.toolbar-right :deep(.el-input-number) { width: 108px; }
.percent-sign { margin-left: -5px; }

.formula-bar {
  height: 38px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid #dfe3e8;
  background: #fff;
  color: #646a73;
  font-size: 12px;
}
.cell-address { width: 54px; color: #373c43; font-variant-numeric: tabular-nums; }
.fx { width: 34px; border-left: 1px solid #e5e7eb; color: #8f959e; font-family: Georgia, serif; font-style: italic; font-weight: 700; text-align: center; }
.formula-value, .formula-note {
  height: 30px;
  border: 0;
  outline: 0;
  color: #1f2329;
  background: transparent;
  font: inherit;
}
.formula-value { width: 120px; padding: 0 10px; border-left: 1px solid #e5e7eb; }
.formula-note { min-width: 180px; flex: 1; padding: 0 12px; border-left: 1px solid #e5e7eb; }
.model-hint { padding: 0 12px; color: #8f959e; white-space: nowrap; }
.formula-placeholder { padding-left: 10px; color: #a8adb5; }

.sheet-meta {
  min-height: 36px;
  display: flex;
  align-items: center;
  gap: 18px;
  overflow-x: auto;
  border-bottom: 1px solid #dfe3e8;
  background: #f7f8fa;
  color: #8f959e;
  font-size: 11px;
  white-space: nowrap;
}
.adjusted-legend, .dirty-legend { display: inline-flex; align-items: center; gap: 5px; }
.adjusted-legend i, .dirty-legend i { width: 9px; height: 9px; border-radius: 2px; }
.adjusted-legend i { background: #3370ff; }
.dirty-legend i { background: #fff0b3; border: 1px solid #f5c451; }

.grid-shell {
  min-height: 360px;
  max-height: calc(100vh - 360px);
  overflow: auto;
  background: #fff;
}
.forecast-grid {
  min-width: max-content;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
  color: #1f2329;
  font-size: 12px;
}
.forecast-grid th,
.forecast-grid td {
  height: 34px;
  padding: 0;
  border-right: 1px solid #e6e8eb;
  border-bottom: 1px solid #e6e8eb;
  background: #fff;
}
.forecast-grid thead th {
  position: sticky;
  top: 0;
  z-index: 8;
  height: 48px;
  background: #f5f6f7;
  color: #646a73;
  font-weight: 500;
  text-align: center;
}
.forecast-grid thead th b { display: block; color: #373c43; font-size: 12px; }
.forecast-grid thead th span { display: block; margin-top: 2px; color: #8f959e; font-size: 10px; }
.forecast-grid .weekend { background: #fffaf3; }

.sticky-col { position: sticky; z-index: 5; }
.row-number-col { left: 0; width: 42px; min-width: 42px; max-width: 42px; }
.platform-col { left: 42px; width: 100px; min-width: 100px; max-width: 100px; }
.shop-col { left: 142px; width: 142px; min-width: 142px; max-width: 142px; }
.channel-col { left: 284px; width: 142px; min-width: 142px; max-width: 142px; box-shadow: 3px 0 4px rgba(16, 24, 40, .05); }
.forecast-grid thead .sticky-col { z-index: 12; background: #f5f6f7; }
.date-col, .value-cell { width: 92px; min-width: 92px; max-width: 92px; }
.total-col { width: 106px; min-width: 106px; max-width: 106px; background: #f3f7ff !important; }
.row-number { background: #f8f9fa !important; color: #a2a8b0; text-align: center; }
.dimension-cell { overflow: hidden; padding: 0 9px !important; color: #4e5969; text-overflow: ellipsis; white-space: nowrap; }
.shop-name { color: #1f2329; font-weight: 500; }

.value-cell { position: relative; }
.value-cell input {
  width: 100%;
  height: 33px;
  padding: 0 9px;
  border: 0;
  outline: 0;
  color: #1f2329;
  background: transparent;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: right;
  appearance: textfield;
}
.value-cell input::-webkit-inner-spin-button { appearance: none; }
.value-cell:hover { background: #f4f7ff; }
.value-cell.selected { box-shadow: inset 0 0 0 2px #3370ff; z-index: 3; }
.value-cell.adjusted::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 0;
  height: 0;
  border-top: 8px solid #3370ff;
  border-left: 8px solid transparent;
}
.value-cell.dirty { background: #fff8d8; }
.value-cell.dirty::before { border-top-color: #f5a623; }
.cell-reset {
  position: absolute;
  right: 2px;
  bottom: 1px;
  display: none;
  width: 15px;
  height: 15px;
  border: 0;
  border-radius: 3px;
  color: #fff;
  background: #8f959e;
  cursor: pointer;
  font-size: 12px;
  line-height: 14px;
}
.value-cell:hover .cell-reset { display: block; }
.empty-cell { display: block; color: #c9cdd4; text-align: center; }
.row-total, .date-total, .grand-total { padding: 0 9px !important; color: #1d4ed8; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }

.forecast-grid tfoot td {
  position: sticky;
  bottom: 0;
  z-index: 7;
  height: 38px;
  background: #edf3ff;
  border-top: 1px solid #cddaf5;
}
.forecast-grid tfoot .sticky-col { z-index: 10; }
.total-label { left: 42px; padding-left: 12px !important; color: #1d4ed8; font-weight: 600; text-align: left; }
.grand-total { background: #dfeaff !important; }

.sheet-footnote {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-top: 1px solid #eaedf0;
  color: #8f959e;
  background: #fbfcfd;
  font-size: 11px;
}

@media (max-width: 1000px) {
  .sheet-heading, .sheet-toolbar { align-items: flex-start; flex-direction: column; padding-top: 12px; padding-bottom: 12px; }
  .sheet-actions, .toolbar-right { margin-left: 0; flex-wrap: wrap; }
  .sheet-toolbar { gap: 10px; }
  .toolbar-group { width: 100%; flex-wrap: wrap; }
  .search-input { width: 100%; }
  .filter-select { flex: 1; min-width: 120px; }
  .grid-shell { max-height: 58vh; }
  .sheet-footnote { align-items: flex-start; flex-direction: column; padding-top: 10px; padding-bottom: 10px; }
}
</style>
