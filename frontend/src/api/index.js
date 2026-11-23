import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

api.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    if (!err.config?.suppressErrorToast) ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    return Promise.reject(err)
  }
)

// ── 配置 ──────────────────────────────────────────────────────────────────
export const operationsApi = {
  agentState: (params = {}) => api.get('/operations/agent/state', { params, suppressErrorToast: true }),
  agentWindow: id => api.get(`/operations/agent/windows/${id}`, { suppressErrorToast: true }),
  agentNode: id => api.get(`/operations/agent/queue/${id}`, { suppressErrorToast: true }),
  agentRetry: id => api.post(`/operations/agent/queue/${id}/retry`, {}, { suppressErrorToast: true }),
  saveAgentTask: (data, id) => id ? api.put(`/operations/agent/tasks/${id}`, data, { suppressErrorToast: true }) : api.post('/operations/agent/tasks', data, { suppressErrorToast: true }),
  agentAction: (id, action) => api.post(`/operations/agent/tasks/${id}/${action}`, {}, { suppressErrorToast: true }),
  agentPreview: data => api.post('/operations/agent/preview', data, { timeout: 200000, suppressErrorToast: true }),
  agentFollowup: (id, question_text) => api.post(`/operations/agent/opportunities/${id}/followup`, { confirmed: true, question_text }, { timeout: 65000, suppressErrorToast: true }),
  agentReview: (id, data) => api.patch(`/operations/agent/opportunities/${id}`, data, { suppressErrorToast: true }),
  send: data => api.post('/operations/messages/send', data, { timeout: 65000, suppressErrorToast: true }),
  image: (id, key) => api.get(`/operations/messages/${encodeURIComponent(id)}/image`, { params: { file_key: key }, responseType: 'blob', timeout: 65000, suppressErrorToast: true }),
  status: () => api.get('/operations/messages/status', { suppressErrorToast: true }),
  targets: params => api.get('/operations/messages/targets', { params, suppressErrorToast: true }),
  read: data => api.post('/operations/messages/read', data, { timeout: 65000, suppressErrorToast: true }),
}

