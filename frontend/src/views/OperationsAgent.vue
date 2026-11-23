<template>
  <section class="agent-page">
    <OperationsMenu/>
    <div class="heading"><div><h1>运营 Agent</h1><p class="muted">延迟一分钟扫描时间段 → 直播判断 → 待办入队 → 五分钟 Agent → 信息齐全写正式排期</p></div><el-button type="primary" @click="editTask()">新建监听任务</el-button></div>
    <el-alert v-if="error" type="error" :title="error" :closable="false"/>
    <p class="muted">{{ state.environment?.runtime }}。{{ state.environment?.worker_running ? 'Loop 工作线程已就绪' : 'Loop 工作线程未启动' }}；规则基线可直接试用，模型引擎复用现有模型设置。</p>
    <div class="section">
      <div class="heading"><h2>监听任务</h2><el-button :loading="loading" @click="load()">刷新</el-button></div>
      <el-table :data="state.tasks" empty-text="先添加要监听的群组和发送人，保存后可试跑或启动" row-key="id">
        <el-table-column prop="name" label="任务" min-width="140"/><el-table-column label="监听范围" min-width="240"><template #default="{row}"><div>群：{{ row.groups.map(x=>x.name).join('、') }}</div><small>人员：{{ row.senders.map(x=>x.name).join('、') }}</small></template></el-table-column>
        <el-table-column label="执行设置" min-width="150"><template #default="{row}">{{ row.interval_seconds }}秒 · {{ engines[row.engine] }}<br/>{{ row.followup_mode === 'auto' ? '自动私聊原发送人' : '仅生成追问草稿' }}</template></el-table-column>
        <el-table-column label="状态" min-width="180"><template #default="{row}"><el-tag :type="row.last_error ? 'danger' : row.enabled ? 'success' : 'info'">{{ row.running ? '执行中' : row.enabled ? '监听中' : '已停止' }}</el-tag><p v-if="row.last_error" class="failure">{{ row.last_error }}</p><small>最近执行：{{ time(row.last_run_at) }}</small></template></el-table-column>
        <el-table-column label="操作" width="255"><template #default="{row}"><el-button link :disabled="row.enabled || row.running" @click="editTask(row)">编辑</el-button><el-button link :disabled="row.enabled || row.running" @click="control(row,'once')">试跑一次</el-button><el-button link :type="row.enabled ? 'danger' : 'primary'" @click="control(row,row.enabled?'stop':'start')">{{ row.enabled ? '停止' : '启动' }}</el-button></template></el-table-column>
      </el-table>
    </div>
    <div class="section">
      <div class="heading"><h2>时间段记录</h2><span class="muted">首次群扫描至少延迟60秒；读完、判断并保存后不再拉取原时间段</span></div>
      <el-table :data="state.windows || []" row-key="id" empty-text="暂无时间段记录">
        <el-table-column prop="group_name" label="群组" min-width="140"/><el-table-column label="扫描范围" min-width="230"><template #default="{row}">{{ time(row.start) }}<br/>{{ time(row.end) }}</template></el-table-column>
        <el-table-column label="读取 / 判断" width="110"><template #default="{row}">{{ row.raw_message_count ?? row.message_count }} / {{ row.message_count }}</template></el-table-column><el-table-column label="状态 / 判断" min-width="240"><template #default="{row}"><el-tag :type="row.status==='error'?'danger':row.status==='completed'?'success':'info'">{{ windowStates[row.status] || row.status }}</el-tag><p>{{ row.judgment || '等待读取与判断' }}</p><small class="failure">{{ row.last_error }}</small></template></el-table-column>
        <el-table-column label="待办数" width="80"><template #default="{row}">{{ row.todo_ids.length }}</template></el-table-column><el-table-column label="原始记录" width="120"><template #default="{row}"><el-button link @click="showTrace('window',row.id)">查看消息与判断</el-button></template></el-table-column>
      </el-table>
      <el-config-provider :locale="zhCn"><el-pagination v-model:current-page="windowPage" v-model:page-size="windowPageSize" :page-sizes="[10,20,50,100]" :total="state.window_total || 0" layout="total, sizes, prev, pager, next, jumper" style="margin-top:16px" @current-change="load(false,true)" @size-change="changeWindowSize"/></el-config-provider>
    </div>
    <div class="section">
      <div class="heading"><h2>消息队列 / Agent 节点</h2><span class="muted">每次执行最多5分钟；异常重试当前节点，正常未完成才创建后续节点</span></div>
      <el-table :data="state.queue || []" row-key="id" empty-text="缺失直播信息时，会生成关联时间段的待办队列">
        <el-table-column label="节点 / 上一节点" min-width="200"><template #default="{row}"><span :title="row.id">{{ row.id.slice(0,12) }}</span><div class="muted" :title="row.parent_id">上一节点：{{ row.parent_id?.slice(0,12) || '首节点' }}</div></template></el-table-column>
        <el-table-column label="目标" min-width="190"><template #default="{row}">{{ row.goal.missing.join('、') || '核验并写入正式排期' }}<div class="muted">时间段 {{ row.window_id.slice(0,12) }}</div></template></el-table-column>
        <el-table-column label="消费状态" min-width="190"><template #default="{row}"><el-tag :type="row.status==='retry'?'danger':row.status==='completed'?'success':'info'">{{ queueStates[row.status] || row.status }}</el-tag><span> · {{ row.attempts }}次</span><p class="failure">{{ row.last_error }}</p></template></el-table-column>
        <el-table-column label="操作" width="220"><template #default="{row}"><el-button link @click="showTrace('node',row.id)">执行详情</el-button><el-button v-if="row.status==='awaiting_approval'" link type="primary" @click="approveNode(row)">执行待办</el-button><el-button v-if="row.status==='retry'" link type="primary" @click="retryNode(row)">重新调度</el-button></template></el-table-column>
      </el-table>
    </div>
    <div class="section">
      <div class="heading"><h2>直播待办与正式排期</h2><el-select v-model="filter" style="width:180px"><el-option label="全部意向" value="all"/><el-option label="待补信息" value="needs_info"/><el-option label="等待私聊回复" value="awaiting_reply"/><el-option label="信息已齐全" value="ready"/><el-option label="已写正式排期" value="persisted"/><el-option label="已忽略" value="ignored"/></el-select></div>
      <el-table :data="opportunities" row-key="id" empty-text="暂无直播意向，启动任务后会在这里归集">
        <el-table-column label="主播 / 日期" min-width="150"><template #default="{row}"><strong>{{ row.analysis.anchor || '待确认主播' }}</strong><div>{{ row.analysis.live_date || '日期待确认' }}</div></template></el-table-column>
        <el-table-column label="商品与销售计划" min-width="230"><template #default="{row}"><div>{{ row.analysis.products.join('、') || '商品待补充' }}</div><small>单量：{{ row.analysis.planned_quantity ?? '未提供' }}；{{ row.analysis.sales_basis }}金额：{{ row.analysis.sales_amount ?? '未提供' }}</small></template></el-table-column>
        <el-table-column label="来源" min-width="170"><template #default="{row}">{{ row.group_name }}<div>{{ row.sender_name }}</div></template></el-table-column>
        <el-table-column label="进展" min-width="180"><template #default="{row}"><el-tag :type="['ready','persisted'].includes(row.state)?'success':'warning'">{{ states[row.state] || row.state }}</el-tag><div class="muted">{{ row.missing.join('、') }}</div><small v-if="row.schedule_id">正式排期 ID：{{ row.schedule_id }}</small><small v-else-if="row.question_status">追问：{{ questionStates[row.question_status] || row.question_status }}</small></template></el-table-column>
        <el-table-column label="操作" width="230"><template #default="{row}"><el-button link @click="inspect(row)">详情 / 完善</el-button><el-button v-if="row.missing.length && ['draft','failed'].includes(row.question_status) && row.state!=='ignored'" link type="primary" :loading="sending===row.id" @click="sendQuestion(row)">发送追问</el-button><el-button v-if="row.state!=='ignored'" link @click="ignore(row)">忽略</el-button></template></el-table-column>
      </el-table>
    </div>
    <div class="section">
      <div class="heading"><h2>实时步骤</h2><span class="muted">页面可见时每3秒刷新，展示最近150条步骤</span></div>
      <div class="log-list"><div v-for="entry in state.logs" :key="entry.id" class="log"><span>{{ time(entry.time) }}</span><el-tag size="small" :type="entry.step==='error'?'danger':'info'">{{ entry.step }}</el-tag><strong>{{ entry.message }}</strong><details v-if="Object.keys(entry.details).length"><summary>调用与决策详情</summary><pre>{{ JSON.stringify(entry.details,null,2) }}</pre></details></div><el-empty v-if="!state.logs?.length" description="尚无执行步骤"/></div>
    </div>
    <el-collapse class="section"><el-collapse-item title="职责、工具、环境与运行机制"><p>群消息首次读取延迟至少一分钟，私聊回复实时读取。消息包含飞书文档链接时，先通过CLI阅读正文和表格，再交给模型判断。文档权限或读取异常会重试，不会按无直播处理。无直播信息的时间段直接完成；有直播但缺信息时，生成包含判断、原文、目标和 System Prompt 的待办节点。</p><p>每个 Agent 最多驻留五分钟。正常未完成时保存结果和剩余目标，创建关联上一节点的新节点；LLM、工具或数据库异常时不确认消费成功，继续重试原节点。排期与队列状态在同一业务数据库事务提交。</p><p>试跑不会发送私聊或写正式排期；正常监听中信息齐全会写入绑定店铺的正式排期。纯销金额单独保存，不当作GMV。同一人多场待办的回复需要明确任务编号才能自动关联。</p><p>队列存储：{{ state.mechanism?.storage }}。运行环境：{{ state.environment?.os }}；飞书 CLI：{{ state.environment?.cli_available ? '已安装' : '未安装' }}。</p><pre>{{ state.prompt }}</pre></el-collapse-item></el-collapse>
    <el-dialog v-model="taskVisible" :title="editingId?'编辑监听任务':'新建监听任务'" width="720px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="任务名称"><el-input v-model="form.name" maxlength="100"/></el-form-item>
        <div class="form-grid"><el-form-item label="正式排期所属店铺（启动前必选）"><el-select v-model="form.shop_id" filterable style="width:100%"><el-option v-for="shop in state.shops || []" :key="shop.id" :value="shop.id" :label="`${shop.name}（${shop.id}）`"/></el-select></el-form-item><el-form-item label="正式排期归属渠道"><el-input v-model="form.schedule_channel" maxlength="64"/></el-form-item></div>
        <el-form-item label="监听群组（可多选，输入群名搜索或填写 oc_ ID）"><el-select v-model="form.groupIds" multiple filterable remote allow-create :remote-method="q=>search('group',q)" :loading="searching.group" style="width:100%"><el-option v-for="item in choices.group" :key="item.id" :value="item.id" :label="item.name"/></el-select></el-form-item>
        <el-form-item label="指定发送人（可多选，只处理这些人员的消息）"><el-select v-model="form.senderIds" multiple filterable remote allow-create :remote-method="q=>search('person',q)" :loading="searching.person" style="width:100%"><el-option v-for="item in choices.person" :key="item.id" :value="item.id" :label="item.name"/></el-select></el-form-item>
        <div class="form-grid"><el-form-item label="轮询间隔（秒）"><el-input-number v-model="form.interval_seconds" :min="30" :max="3600"/></el-form-item><el-form-item label="首次读取过去多少小时"><el-input-number v-model="form.lookback_hours" :min="1" :max="168"/></el-form-item></div>
        <div class="form-grid"><el-form-item label="扫描时间段上限（分钟）"><el-input-number v-model="form.window_minutes" :min="1" :max="60"/></el-form-item><el-form-item label="首次群扫描延迟"><el-tag>固定至少60秒</el-tag></el-form-item></div>
        <el-form-item label="限定扫描时间范围（选填，留空则持续监听）"><el-date-picker v-model="form.scanRange" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ssZ" start-placeholder="开始时间" end-placeholder="结束时间" style="width:100%"/></el-form-item>
        <el-form-item label="识别引擎"><el-select v-model="form.engine" style="width:100%"><el-option v-for="(name,key) in engines" :key="key" :value="key" :label="name+(key!=='rules'&&!state.providers?.[key]?.configured?'（待配置密钥）':'')"/></el-select></el-form-item>
        <el-form-item label="缺失信息时"><el-radio-group v-model="form.followup_mode"><el-radio value="draft">仅生成追问草稿</el-radio><el-radio value="auto">自动私聊原消息发送人</el-radio></el-radio-group></el-form-item>
        <p class="muted">金额保留“纯销 / GMV”口径，未提供商品或销售数量/金额时追问；同一条意向不重复发送追问。</p>
      </el-form><template #footer><el-button @click="taskVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveTask">保存任务</el-button></template>
    </el-dialog>
    <div class="section"><h2>消息识别试验台</h2><p class="muted">只测试提取与缺失项，不读取群消息、不发送私聊。</p><el-input v-model="sample" type="textarea" :rows="4"/><div class="tabs" style="margin-top:12px"><el-select v-model="sampleEngine" style="width:190px"><el-option v-for="(name,key) in engines" :key="key" :label="name" :value="key"/></el-select><el-button :loading="previewing" @click="preview">识别示例</el-button></div><pre v-if="sampleResult">{{ JSON.stringify(sampleResult,null,2) }}</pre></div>
    <el-dialog v-model="detailVisible" title="直播意向详情 / 人工完善" width="800px">
      <template v-if="detail"><p>来源：{{ detail.group_name }} · {{ detail.sender_name }} · {{ detail.source_message_id }}</p><pre><MessageLinkText :text="detail.source_content"/></pre>
        <p>关联时间段：{{ detail.window_ids?.join('、') || detail.window_id }}</p><h3>{{ detail.engine==='rules' ? '规则判断' : 'LLM判断' }}</h3><pre>{{ detail.llm_judgment }}</pre>
        <el-form label-position="top"><div class="form-grid"><el-form-item label="主播"><el-input v-model="detail.analysis.anchor"/></el-form-item><el-form-item label="直播日期"><el-date-picker v-model="detail.analysis.live_date" value-format="YYYY-MM-DD"/></el-form-item><el-form-item label="预估单量"><el-input-number v-model="detail.analysis.planned_quantity" :min="0" :precision="0"/></el-form-item><el-form-item label="预估金额"><el-input-number v-model="detail.analysis.sales_amount" :min="0" :precision="2"/></el-form-item></div><el-form-item label="商品（每行一个）"><el-input v-model="detailProducts" type="textarea" :rows="3"/></el-form-item><el-form-item label="金额口径"><el-select v-model="detail.analysis.sales_basis"><el-option v-for="name in ['纯销','GMV','未说明']" :key="name" :label="name" :value="name"/></el-select></el-form-item></el-form>
        <h3>追问草稿 / 已发送内容</h3><pre>{{ detail.question_text || '无需追问' }}</pre><p v-if="detail.question_error" class="failure">{{ detail.question_error }}</p><h3 v-if="detail.replies?.length">私聊补充</h3><pre v-for="reply in detail.replies || []" :key="reply.id">{{ reply.text }}</pre>
      </template><template #footer><el-button @click="detailVisible=false">关闭</el-button><el-button type="primary" :loading="saving" @click="saveDetail">保存人工完善</el-button></template>
    </el-dialog>
    <el-dialog v-model="traceVisible" :title="traceKind==='window'?'时间段原始消息与判断':'队列节点执行详情'" width="900px">
      <template v-if="trace">
        <el-descriptions :column="1" border><el-descriptions-item label="ID">{{ trace.id }}</el-descriptions-item><el-descriptions-item label="关联时间段">{{ trace.window_id || `${time(trace.start)} 至 ${time(trace.end)}` }}</el-descriptions-item><el-descriptions-item v-if="traceKind==='node'" label="上一节点">{{ trace.parent_id || '首节点' }}</el-descriptions-item><el-descriptions-item label="状态">{{ queueStates[trace.status] || windowStates[trace.status] || trace.status }}</el-descriptions-item></el-descriptions>
        <template v-if="traceKind==='window'">
          <h3>读取到的聊天原文（{{ (trace.raw_messages || trace.messages || []).length }}条，用于判断 {{ trace.messages?.length || 0 }}条）</h3>
          <article v-for="message in trace.raw_messages || trace.messages || []" :key="message.id" class="original-message"><div class="original-meta"><strong>{{ message.sender || message.sender_id }}</strong><el-tag v-if="message.included_in_judgment !== undefined" size="small" :type="message.included_in_judgment ? 'success' : 'info'">{{ message.included_in_judgment ? '用于判断' : '范围外/已撤回，不参与判断' }}</el-tag><time>{{ time(message.time) }}</time><small>消息 ID：{{ message.id }}</small></div><pre><MessageLinkText :text="message.content || '（无可显示的文本）'"/></pre><details v-if="message.raw_content && message.raw_content !== message.content"><summary>查看消息原始结构</summary><pre>{{ typeof message.raw_content === 'string' ? message.raw_content : JSON.stringify(message.raw_content,null,2) }}</pre></details></article>
          <el-empty v-if="!(trace.raw_messages || trace.messages || []).length" description="该时间段没有保存到聊天原文；历史记录仅展示当时已保存的指定人员消息"/>
          <template v-if="Object.keys(trace.document_cache || {}).length"><h3>已读取的云文档</h3><details v-for="document in Object.values(trace.document_cache)" :key="document.url" class="document-snapshot"><summary>{{ document.title }}</summary><p><MessageLinkText :text="document.url"/></p><small>读取时间：{{ time(document.read_at) }} · 版本：{{ document.revision ?? '未提供' }}</small><pre><MessageLinkText :text="document.content"/></pre></details></template>
          <h3>LLM / 规则判断结果</h3><pre>{{ trace.judgment ? JSON.stringify(trace.judgment,null,2) : '尚未完成判断' }}</pre>
        </template>
        <template v-else>
          <h3>原始聊天消息</h3><pre><MessageLinkText :text="trace.todo?.source_content || ''"/></pre><h3>LLM / 规则判断</h3><pre>{{ trace.todo?.llm_judgment }}</pre>
          <details v-for="document in trace.todo?.documents || []" :key="document.url" class="document-snapshot"><summary>云文档：{{ document.title }}</summary><MessageLinkText :text="document.url"/><pre><MessageLinkText :text="document.content"/></pre></details>
          <h3>待办目标</h3><pre>{{ JSON.stringify(trace.goal,null,2) }}</pre><h3>System Prompt</h3><pre>{{ trace.system_prompt }}</pre><h3>已保存处理进度</h3><pre>{{ JSON.stringify(trace.checkpoint,null,2) }}</pre><h3>处理结果 / 后续目标</h3><pre>{{ JSON.stringify(trace.result,null,2) }}</pre>
        </template>
        <p class="failure">{{ trace.last_error }}</p>
      </template>
    </el-dialog>
  </section>
