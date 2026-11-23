<template>
  <div class="gmv-page" v-loading="pageLoading && !state.sources.length" :element-loading-text="loadProgress">
    <header class="gmv-header">
      <div><span class="eyebrow">销售规划 · {{ state.sources.find(s => s.id === sourceId)?.shop || '店铺' }}</span><h1>预期GMV</h1><p>从营销规划提取渠道目标与活动节奏，每次解析均保留版本和核验依据。</p></div>
      <div class="actions"><el-button type="primary" plain @click="orderPreview.open()">预览总订单数据</el-button><el-button :loading="importingCounter" @click="counterFileInput.click()">导入专柜Excel</el-button><input ref="counterFileInput" type="file" accept=".xlsx" style="display:none" @change="importCounter"/><el-button @click="openModels">模型设置</el-button><el-button @click="editSource()">添加店铺表格</el-button><el-button :loading="pageLoading" @click="load(true)">刷新</el-button></div>
    </header>
    <el-alert v-if="pageLoadError" type="error" :closable="false" title="数据加载失败">
      <p>{{ pageLoadError }}</p><el-button :loading="pageLoading" @click="load">重新加载</el-button>
    </el-alert>
    <div class="source-toolbar">
      <div class="source-picker">
        <label id="gmv-shop-label">店铺分类</label>
        <el-select v-model="selectedShop" filterable placeholder="选择店铺分类" aria-labelledby="gmv-shop-label" class="shop-select" @change="selectShop">
          <el-option v-for="shop in shopCategories" :key="shop" :value="shop" :label="shop"/>
        </el-select>
        <label id="gmv-source-label">月份 / 规划</label>
        <el-select v-model="sourceId" filterable placeholder="选择月份或规划" aria-labelledby="gmv-source-label" class="source-select" :disabled="!selectedShop" @change="selectSource">
          <el-option v-for="s in shopSources" :key="s.id" :value="s.id" :label="s.name"/>
        </el-select>
        <el-tag v-if="selectedSource" :type="statusType(latest(sourceId)?.status)">{{ selectedSource.source_type === 'manual' && !latest(sourceId) ? '待填报' : statusText(latest(sourceId)?.status) }}</el-tag>
      </div>
      <div v-if="selectedSource" class="actions source-actions">
        <template v-if="selectedSource.source_type === 'manual'"><el-button type="primary" @click="openManualSource(selectedSource)">手动填报</el-button></template>
        <template v-else-if="selectedSource.source_type === 'excel'"><span class="source-filename muted">{{ selectedSource.filename }}</span><el-button type="primary" :loading="importingCounter" @click="counterFileInput.click()">重新导入</el-button></template>
        <template v-else><el-button type="primary" :loading="parsing === sourceId" :disabled="!!parsing" @click="parse(selectedSource)">读取并解析</el-button><el-button @click="editSource(selectedSource)">编辑来源</el-button><a :href="selectedSource.url" target="_blank" rel="noopener noreferrer">打开原表 ↗</a></template>
      </div>
    </div>
    <section class="panel" v-loading="runLoading" element-loading-text="正在加载所选表格…">
      <div class="actions spread"><h2>解析结果</h2><el-select v-model="runId" placeholder="选择历史解析版本" style="width:420px;max-width:100%"><el-option v-for="r in sourceRuns" :key="r.id" :value="r.id" :label="`${time(r.created_at)} · ${statusText(r.status)} · ${r.id.slice(0,8)}`" /></el-select></div>
      <el-alert v-if="runDetailError" :title="runDetailError" type="error" :closable="false"><el-button @click="loadSelectedRun">重新加载表格</el-button></el-alert>
      <el-empty v-if="!run" :description="state.sources.find(s => s.id === sourceId)?.source_type === 'manual' ? '点击「手动填报」设置日期并填写单量' : '选择表格，点击「读取并解析」开始'" />
      <template v-else>
        <div class="meta">{{ run.source?.source_type === 'manual' ? '填报' : run.source?.source_type === 'excel' ? '导入' : '解析' }} {{ run.id.slice(0,8) }} · {{ run.source?.source_type === 'manual' ? '手动填报快照' : run.source?.source_type === 'excel' ? 'Excel快照' : `规则 ${run.rule_version.slice(0,8)}` }} · {{ time(run.created_at) }} · <el-tag :type="statusType(run.status)">{{ statusText(run.status) }}</el-tag><span v-if="run.result?.target_section"> · 来源：{{ run.result.target_section }}</span><el-button text @click="showSnapshot">查看来源快照</el-button></div><el-collapse v-if="run.rule_attempts?.length"><el-collapse-item :title="`规则自动匹配：尝试 ${run.rule_attempts.length} 个版本`"><div v-for="(attempt, index) in run.rule_attempts" :key="index" class="meta">{{ index + 1 }}. {{ attempt.rule_version.slice(0, 8) }} · {{ statusText(attempt.status) }}<span v-if="attempt.error"> · {{ attempt.error }}</span></div></el-collapse-item></el-collapse>
        <el-alert v-if="run.error" :title="run.error" type="error" :closable="false" />
        <el-alert v-if="run.verification?.scope === 'source_ranges' && !run.verification.passed" title="代码读取的范围未通过审查，需修正规则后重新解析。" type="error" :closable="false" />
        <div v-if="canRepair" class="repair-banner"><div><b>首次规则解析失败，可以启动 AI 更新规则</b><p>维护模型读取飞书与规则代码 → 读取范围核验 → 独立审查 → 启用并重解析。</p></div><div class="actions"><el-select v-model="provider" style="width:140px"><el-option label="DeepSeek" value="deepseek"/><el-option label="MiniMax" value="minimax"/></el-select><span>审查</span><el-select v-model="reviewer" style="width:140px"><el-option label="MiniMax" value="minimax"/><el-option label="DeepSeek" value="deepseek"/></el-select><el-button type="primary" :disabled="!state.models?.[provider]?.configured || !state.models?.[reviewer]?.configured" @click="repair">AI 更新解析规则</el-button></div><small v-if="!state.models?.[provider]?.configured || !state.models?.[reviewer]?.configured">请先在模型设置中填写对应 API Key。</small></div>
        <el-tabs v-model="resultTab" v-if="run.result">
          <el-tab-pane :label="quantityOnly ? '预估单量' : '渠道目标'" name="targets">
            <div class="actions"><el-radio-group v-model="grain"><el-radio-button value="day">每日目标</el-radio-button><el-radio-button value="summary">{{ run.result.summary_label || '原表汇总' }}</el-radio-button><el-radio-button v-if="canForecast" value="forecast">预估单量</el-radio-button></el-radio-group><span v-if="forecastMode" class="muted">仅预期数据；GMV / 客单价：元；预估单量：单</span><span v-else-if="quantityOnly" class="muted">单量：单</span><span v-else class="muted">GMV / 客单价：元；单量：单；达成率：%；{{ run.result.parser === 'vip' ? '唯品会GMV及预估单量' : run.result.parser === 'douyin' ? '抖音自播 / 商城 / 达播' : run.result.parser === 'douyin_baby' ? '抖音宝贝五渠道' : '五类渠道不包含自播' }}</span></div>
            <p v-if="!quantityOnly && grain !== 'summary'" class="gmv-trend-legend">预期GMV对比同渠道前一天：↑ 增长 / ↓ 下降；涨跌达到20%时，<span class="rise">大幅增长标红</span>、<span class="fall">大幅下降标绿</span>。首日或前一天缺失时不计算。</p>
            <div v-if="forecastMode" class="actions"><el-button type="primary" :loading="forecastLoading" @click="openForecastEditor">编辑 / 批量填充</el-button><span class="muted">单量四舍五入到整数单；保存后保留独立填报历史</span></div>
            <p v-if="forecastMode" class="forecast-totals">全日期GMV合计：<b>{{ amount(forecastGrandTotals.gmv) }}元</b> · 预估单量合计：<b>{{ amount(forecastGrandTotals.orders) }}单</b>（逐日相加{{ isBaby ? '，含PDD' : '，含派样' }}；未填写项不参与统计）</p>
            <el-alert v-if="forecastError && forecastMode" :title="forecastError" type="error" :closable="false"><el-button @click="loadForecastInputs(true)">重新加载填报数据</el-button></el-alert>
            <el-alert v-if="forecastMode" :title="isDouyin ? '自播和商城支持预估客单、预估单量填写；达播单独填写精选联盟订单、达播单量，自动相加为达播汇总单量，底部只汇总一次，另含独立填写的派样单量。' : '客单价和预估单量支持手动编辑及批量设值、加减。未手填单量时按GMV÷客单价计算；手填单量优先，清空可恢复自动。'" type="info" :closable="false"/><el-alert v-if="run.result.conversion_note" :title="run.result.conversion_note" type="info" :closable="false"/>
            <p v-if="isBaby" class="muted">抖音宝贝自播、商城、KOC、达播实绩已接入；DP合计待提供SQL。底部实际汇总为已接入渠道合计。</p>
            <p v-if="isDouyin" class="muted">抖音实绩：汇总指定四家店铺、订单状态900；自播取“自营直播间”，商城合并“自营其他 / 自营短视频”。达播合并“达人短视频 / 达人直播间 / 达人其他”。底部实际合计包含自播、商城和达播。{{ actualLoading ? '读取实绩中…' : shelfActuals?.queried_at ? ` 查询时间：${time(shelfActuals.queried_at)}` : '' }}</p>
            <p v-if="!forecastMode && shelfActuals?.supported && !isDouyin && !isBaby" class="muted">实际数据：薇诺娜天猫旗舰店 · TB / TM · 订单状态900；店铺对应货架-常规，CPS开头渠道对应货架-CPS，百亿补贴对应百补；达播中李佳琦为超头，其余为二梯队。达成率＝该渠道实际实付GMV ÷ 对应目标；区间按已有实绩累计，未查询到数据的日期显示“—”。{{ actualLoading ? '读取实绩中…' : shelfActuals.queried_at ? ` 查询时间：${time(shelfActuals.queried_at)}` : '' }}</p><el-alert v-if="actualError && !forecastMode" :title="actualError" type="warning" :closable="false"><el-button :loading="actualLoading" @click="loadActuals(true)">重新读取实际数据</el-button></el-alert><GMVTargetTable :rows="targetRows" :periods="targetPeriods" :summaries="targetSummaries" :format="metricAmount" :evidence="evidence" :show-trend="!quantityOnly && grain !== 'summary'"/>
          </el-tab-pane>
          <el-tab-pane lazy v-if="!quantityOnly" :label="run.result.schedule_label || '官方 / 店铺节奏'" name="schedule"><el-alert :title="run.result.schedule_note || '保留原文及所在日期列；活动准确起止时间以原文为准，未推断合并单元格覆盖区间。'" type="info" :closable="false"/><el-table :data="run.result.schedules" border max-height="560"><el-table-column prop="type" label="节奏" width="110"/><el-table-column prop="date" label="日期 / 档期" width="160"/><el-table-column prop="subtype" label="子类" width="110"/><el-table-column prop="text" label="活动原文" min-width="400"><template #default="scope"><div class="preserve">{{ scope.row.text }}</div></template></el-table-column><el-table-column prop="cell" label="源单元格" width="100"/></el-table></el-tab-pane>
        </el-tabs>
      </template>
    </section>
    <section class="panel"><div class="actions spread"><h2>AI 规则更新任务</h2><el-select v-model="taskId" placeholder="选择任务" style="width:420px;max-width:100%"><el-option v-for="t in state.tasks" :key="t.id" :value="t.id" :label="`${time(t.created_at)} · ${statusText(t.status)} · ${t.id.slice(0,8)}`"/></el-select></div><p class="muted">实时显示工具、读取代码、修改说明与独立审查结论。运行时每 2 秒刷新。</p><el-empty v-if="!task" description="暂无规则更新任务"/><template v-else><el-tag :type="statusType(task.status)">{{ statusText(task.status) }}</el-tag><p v-if="task.error">{{ task.error }}</p><div class="timeline"><div v-for="e in task.events" :key="e.seq" class="event"><div><span class="muted">{{ time(e.time) }} · {{ e.kind }}</span><b>{{ e.name }}</b></div><pre>{{ typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail, null, 2) }}</pre></div></div></template></section>
    <section class="panel"><h2>解析规则库（自动匹配）</h2><p class="meta">优先使用该来源上次成功的版本，失败后依次尝试默认及历史规则；通过解析和范围核验后采用，无需手动回滚。</p><el-table :data="state.rules" border><el-table-column prop="rule_key" label="店铺规则" width="140"/><el-table-column label="版本" min-width="160"><template #default="scope"><code>{{ scope.row.id.slice(0,12) }}</code><el-tag v-if="state.active?.[scope.row.rule_key] === scope.row.id" size="small">默认</el-tag></template></el-table-column><el-table-column label="创建时间" width="180"><template #default="scope">{{ time(scope.row.created_at) }}</template></el-table-column><el-table-column prop="reason" label="变更说明" min-width="180"/><el-table-column label="操作" width="160"><template #default="scope"><el-button text @click="code = scope.row.code; codeVisible = true">查看代码</el-button></template></el-table-column></el-table></section>
    <el-dialog append-to-body v-model="modelVisible" title="AI 维护与审查模型" width="650px"><p>密钥加密保存；留空保留原密钥。模型名称可调整，调用前后校验消息结构。</p><el-form label-position="top"><template v-for="p in ['deepseek','minimax']" :key="p"><h3>{{ p === 'deepseek' ? 'DeepSeek' : 'MiniMax' }} <el-tag>{{ state.models?.[p]?.configured ? '已配置' : '待配置密钥' }}</el-tag></h3><el-form-item v-if="p === 'minimax'" label="MiniMax 接口区域（须与密钥所属平台一致）"><el-select v-model="modelForm[p].region" style="width:100%"><el-option label="国内 · api.minimaxi.com" value="china"/><el-option label="国际 · api.minimax.io" value="international"/></el-select></el-form-item><el-form-item label="多模态模型名称"><el-input v-model="modelForm[p].model"/></el-form-item><el-form-item label="API Key"><el-input v-model="modelForm[p].api_key" type="password" show-password autocomplete="new-password"/></el-form-item></template></el-form><p class="muted">默认模型：deepseek-flash / MiniMax-M3.1-Flash。本任务读取结构化表格，无须图片输入。</p><template #footer><el-button :disabled="!!testingModel || (!state.models?.deepseek?.configured && !modelForm.deepseek.api_key.trim())" :loading="testingModel === 'deepseek'" @click="testModel('deepseek')">验证 DeepSeek</el-button><el-button :disabled="!!testingModel || (!state.models?.minimax?.configured && !modelForm.minimax.api_key.trim())" :loading="testingModel === 'minimax'" @click="testModel('minimax')">验证 MiniMax</el-button><el-button @click="modelVisible=false">取消</el-button><el-button type="primary" @click="saveModels">保存</el-button></template></el-dialog>
    <el-dialog append-to-body v-model="sourceVisible" :title="editingId ? '编辑表格来源' : '添加店铺表格'" width="650px"><el-form label-position="top"><el-form-item label="店铺分类" required><el-select v-model="sourceForm.shop" filterable allow-create default-first-option placeholder="请选择店铺分类，也可输入新分类" style="width:100%" @change="sourceCategoryChanged"><el-option v-for="shop in onlineShopCategories" :key="shop" :value="shop" :label="shop"/></el-select></el-form-item><el-form-item label="月份 / 规划名称" required><el-input v-model="sourceForm.name" @change="inferFormMonths" placeholder="例如：2026年9月、2026年10+11月"/></el-form-item><el-form-item label="月份（可多选）" required><el-select v-model="sourceForm.months" multiple placeholder="选择数字月份" style="width:100%"><el-option v-for="month in 12" :key="month" :label="String(month)" :value="month"/></el-select><span class="muted">根据规划名称自动识别，可手动调整；跨月规划请多选。</span></el-form-item><el-form-item label="飞书 Wiki / Sheets 电子表格链接"><el-input v-model="sourceForm.url" placeholder="支持 /wiki/ 和 /sheets/；不带 sheet 时读取第一个可见工作表"/></el-form-item><el-form-item v-if="sourceForm.parser === 'douyin'" label="历史参考"><el-checkbox v-model="sourceForm.history_enabled">纳入自播历史参考</el-checkbox></el-form-item><el-form-item label="数据年份"><el-input-number v-model="sourceForm.year" :min="2000" :max="2100"/></el-form-item><el-form-item label="解析方式"><el-select v-model="sourceForm.parser" :disabled="!!editingId"><el-option label="天猫规划表" value="tmall"/><el-option label="抖音日期纵向表" value="douyin"/><el-option label="抖音宝贝横向销售表" value="douyin_baby"/><el-option label="唯品会GMV及预估单量" value="vip"/></el-select></el-form-item><el-form-item v-if="sourceForm.parser === 'douyin'" label="金额读取口径"><el-select v-model="sourceForm.amount_mode"><el-option label="按表头说明换算GMV" value="source_conversion"/><el-option label="直接读取原表数值（不换算）" value="source_value"/></el-select></el-form-item><el-form-item label="规则标识（不同店铺使用不同标识）"><el-input v-model="sourceForm.rule_key" :disabled="!!editingId"/></el-form-item><el-form-item label="读取范围（包含日期表头、GMV区域与核心指标边界）"><el-input v-model="sourceForm.range"/></el-form-item></el-form><template #footer><el-button type="primary" @click="saveSource">保存</el-button></template></el-dialog>
    <GMVOrderPreview ref="orderPreview"/>
    <GMVTmallForecastEditor ref="forecastEditor" @saved="forecastSaved"/>
    <GMVTmallBabyEditor ref="tmallBabyEditor" :sources="state.sources" :runs="state.runs" @saved="manualSaved"/>
    <el-dialog v-model="codeVisible" append-to-body title="解析规则代码" width="85%"><pre class="code-view">{{ code }}</pre></el-dialog>
    <el-dialog v-model="snapshotVisible" append-to-body title="来源快照" width="92%" top="4vh" destroy-on-close>
      <GMVSourceSnapshot v-if="snapshotData" :snapshot="snapshotData" :parsed-result="snapshotRun?.result" :title="snapshotRun?.source?.name" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h, markRaw } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { gmvApi } from '../api'
