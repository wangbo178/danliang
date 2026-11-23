<template>
  <div>
    <h1 class="page-title"><el-icon class="page-title-icon"><Cpu /></el-icon>模型训练</h1>
    <p class="page-subtitle">配置预测周期并启动完整流水线；关闭页面后任务仍会继续，重新进入可查看任务历史。</p>

    <div class="live-training-banner">
      <div class="live-banner-icon"><el-icon><VideoCamera /></el-icon></div>
      <div class="live-banner-copy">
        <div class="live-banner-title">
          达人直播子模型已接入训练流水线
          <el-tag type="success" effect="dark" size="small">自动融合</el-tag>
        </div>
        <p>勾选 Phase2 渠道模型后，会同步训练达人直播模型；未来排期的直播单量将自动加到基础预测中。</p>
      </div>
      <div class="live-formula">
        <span>非达人基础预测</span><b>＋</b><span>达人直播加单</span><b>＝</b><strong>最终预测单量</strong>
      </div>
      <el-button type="primary" plain @click="$router.push('/live-model')">查看直播模型</el-button>
    </div>

    <el-row :gutter="20">
      <!-- 左侧：训练参数 -->
      <el-col :xs="24" :lg="10">
        <div class="card">
          <div class="card-header">
            <span class="card-title">训练参数</span>
          </div>

          <el-form :model="trainParams" label-width="100px" size="default">
            <el-form-item label="训练阶段">
              <el-checkbox-group v-model="trainParams.phases">
                <el-checkbox label="global">Phase1 全局模型</el-checkbox>
                <el-checkbox label="channel">Phase2 渠道模型 + 直播模型</el-checkbox>
                <el-checkbox label="routing">Phase3 分仓路由</el-checkbox>
              </el-checkbox-group>
            </el-form-item>



            <el-alert type="info" :closable="false" show-icon style="margin-bottom:20px"
              title="训练直接读取源库最新历史。切换数据源后请先完整训练三个阶段；仓库按库存地点归并，主播历史独立匹配。" />
            <el-form-item label="预测天数">
              <el-input-number v-model="trainParams.forecast_days" :min="7" :max="90" style="width:160px" />
              <span class="field-help">生成未来库存与渠道计划</span>
            </el-form-item>

            <el-form-item label="评估天数">
              <el-input-number v-model="trainParams.holdout_days" :min="7" :max="90" style="width:160px" />
              <span class="field-help">用于计算 WAPE，不参与最终训练</span>
            </el-form-item>

            <el-divider content-position="left">
              <span style="font-size:13px;color:#999">高级参数（可选）</span>
            </el-divider>

            <el-form-item label="LGB 叶数">
              <el-input-number
                v-model="advancedParams.num_leaves"
                :min="16" :max="256"
                placeholder="默认 63"
                style="width:160px"
              />
            </el-form-item>

            <el-form-item label="学习率">
              <el-input-number
                v-model="advancedParams.learning_rate"
                :min="0.001" :max="0.5" :step="0.01" :precision="3"
                placeholder="默认 0.05"
                style="width:160px"
              />
            </el-form-item>

            <el-form-item label="迭代轮数">
              <el-input-number
                v-model="advancedParams.n_estimators"
                :min="50" :max="2000"
                placeholder="默认 500"
                style="width:160px"
              />
            </el-form-item>
          </el-form>

          <div style="margin-top:8px">
            <el-button
              class="btn-primary"
              type="primary"
              size="large"
              :loading="trainingStore.isRunning"
              :disabled="trainingStore.isRunning"
              style="width:100%"
              @click="startTraining"
            >
              <el-icon v-if="!trainingStore.isRunning"><VideoPlay /></el-icon>
              {{ trainingStore.isRunning ? '训练中...' : '开始训练' }}
            </el-button>
          </div>
        </div>

        <!-- 任务历史 -->
        <div class="card" style="margin-top:0">
          <div class="card-header">
            <span class="card-title">任务历史</span>
            <el-button size="small" text @click="trainingStore.fetchJobs()">刷新</el-button>
          </div>
          <el-table :data="trainingStore.jobs" size="small" stripe border max-height="260">
            <el-table-column prop="job_id" label="任务 ID" width="100">
              <template #default="{ row }">{{ row.job_id.slice(0, 8) }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.status === 'done' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'running' ? 'warning' : 'info'"
                >{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="progress" label="进度" width="70">
              <template #default="{ row }">{{ row.progress }}%</template>
            </el-table-column>
            <el-table-column label="完成时间">
              <template #default="{ row }">
                <span style="font-size:12px">{{ formatTime(row.finished_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>

      <!-- 右侧：训练进度 + 日志 -->
      <el-col :xs="24" :lg="14">
        <div class="card">
          <div class="card-header">
            <span class="card-title">训练进度</span>
            <el-tag
              :type="trainingStore.isRunning ? 'warning' : 'info'"
              size="small"
            >{{ trainingStore.isRunning ? '训练中' : '待机' }}</el-tag>
          </div>

          <el-progress
            :percentage="trainingStore.progress"
            :status="progressStatus"
            :stroke-width="14"
            style="margin-bottom:20px"
            :color="progressColors"
          />

          <div v-if="trainingStore.currentJob" style="margin-bottom:12px;font-size:13px;color:#666">
            当前任务：{{ trainingStore.currentJob.job_id?.slice(0, 8) }}
            <el-tag size="small" :type="statusTagType">{{ trainingStore.currentJob.status }}</el-tag>
          </div>

          <TrainingConsole
            :logs="trainingStore.logs"
            :running="trainingStore.isRunning"
            @clear="trainingStore.logs = []"
          />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useTrainingStore } from '../stores/training.js'
import TrainingConsole from '../components/TrainingConsole.vue'
import { ElMessage } from 'element-plus'

const trainingStore = useTrainingStore()

const trainParams = reactive({
  phases: ['global', 'channel', 'routing'],
  forecast_days: 30,
  holdout_days: 30,
})


const advancedParams = reactive({
  num_leaves: undefined,
  learning_rate: undefined,
  n_estimators: undefined,
})

const progressColors = [
  { color: '#2563eb', percentage: 40 },
  { color: '#0ea5e9', percentage: 70 },
  { color: '#12b76a', percentage: 100 },
]

const progressStatus = computed(() => {
  const job = trainingStore.currentJob
  if (!job) return ''
  if (job.status === 'done') return 'success'
  if (job.status === 'error') return 'exception'
  return ''
})

const statusTagType = computed(() => {
  const s = trainingStore.currentJob?.status
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  if (s === 'running') return 'warning'
  return 'info'
})

function formatTime(t) {
  if (!t) return '—'
  return new Date(t).toLocaleString('zh-CN', { hour12: false }).slice(0, 16)
}

async function startTraining() {
  if (!trainParams.phases.length) {
    ElMessage.warning('请至少选择一个训练阶段')
    return
  }
  const params = { ...trainParams }

  // 注入高级参数（只有用户填写了才包含）
  const lgbParams = {}
  if (advancedParams.num_leaves) lgbParams.num_leaves = advancedParams.num_leaves
  if (advancedParams.learning_rate) lgbParams.learning_rate = advancedParams.learning_rate
  if (advancedParams.n_estimators) lgbParams.n_estimators = advancedParams.n_estimators
  if (Object.keys(lgbParams).length) {
    params.channel_model_params = lgbParams
  }

  try {
    await trainingStore.startTraining(params)
  } catch (e) {
    ElMessage.error('启动训练失败')
  }
}

onMounted(() => {
  trainingStore.fetchJobs()
})
</script>

<style scoped>
.field-help { display: block; width: 100%; margin-top: 4px; color: #8a94a6; font-size: 12px; line-height: 1.4; }
.live-training-banner { margin-bottom: 20px; padding: 18px 20px; display: flex; align-items: center; gap: 16px; border: 1px solid #c4b5fd; border-radius: 14px; background: linear-gradient(120deg, #f5f3ff 0%, #eff6ff 100%); box-shadow: 0 8px 24px rgba(124, 58, 237, .08); }
.live-banner-icon { width: 46px; height: 46px; flex: 0 0 46px; display: grid; place-items: center; border-radius: 13px; color: #fff; background: linear-gradient(135deg, #7c3aed, #2563eb); font-size: 24px; }
.live-banner-copy { min-width: 260px; flex: 1; }
.live-banner-title { display: flex; align-items: center; gap: 10px; color: #35226b; font-size: 16px; font-weight: 700; }
.live-banner-copy p { margin: 7px 0 0; color: #667085; font-size: 13px; line-height: 1.55; }
.live-formula { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 10px; background: rgba(255,255,255,.8); color: #667085; font-size: 12px; white-space: nowrap; }
.live-formula b { color: #7c3aed; font-size: 16px; }
.live-formula strong { color: #2563eb; }
@media (max-width: 991px) { .card { margin-bottom: 18px !important; } }
@media (max-width: 1100px) { .live-training-banner { align-items: flex-start; flex-wrap: wrap; } .live-formula { order: 4; width: 100%; justify-content: center; } }
</style>
