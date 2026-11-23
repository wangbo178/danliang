<template>
  <div>
    <div class="page-heading">
      <div>
        <h1 class="page-title"><el-icon class="page-title-icon"><VideoCamera /></el-icon>达人直播模型</h1>
        <p class="page-subtitle">独立测算直播场次单量，并自动合并进渠道与仓库最终预测。</p>
      </div>
      <div class="heading-actions">
        <el-button @click="load" :loading="loading"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button type="primary" @click="$router.push('/training')"><el-icon><VideoPlay /></el-icon>开始训练</el-button>
      </div>
    </div>

    <div class="model-status-card">
      <div class="status-main">
        <span class="pulse" :class="modelReady ? 'ready' : 'pending'" />
        <div>
          <div class="status-title">{{ modelReady ? '直播模型已产出并参与最终预测' : '直播模型已接入，等待新训练任务产出' }}</div>
          <div class="status-note">{{ statusNote }}</div>
        </div>
      </div>
      <el-tag :type="modelReady ? 'success' : 'warning'" effect="dark">{{ modelReady ? '运行中' : '待训练' }}</el-tag>
    </div>

    <el-row :gutter="18" class="metrics-row">
      <el-col :xs="12" :md="6">
        <div class="metric-box purple"><span>直播实绩数据</span><strong>{{ fmt(overview.anchor_live_orders) }}</strong><small>条源库记录，训练前去重</small></div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="metric-box blue"><span>直播预测加单</span><strong>{{ fmt(summary.total_anchor_predicted_orders) }}</strong><small>当前预测周期</small></div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="metric-box green"><span>直播模型 WAPE</span><strong>{{ metric(summary.anchor_wape) }}</strong><small>越低越好</small></div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="metric-box orange"><span>直播模型 Bias</span><strong>{{ metric(summary.anchor_bias) }}</strong><small>越接近 0 越好</small></div>
      </el-col>
    </el-row>

    <el-row :gutter="20">
      <el-col :xs="24" :lg="15">
        <div class="card pipeline-card">
          <div class="card-header"><span class="card-title">预测融合流程</span><el-tag type="info">独立训练 · 自动加总</el-tag></div>
          <div class="pipeline">
            <div class="pipeline-node base">
              <span class="node-index">01</span><el-icon><DataAnalysis /></el-icon>
              <b>基础渠道模型</b><small>测算不含达人直播的自然单量</small>
            </div>
            <div class="operator">＋</div>
            <div class="pipeline-node live">
              <span class="node-index">02</span><el-icon><VideoCamera /></el-icon>
              <b>达人直播模型</b><small>按达人、店铺、近期表现和排期目标测算</small>
            </div>
            <div class="operator">＝</div>
            <div class="pipeline-node final">
              <span class="node-index">03</span><el-icon><TrendCharts /></el-icon>
              <b>最终预测单量</b><small>进入预测表、分仓计算和 Excel 导出</small>
            </div>
          </div>
          <div class="formula-strip">
            <code>predicted_orders</code>
            <span>=</span><code>base_predicted_orders</code>
            <span>+</span><code class="live-code">anchor_predicted_orders</code>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-title">效果基准</span></div>
          <div class="comparison">
            <div><span>飞书排期目标 WAPE</span><strong>{{ metric(summary.anchor_target_wape) }}</strong><small>原始业务目标误差</small></div>
            <el-icon><Right /></el-icon>
            <div class="highlight"><span>直播模型 WAPE</span><strong>{{ metric(summary.anchor_wape) }}</strong><small>{{ improvementText }}</small></div>
          </div>
        </div>
      </el-col>

      <el-col :xs="24" :lg="9">
        <div class="card data-card">
          <div class="card-header"><span class="card-title">训练输入检查</span></div>
          <div class="check-row"><span><el-icon><DocumentChecked /></el-icon>抖音主播历史（不关联店铺）</span><el-tag :type="overview.anchor_live_orders ? 'success' : 'danger'">{{ overview.anchor_live_orders ? '只读库直连' : '缺少源库数据' }}</el-tag></div>
          <div class="check-row"><span><el-icon><Calendar /></el-icon>未来直播排期</span><el-tag :type="scheduleBatchCount ? 'success' : 'warning'">{{ scheduleBatchCount ? `${scheduleBatchCount} 个批次` : '请上传' }}</el-tag></div>
          <div class="check-row"><span><el-icon><Connection /></el-icon>渠道订单历史</span><el-tag :type="overview.channel_orders ? 'success' : 'warning'">{{ overview.channel_orders ? '只读库直连' : '暂无源库数据' }}</el-tag></div>
          <el-divider />
          <ol class="steps">
            <li>在“数据管理”查询源库最新主播历史，不关联来源店铺。</li>
            <li>通过飞书 App ID 自动同步未来直播排期和目标单量。</li>
            <li>执行 Phase2，直播模型会随渠道模型一起训练。</li>
            <li>到“预测结果”查看基础、直播加单和最终值。</li>
          </ol>
          <el-button class="full-btn" @click="$router.push('/data')">管理直播数据</el-button>
          <el-button class="full-btn" type="primary" plain @click="$router.push('/results')">查看融合结果</el-button>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { VideoCamera, Refresh, VideoPlay, DataAnalysis, TrendCharts, Right, DocumentChecked, Calendar, Connection } from '@element-plus/icons-vue'