export const gmvApi = {
  orderPreview: (params = {}) => api.get('/expected-gmv/order-preview', {params}),
  orderWarehousePreview: (params = {}) => api.get('/expected-gmv/order-preview/warehouse', {params}),
  historyReferenceTwo: (id, dates, scope) => api.get(`/expected-gmv/runs/${id}/history-reference-two`, {params: {dates: dates.join(','),scope}}),
  forecastSpecialShelf: (id, start_date, end_date, scope) => api.get(`/expected-gmv/runs/${id}/forecast-special-shelf`, {params: {start_date,end_date,scope}}),
  forecastBabyAov: (id, months) => api.get(`/expected-gmv/runs/${id}/forecast-baby-aov`, {params: {months: months.join(',')}}),
  forecastAlliance: (id, months) => api.get(`/expected-gmv/runs/${id}/forecast-alliance`, {params: {months: months.join(',')}}),
  historyReference: (id, targetDate) => api.get(`/expected-gmv/runs/${id}/history-reference`, {params: {target_date: targetDate}}),
  historyReferenceAverages: id => api.get(`/expected-gmv/runs/${id}/history-reference/averages`),
  forecastOrderFloor: (id, month) => api.get(`/expected-gmv/runs/${id}/forecast-order-floor`, {params: {month}}),
  forecastCpsAverages: (id, months) => api.get(`/expected-gmv/runs/${id}/forecast-cps-averages`, {params: {months: months.join(',')}}),
  forecastSamplingAverages: (id, months) => api.get(`/expected-gmv/runs/${id}/forecast-sampling-averages`, {params: {months: months.join(',')}}),
  forecastSuperAnchor: (id, mode) => api.get(`/expected-gmv/runs/${id}/forecast-super-anchor`, {params: {mode}}),
  forecastSecondAnchor: (id, mode) => api.get(`/expected-gmv/runs/${id}/forecast-second-anchor`, {params: {mode}}),
  forecastSampleFestival: (id, nearbyDays = 1) => api.get(`/expected-gmv/runs/${id}/forecast-sample-festival`, {params: {nearby_days: nearbyDays}}),
  forecastInputs: (id, version) => api.get(`/expected-gmv/runs/${id}/forecast-inputs`, {params: version ? {version} : {}}),
  saveForecastInputs: (id, data) => api.put(`/expected-gmv/runs/${id}/forecast-inputs`, data),
  saveOtherChannel: (data) => api.post('/expected-gmv/manual/other', data),
  saveAliHealthChannel: (data) => api.post('/expected-gmv/manual/ali-health', data),
  savePddChannel: (data) => api.post('/expected-gmv/manual/pdd', data),
  saveKaChannel: (data) => api.post('/expected-gmv/manual/ka-channel', data),
  savePrivateChannel: (data) => api.post('/expected-gmv/manual/private-channel', data),
  saveTmallBaby: (data) => api.post('/expected-gmv/manual/tmall-baby', data),
  importCounter: (data) => api.post('/expected-gmv/imports/counter', data),
  overview: (config = {}) => api.get('/expected-gmv', { ...config, params: { ...config.params, compact: true } }),
  createSource: (data) => api.post('/expected-gmv/sources', data),
  updateSource: (id, data) => api.put(`/expected-gmv/sources/${id}`, data),
  parse: (id) => api.post(`/expected-gmv/sources/${id}/parse`, {}, { timeout: 180000 }),
  repair: (id, data) => api.post(`/expected-gmv/runs/${id}/repair`, data),
  task: (id) => api.get(`/expected-gmv/tasks/${id}`),
  rollback: (id) => api.post(`/expected-gmv/rules/${id}/rollback`),
  models: (data) => api.put('/expected-gmv/models', data),
  testModel: (provider) => api.post(`/expected-gmv/models/${provider}/test`, {}, { timeout: 200000 }),
  runDetail: (id) => api.get(`/expected-gmv/runs/${id}`),
  snapshot: (id) => api.get(`/expected-gmv/runs/${id}/snapshot`),
  shelfActuals: (id) => api.get(`/expected-gmv/runs/${id}/shelf-actuals`, { timeout: 120000 }),
  actuals: (id) => api.get(`/expected-gmv/runs/${id}/actuals`, { timeout: 120000 }),
}

export const configApi = {
  bootstrap: () => api.get('/config/bootstrap'),
  listPlatforms: () => api.get('/config/platforms'),
  createPlatform: (data) => api.post('/config/platforms', data),
  updatePlatform: (id, data) => api.put(`/config/platforms/${id}`, data),
  deletePlatform: (id) => api.delete(`/config/platforms/${id}`),

  listShops: () => api.get('/config/shops'),
  createShop: (data) => api.post('/config/shops', data),
  updateShop: (id, data) => api.put(`/config/shops/${id}`, data),
  deleteShop: (id) => api.delete(`/config/shops/${id}`),

  listWarehouses: () => api.get('/config/warehouses'),
  createWarehouse: (data) => api.post('/config/warehouses', data),
  updateWarehouse: (id, data) => api.put(`/config/warehouses/${id}`, data),
  deleteWarehouse: (id) => api.delete(`/config/warehouses/${id}`),

  listCalendar: () => api.get('/config/calendar'),
  createCalendar: (data) => api.post('/config/calendar', data),
  updateCalendar: (id, data) => api.put(`/config/calendar/${id}`, data),
  deleteCalendar: (id) => api.delete(`/config/calendar/${id}`),

  exportConfig: () => api.get('/config/export'),
  getFeishuScheduleConfig: () => api.get('/config/feishu-schedule'),
  saveFeishuScheduleConfig: (data) => api.put('/config/feishu-schedule', data),
  testFeishuScheduleConfig: () => api.post('/config/feishu-schedule/test'),
  listFeishuScheduleBindings: () => api.get('/config/feishu-schedule/bindings'),
  createFeishuScheduleBinding: (data) => api.post('/config/feishu-schedule/bindings', data),
  updateFeishuScheduleBinding: (id, data) => api.put(`/config/feishu-schedule/bindings/${id}`, data),
  deleteFeishuScheduleBinding: (id) => api.delete(`/config/feishu-schedule/bindings/${id}`),
}

