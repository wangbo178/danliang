<template>
  <div class="metric-card" :style="{ '--accent': color }">
    <div class="metric-icon"><el-icon><component :is="icon" /></el-icon></div>
    <div class="metric-body">
      <div class="metric-value" :class="{ 'is-text': typeof value === 'string' }" :title="displayValue">
        <el-skeleton v-if="loading" :rows="1" animated style="width:100px" />
        <span v-else>{{ displayValue }}</span>
      </div>
      <div class="metric-label">{{ label }}</div>
      <div v-if="sub" class="metric-sub">{{ sub }}</div>
    </div>
    <div class="metric-accent" />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: null },
  icon: { type: [Object, Function, String], required: true },
  color: { type: String, default: '#2563eb' },
  unit: { type: String, default: '' },
  sub: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

const displayValue = computed(() => {
  if (props.value === null || props.value === undefined) return '—'
  const v = typeof props.value === 'number'
    ? props.value.toLocaleString('zh-CN')
    : props.value
  return `${v}${props.unit}`
})
</script>

<style scoped>
.metric-card {
  background: #fff;
  border: 1px solid var(--line, #e8eaf0);
  border-radius: 12px;
  box-shadow: var(--shadow-sm, 0 4px 14px rgba(16, 24, 40, 0.05));
  padding: 24px;
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
  cursor: default;
}

.metric-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 24px rgba(16, 24, 40, 0.09);
}

.metric-icon {
  color: var(--accent);
  font-size: 26px;
  line-height: 1;
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.metric-body {
  flex: 1;
  min-width: 0;
}

.metric-value {
  font-size: clamp(21px, 2vw, 28px);
  font-weight: 700;
  color: #1a1a2e;
  line-height: 1.2;
  white-space: nowrap;
}

.metric-value.is-text {
  overflow: hidden;
  font-size: clamp(16px, 1.35vw, 21px);
  line-height: 1.35;
  text-overflow: ellipsis;
}

.metric-label {
  font-size: 13px;
  color: #8c8c8c;
  margin-top: 4px;
}

.metric-sub {
  font-size: 12px;
  color: var(--accent);
  margin-top: 2px;
  font-weight: 500;
}

.metric-accent {
  position: absolute;
  left: 0;
  top: 18px;
  bottom: 18px;
  width: 3px;
  background: var(--accent);
  border-radius: 0 3px 3px 0;
}

@media (max-width: 768px) {
  .metric-card { padding: 18px; }
  .metric-icon { width: 48px; height: 48px; font-size: 28px; }
}
</style>
