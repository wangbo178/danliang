<template>
  <div class="save-action-bar" :class="`is-${status}`">
    <template v-if="status === 'saved'">
      <div class="save-receipt-icon"><el-icon><CircleCheckFilled /></el-icon></div>
      <div class="save-receipt-copy">
        <strong>数据已保存到数据库</strong>
        <span>
          {{ Number(receipt?.saved || 0).toLocaleString('zh-CN') }} 条记录
          <template v-if="receipt?.skipped"> · 跳过 {{ Number(receipt.skipped).toLocaleString('zh-CN') }} 条无效记录</template>
          <template v-if="receipt?.batchId"> · 批次 {{ receipt.batchId }}</template>
          <template v-if="receipt?.savedAt"> · {{ receipt.savedAt }}</template>
        </span>
      </div>
      <el-button type="primary" plain size="small" @click="$emit('reset')">继续导入</el-button>
    </template>

    <template v-else>
      <el-button
        class="btn-primary"
        type="primary"
        :loading="status === 'saving'"
        :disabled="disabled || status === 'saving'"
        @click="$emit('save')"
      >
        <el-icon v-if="status !== 'saving'"><Upload /></el-icon>
        {{ status === 'saving' ? '正在写入数据库...' : `确认保存 ${Number(recordCount || 0).toLocaleString('zh-CN')} 条` }}
      </el-button>
      <span v-if="disabled && disabledReason" class="save-disabled-reason">
        <el-icon><WarningFilled /></el-icon>{{ disabledReason }}
      </span>
      <span v-else class="save-hint">保存后将生成可追溯的上传批次</span>
    </template>
  </div>
</template>

<script setup>
defineProps({
  status: { type: String, default: 'ready' },
  receipt: { type: Object, default: null },
  recordCount: { type: Number, default: 0 },
  disabled: { type: Boolean, default: false },
  disabledReason: { type: String, default: '' },
})

defineEmits(['save', 'reset'])
</script>

<style scoped>
.save-action-bar { min-height: 56px; padding: 12px 14px; display: flex; align-items: center; gap: 12px; border: 1px solid #e4e7ec; border-radius: 10px; background: #f8fafc; }
.save-action-bar.is-saved { border-color: #abefc6; background: #ecfdf3; }
.save-receipt-icon { width: 34px; height: 34px; flex: 0 0 34px; display: flex; align-items: center; justify-content: center; border-radius: 50%; color: #12b76a; background: #d1fadf; font-size: 20px; }
.save-receipt-copy { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.save-receipt-copy strong { color: #067647; font-size: 14px; }
.save-receipt-copy span, .save-hint { color: #667085; font-size: 12px; }
.save-disabled-reason { display: inline-flex; align-items: center; gap: 5px; color: #b54708; font-size: 12px; }
@media (max-width: 640px) {
  .save-action-bar { align-items: flex-start; flex-wrap: wrap; }
  .save-receipt-copy { min-width: calc(100% - 50px); }
  .save-action-bar.is-saved .el-button { margin-left: 46px; }
}
</style>
