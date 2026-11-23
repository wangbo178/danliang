import { defineStore } from 'pinia'
import { ref } from 'vue'
import { trainingApi, createTrainingWS } from '../api/index.js'
import { ElMessage } from 'element-plus'

export const useTrainingStore = defineStore('training', () => {
  const jobs = ref([])
  const currentJob = ref(null)
  const progress = ref(0)
  const logs = ref([])
  const isRunning = ref(false)
  let ws = null
  let pollTimer = null

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer)
    pollTimer = null
  }

  async function syncJob(jobId) {
    const job = await trainingApi.getJob(jobId)
    currentJob.value = job
    progress.value = job.progress || 0
    if (job.status === 'done' || job.status === 'error') {
      isRunning.value = false
      stopPolling()
      await loadJobLogs(jobId)
      await fetchJobs()
      if (job.status === 'done') ElMessage.success('训练完成！')
      else ElMessage.error(`训练失败：${job.error_msg || '请查看任务日志'}`)
    }
  }

  function startPolling(jobId) {
    if (pollTimer) return
    syncJob(jobId).catch(() => {})
    pollTimer = window.setInterval(() => syncJob(jobId).catch(() => {}), 3000)
  }

  async function fetchJobs() {
    jobs.value = await trainingApi.listJobs()
  }

  async function startTraining(params) {
    isRunning.value = true
    progress.value = 0
    logs.value = []

    const res = await trainingApi.startTraining(params)
    currentJob.value = { job_id: res.job_id, status: 'running' }

    // 连接 WebSocket
    ws = createTrainingWS(res.job_id, {
      onLog: (text) => {
        logs.value.push(text)
      },
      onProgress: (pct) => {
        progress.value = pct
      },
      onDone: () => {
        stopPolling()
        isRunning.value = false
        progress.value = 100
        currentJob.value = { ...currentJob.value, status: 'done' }
        ElMessage.success('训练完成！')
        fetchJobs()
        ws?.close()
      },
      onError: (err) => {
        stopPolling()
        isRunning.value = false
        currentJob.value = { ...currentJob.value, status: 'error' }
        ElMessage.error(`训练失败：${err}`)
        ws?.close()
      },
      onTransportError: (message) => {
        logs.value.push(`[连接] ${message}`)
        startPolling(res.job_id)
      },
      onClose: () => {
        if (isRunning.value) {
          startPolling(res.job_id)
        }
      },
    })

    return res
  }

  function stopWS() {
    ws?.close()
    ws = null
    stopPolling()
  }

  async function loadJobLogs(jobId) {
    const logItems = await trainingApi.getJobLogs(jobId)
    logs.value = logItems.map(l => l.log_text)
  }

  return {
    jobs, currentJob, progress, logs, isRunning,
    fetchJobs, startTraining, stopWS, loadJobLogs,
  }
})
