<template>
  <div class="snapshot-view">
    <div class="snapshot-meta">
      <b>{{ snapshot.title || title || '来源表格' }}</b>
      <span>读取时间：{{ snapshot.read_at ? new Date(snapshot.read_at).toLocaleString('zh-CN') : '未记录' }}</span>
      <span v-if="snapshot.source_type === 'excel'">工作表：{{ snapshot.sheet_name }}</span><span v-if="!['excel', 'manual'].includes(snapshot.source_type)">表格版本：{{ snapshot.revision ?? '未记录' }}</span>
      <span v-if="snapshot.range">范围：{{ snapshot.range }}</span>
      <a v-if="snapshot.url" :href="snapshot.url" target="_blank" rel="noopener noreferrer">打开飞书原表 ↗</a>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="单元格视图" name="cells">
        <div class="snapshot-toolbar">
          <el-radio-group v-model="section">
            <el-radio-button v-if="parsedResult" value="gmv">{{ parsedResult.quantity_only ? '预估单量区域' : 'GMV目标区域' }}</el-radio-button>
            <el-radio-button v-if="parsedResult && !parsedResult.quantity_only" value="schedule">营销节奏区域</el-radio-button>
            <el-radio-button value="all">全部来源</el-radio-button>
          </el-radio-group>
          <span>{{ rows.length }} 行 · {{ columns.length }} 列；高亮为解析引用的单元格，点击查看原值与公式。</span>
        </div>
        <el-empty v-if="!rows.length" description="该版本没有可展示的来源单元格" />
        <div v-else class="snapshot-scroll" tabindex="0" aria-label="来源单元格表格，可横向滚动">
          <table class="snapshot-grid">
            <thead><tr><th class="row-number">行号</th><th v-for="col in columns" :key="col">{{ col }}</th></tr></thead>
            <tbody>
              <tr v-for="row in rows" :key="row.number">
                <th class="row-number">{{ row.number }}</th>
                <td v-for="col in columns" :key="col" :class="{ referenced: references.has(`${col}${row.number}`), selected: selected?.ref === `${col}${row.number}` }">
                  <button v-if="row.cells[col]" class="snapshot-cell" :title="`${col}${row.number}：${display(row.cells[col].value)}`" @click="selected = row.cells[col]">{{ display(row.cells[col].value) }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="selected" class="cell-detail"><b>{{ selected.ref }}</b><div><span>单元格原值</span><pre>{{ display(selected.value) }}</pre></div><div><span>公式</span><pre>{{ selected.formula || '无公式记录' }}</pre></div></div>
        <p class="snapshot-note">这是导入或解析时保存的历史单元格快照，保留原值与坐标，未还原颜色、合并单元格或实时改动。</p>
      </el-tab-pane>
      <el-tab-pane label="原始 JSON" name="json"><pre class="snapshot-json">{{ JSON.stringify(snapshot, null, 2) }}</pre></el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ snapshot: { type: Object, required: true }, parsedResult: Object, title: String })
