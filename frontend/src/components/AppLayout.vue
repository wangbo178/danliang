<template>
  <el-container class="app-layout">
    <SideBar class="desktop-sidebar" />
    <el-drawer v-model="mobileMenuOpen" direction="ltr" size="280px" :with-header="false" class="mobile-drawer">
      <SideBar embedded @navigate="mobileMenuOpen = false" />
    </el-drawer>
    <el-container class="main-container" direction="vertical">
      <header class="top-bar">
        <button class="mobile-menu-button" type="button" aria-label="打开导航" @click="mobileMenuOpen = true">
          <el-icon><Menu /></el-icon>
        </button>
        <div class="top-bar-copy">
          <span class="top-bar-eyebrow">BTN物流单量预测系统</span>
          <strong>{{ route.meta.title || '运营概览' }}</strong>
        </div>
        <div class="top-bar-actions">
          <span class="top-bar-date">{{ currentDate }}</span>
          <div class="environment-badge"><span />生产工作台</div>
        </div>
      </header>
      <el-main class="main-content">
        <div class="ambient-background" aria-hidden="true">
          <span class="ambient-orb ambient-orb-one" />
          <span class="ambient-orb ambient-orb-two" />
        </div>
        <div class="page-stage">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Menu } from '@element-plus/icons-vue'
import SideBar from './SideBar.vue'

const route = useRoute()
const mobileMenuOpen = ref(false)
const currentDate = computed(() => new Intl.DateTimeFormat('zh-CN', {
  month: 'long', day: 'numeric', weekday: 'short',
}).format(new Date()))
</script>

<style>
:root {
  --brand-600: #2563eb;
  --brand-700: #1d4ed8;
  --brand-soft: #eff6ff;
  --ink-900: #101828;
  --ink-600: #667085;
  --surface-soft: #f5f7fa;
  --line: #e4e7ec;
  --shadow-sm: 0 1px 2px rgba(16, 24, 40, 0.04), 0 4px 14px rgba(16, 24, 40, 0.05);
  --el-color-primary: #2563eb;
  --el-color-success: #12b76a;
  --el-color-warning: #f79009;
  --el-color-danger: #f04438;
  --el-border-radius-base: 8px;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', sans-serif;
  color: var(--ink-900);
  background:
    linear-gradient(135deg, rgba(239, 246, 255, .72) 0%, rgba(245, 247, 250, .96) 38%, rgba(240, 253, 250, .58) 100%);
  -webkit-font-smoothing: antialiased;
}

.app-layout {
  min-height: 100vh;
}

.main-container {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  margin-left: 240px;
}

.main-content {
  padding: 28px 32px 40px;
  overflow-y: auto;
  height: calc(100vh - 68px);
  background: var(--surface-soft);
  position: relative;
  isolation: isolate;
}

.page-stage { position: relative; z-index: 2; }

.ambient-background {
  position: fixed;
  inset: 68px 0 0 240px;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(37, 99, 235, .055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(37, 99, 235, .055) 1px, transparent 1px);
  background-size: 36px 36px;
  mask-image: linear-gradient(to bottom, #000 0%, rgba(0, 0, 0, .7) 48%, transparent 100%);
}

.ambient-orb {
  position: absolute;
  display: block;
  border-radius: 50%;
}

.ambient-orb-one {
  width: 620px;
  height: 620px;
  right: -180px;
  top: -210px;
  background: radial-gradient(circle, rgba(37, 99, 235, .22) 0%, rgba(37, 99, 235, .06) 38%, rgba(37, 99, 235, 0) 72%);
}

.ambient-orb-two {
  width: 540px;
  height: 540px;
  left: 8%;
  bottom: -250px;
  background: radial-gradient(circle, rgba(14, 165, 233, .17) 0%, rgba(20, 184, 166, .06) 42%, rgba(14, 165, 233, 0) 72%);
}

/* Keep decorative backgrounds static so idle pages do not continuously composite GPU layers. */

.top-bar { height: 68px; flex: 0 0 68px; padding: 0 32px; display: flex; align-items: center; gap: 14px; background: rgba(255,255,255,.94); border-bottom: 1px solid var(--line); backdrop-filter: blur(14px); z-index: 20; }
.top-bar-copy { display: flex; flex-direction: column; gap: 1px; }
.top-bar-copy strong { font-size: 16px; line-height: 1.25; }
.top-bar-eyebrow { color: #8a94a6; font-size: 11px; }
.top-bar-actions { margin-left: auto; display: flex; align-items: center; gap: 16px; }
.top-bar-date { color: var(--ink-600); font-size: 13px; }
.environment-badge { display: flex; align-items: center; gap: 7px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 999px; color: var(--ink-600); font-size: 12px; }
.environment-badge span { width: 7px; height: 7px; border-radius: 50%; background: #12b76a; box-shadow: 0 0 0 3px #d1fadf; }
.mobile-menu-button { display: none; width: 38px; height: 38px; border: 1px solid var(--line); border-radius: 10px; background: #fff; color: var(--ink-900); cursor: pointer; font-size: 18px; }
.mobile-drawer .el-drawer__body { padding: 0; }

/* 卡片样式 */
.card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
  padding: 24px;
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink-900);
}

/* 主按钮渐变 */
.btn-primary {
  background: var(--brand-600) !important;
  border: none !important;
  border-radius: 8px !important;
  transition: transform 0.2s, box-shadow 0.2s !important;
}

.btn-primary:hover {
  background: var(--brand-700) !important;
  transform: translateY(-1px) !important;
  box-shadow: 0 6px 18px rgba(37, 99, 235, 0.24) !important;
}

/* 页面标题 */
.page-title {
  font-size: 26px;
  font-weight: 700;
  color: var(--ink-900);
  margin-bottom: 22px;
  display: flex;
  align-items: center;
  gap: 10px;
  letter-spacing: -.3px;
}

.page-title-icon { width: 36px; height: 36px; border-radius: 9px; color: var(--brand-600); background: var(--brand-soft); font-size: 20px; }

.el-table { --el-table-header-bg-color: #f8fafc; --el-table-header-text-color: #475467; --el-table-border-color: #eaecf0; }
.el-tabs__item { color: #667085; font-weight: 500; }
.el-tabs__item.is-active { color: var(--brand-600); font-weight: 600; }

.page-subtitle { margin: -14px 0 24px; color: var(--ink-600); font-size: 13px; }

/* 过渡动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 1100px) { .main-content { padding: 24px; } }

@media (max-width: 768px) {
  .desktop-sidebar { display: none !important; }
  .main-container { margin-left: 0; }
  .ambient-background { left: 0; }
  .top-bar { padding: 0 16px; }
  .mobile-menu-button { display: inline-flex; align-items: center; justify-content: center; }
  .top-bar-date { display: none; }
  .main-content { padding: 18px 14px 32px; }
  .page-title { font-size: 22px; margin-bottom: 18px; }
  .card { padding: 16px; }
  .card-header { align-items: flex-start; gap: 12px; flex-wrap: wrap; }
  .el-message { min-width: min(320px, calc(100vw - 32px)) !important; }
  .el-row > [class*="el-col-"] { max-width: 100%; flex: 0 0 100%; }
  .el-tabs__nav-wrap { overflow-x: auto; scrollbar-width: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
</style>
