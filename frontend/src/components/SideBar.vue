<template>
  <div class="sidebar" :class="{ embedded }">
    <!-- Logo -->
    <div class="sidebar-logo">
      <div class="logo-icon"><el-icon><Van /></el-icon></div>
      <div class="logo-text">
        <div class="logo-main">BTN物流单量预测系统</div>
        <div class="logo-sub">LOGISTICS FORECASTING</div>
      </div>
    </div>

    <!-- 导航菜单 -->
    <nav class="sidebar-nav">
      <router-link
        v-for="item in menuItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
        @click="emit('navigate')"
      >
        <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
        <span class="nav-label">{{ item.label }}</span>
        <div v-if="isActive(item.path)" class="active-indicator" />
      </router-link>
    </nav>

    <!-- 底部信息 -->
    <div class="sidebar-footer">
      <div class="footer-status">
        <span class="status-dot" :class="apiOnline ? 'online' : 'offline'" />
        <span>{{ apiOnline ? 'API 已连接' : 'API 离线' }}</span>
      </div>
      <div class="footer-version">v2.0.0</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import { DataLine, FolderOpened, Setting, Cpu, TrendCharts, Van, VideoCamera, ChatDotRound } from '@element-plus/icons-vue'

defineProps({ embedded: { type: Boolean, default: false } })
const emit = defineEmits(['navigate'])

const route = useRoute()
const apiOnline = ref(false)

const menuItems = [
  { path: '/dashboard', label: '数据看板',  icon: DataLine },
  { path: '/expected-gmv', label: '预期GMV', icon: TrendCharts },
  { path: '/operations', label: '运营', icon: ChatDotRound },
  { path: '/data',      label: '数据管理',  icon: FolderOpened },
  { path: '/config',    label: '基础配置',  icon: Setting },
  { path: '/training',  label: '模型训练',  icon: Cpu },
  { path: '/live-model', label: '直播模型',  icon: VideoCamera },
  { path: '/results',   label: '预测结果',  icon: TrendCharts },
]

let healthTimer

function isActive(path) {
  return route.path === path || (path !== '/' && route.path.startsWith(path))
}

async function checkApi() {
  try {
    await axios.get('/api/health', { timeout: 3000 })
    apiOnline.value = true
  } catch {
    apiOnline.value = false
  }
}

onMounted(() => {
  checkApi()
  healthTimer = window.setInterval(checkApi, 30000)
})
onUnmounted(() => window.clearInterval(healthTimer))
</script>

<style scoped>
.sidebar {
  width: 240px;
  min-height: 100vh;
  background: linear-gradient(180deg, #101828 0%, #172033 100%);
  display: flex;
  flex-direction: column;
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  z-index: 100;
}

.sidebar.embedded { position: relative; width: 100%; min-height: 100%; height: 100%; }

.sidebar-logo {
  min-height: 80px;
  padding: 20px 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo-icon {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 10px;
  background: #2563eb;
  color: #fff;
  font-size: 22px;
}

.logo-main {
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  letter-spacing: .1px;
  white-space: nowrap;
}

.logo-sub {
  font-size: 9px;
  color: rgba(255, 255, 255, 0.45);
  margin-top: 4px;
  letter-spacing: .7px;
}

.sidebar-nav {
  flex: 1;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 10px;
  color: rgba(255, 255, 255, 0.6);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  overflow: hidden;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.07);
  color: rgba(255, 255, 255, 0.9);
}

.nav-item.active {
  background: #2563eb;
  color: #fff;
  font-weight: 600;
}

.nav-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.nav-label {
  flex: 1;
}

.active-indicator {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: #7dd3fc;
  border-radius: 2px 0 0 2px;
}

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.footer-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.status-dot.online {
  background: #12b76a;
  box-shadow: 0 0 6px #12b76a;
}

.status-dot.offline {
  background: #f5222d;
}

.footer-version {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.3);
}
</style>