const section = ref(props.parsedResult ? 'gmv' : 'all'), tab = ref('cells'), selected = ref(null)
const references = computed(() => new Set([...(props.parsedResult?.records || []), ...(props.parsedResult?.estimated_orders || []), ...(props.parsedResult?.quantity_rows || []).flatMap(row => row.records), ...(props.parsedResult?.schedules || [])].flatMap(r => r.source_cells || [r.cell])))
const cells = computed(() => {
  const all = props.snapshot.cells || [], result = props.parsedResult
  if (!result || section.value === 'all') return all
  if (['douyin_baby', 'vip', 'counter_excel', 'tmall_baby_manual', 'private_manual', 'ka_manual', 'pdd_manual', 'ali_health_manual', 'other_manual'].includes(result.parser)) {
    const records = section.value === 'gmv' ? [...result.records, ...(result.estimated_orders || []), ...(result.quantity_rows || []).flatMap(row => row.records)] : result.schedules
    const refs = new Set(records.flatMap(r => r.source_cells || [r.cell]))
    const rows = new Set(all.filter(c => refs.has(c.ref)).map(c => c.row))
    const cols = new Set(all.filter(c => refs.has(c.ref)).map(c => c.col))
    return all.filter(c => refs.has(c.ref) || (c.row === result.header_row && cols.has(c.col)) || (section.value === 'gmv' && ((c.row === result.header_row - 1 && cols.has(c.col)) || (['A', 'B'].includes(c.col) && rows.has(c.row)) || (c.row === 1 && c.col === 'A'))))
  }
  if (result.parser === 'douyin') {
    const records = section.value === 'gmv' ? [...result.records, ...(result.estimated_orders || []), ...(result.quantity_rows || []).flatMap(row => row.records)] : result.schedules
    const refs = new Set(records.flatMap(r => r.source_cells || [r.cell]))
    const cols = new Set(all.filter(c => refs.has(c.ref)).map(c => c.col))
    if (section.value === 'gmv') cols.add('A')
    return all.filter(c => refs.has(c.ref) || (c.row === result.header_row && cols.has(c.col)) || (section.value === 'gmv' && ((c.col === 'A' && c.row >= result.section_rows[0] && c.row < result.section_rows[1]) || (c.row === 1 && ['A','B'].includes(c.col)))))
  }
  const [start, end] = result.section_rows
  const headerColumns = new Set(all.filter(c => c.row === result.header_row).map(c => c.col))
  if (section.value === 'gmv') return all.filter(c => (c.row === result.header_row || (c.row >= start && c.row < end)) && (headerColumns.has(c.col) || ['A','B'].includes(c.col)))
  return all.filter(c => c.row < start)
})
function colNumber(col) { return [...col].reduce((sum, ch) => sum * 26 + ch.charCodeAt(0) - 64, 0) }
const columns = computed(() => [...new Set(cells.value.map(c => c.col))].sort((a,b) => colNumber(a) - colNumber(b)))
const rows = computed(() => {
  const grouped = new Map()
  for (const c of cells.value) { if (!grouped.has(c.row)) grouped.set(c.row, { number: c.row, cells: {} }); grouped.get(c.row).cells[c.col] = c }
  return [...grouped.values()].sort((a,b) => a.number - b.number)
})
function display(value) { return value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value) }
</script>

<style scoped>
.snapshot-meta,.snapshot-toolbar{display:flex;gap:12px 20px;align-items:center;flex-wrap:wrap;font-size:13px;color:#667085;margin-bottom:16px}.snapshot-meta b{color:#172033}.snapshot-meta a{color:#2563eb}.snapshot-scroll{max-height:52vh;overflow:auto;border:1px solid #d0d5dd;border-radius:6px}.snapshot-grid{border-collapse:separate;border-spacing:0;font-size:13px;table-layout:fixed;width:max-content}.snapshot-grid th,.snapshot-grid td{min-width:150px;width:150px;max-width:150px;border-right:1px solid #e4e7ec;border-bottom:1px solid #e4e7ec;vertical-align:top;overflow-wrap:anywhere}.snapshot-grid thead th{position:sticky;top:0;background:#eef2f7;z-index:2;text-align:center;padding:9px;font-weight:600}.snapshot-grid .row-number{position:sticky;left:0;min-width:60px;width:60px;max-width:60px;background:#eef2f7;text-align:center;padding:9px;z-index:1}.snapshot-grid thead .row-number{z-index:3}.snapshot-cell{display:block;width:100%;min-height:36px;max-height:150px;overflow:auto;border:0;background:transparent;padding:8px;white-space:pre-wrap;text-align:left;font:inherit;color:#344054;cursor:pointer}.snapshot-grid td.referenced{background:#eff6ff}.snapshot-grid td.selected{box-shadow:inset 0 0 0 2px #2563eb}.cell-detail{display:flex;gap:20px;align-items:flex-start;background:#f8fafc;border:1px solid #e4e7ec;border-radius:6px;padding:12px;margin-top:12px;max-height:20vh;overflow:auto;font-size:13px}.cell-detail>div{flex:1;min-width:0}.cell-detail span{color:#667085;font-size:12px}.cell-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0 0;font:inherit}.snapshot-note{font-size:12px;color:#667085;margin-top:12px;line-height:1.7}.snapshot-json{max-height:65vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f8fafc;padding:16px}
</style>
