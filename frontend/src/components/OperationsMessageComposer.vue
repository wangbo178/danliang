<template>
  <el-dialog v-model="visible" title="发送飞书消息" width="680px" :close-on-click-modal="false" :close-on-press-escape="!sending" :show-close="!sending" :before-close="beforeClose">
    <el-descriptions :column="1" border>
      <el-descriptions-item label="发送身份">当前飞书用户{{ recipient.sender ? ` · ${recipient.sender}` : '' }}</el-descriptions-item>
      <el-descriptions-item label="收件对象">{{ recipient.kind === 'person' ? '人员' : '群组' }} · {{ recipient.name }}<div class="recipient-id">{{ recipient.id }}</div></el-descriptions-item>
    </el-descriptions>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <template v-if="step === 'edit'">
      <p class="content-label">消息内容（纯文本）</p>
      <el-input v-model="text" type="textarea" :rows="9" maxlength="5000" show-word-limit placeholder="输入需要发送的消息，支持换行"/>
    </template>
    <template v-else-if="step === 'preview'">
      <p class="content-label">待发送内容</p><pre class="content-preview"><MessageLinkText :text="draft.text"/></pre>
      <p class="hint">确认收件对象与内容后，点击“确认发送”将以当前飞书用户身份发送。</p>
    </template>
    <template v-else>
      <el-result icon="success" title="消息已发送"><template #sub-title><p>收件对象：{{ recipient.name }}</p><p class="recipient-id">消息 ID：{{ result.message_id }}</p></template></el-result>
    </template>
    <template #footer>
      <el-button :disabled="sending" @click="visible=false">{{ step === 'success' ? '关闭' : '取消' }}</el-button>
      <el-button v-if="step === 'edit'" type="primary" :disabled="!text.trim()" @click="preview">预览发送</el-button>
      <template v-if="step === 'preview'"><el-button :disabled="sending" @click="step='edit';error=''">返回编辑</el-button><el-button type="primary" :loading="sending" @click="send">确认发送</el-button></template>
    </template>
  </el-dialog>
</template>
<script setup>
import {ref} from 'vue'
import {operationsApi} from '../api'
import MessageLinkText from './MessageLinkText.vue'
const emit=defineEmits(['sent'])
const visible=ref(false),sending=ref(false),step=ref('edit'),text=ref(''),error=ref(''),recipient=ref({}),draft=ref(null),result=ref(null)
function open(value){if(sending.value)return;recipient.value={...value};text.value='';error.value='';step.value='edit';draft.value=null;result.value=null;visible.value=true}
function preview(){if(!text.value.trim())return;const previous=draft.value;draft.value={kind:recipient.value.kind,target_id:recipient.value.id,text:text.value,idempotency_key:previous?.kind===recipient.value.kind&&previous?.target_id===recipient.value.id&&previous?.text===text.value?previous.idempotency_key:crypto.randomUUID(),confirmed:true};error.value='';step.value='preview'}
function beforeClose(done){if(!sending.value)done()}
async function send(){
  if(sending.value||!draft.value)return
  sending.value=true;error.value=''
  try{result.value=await operationsApi.send({...draft.value});step.value='success';emit('sent',result.value)}
  catch(e){const d=e.response?.data?.detail;error.value=Array.isArray(d)?d.map(x=>x.msg).join('；'):typeof d==='object'&&d?[d.message,d.hint,d.missing_scopes?.length?`缺少权限：${d.missing_scopes.join('、')}`:''].filter(Boolean).join('；'):d||e.message||'发送失败'}
  finally{sending.value=false}
}
defineExpose({open})
</script>
<style scoped>
.recipient-id{color:#667085;font-size:12px;overflow-wrap:anywhere}.content-label{margin:18px 0 10px;font-weight:600}.content-preview{white-space:pre-wrap;overflow-wrap:anywhere;padding:16px;border:1px solid #d0d5dd;border-radius:8px;background:#f8fafc;max-height:50vh;overflow:auto;font-family:inherit;line-height:1.8}.hint{font-size:13px;color:#667085;margin-top:12px}.el-alert{margin-top:16px}
</style>