import { dataApi, resultsApi } from '../api/index.js'

const loading = ref(false)
const overview = reactive({ anchor_live_orders: 0 })
const batches = reactive({ channel: [], schedule: [] })
const summary = reactive({})

const scheduleBatchCount = computed(() => batches.schedule?.length || 0)
const modelReady = computed(() => summary.total_anchor_predicted_orders != null || summary.anchor_wape != null)
const statusNote = computed(() => modelReady.value
  ? `最近完成：${summary.finished_at ? new Date(summary.finished_at).toLocaleString('zh-CN', { hour12: false }) : '已生成最新结果'}`
  : '完成一次包含 Phase2 的新训练后，这里会显示直播加单量和回测指标。')
const improvementText = computed(() => {
  const before = Number(summary.anchor_target_wape)
  const after = Number(summary.anchor_wape)
  return Number.isFinite(before) && Number.isFinite(after) ? `较排期目标改善 ${Math.max(0, before - after).toFixed(1)} 个百分点` : '完成训练后显示改善幅度'
})
const fmt = (value) => Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
const metric = (value) => value == null || Number.isNaN(Number(value)) ? '—' : `${Number(value).toFixed(1)}%`

async function load() {
  loading.value = true
  const [overviewResult, batchResult, summaryResult] = await Promise.allSettled([
    dataApi.getOverview(), dataApi.getBatches(), resultsApi.getSummary(),
  ])
  if (overviewResult.status === 'fulfilled') Object.assign(overview, overviewResult.value || {})
  if (batchResult.status === 'fulfilled') Object.assign(batches, batchResult.value || {})
  if (summaryResult.status === 'fulfilled') Object.assign(summary, summaryResult.value || {})
  loading.value = false
}

onMounted(load)
</script>

