<template>
  <section class="operations-page">
    <OperationsMenu/>
    <div class="page-heading"><div><p class="eyebrow">运营</p><h1>读取聊天消息</h1><p class="muted">通过本机飞书 CLI，读取指定人员或群组的消息记录。</p></div><el-button :loading="checking" @click="checkStatus">检查飞书连接</el-button></div>
    <el-alert v-if="connection" :type="connection.available ? 'success' : 'warning'" :closable="false" show-icon :title="connection.available ? `飞书 CLI 已连接${connection.user_name ? ' · '+connection.user_name : ''}` : connection.message"/>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <div class="query-panel">
      <el-form label-position="top" @submit.prevent="readMessages(false)">
        <div class="query-grid">
          <el-form-item label="消息对象"><el-radio-group v-model="kind" @change="resetTarget"><el-radio-button value="person">指定人员</el-radio-button><el-radio-button value="group">指定群组</el-radio-button></el-radio-group></el-form-item>
          <el-form-item :label="kind === 'person' ? '搜索人员 / 填写人员 ID' : '搜索群组 / 填写群 ID'">
            <el-select v-model="target" filterable remote allow-create default-first-option :remote-method="queueSearch" :loading="searching" :placeholder="kind === 'person' ? '输入姓名搜索，或填写 ou_ 开头的 ID' : '输入群名搜索，或填写 oc_ 开头的 ID'" style="width:100%">
              <el-option v-for="item in candidates" :key="item.id" :value="item.id" :label="item.name"><div class="candidate"><span>{{ item.name }}</span><small>{{ item.description || item.id }}</small></div></el-option>
            </el-select>
            <span v-if="target" class="target-id">{{ target }}</span>
            <el-button v-if="kind === 'group' && targetPageToken" link :loading="searching" @click="searchTargets(targetQuery,true)">更多群组搜索结果</el-button>
            <span v-if="refineSearch" class="muted">匹配人员较多，请补充姓名或邮箱缩小范围。</span>
          </el-form-item>
          <el-form-item v-if="kind === 'person'" label="人员消息范围"><el-select v-model="personScope" style="width:100%"><el-option label="与该人员的私聊会话" value="conversation"/><el-option label="该人员发出的消息（含群组）" value="sent"/></el-select></el-form-item>
          <el-form-item label="时间范围"><el-config-provider :locale="zhCn"><el-date-picker v-model="range" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ssZ" format="YYYY-MM-DD HH:mm" start-placeholder="开始时间" end-placeholder="结束时间" style="width:100%"/></el-config-provider></el-form-item>
          <el-form-item label="消息关键词（可选）"><el-input v-model="keyword" clearable maxlength="100" placeholder="按消息内容筛选"/></el-form-item>
          <el-form-item label="每页消息数"><el-select v-model="pageSize"><el-option v-for="n in [20,30,50]" :key="n" :label="n+' 条'" :value="n"/></el-select></el-form-item>
        </div>
        <div class="actions"><el-button type="primary" native-type="submit" :loading="loading">读取消息</el-button><el-button type="success" @click="openComposer">发送消息</el-button><span class="muted">使用当前登录用户的访问权限；默认最近7天，分页读取消息。</span></div>
      </el-form>
    </div>
    <div class="messages-panel">
      <div class="result-heading"><h2>消息记录 <span class="muted">{{ items.length }} 条</span></h2><el-tag type="info">只读</el-tag></div>
      <p v-if="activeLabel" class="muted">当前结果：{{ activeLabel }}</p>
      <el-alert v-if="filterNote" type="info" :title="filterNote" :closable="false"/>
      <el-alert v-if="paginationWarning" type="warning" :title="paginationWarning" :closable="false"/>
      <div v-loading="loading" element-loading-text="正在读取飞书消息…" class="message-list">
        <article v-for="(item,index) in items" :key="item.id || index" class="message">
          <div class="message-meta"><strong>{{ item.sender }}</strong><time>{{ formatTime(item.time) }}</time><el-tag size="small" type="info">{{ typeLabel(item.type) }}</el-tag><el-tag v-if="item.deleted" size="small" type="warning">已撤回</el-tag><span v-if="item.updated" class="muted">已编辑</span></div>
          <p class="message-content"><MessageLinkText :text="item.deleted ? '该消息已撤回' : item.type === 'image' && item.images?.length ? '图片消息' : item.content || '（无可显示的文本内容）'"/></p>
          <div v-if="!item.deleted && item.images?.length" class="message-images">
            <div v-for="key in item.images" :key="key" class="message-image">
              <el-image v-if="imageState(item,key).url" :src="imageState(item,key).url" :preview-src-list="[imageState(item,key).url]" preview-teleported hide-on-click-modal fit="contain" class="image-preview"><template #error><span>图片解码失败，请查看原始消息</span></template></el-image>
              <el-button v-else :loading="imageState(item,key).loading" @click="loadImage(item,key)">{{ imageState(item,key).error ? '重试加载图片' : '加载图片' }}</el-button>
              <p v-if="imageState(item,key).error" class="image-error">{{ imageState(item,key).error }}</p>
            </div>
          </div>
          <p v-if="personScope === 'sent' && kind === 'person' && item.chat_id" class="message-id">会话：{{ item.chat_id }}</p>
          <details><summary>消息详情</summary><p class="message-id">消息 ID：{{ item.id }}<span v-if="item.thread_id"> · 话题：{{ item.thread_id }}</span></p><pre>{{ typeof item.raw_content === 'string' ? item.raw_content : JSON.stringify(item.raw_content,null,2) }}</pre></details>
        </article>
        <el-empty v-if="!items.length && !loading" :description="queried ? '当前页没有匹配消息' : '选择人员或群组后，点击读取消息'"/>
      </div>
      <div v-if="queried" class="more"><el-button v-if="hasMore && nextToken" :loading="loading" @click="readMessages(true)">加载下一页</el-button><span v-else-if="!hasMore" class="muted">当前时间范围的消息已读取完毕</span></div>
      <p class="muted note">图片可点击“加载图片”查看，加载后点击可放大；富文本中的图片也支持预览。文件、语音和视频显示资源标识。</p>
    </div>
    <OperationsMessageComposer ref="composer"/>
  </section>
