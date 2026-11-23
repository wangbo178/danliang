<template>
  <nav class="operations-navigation" aria-label="运营功能导航">
    <el-menu mode="horizontal" router :default-active="route.path" :ellipsis="false">
      <el-menu-item index="/operations/messages">读取聊天消息</el-menu-item>
      <el-menu-item index="/operations/agent">运营 Agent</el-menu-item>
    </el-menu>
  </nav>
</template>
<script setup>
import {useRoute} from 'vue-router'
const route=useRoute()
</script>
<style scoped>
.operations-navigation{margin-bottom:24px;background:#fff;border-radius:10px 10px 0 0;overflow-x:auto}
.el-menu{--el-menu-horizontal-height:54px;--el-menu-active-color:#2563eb;--el-menu-text-color:#475467;--el-menu-hover-bg-color:#eff6ff;min-width:max-content}
.el-menu-item{font-weight:600;padding:0 24px}
</style>
