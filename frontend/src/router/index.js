import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/operations', redirect: '/operations/messages' },
  { path: '/operations/messages', name: 'OperationsMessages', component: () => import('../views/OperationsMessages.vue'), meta: { title: '运营 · 读取聊天消息', icon: 'ChatDotRound' } },
  { path: '/operations/agent', name: 'OperationsAgent', component: () => import('../views/OperationsAgent.vue'), meta: { title: '运营 Agent', icon: 'ChatDotRound' } },
  {
    path: '/expected-gmv',
    name: 'ExpectedGMV',
    component: () => import('../views/ExpectedGMV.vue'),
    meta: { title: '预期GMV', icon: 'TrendCharts' },
  },
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '数据看板', icon: 'DataLine' },
  },
  {
    path: '/data',
    name: 'DataManagement',
    component: () => import('../views/DataManagement.vue'),
    meta: { title: '数据管理', icon: 'FolderOpened' },
  },
  {
    path: '/config',
    name: 'Configuration',
    component: () => import('../views/Configuration.vue'),
    meta: { title: '基础配置', icon: 'Setting' },
  },
  {
    path: '/training',
    name: 'ModelTraining',
    component: () => import('../views/ModelTraining.vue'),
    meta: { title: '模型训练', icon: 'Cpu' },
  },
  {
    path: '/live-model',
    name: 'LiveModel',
    component: () => import('../views/LiveModelView.vue'),
    meta: { title: '达人直播模型', icon: 'VideoCamera' },
  },
  {
    path: '/results',
    name: 'Results',
    component: () => import('../views/Results.vue'),
    meta: { title: '预测结果', icon: 'TrendCharts' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '运营概览'} | BTN物流单量预测系统`
})

export default router