<style scoped>
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.heading-actions { display: flex; gap: 10px; padding-top: 4px; }
.model-status-card { margin-bottom: 20px; padding: 18px 22px; display: flex; align-items: center; justify-content: space-between; border-radius: 14px; color: #fff; background: linear-gradient(115deg, #29165f, #49339a 55%, #2563eb); box-shadow: 0 12px 30px rgba(64, 46, 145, .22); }
.status-main { display: flex; align-items: center; gap: 14px; }
.pulse { width: 12px; height: 12px; border-radius: 50%; background: #fbbf24; box-shadow: 0 0 0 7px rgba(251,191,36,.16); }
.pulse.ready { background: #34d399; box-shadow: 0 0 0 7px rgba(52,211,153,.16); }
.status-title { font-size: 17px; font-weight: 700; }
.status-note { margin-top: 5px; color: rgba(255,255,255,.68); font-size: 12px; }
.metrics-row { margin-bottom: 2px; }
.metric-box { min-height: 122px; margin-bottom: 18px; padding: 18px; border: 1px solid #e6eaf0; border-radius: 13px; background: #fff; box-shadow: 0 4px 14px rgba(16,24,40,.04); }
.metric-box span,.metric-box small { display: block; color: #7c8799; font-size: 12px; }
.metric-box strong { display: block; margin: 11px 0 7px; color: #182230; font-size: 28px; line-height: 1; }
.metric-box.purple { border-top: 3px solid #7c3aed; }.metric-box.blue { border-top: 3px solid #2563eb; }.metric-box.green { border-top: 3px solid #12b76a; }.metric-box.orange { border-top: 3px solid #f79009; }
.pipeline-card { overflow: hidden; }
.pipeline { padding: 15px 0 8px; display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; align-items: stretch; gap: 10px; }
.pipeline-node { position: relative; min-height: 150px; padding: 22px 16px 16px; display: flex; flex-direction: column; align-items: flex-start; border: 1px solid #e4e7ec; border-radius: 13px; background: #f9fafb; }
.pipeline-node > .el-icon { margin-bottom: 18px; padding: 9px; box-sizing: content-box; border-radius: 10px; color: #2563eb; background: #eaf2ff; font-size: 24px; }
.pipeline-node.live { border-color: #c4b5fd; background: #faf8ff; }.pipeline-node.live > .el-icon { color: #7c3aed; background: #ede9fe; }
.pipeline-node.final { border-color: #a7f3d0; background: #f3fdf8; }.pipeline-node.final > .el-icon { color: #079455; background: #dff8ea; }
.pipeline-node b { color: #253046; font-size: 14px; }.pipeline-node small { margin-top: 8px; color: #7d899b; line-height: 1.55; }
.node-index { position: absolute; top: 12px; right: 12px; color: #c4cad4; font-size: 11px; font-weight: 700; }
.operator { display: grid; place-items: center; color: #7c3aed; font-size: 26px; font-weight: 300; }
.formula-strip { margin-top: 15px; padding: 13px 16px; display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 9px; border-radius: 10px; color: #667085; background: #f2f4f7; }
.formula-strip code { color: #175cd3; font-weight: 600; }.formula-strip .live-code { color: #7c3aed; }
.comparison { display: grid; grid-template-columns: 1fr 40px 1fr; align-items: center; gap: 12px; }
.comparison > div { padding: 18px; border-radius: 12px; background: #f8fafc; }.comparison > .el-icon { justify-self: center; color: #98a2b3; }
.comparison span,.comparison small { display: block; color: #7d899b; font-size: 12px; }.comparison strong { display: block; margin: 8px 0; color: #475467; font-size: 28px; }
.comparison .highlight { background: #ecfdf3; }.comparison .highlight strong { color: #079455; }
.data-card { min-height: 100%; }
.check-row { padding: 13px 0; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #eef1f5; }
.check-row > span { display: flex; align-items: center; gap: 9px; color: #344054; font-size: 13px; }.check-row .el-icon { color: #667085; font-size: 17px; }
.steps { margin: 0 0 18px; padding-left: 22px; color: #667085; font-size: 13px; line-height: 1.75; }.steps li { margin-bottom: 7px; }
.full-btn { width: 100%; margin: 8px 0 0 !important; }
@media (max-width: 900px) { .pipeline { grid-template-columns: 1fr; }.operator { height: 24px; }.page-heading { flex-direction: column; }.model-status-card { align-items: flex-start; gap: 12px; }.comparison { grid-template-columns: 1fr; }.comparison > .el-icon { transform: rotate(90deg); } }
</style>