</template>
<script setup>
import {onMounted,onUnmounted,ref} from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import {operationsApi} from '../api'
import OperationsMessageComposer from '../components/OperationsMessageComposer.vue'
import OperationsMenu from '../components/OperationsMenu.vue'
import MessageLinkText from '../components/MessageLinkText.vue'
const composer=ref(null)
function openComposer(){const prefix=kind.value==='person'?'ou_':'oc_';if(!new RegExp('^'+prefix+'[A-Za-z0-9]+$').test(target.value)){error.value='请先选择收件人员或群组';return}error.value='';composer.value.open({kind:kind.value,id:target.value,name:candidates.value.find(x=>x.id===target.value)?.name||target.value,sender:connection.value?.user_name})}
const kind=ref('person'),target=ref(''),personScope=ref('conversation'),keyword=ref(''),pageSize=ref(30)
const range=ref([new Date(Date.now()-7*86400000).toISOString(),new Date().toISOString()])
const connection=ref(null),checking=ref(false),loading=ref(false),searching=ref(false),error=ref(''),candidates=ref([])
const targetPageToken=ref(''),targetQuery=ref(''),refineSearch=ref(false),items=ref([]),queried=ref(false),hasMore=ref(false),nextToken=ref(''),filterNote=ref(''),paginationWarning=ref(''),activeLabel=ref('')
const imageStates=ref({})
let imageGeneration=0
function imageState(item,key){return imageStates.value[`${item.id}:${key}`] || {}}
function clearImages(){imageGeneration++;Object.values(imageStates.value).forEach(image=>{if(image.url)URL.revokeObjectURL(image.url)});imageStates.value={}}
async function loadImage(item,key){
  const id=`${item.id}:${key}`,generation=imageGeneration
  if(imageStates.value[id]?.loading)return
  imageStates.value[id]={loading:true,error:''}
  try{const blob=await operationsApi.image(item.id,key);if(generation!==imageGeneration)return;imageStates.value[id]={loading:false,url:URL.createObjectURL(blob)}}
  catch(e){if(generation!==imageGeneration)return;if(e.response?.data instanceof Blob){try{e.response.data=JSON.parse(await e.response.data.text())}catch{}}if(generation!==imageGeneration)return;imageStates.value[id]={loading:false,error:explain(e)}}
}
let timer,searchRequest=0,messageRequest=0,snapshot=null
function explain(e){const d=e.response?.data?.detail;if(Array.isArray(d))return d.map(x=>x.msg).join('；');if(d&&typeof d==='object')return [d.message,d.hint,d.missing_scopes?.length?`缺少权限：${d.missing_scopes.join('、')}`:''].filter(Boolean).join('；');return d||e.message||'读取失败'}
async function checkStatus(){checking.value=true;try{connection.value=await operationsApi.status()}catch(e){error.value=explain(e)}finally{checking.value=false}}
function resetTarget(){clearImages();clearTimeout(timer);searchRequest++;messageRequest++;target.value='';candidates.value=[];targetPageToken.value='';refineSearch.value=false;searching.value=false;loading.value=false;items.value=[];queried.value=false;error.value='';activeLabel.value='';snapshot=null}
function queueSearch(query){clearTimeout(timer);searchRequest++;targetQuery.value=query;targetPageToken.value='';if(!query.trim()){candidates.value=[];searching.value=false;return}timer=setTimeout(()=>searchTargets(query),300)}
async function searchTargets(query,more=false){const id=++searchRequest;searching.value=true;error.value='';try{const result=await operationsApi.targets({kind:kind.value,query,...(more?{page_token:targetPageToken.value}:{})});if(id!==searchRequest)return;const merged=more?[...candidates.value,...result.items]:result.items;candidates.value=[...new Map(merged.map(x=>[x.id,x])).values()];targetPageToken.value=result.page_token;refineSearch.value=result.refine_search}catch(e){if(id===searchRequest)error.value=explain(e)}finally{if(id===searchRequest)searching.value=false}}
async function readMessages(more=false){
  if(loading.value)return
  if(!more){
    const prefix=kind.value==='person'?'ou_':'oc_'
    if(!new RegExp('^'+prefix+'[A-Za-z0-9]+$').test(target.value)){error.value='请从搜索结果选择人员或群组，或输入正确的飞书 ID';return}
    if(!range.value?.[0]||!range.value?.[1]){error.value='请选择开始和结束时间';return}
    snapshot={kind:kind.value,target_id:target.value,person_scope:personScope.value,start:range.value[0],end:range.value[1],keyword:keyword.value.trim(),page_size:pageSize.value}
    activeLabel.value=`${candidates.value.find(x=>x.id===target.value)?.name||target.value} · ${formatTime(range.value[0])} 至 ${formatTime(range.value[1])}`
    clearImages();items.value=[];hasMore.value=false;nextToken.value='';filterNote.value='';paginationWarning.value='';queried.value=false
  }
  if(!snapshot)return
  const id=++messageRequest;loading.value=true;error.value=''
  try{const data=await operationsApi.read({...snapshot,page_token:more?nextToken.value:''});if(id!==messageRequest)return;const merged=more?[...items.value,...data.items]:data.items;items.value=[...new Map(merged.map((item,index)=>[item.id||`missing-${index}`,item])).values()];hasMore.value=data.has_more;nextToken.value=data.page_token;filterNote.value=data.filter_note;paginationWarning.value=data.pagination_warning;queried.value=true}
  catch(e){if(id===messageRequest)error.value=explain(e)}finally{if(id===messageRequest)loading.value=false}
}
function formatTime(value){if(value==null)return '—';let parsed=value;if(/^\d+$/.test(String(value))){parsed=Number(value);if(parsed<1e12)parsed*=1000}const d=new Date(parsed);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{hour12:false})}
function typeLabel(value){return {text:'文本',post:'富文本',image:'图片',file:'文件',audio:'语音',video:'视频',media:'媒体',interactive:'卡片',system:'系统消息',sticker:'表情'}[value]||value||'消息'}
onMounted(checkStatus)
onUnmounted(()=>{clearImages();clearTimeout(timer);searchRequest++;messageRequest++})
</script>
<style scoped>
.message-images{display:flex;flex-wrap:wrap;gap:16px;margin:12px 0}.message-image{max-width:100%}.image-preview{width:320px;max-width:100%;height:240px;background:#f8fafc;border:1px solid #e4e7ec;border-radius:8px;cursor:zoom-in}.image-error{color:#d92d20;font-size:12px;max-width:500px;overflow-wrap:anywhere;margin-top:8px}
.operations-page{max-width:1500px;margin:auto}.page-heading,.result-heading,.actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-heading{margin-bottom:20px}.eyebrow{color:#2563eb;font-size:13px;margin-bottom:6px}h1{font-size:26px;margin-bottom:8px}h2{font-size:18px}.muted{color:#667085;font-size:13px;line-height:1.8}.query-panel,.messages-panel{padding:24px;background:white;border:1px solid #e4e7ec;border-radius:12px;margin-top:18px}.query-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 20px}.actions{justify-content:flex-start}.target-id{display:block;font-size:12px;color:#667085;overflow-wrap:anywhere}.candidate{display:flex;gap:16px;justify-content:space-between}.candidate small{color:#667085;max-width:220px;overflow:hidden;text-overflow:ellipsis}.message-list{min-height:180px}.message{padding:20px 0;border-bottom:1px solid #e4e7ec}.message-meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.message-meta time,.message-id{font-size:12px;color:#667085}.message-content,pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin:12px 0}.message-id{overflow-wrap:anywhere}details{font-size:12px;color:#667085}summary{cursor:pointer}pre{background:#f8fafc;padding:12px;max-height:260px;overflow:auto}.more{text-align:center;margin-top:20px}.note{margin-top:20px}.el-alert{margin:12px 0}@media(max-width:1000px){.query-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.query-grid{grid-template-columns:1fr}.page-heading{align-items:flex-start}.query-panel,.messages-panel{padding:16px}}
</style>