// ── 数据 ──────────────────────────────────────────────────────────────────
export const dataApi = {
  
  
  

  
  
  

  uploadSchedule: (formData) => api.post('/data/upload/schedule', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  saveSchedule: (records, options = {}) => api.post('/data/save/schedule', { records, ...options }),
  getFeishuScheduleStatus: () => api.get('/data/feishu-schedule/status'),
  syncFeishuSchedule: () => api.post('/data/feishu-schedule/sync'),

  uploadCommission: (formData) => api.post('/data/upload/commission', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  saveCommission: (records, options = {}) => api.post('/data/save/commission', { records, ...options }),

  uploadPresale: (formData) => api.post('/data/upload/presale', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  savePresale: (records, options = {}) => api.post('/data/save/presale', { records, ...options }),

  
  

  getOverview: () => api.get('/data/overview'),
  getBatches: (params) => api.get('/data/batches', { params }),
  getRecords: (params) => api.get('/data/records', { params }),
  getRecordOptions: (params) => api.get('/data/record-options', { params }),
  getWarehouseDaily: (params) => api.get('/data/warehouse-daily', { params }),
  getAovDaily: (params) => api.get('/data/aov-daily', { params }),
  deleteData: (type, params) => api.delete(`/data/${type}`, { params }),
}

// ── 训练 ──────────────────────────────────────────────────────────────────
export const trainingApi = {
  startTraining: (params) => api.post('/training/start', params),
  listJobs: () => api.get('/training/jobs'),
  getJob: (jobId) => api.get(`/training/jobs/${jobId}`),
  getJobLogs: (jobId) => api.get(`/training/jobs/${jobId}/logs`),
}

// ── 结果 ──────────────────────────────────────────────────────────────────
export const resultsApi = {
  getLatestPredictions: (params) => api.get('/results/predictions/latest', { params }),
  getPredictions: (params) => api.get('/results/predictions', { params }),
  getForecastSheet: (params) => api.get('/results/forecast-sheet', { params }),
  saveForecastSheet: (data) => api.patch('/results/forecast-sheet', data),
  getMetrics: (params) => api.get('/results/metrics', { params }),
  getSummary: () => api.get('/results/summary'),
  getBacktestDaily: (params) => api.get('/results/backtest-daily', { params }),
  getWarehouseStats: (params) => api.get('/results/warehouse-stats', { params }),
  getWarehouseDaily: (params) => api.get('/results/warehouse-daily', { params }),
  exportExcel: (jobId) => {
    const url = jobId ? `/results/export/excel?job_id=${jobId}` : '/results/export/excel'
    window.open(`/api${url}`, '_blank')
  },
}

// ── WebSocket 工厂 ────────────────────────────────────────────────────────
export function createTrainingWS(jobId, handlers = {}) {
  // 跟随当前域名和协议，兼容反向代理、HTTPS 与非本机部署。
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${protocol}//${window.location.host}/api/training/ws/${jobId}`)
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'log' && handlers.onLog) handlers.onLog(msg.data)
      if (msg.type === 'progress' && handlers.onProgress) handlers.onProgress(msg.data)
      if (msg.type === 'done' && handlers.onDone) handlers.onDone(msg.data)
      if (msg.type === 'error' && handlers.onError) handlers.onError(msg.data)
    } catch (error) {
      handlers.onProtocolError?.(error)
    }
  }
  ws.onerror = () => handlers.onTransportError?.('实时连接中断，已切换轮询')
  ws.onclose = () => handlers.onClose?.()
  return ws
}

export default api