import { inferSourceMonths } from '../utils/gmvSourceMonths.mjs'
import GMVOrderPreview from '../components/GMVOrderPreview.vue'
import GMVSourceSnapshot from '../components/GMVSourceSnapshot.vue'
import { buildTmallForecastRows, buildChannelForecastRows, forecastOrderTotal, forecastGmvTotal, forecastTotals, priceRecords, orderRecords } from '../utils/tmallForecast.mjs'
import GMVTmallForecastEditor from '../components/GMVTmallForecastEditor.vue'
import GMVTargetTable from '../components/GMVTargetTable.vue'
import GMVTmallBabyEditor from '../components/GMVTmallBabyEditor.vue'
const state = ref({ sources: [], runs: [], rules: [], tasks: [], models: {} })
const pageLoading = ref(false), pageLoadError = ref(''), loadProgress = ref('正在加载目标和历史解析…')
let loadPromise = null
const sourceId = ref(''), runId = ref(''), taskId = ref(''), task = ref(null), parsing = ref('')
const provider = ref('deepseek'), reviewer = ref('minimax'), grain = ref('day'), resultTab = ref('targets')
const modelVisible = ref(false), sourceVisible = ref(false), codeVisible = ref(false), code = ref(''), editingId = ref('')
const modelForm = ref({ deepseek: { model: '', api_key: '' }, minimax: { model: '', api_key: '' } })
const orderPreview = ref(null)
const sourceForm = ref({}), testingModel = ref('')
const snapshotVisible = ref(false), snapshotData = ref(null), snapshotRun = ref(null)
const shelfActuals = ref(null), actualLoading = ref(false), actualError = ref('')
let actualRequest = 0, detailRequest = 0
const runLoading = ref(false), runDetailError = ref('')
const detailCache = new Map(), detailPending = new Map(), actualCache = new Map()
function setOverview(result) { state.value = {...result, runs: markRaw(result.runs.map(r => detailCache.get(r.id) || r))} }
async function ensureRunDetail(id) {
  if (detailCache.has(id)) return detailCache.get(id)
  if (!detailPending.has(id)) detailPending.set(id, gmvApi.runDetail(id).then(detail => {
    detailCache.set(id, markRaw(detail))
    state.value = {...state.value, runs: markRaw(state.value.runs.map(r => r.id === id ? detail : r))}
    return detail
  }).finally(() => detailPending.delete(id)))
  return detailPending.get(id)
}
async function loadSelectedRun() {
  const id = runId.value, request = ++detailRequest
  ++actualRequest; shelfActuals.value = null; actualLoading.value = false
  runDetailError.value = ''; runLoading.value = !!id
  if (!id) return
  try {
    await ensureRunDetail(id)
    if (request !== detailRequest || id !== runId.value) return
    runLoading.value = false
    if (forecastMode.value) await loadForecastInputs()
    else await loadActuals()
  } catch (error) { if (request === detailRequest) runDetailError.value = error.response?.data?.detail || '所选表格加载失败，请重试' }
  finally { if (request === detailRequest) runLoading.value = false }
}
const channels = computed(() => run.value?.result?.channel_order || [...new Set((run.value?.result?.records || []).map(r => r.channel))])
const isTmall = computed(() => run.value?.rule_key === 'tmall')
const isDouyin = computed(() => run.value?.result?.parser === 'douyin')
const isBaby = computed(() => run.value?.result?.parser === 'douyin_baby')
const canForecast = computed(() => isTmall.value || isDouyin.value || isBaby.value)
const forecastMode = computed(() => canForecast.value && grain.value === 'forecast')
const forecastInput = ref(null), forecastLoading = ref(false), forecastError = ref(''), forecastEditor = ref(null)
const forecastPending = new Map()
let forecastRequest = 0
const forecastPrices = computed(() => priceRecords(forecastInput.value?.values || {}))
const forecastOverrides = computed(() => orderRecords(forecastInput.value?.values || {}))
async function loadForecastInputs(force = false) {
  const id = runId.value
  if (!id || !canForecast.value) return
  if (!force && forecastInput.value?.run_id === id) return
  const request = ++forecastRequest
  forecastLoading.value = true; forecastError.value = ''
  try {
    if (!forecastPending.has(id)) forecastPending.set(id, gmvApi.forecastInputs(id).finally(() => forecastPending.delete(id)))
    const response = await forecastPending.get(id)
    if (request === forecastRequest && id === runId.value) forecastInput.value = response
  } catch (error) { if (request === forecastRequest) forecastError.value = error.response?.data?.detail || '填报数据加载失败' }
  finally { if (request === forecastRequest) forecastLoading.value = false }
}
async function openForecastEditor() {
  await loadForecastInputs()
  if (forecastInput.value?.run_id === runId.value) forecastEditor.value.open(run.value, forecastInput.value)
}
function forecastSaved(response) { if (response.run_id === runId.value) forecastInput.value = response }

