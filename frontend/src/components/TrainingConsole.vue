<template>
  <div class="training-console">
    <div class="console-header">
      <span class="console-title">
        <span class="blink-dot" :class="{ active: running }" />
        训练日志
      </span>
      <el-button size="small" text type="info" @click="clearLogs">清空</el-button>
    </div>
    <div ref="consoleBody" class="console-body">
      <div
        v-for="(line, i) in logs"
        :key="i"
        class="log-line"
        :class="getLineClass(line)"
      >
        <span class="log-prefix">›</span>
        <span class="log-text">{{ line }}</span>
      </div>
      <div v-if="logs.length === 0" class="log-empty">
        等待训练启动...
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  logs: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})

const emit = defineEmits(['clear'])
const consoleBody = ref(null)

function clearLogs() {
  emit('clear')
}

function getLineClass(line) {
  if (!line) return ''
  const lower = line.toLowerCase()
  if (lower.includes('error') || lower.includes('失败') || lower.includes('错误')) return 'log-error'
  if (lower.includes('warn') || lower.includes('警告')) return 'log-warn'
  if (lower.includes('完成') || lower.includes('done') || lower.includes('success')) return 'log-success'
  if (lower.includes('phase') || lower.includes('[phase')) return 'log-phase'
  return ''
}

watch(() => props.logs.length, async () => {
  await nextTick()
  if (consoleBody.value) {
    consoleBody.value.scrollTop = consoleBody.value.scrollHeight
  }
})
</script>

<style scoped>
.training-console {
  background: #0d1117;
  border-radius: 12px;
  overflow: hidden;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
}

.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
}

.console-title {
  font-size: 13px;
  color: #8b949e;
  display: flex;
  align-items: center;
  gap: 8px;
}

.blink-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #484f58;
  display: inline-block;
}

.blink-dot.active {
  background: #2ea043;
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.console-body {
  height: 340px;
  overflow-y: auto;
  padding: 12px 16px;
  scrollbar-width: thin;
  scrollbar-color: #30363d transparent;
}

.console-body::-webkit-scrollbar {
  width: 6px;
}

.console-body::-webkit-scrollbar-track {
  background: transparent;
}

.console-body::-webkit-scrollbar-thumb {
  background: #30363d;
  border-radius: 3px;
}

.log-line {
  display: flex;
  gap: 8px;
  padding: 2px 0;
  font-size: 13px;
  line-height: 1.6;
}

.log-prefix {
  color: #3fb950;
  flex-shrink: 0;
}

.log-text {
  color: #c9d1d9;
  word-break: break-all;
}

.log-error .log-prefix { color: #f85149; }
.log-error .log-text   { color: #f85149; }
.log-warn  .log-prefix { color: #e3b341; }
.log-warn  .log-text   { color: #e3b341; }
.log-success .log-text { color: #3fb950; }
.log-phase .log-text   { color: #79c0ff; font-weight: 600; }

.log-empty {
  color: #484f58;
  font-size: 13px;
  padding: 80px 0;
  text-align: center;
}
</style>