</template>
<script setup>
import {computed,onMounted,onUnmounted,ref,reactive} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {operationsApi} from '../api'
import MessageLinkText from '../components/MessageLinkText.vue'
import OperationsMenu from '../components/OperationsMenu.vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
const state=ref({tasks:[],opportunities:[],logs:[],windows:[],queue:[],shops:[]}),loading=ref(false),error=ref(''),filter=ref('all'),sending=ref('')
const windowPage=ref(1),windowPageSize=ref(20)
const engines={rules:'规则基线',deepseek:'DeepSeek',minimax:'MiniMax'},states={needs_info:'待补信息',awaiting_reply:'等待私聊回复',ready:'信息已齐全',persisted:'已写正式排期',ignored:'已忽略'},questionStates={draft:'草稿',sending:'发送中',sent:'已发送',failed:'发送失败',unknown:'结果待核实',queued:'已入队'}
const windowStates={reading:'分页读取中',analyzing:'判断中',error:'失败待重试',waiting:'等待目标完成',completed:'时间段已完成'}
const queueStates={awaiting_approval:'等待执行确认',pending:'待消费',running:'Agent执行中',retry:'消费失败待重试',succeeded:'本轮已完成，已创建后续节点',completed:'目标已完成'}
const traceVisible=ref(false),trace=ref(null),traceKind=ref('node')
const opportunities=computed(()=>state.value.opportunities.filter(x=>filter.value==='all'||x.state===filter.value))
const taskVisible=ref(false),editingId=ref(null),saving=ref(false),form=ref({}),choices=reactive({group:[],person:[]}),searching=reactive({group:false,person:false})
const detailVisible=ref(false),detail=ref(null),detailProducts=ref('')
const sample=ref('11月达播机制表\n李芊默  11月11日    预估纯销15w   辛苦检查链接@B6李智'),sampleEngine=ref('rules'),sampleResult=ref(null),previewing=ref(false)
let timer,searchTimers={},searchIds={group:0,person:0},disposed=false,loadRequest=0
function explain(e){const d=e.response?.data?.detail;return Array.isArray(d)?d.map(x=>x.msg).join('；'):typeof d==='object'&&d?d.message||JSON.stringify(d):d||e.message}
async function load(silent=false,force=false){if(loading.value&&!force)return;const id=++loadRequest;loading.value=true;try{const data=await operationsApi.agentState({window_page:windowPage.value,window_page_size:windowPageSize.value});if(!disposed&&id===loadRequest)state.value=data}catch(e){if(!disposed&&id===loadRequest&&!silent)error.value=explain(e)}finally{if(id===loadRequest)loading.value=false}}
function changeWindowSize(){windowPage.value=1;load(false,true)}
function time(v){if(v==null)return '尚未执行';let parsed=v;if(/^\d+$/.test(String(v))){parsed=Number(v);if(parsed<1e12)parsed*=1000}const value=new Date(parsed);return Number.isNaN(value.getTime())?String(v):value.toLocaleString('zh-CN',{hour12:false})}
function editTask(task){editingId.value=task?.id||null;choices.group=task?.groups||[];choices.person=task?.senders||[];form.value={name:task?.name||'达播意向跟进',groupIds:task?.groups.map(x=>x.id)||[],senderIds:task?.senders.map(x=>x.id)||[],interval_seconds:task?.interval_seconds||60,lookback_hours:task?.lookback_hours||1,engine:task?.engine||'deepseek',followup_mode:task?.followup_mode||'draft',shop_id:task?.shop_id||'',schedule_channel:task?.schedule_channel||'达播',window_minutes:task?.window_minutes||5,scanRange:task?.scan_start?[task.scan_start,task.scan_end]:[]};taskVisible.value=true}
function search(kind,q){clearTimeout(searchTimers[kind]);const id=++searchIds[kind];if(!q.trim())return;searchTimers[kind]=setTimeout(async()=>{searching[kind]=true;try{const data=await operationsApi.targets({kind,query:q});if(id===searchIds[kind])choices[kind]=[...new Map([...choices[kind],...data.items].map(x=>[x.id,x])).values()]}catch(e){error.value=explain(e)}finally{if(id===searchIds[kind])searching[kind]=false}},300)}
async function saveTask(){saving.value=true;error.value='';try{const f=form.value;const resolve=(ids,kind)=>ids.map(id=>choices[kind].find(x=>x.id===id)||{id,name:id});await operationsApi.saveAgentTask({name:f.name,groups:resolve(f.groupIds,'group').map(({id,name})=>({id,name})),senders:resolve(f.senderIds,'person').map(({id,name})=>({id,name})),interval_seconds:f.interval_seconds,lookback_hours:f.lookback_hours,engine:f.engine,followup_mode:f.followup_mode,shop_id:f.shop_id,schedule_channel:f.schedule_channel,window_minutes:f.window_minutes,scan_start:f.scanRange?.[0]||null,scan_end:f.scanRange?.[1]||null},editingId.value);taskVisible.value=false;await load();ElMessage.success('监听任务已保存，尚未启动')}catch(e){error.value=explain(e)}finally{saving.value=false}}
async function control(task,operation){try{if(operation==='start'&&task.followup_mode==='auto')await ElMessageBox.confirm(`启动后会以当前飞书用户身份，监听群「${task.groups.map(x=>x.name).join('、')}」中「${task.senders.map(x=>x.name).join('、')}」的直播意向；缺失信息时自动私聊原发送人，每条意向追问一次。`,'启动自动私聊任务',{confirmButtonText:'启动自动跟进',cancelButtonText:'取消'});await operationsApi.agentAction(task.id,operation);await load();ElMessage.success(operation==='once'?'已开始试跑，仅生成草稿':operation==='start'?'监听已启动':'监听已停止')}catch(e){if(e!=='cancel'&&e!=='close')error.value=explain(e)}}
async function sendQuestion(item){try{await ElMessageBox.confirm(`收件人：${item.sender_name}（${item.sender_id}）\n身份：当前飞书用户\n\n${item.question_text}`,'确认私聊追问',{confirmButtonText:'确认发送',cancelButtonText:'取消'});sending.value=item.id;const result=await operationsApi.agentFollowup(item.id,item.question_text);await load();if(result.question_status==='queued')ElMessage.success('待办已进入队列，Agent将按目标执行');else if(result.question_status==='sent')ElMessage.success('追问已发送');else ElMessage.warning(result.question_error||'发送结果待核实')}catch(e){if(e!=='cancel'&&e!=='close')error.value=explain(e)}finally{sending.value=''}}
async function showTrace(kind,id){try{trace.value=await (kind==='window'?operationsApi.agentWindow(id):operationsApi.agentNode(id));traceKind.value=kind;traceVisible.value=true}catch(e){error.value=explain(e)}}
async function approveNode(node){try{const data=await operationsApi.agentNode(node.id);await ElMessageBox.confirm(`以当前飞书用户身份执行待办。收件人：${data.todo.sender_name}（${data.todo.sender_id}）\n目标：${data.goal.missing.join('、')||'核验并写入正式排期'}\n\n${data.todo.question_text||'信息已齐全，执行后写入正式排期。'}`,'确认执行待办',{confirmButtonText:'执行待办',cancelButtonText:'取消'});await operationsApi.agentFollowup(node.todo_id,data.todo.question_text||'');await load()}catch(e){if(e!=='cancel'&&e!=='close')error.value=explain(e)}}
async function retryNode(node){try{await operationsApi.agentRetry(node.id);await load();ElMessage.success('已重新调度当前节点，原进度会保留')}catch(e){error.value=explain(e)}}
function inspect(item){detail.value=JSON.parse(JSON.stringify(item));detailProducts.value=item.analysis.products.join('\n');detailVisible.value=true}
async function saveDetail(){saving.value=true;try{const analysis={...detail.value.analysis,products:detailProducts.value.split('\n').map(x=>x.trim()).filter(Boolean)};await operationsApi.agentReview(detail.value.id,{analysis});detailVisible.value=false;await load()}catch(e){error.value=explain(e)}finally{saving.value=false}}
async function ignore(item){try{await operationsApi.agentReview(item.id,{ignore:true});await load()}catch(e){error.value=explain(e)}}
async function preview(){previewing.value=true;try{sampleResult.value=await operationsApi.agentPreview({text:sample.value,year:new Date().getFullYear(),engine:sampleEngine.value})}catch(e){error.value=explain(e)}finally{previewing.value=false}}
onMounted(()=>{load();timer=setInterval(()=>{if(!document.hidden)load(true)},3000)})
onUnmounted(()=>{disposed=true;clearInterval(timer);Object.values(searchTimers).forEach(clearTimeout);searchIds.group++;searchIds.person++})
</script>
<style scoped>
.original-message{border:1px solid #e4e7ec;border-radius:8px;padding:14px;margin:12px 0}.original-meta{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:13px}.original-meta time,.original-meta small{color:#667085;overflow-wrap:anywhere}.document-snapshot{padding:12px;border:1px solid #dbeafe;margin:12px 0;border-radius:8px}.document-snapshot summary{cursor:pointer;font-weight:600}

.agent-page{max-width:1600px;margin:auto}.tabs,.heading{display:flex;align-items:center;gap:12px}.heading{justify-content:space-between;margin:18px 0}h1{font-size:26px}h2{font-size:18px}h3{font-size:14px;margin:16px 0}.muted,small{font-size:12px;color:#667085;line-height:1.8}.section{padding:20px;border:1px solid #e4e7ec;border-radius:10px;background:white;margin:18px 0}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.failure{color:#d92d20;font-size:12px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f8fafc;padding:12px;line-height:1.8;font-size:12px;max-height:400px;overflow:auto}.log-list{max-height:450px;overflow:auto}.log{padding:12px 0;border-bottom:1px solid #e4e7ec;display:flex;align-items:center;flex-wrap:wrap;gap:10px;font-size:12px}.log>span{color:#667085}.log details{width:100%}.el-alert{margin:12px 0}
</style>