const tmallBabyEditor = ref(null)
const counterFileInput = ref(null), importingCounter = ref(false)
const quantityOnly = computed(() => !!run.value?.result?.quantity_only)
const pendingChannels = computed(() => shelfActuals.value?.pending_channels || (isBaby.value ? channels.value : []))
const selectedSource = computed(() => state.value.sources.find(s => s.id === sourceId.value))
const selectedShop = ref('')
const shopCategories = computed(() => [...new Set(state.value.sources.map(s => s.shop).filter(Boolean))])
const shopSources = computed(() => state.value.sources.filter(s => s.shop === selectedShop.value)
  .sort((a, b) => (b.year || 0) - (a.year || 0) || Math.max(0, ...(b.months || inferSourceMonths(b.name))) - Math.max(0, ...(a.months || inferSourceMonths(a.name))) || b.name.localeCompare(a.name, 'zh-CN', {numeric: true})))
const onlineShopCategories = computed(() => [...new Set(state.value.sources.filter(s => !s.source_type).map(s => s.shop))])
const sourceRuns = computed(() => state.value.runs.filter(r => r.source_id === sourceId.value))
const run = computed(() => state.value.runs.find(r => r.id === runId.value))
const canRepair = computed(() => (run.value?.status === 'parse_failed' || (run.value?.status === 'verification_failed' && run.value?.verification?.scope === 'source_ranges')) && !run.value?.repair_task && sourceRuns.value[0]?.id === run.value?.id && state.value.active?.[run.value?.rule_key] === run.value?.rule_version)
const targetRecords = computed(() => (quantityOnly.value ? (run.value.result.quantity_rows?.flatMap(row => row.records) || run.value.result.estimated_orders) : run.value?.result?.records || []).filter(r => r.kind === (forecastMode.value ? 'day' : grain.value)))
const targetPeriods = computed(() => {
  const periods = [...new Set(targetRecords.value.map(r => r.period))]
  return grain.value === 'day' || forecastMode.value ? periods.sort() : periods
})
const targetRows = computed(() => {
  if (forecastMode.value) return isTmall.value ? buildTmallForecastRows(run.value?.result?.records || [], channels.value, forecastPrices.value, forecastOverrides.value) : buildChannelForecastRows(run.value?.result?.records || [], channels.value, forecastPrices.value, forecastOverrides.value)
  if (run.value?.result?.quantity_rows) return run.value.result.quantity_rows.map(row => ({channel: row.channel, metric: row.metric, kind: 'target', values: Object.fromEntries(row.records.filter(r => r.kind === grain.value).map(r => [r.period, r]))}))
  const rows = channels.value.map(channel => ({
    channel, kind: 'target',
    values: Object.fromEntries(targetRecords.value.filter(r => r.channel === channel).map(r => [r.period, r])),
  }))
  if (run.value?.result?.estimated_orders) rows.push({channel: '预估单量', kind: 'target', metric: 'estimated_orders', values: Object.fromEntries(run.value.result.estimated_orders.filter(r => r.kind === grain.value).map(r => [r.period, r]))})
  if (!shelfActuals.value?.supported && !isDouyin.value && !isBaby.value) return rows
  const actualGroups = isBaby.value ? channels.value.map(channel => [channel, channel === '自播官旗' ? '自营直播间' : channel === '商城' ? '自营其他' : channel === 'KOC' ? '达人短视频' : channel === '抖音达播' ? '达人直播间' : '待接入宝贝SQL']) : isDouyin.value ? channels.value.map(channel => [channel, channel === '抖音自播' ? '自营直播间' : channel === '抖音商城' ? '自营其他 / 自营短视频' : '达人短视频 / 达人直播间 / 达人其他']) : [['货架-常规', '店铺'], ['货架-CPS', 'CPS开头的渠道'], ['百补', '百亿补贴'], ['达播-超头', '达播 · 李佳琦'], ['达播-二梯队', '达播 · 非李佳琦']]
  for (const [targetChannel, sourceChannel] of actualGroups) {
    const channelPending = pendingChannels.value.includes(targetChannel)
    const targetRow = rows.find(row => row.channel === targetChannel)
    const channelActuals = shelfActuals.value?.channels?.[targetChannel] || (targetChannel === '货架-常规' ? shelfActuals.value : null)
    const metrics = [
      { channel: '实际单量', metric: 'order_qty' },
      { channel: '实际GMV', metric: 'payment_amount' },
      { channel: '实际客单价', metric: 'avg_order_value' },
      { channel: 'GMV达成率', metric: 'achievement_rate' },
    ].map(metric => ({ ...metric, kind: 'actual', values: Object.fromEntries(targetPeriods.value.map(period => {
      const actual = channelActuals?.periods?.[period]
      const target = targetRow?.values[period]?.amount
      const value = metric.metric === 'achievement_rate'
        ? actual?.payment_amount != null && target > 0 ? actual.payment_amount / target : null
        : actual?.[metric.metric]
      return [period, { amount: value, evidence: channelPending ? '该店铺渠道待接入SQL' : actualLoading.value ? '读取实绩中' : `${isBaby.value ? '指定宝贝店铺（含空店名）' : isDouyin.value ? '指定四家抖音店铺' : '薇诺娜天猫旗舰店 · TB/TM'} · ${sourceChannel} · 状态900；${actual?.days_with_data || 0}天有记录${actual?.last_date ? `，最近${actual.last_date}` : ''}${metric.metric === 'achievement_rate' ? `；实际实付GMV / ${targetChannel}目标` : ''}` }]
    })) }))
    rows.splice(rows.findIndex(row => row.channel === targetChannel) + 1, 0, ...metrics)
  }
  return rows
})
function metricAmount(row, value) {
  if (row.kind === 'target' && !row.metric) return targetGmvAmount(value)
  return row.metric === 'achievement_rate' ? value == null ? '—' : `${(value * 100).toFixed(2)}%` : amount(value)
}
async function loadActuals(force = false) {
  const id = runId.value, request = ++actualRequest
  if (force === true) actualCache.delete(id)
  shelfActuals.value = null
  actualError.value = ''
  if (!id || !run.value?.result || quantityOnly.value || forecastMode.value) { actualLoading.value = false; return }
  if (run.value.rule_key === 'tmall' || isDouyin.value || isBaby.value) shelfActuals.value = { supported: true, periods: {}, pending_channels: [] }
  actualLoading.value = true
  try {
    let cached = actualCache.get(id)
    if (!cached || Date.now() - cached.time > 30000) {
      cached = {time: Date.now(), promise: gmvApi.actuals(id).catch(error => { actualCache.delete(id); throw error })}
      actualCache.set(id, cached)
    }
    const result = await cached.promise
    if (request === actualRequest && id === runId.value) shelfActuals.value = markRaw(result)
  }
  catch (error) { if (request === actualRequest) actualError.value = error.response?.data?.detail || '渠道实际数据读取失败，请刷新重试' }
  finally { if (request === actualRequest) actualLoading.value = false }
}
const forecastGrandTotals = computed(() => forecastTotals(targetRows.value, targetPeriods.value))
const targetSummaries = computed(() => targetSummary({columns: Array(targetPeriods.value.length + 1).fill(null), data: targetRows.value}))
function targetSummary({ columns, data }) {
  return columns.map((_, index) => {
    const period = targetPeriods.value[index - 1]
    const targetTotal = targetGmvAmount(forecastGmvTotal(data, period))
    const lines = quantityOnly.value ? [] : [{ label: 'GMV合计', value: targetTotal, kind: 'target' }]
    if (forecastMode.value) lines.push({label: '预估单量合计', value: amount(forecastOrderTotal(data, period)), kind: 'target'})
    const manualTotal = data.find(row => row.metric === 'total_orders')
    if (manualTotal) lines.push({label: manualTotal.channel, value: amount(manualTotal.values[period]?.amount), kind: 'target'})
    const estimated = data.find(row => row.metric === 'estimated_orders')
    if (estimated) lines.push({label: '预估单量合计', value: amount(estimated.values[period]?.amount), kind: 'target'})
    if (!forecastMode.value && (shelfActuals.value?.supported || isDouyin.value)) lines.push(
      { label: '实际GMV合计', value: actualTotal(period, 'payment_amount'), kind: 'actual' },
      { label: '实际单量合计', value: actualTotal(period, 'order_qty'), kind: 'actual' },
    )
    return h('div', { class: 'summary-stack' }, lines.map(line =>
      h('div', { class: `summary-item summary-${line.kind}` }, index === 0 ? line.label : line.value)))
  })
}
function actualTotal(period, metric) {
  if (actualLoading.value || actualError.value) return '—'
  const records = channels.value.map(channel => shelfActuals.value?.channels?.[channel]?.periods?.[period])
    .filter(record => record?.days_with_data > 0)
  // 无源记录的渠道不参与；已匹配源记录但字段缺失时不显示不完整合计。
  const values = records.map(record => record[metric])
  return values.length && values.every(value => typeof value === 'number' && Number.isFinite(value))
    ? amount(values.reduce((total, value) => total + value, 0)) : '—'
}
function latest(id) { return state.value.runs.find(r => r.source_id === id) }
function statusText(s) { return ({ success: '已通过', parse_failed: '规则解析失败', read_failed: '读取失败', verification_failed: '解析待核实', running: '执行中', failed: '失败' })[s] || '待解析' }
function statusType(s) { return s === 'success' ? 'success' : ['failed','parse_failed','read_failed','verification_failed'].includes(s) ? 'danger' : 'info' }
function time(v) { return v ? new Date(v).toLocaleString('zh-CN', { hour12:false }) : '—' }
const amountFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const integerAmountFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })
function amount(v) { return v == null ? '—' : amountFormat.format(Number(v)) }
function targetGmvAmount(v) { return isTmall.value ? v == null ? '—' : integerAmountFormat.format(Number(v)) : amount(v) }
function evidence(r) { return r?.evidence || (r ? `${r.derived ? '派生汇总' : '来源'} ${r.cell} · 原值 ${r.raw}${r.conversion ? ` · ${r.conversion}` : ''}` : '缺失') }
function selectShop(shop) { selectedShop.value = shop; selectSource(shopSources.value[0]?.id || '') }
function selectSource(id) { sourceId.value = id; selectedShop.value = state.value.sources.find(s => s.id === id)?.shop || ''; runId.value = latest(id)?.id || ''; resultTab.value = 'targets'; if (grain.value === 'forecast' && !['tmall','douyin','douyin_baby'].includes(latest(id)?.rule_key) && !['douyin','douyin_baby'].includes(latest(id)?.source?.parser)) grain.value = 'day' }
function load(forceActuals = false) {
  if (forceActuals === true) { actualCache.clear(); forecastInput.value = null }
  if (loadPromise) return loadPromise
  loadPromise = loadOverview().finally(() => { loadPromise = null })
  return loadPromise
}
async function loadOverview() {
  pageLoading.value = true
  pageLoadError.value = ''
  const previousRunId = runId.value
  const followingLatest = sourceRuns.value[0]?.id === runId.value
  try {
    let result
    for (let attempt = 0; attempt < 3; attempt++) {
      loadProgress.value = attempt ? `连接暂未就绪，正在重试（${attempt}/2）…` : '正在加载目标和历史解析…'
      try { result = await gmvApi.overview({ timeout: 15000, suppressErrorToast: true }); break }
      catch (error) {
        const status = error.response?.status
        if (attempt === 2 || (status && status < 500)) throw error
        await new Promise(resolve => setTimeout(resolve, 750 * (attempt + 1)))
      }
    }
    setOverview(result)
    if (selectedSource.value) selectedShop.value = selectedSource.value.shop
    if (followingLatest) runId.value = latest(sourceId.value)?.id || runId.value
    if (!sourceId.value || !state.value.sources.some(s => s.id === sourceId.value)) selectSource(state.value.sources[0]?.id || '')
    if (!runId.value && sourceId.value) runId.value = latest(sourceId.value)?.id || ''
    if (!taskId.value) taskId.value = state.value.tasks[0]?.id || ''
  } catch (error) {
    pageLoadError.value = error.response?.data?.detail || '暂时无法连接数据接口，已自动重试；请点击重新加载。'
    return false
  } finally { pageLoading.value = false }
  try { await refreshTask() } catch {}
  if (previousRunId === runId.value) await loadSelectedRun()
  return true
}
async function openManualSource(source) { const latestRun = latest(source.id); const detail = latestRun ? await ensureRunDetail(latestRun.id) : null; tmallBabyEditor.value.open(detail, source.id) }
async function manualSaved(imported) { detailCache.set(imported.id, markRaw(imported)); await load(); sourceId.value = imported.source_id; runId.value = imported.id; resultTab.value = 'targets'; grain.value = 'day' }
async function importCounter(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importingCounter.value = true
  try {
    const data = new FormData(); data.append('file', file)
    const imported = await gmvApi.importCounter(data)
    detailCache.set(imported.id, markRaw(imported))
    await load(); sourceId.value = imported.source_id; runId.value = imported.id
    resultTab.value = 'targets'; grain.value = 'day'
    ElMessage.success('专柜预估单量已导入，历史版本已保留')
  } finally { importingCounter.value = false; event.target.value = '' }
}
async function parse(s) { parsing.value = s.id; try { const r = await gmvApi.parse(s.id); detailCache.set(r.id, markRaw(r)); await load(); sourceId.value = s.id; runId.value = r.id; ElMessage[r.status === 'success' ? 'success' : 'warning'](statusText(r.status)) } finally { parsing.value = '' } }
async function repair() { const t = await gmvApi.repair(runId.value, { provider: provider.value, reviewer: reviewer.value }); taskId.value = t.id; task.value = t; await load() }
async function refreshTask() { if (!taskId.value) return; const t = await gmvApi.task(taskId.value); const previous = task.value?.status; task.value = t; if (previous === 'running' && t.status !== 'running') { setOverview(await gmvApi.overview()); if (t.result_run_id) runId.value = t.result_run_id } }
function openModels() { for (const p of ['deepseek','minimax']) modelForm.value[p] = { model: state.value.models[p]?.model || '', api_key: '', region: state.value.models[p]?.region || 'international' }; modelVisible.value = true }
async function testModel(p) { testingModel.value = p; try { state.value.models = await gmvApi.models(modelForm.value); for (const provider of ['deepseek','minimax']) modelForm.value[provider].api_key = ''; await gmvApi.testModel(p); ElMessage.success('多模态输入与 JSON 输出验证通过') } finally { testingModel.value = '' } }
async function saveModels() { await gmvApi.models(modelForm.value); for (const p of ['deepseek','minimax']) modelForm.value[p].api_key = ''; modelVisible.value = false; await load(); ElMessage.success('模型配置已保存') }
function editSource(s) { editingId.value = s?.id || ''; sourceForm.value = s ? { ...s, parser: s.parser || 'tmall', amount_mode: s.amount_mode || 'source_conversion', history_enabled: s.history_enabled !== false, months: s.months ? [...s.months] : inferSourceMonths(s.name) } : { shop:'', name:'', url:'', year:2026, rule_key:'', parser:'tmall', amount_mode:'source_conversion', history_enabled:true, months:[], range:'A1:CZ140' }; if (!s && onlineShopCategories.value.includes(selectedShop.value)) { sourceForm.value.shop = selectedShop.value; sourceCategoryChanged(selectedShop.value) }; sourceVisible.value = true }
function inferFormMonths() { const inferred = inferSourceMonths(sourceForm.value.name); if (inferred.length) sourceForm.value.months = inferred }
function sourceCategoryChanged(shop) {
  if (editingId.value) return
  const template = state.value.sources.find(s => s.shop === shop && !s.source_type)
  if (template) for (const key of ['parser', 'rule_key', 'range', 'amount_mode']) sourceForm.value[key] = template[key] || (key === 'amount_mode' ? 'source_conversion' : sourceForm.value[key])
}
async function saveSource() {
  sourceForm.value.shop = sourceForm.value.shop?.trim() || ''
  if (!sourceForm.value.shop) { ElMessage.warning('请指定店铺分类'); return }
  if (!sourceForm.value.name?.trim()) { ElMessage.warning('请填写月份 / 规划名称'); return }
  if (!sourceForm.value.months?.length) { ElMessage.warning('请选择数字月份'); return }
  const saved = editingId.value ? await gmvApi.updateSource(editingId.value, sourceForm.value) : await gmvApi.createSource(sourceForm.value)
  sourceVisible.value = false
  await load()
  selectSource(saved.id)
}
async function showSnapshot() { const selectedRun = run.value; const data = await gmvApi.snapshot(selectedRun.id); snapshotRun.value = selectedRun; snapshotData.value = data; snapshotVisible.value = true }
let timer, pending = false
onMounted(async () => { await load(); timer = setInterval(async () => { if (pending) return; pending = true; try { if (taskId.value && task.value?.status === 'running') await refreshTask() } catch {} finally { pending = false } }, 2000) })
onUnmounted(() => clearInterval(timer))
// 切换历史任务立即读取。
import { watch } from 'vue'
watch(taskId, () => refreshTask())
watch(sourceId, id => { selectedShop.value = state.value.sources.find(s => s.id === id)?.shop || '' })
watch(runId, () => { ++forecastRequest; forecastInput.value = null; forecastLoading.value = false; forecastError.value = ''; loadSelectedRun() })
watch(grain, () => { if (run.value?.result) { if (forecastMode.value) loadForecastInputs(); else loadActuals() } })
</script>

<style scoped>
.gmv-trend-legend{font-size:12px;color:#667085}.gmv-trend-legend .rise{color:#b42318;font-weight:600}.gmv-trend-legend .fall{color:#067647;font-weight:600}
.gmv-page{padding:28px;max-width:1700px;margin:auto;color:#172033}.gmv-header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:26px}.eyebrow{font-size:12px;color:#496caa;font-weight:600}h1{font-size:29px;margin:8px 0}h2{font-size:18px;margin:0 0 16px}p,.muted{color:#667085;font-size:13px;line-height:1.7}.actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.spread{justify-content:space-between}.source-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:16px 0 20px;margin-bottom:8px;border-bottom:1px solid #e4e7ec}.source-picker{display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-width:0}.source-picker label{font-size:14px;font-weight:600;white-space:nowrap}.shop-select{width:210px;max-width:100%}.source-select{width:330px;max-width:100%}.source-actions a{font-size:13px;color:#3468bd}.source-filename{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.panel{border:1px solid #e4e7ec;background:white;border-radius:12px;padding:22px;margin-bottom:20px}.meta{display:flex;align-items:center;gap:12px;color:#667085;font-size:12px;margin-bottom:16px;flex-wrap:wrap}.repair-banner{background:#f0f5ff;padding:18px;border-radius:8px;margin-top:18px}.repair-banner small{display:block;margin-top:12px;color:#b54708}.preserve{white-space:pre-wrap;line-height:1.7;padding:8px 0}.timeline{max-height:540px;overflow:auto;margin-top:20px}.event{border-left:2px solid #9cb9e7;padding:0 18px 18px;margin-left:6px}.event b{display:block;font-size:14px;margin-top:6px}.event pre,.code-view{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;padding:14px;border-radius:6px;max-height:600px;overflow:auto}.el-alert{margin:12px 0}code{margin-right:8px}@media(max-width:900px){.gmv-header{align-items:flex-start;flex-direction:column}.gmv-trend-legend{font-size:12px;color:#667085}.gmv-trend-legend .rise{color:#b42318;font-weight:600}.gmv-trend-legend .fall{color:#067647;font-weight:600}
.gmv-page{padding:16px}.source-picker{width:100%}.shop-select,.source-select{flex:1;min-width:180px;width:auto}.source-actions{width:100%}.gmv-header>.actions{gap:8px}}
</style>

<style scoped>
.forecast-totals{padding:12px 16px;background:#eff6ff;color:#1e3a5f;border-radius:6px;font-size:14px;line-height:1.8}
</style>
