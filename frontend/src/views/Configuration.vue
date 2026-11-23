<template>
  <div>
    <h1 class="page-title"><el-icon class="page-title-icon"><Setting /></el-icon>基础配置</h1>
    <p class="page-subtitle">维护平台、渠道、店铺、仓库和大促日历；这些配置会随每次训练固化为快照。</p>

    <el-tabs v-model="activeTab">
      <!-- ── 平台与渠道 ── -->
      <el-tab-pane label="平台与渠道" name="platforms" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">平台列表</span>
            <el-button class="btn-primary" type="primary" size="small" @click="openPlatformDialog()">
              <el-icon><Plus /></el-icon> 新增平台
            </el-button>
          </div>

          <el-table
            :data="configStore.platforms"
            stripe border size="small"
            v-loading="configStore.loading"
            row-key="platform_id"
          >
            <el-table-column prop="platform_id" label="平台 ID" width="140" />
            <el-table-column prop="name" label="名称" width="140" />
            <el-table-column label="维护方式" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.maintenance_mode === 'auto' ? 'success' : 'info'">
                  {{ row.maintenance_mode === 'auto' ? '自动维护' : '手动新增' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="渠道">
              <template #default="{ row }">
                <div style="display:flex;flex-wrap:wrap;gap:4px">
                  <el-tag
                    v-for="ch in row.channels"
                    :key="ch.channel_id"
                    size="small"
                    :type="ch.channel_type === 'event' ? 'warning' : ch.channel_type === 'affiliate' ? 'success' : 'info'"
                  >
                    {{ ch.name || ch.channel_id }}
                    <span style="opacity:.65;font-size:11px;margin-left:2px">
                      {{ ch.channel_type === 'event' ? '直播' : ch.channel_type === 'affiliate' ? '联盟' : '货架' }}
                    </span>
                    <span v-if="ch.include_in_forecast === false" style="color:#f04438;font-size:11px;margin-left:4px">
                      不测算
                    </span>
                    <span style="opacity:.65;font-size:11px;margin-left:4px">
                      {{ ch.maintenance_mode === 'auto' ? '自动' : '手动' }}
                    </span>
                  </el-tag>
                  <span v-if="!row.channels?.length" style="color:#bbb;font-size:12px">暂无渠道</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text @click.stop="openPlatformDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click.stop="deletePlatform(row.platform_id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!configStore.loading && !configStore.platforms.length" description="暂无平台配置" />
        </div>
      </el-tab-pane>

      <!-- ── 店铺 ── -->
      <el-tab-pane label="店铺" name="shops" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">店铺列表</span>
            <el-button class="btn-primary" type="primary" size="small" @click="openShopDialog()">
              <el-icon><Plus /></el-icon> 新增店铺
            </el-button>
          </div>
          <el-table :data="configStore.shops" stripe border size="small" v-loading="configStore.loading">
            <el-table-column prop="shop_id" label="店铺 ID" width="140" />
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="platform_id" label="平台" width="120" />
            <el-table-column label="维护方式" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.maintenance_mode === 'auto' ? 'success' : 'info'">
                  {{ row.maintenance_mode === 'auto' ? '自动维护' : '手动新增' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="campaign_mode" label="大促模式" width="100">
              <template #default="{ row }">
                <el-tag :type="row.campaign_mode === 'on' ? 'success' : row.campaign_mode === 'off' ? 'info' : ''">
                  {{ row.campaign_mode }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="发货仓库" width="140">
              <template #default="{ row }">
                <span v-if="row.binding_type === 'single' && row.warehouse_id">
                  {{ warehouseName(row.warehouse_id) }}
                </span>
                <span v-else style="color:#bbb;font-size:12px">多仓路由</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text @click="openShopDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteShop(row.shop_id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- ── 仓库 ── -->
      <el-tab-pane label="仓库" name="warehouses" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">仓库列表</span>
            <el-button class="btn-primary" type="primary" size="small" @click="openWarehouseDialog()">
              <el-icon><Plus /></el-icon> 新增仓库
            </el-button>
          </div>
          <el-table :data="configStore.warehouses" stripe border size="small" v-loading="configStore.loading">
            <el-table-column prop="warehouse_id" label="仓库 ID" width="140" />
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="region" label="区域" width="100" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text @click="openWarehouseDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteWarehouse(row.warehouse_id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- ── 大促日历 ── -->
      <el-tab-pane label="大促日历" name="calendar" lazy>
        <div class="card">
          <div class="card-header">
            <span class="card-title">大促日历</span>
            <el-button class="btn-primary" type="primary" size="small" @click="openCalendarDialog()">
              <el-icon><Plus /></el-icon> 新增大促
            </el-button>
          </div>
          <el-table :data="configStore.calendar" stripe border size="small" v-loading="configStore.loading">
            <el-table-column prop="name" label="大促名称" />
            <el-table-column prop="campaign_type" label="类型" width="100" />
            <el-table-column prop="start_date" label="开始日期" width="130" />
            <el-table-column prop="end_date" label="结束日期" width="130" />
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text @click="openCalendarDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteCalendar(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ── 平台对话框 ── -->
    <el-dialog
      v-model="platformDialogVisible"
      :title="platformIsEdit ? '编辑平台' : '新增平台'"
      width="760px"
      destroy-on-close
    >
      <el-form :model="platformForm" label-width="80px">
        <el-form-item label="平台 ID" required>
          <el-input v-model="platformForm.platform_id" :disabled="platformIsEdit" placeholder="如 douyin" />
        </el-form-item>
        <el-form-item label="平台名称" required>
          <el-input v-model="platformForm.name" placeholder="如 抖音" />
        </el-form-item>
        <el-form-item label="渠道列表">
          <div style="width:100%;font-size:12px;color:#667085;margin-bottom:8px">
            “测算”关闭后，该渠道不参加模型训练和 WAPE 比较；训练完成后仍会生成 0 单排期，可在预测表中手动填写。
          </div>
          <div
            v-for="(ch, idx) in platformForm.channels"
            :key="idx"
            style="display:flex;gap:8px;margin-bottom:8px;align-items:center"
          >
            <el-input v-model="ch.channel_id" placeholder="渠道 ID" style="width:140px" />
            <el-input v-model="ch.name" placeholder="渠道名称" style="width:130px" />
            <el-select v-model="ch.channel_type" style="width:130px" placeholder="渠道类型">
              <el-option label="稳定流量（stable）" value="stable" />
              <el-option label="直播事件（event）" value="event" />
              <el-option label="联盟达人（affiliate）" value="affiliate" />
            </el-select>
            <el-switch
              v-model="ch.include_in_forecast"
              title="是否加入模型训练与 WAPE 评估"
              inline-prompt
              active-text="测算"
              inactive-text="不测"
              style="width:76px"
            />
            <el-button
              size="small" type="danger" plain circle
              @click="platformForm.channels.splice(idx, 1)"
            >
              <el-icon><Minus /></el-icon>
            </el-button>
          </div>
          <el-button size="small" @click="platformForm.channels.push({ channel_id: '', name: '', channel_type: 'stable', include_in_forecast: true, maintenance_mode: 'manual' })">
            <el-icon><Plus /></el-icon> 添加渠道
          </el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="platformDialogVisible = false">取消</el-button>
        <el-button class="btn-primary" type="primary" :loading="submitting" @click="submitPlatform">确定</el-button>
      </template>
    </el-dialog>

    <!-- ── 店铺对话框 ── -->
    <el-dialog
      v-model="shopDialogVisible"
      :title="shopIsEdit ? '编辑店铺' : '新增店铺'"
      width="480px"
      destroy-on-close
    >
      <el-form :model="shopForm" label-width="90px">
        <el-form-item label="店铺 ID" required>
          <el-input v-model="shopForm.shop_id" :disabled="shopIsEdit" />
        </el-form-item>
        <el-form-item label="店铺名称"><el-input v-model="shopForm.name" /></el-form-item>
        <el-form-item label="所属平台">
          <el-select v-model="shopForm.platform_id" clearable style="width:100%">
            <el-option
              v-for="p in configStore.platforms"
              :key="p.platform_id"
              :label="p.name"
              :value="p.platform_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="大促模式">
          <el-select v-model="shopForm.campaign_mode" style="width:100%">
            <el-option label="自动（auto）" value="auto" />
            <el-option label="参与（on）" value="on" />
            <el-option label="不参与（off）" value="off" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定类型">
          <el-radio-group v-model="shopForm.binding_type">
            <el-radio value="multi">多仓路由</el-radio>
            <el-radio value="single">指定单仓</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="指定仓库" v-if="shopForm.binding_type === 'single'">
          <el-select v-model="shopForm.warehouse_id" clearable style="width:100%">
            <el-option
              v-for="w in configStore.warehouses"
              :key="w.warehouse_id"
              :label="w.name || w.warehouse_id"
              :value="w.warehouse_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker
            v-model="shopForm.effective_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shopDialogVisible = false">取消</el-button>
        <el-button class="btn-primary" type="primary" :loading="submitting" @click="submitShop">确定</el-button>
      </template>
    </el-dialog>

    <!-- ── 仓库对话框 ── -->
    <el-dialog
      v-model="warehouseDialogVisible"
      :title="warehouseIsEdit ? '编辑仓库' : '新增仓库'"
      width="400px"
      destroy-on-close
    >
      <el-form :model="warehouseForm" label-width="90px">
        <el-form-item label="仓库 ID" required>
          <el-input v-model="warehouseForm.warehouse_id" :disabled="warehouseIsEdit" />
        </el-form-item>
        <el-form-item label="仓库名称"><el-input v-model="warehouseForm.name" /></el-form-item>
        <el-form-item label="所在区域"><el-input v-model="warehouseForm.region" /></el-form-item>
        <el-form-item label="是否启用">
          <el-switch
            v-model="warehouseForm.is_active"
            active-text="启用"
            inactive-text="停用"
            active-color="#12b76a"
            inactive-color="#f5222d"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="warehouseDialogVisible = false">取消</el-button>
        <el-button class="btn-primary" type="primary" :loading="submitting" @click="submitWarehouse">确定</el-button>
      </template>
    </el-dialog>

    <!-- ── 大促日历对话框（新增 + 编辑）── -->
    <el-dialog
      v-model="calendarDialogVisible"
      :title="calendarIsEdit ? '编辑大促' : '新增大促'"
      width="420px"
      destroy-on-close
    >
      <el-form :model="calendarForm" label-width="90px">
        <el-form-item label="大促名称" required>
          <el-input v-model="calendarForm.name" />
        </el-form-item>
        <el-form-item label="大促类型">
          <el-input v-model="calendarForm.campaign_type" placeholder="如 双11 / 618 / 春节" />
        </el-form-item>
        <el-form-item label="开始日期" required>
          <el-date-picker
            v-model="calendarForm.start_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="结束日期" required>
          <el-date-picker
            v-model="calendarForm.end_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="calendarDialogVisible = false">取消</el-button>
        <el-button class="btn-primary" type="primary" :loading="submitting" @click="submitCalendar">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useConfigStore } from '../stores/config.js'
import { ElMessage, ElMessageBox } from 'element-plus'

const configStore = useConfigStore()
const activeTab = ref('platforms')
const submitting = ref(false)

// ── 平台 ──────────────────────────────────────────────────────────────────
const platformDialogVisible = ref(false)
const platformIsEdit = ref(false)
const platformForm = reactive({ platform_id: '', name: '', maintenance_mode: 'manual', channels: [] })

function openPlatformDialog(p = null) {
  platformIsEdit.value = !!p
  if (p) {
    platformForm.platform_id = p.platform_id
    platformForm.name = p.name
    platformForm.maintenance_mode = p.maintenance_mode || 'manual'
    platformForm.channels = (p.channels || []).map(c => ({
      channel_id: c.channel_id,
      name: c.name || '',
      channel_type: c.channel_type || 'stable',
      include_in_forecast: c.include_in_forecast !== false,
      maintenance_mode: c.maintenance_mode || 'manual',
    }))
  } else {
    platformForm.platform_id = ''
    platformForm.name = ''
    platformForm.maintenance_mode = 'manual'
    platformForm.channels = []
  }
  platformDialogVisible.value = true
}

async function submitPlatform() {
  const channelIds = platformForm.channels
    .map(channel => channel.channel_id.trim())
    .filter(Boolean)
  const normalizedIds = channelIds.map(id => id.toLocaleLowerCase())
  const duplicateIndex = normalizedIds.findIndex((id, index) => normalizedIds.indexOf(id) !== index)
  const duplicateId = duplicateIndex >= 0 ? channelIds[duplicateIndex] : null
  if (duplicateId) {
    ElMessage.warning(`渠道 ID “${duplicateId}”重复，请修改后保存`)
    return
  }

  submitting.value = true
  try {
    const data = {
      platform_id: platformForm.platform_id,
      name: platformForm.name,
      maintenance_mode: platformForm.maintenance_mode,
      channels: platformForm.channels.filter(c => c.channel_id),
    }
    if (platformIsEdit.value) {
      await configStore.updatePlatform(platformForm.platform_id, data)
    } else {
      await configStore.createPlatform(data)
    }
    platformDialogVisible.value = false
  } finally {
    submitting.value = false
  }
}

async function deletePlatform(id) {
  await ElMessageBox.confirm(`确定删除平台 ${id} 及其所有渠道？`, '警告', { type: 'warning' })
  await configStore.deletePlatform(id)
}

// ── 店铺 ──────────────────────────────────────────────────────────────────
const shopDialogVisible = ref(false)
const shopIsEdit = ref(false)
const shopForm = reactive({
  shop_id: '', name: '', platform_id: '', campaign_mode: 'auto',
  binding_type: 'multi', warehouse_id: null, effective_date: null,
  maintenance_mode: 'manual',
})

function openShopDialog(s = null) {
  shopIsEdit.value = !!s
  if (s) {
    shopForm.shop_id = s.shop_id
    shopForm.name = s.name || ''
    shopForm.platform_id = s.platform_id || ''
    shopForm.campaign_mode = s.campaign_mode || 'auto'
    shopForm.binding_type = s.binding_type || 'multi'
    shopForm.warehouse_id = s.warehouse_id || null
    shopForm.effective_date = s.effective_date || null
    shopForm.maintenance_mode = s.maintenance_mode || 'manual'
  } else {
    shopForm.shop_id = ''
    shopForm.name = ''
    shopForm.platform_id = ''
    shopForm.campaign_mode = 'auto'
    shopForm.binding_type = 'multi'
    shopForm.warehouse_id = null
    shopForm.effective_date = null
    shopForm.maintenance_mode = 'manual'
  }
  shopDialogVisible.value = true
}

function warehouseName(wid) {
  const w = configStore.warehouses.find(x => x.warehouse_id === wid)
  return w ? (w.name || wid) : wid
}

async function submitShop() {
  submitting.value = true
  try {
    const data = { ...shopForm }
    if (shopIsEdit.value) {
      await configStore.updateShop(shopForm.shop_id, data)
    } else {
      await configStore.createShop(data)
    }
    shopDialogVisible.value = false
  } finally {
    submitting.value = false
  }
}

async function deleteShop(id) {
  await ElMessageBox.confirm(`确定删除店铺 ${id}？`, '警告', { type: 'warning' })
  await configStore.deleteShop(id)
}

// ── 仓库 ──────────────────────────────────────────────────────────────────
const warehouseDialogVisible = ref(false)
const warehouseIsEdit = ref(false)
const warehouseForm = reactive({ warehouse_id: '', name: '', region: '', is_active: true })

function openWarehouseDialog(w = null) {
  warehouseIsEdit.value = !!w
  if (w) {
    warehouseForm.warehouse_id = w.warehouse_id
    warehouseForm.name = w.name || ''
    warehouseForm.region = w.region || ''
    warehouseForm.is_active = w.is_active !== false  // null/undefined 也视为启用
  } else {
    warehouseForm.warehouse_id = ''
    warehouseForm.name = ''
    warehouseForm.region = ''
    warehouseForm.is_active = true
  }
  warehouseDialogVisible.value = true
}

async function submitWarehouse() {
  submitting.value = true
  try {
    const data = { ...warehouseForm }
    if (warehouseIsEdit.value) {
      await configStore.updateWarehouse(warehouseForm.warehouse_id, data)
    } else {
      await configStore.createWarehouse(data)
    }
    warehouseDialogVisible.value = false
  } finally {
    submitting.value = false
  }
}

async function deleteWarehouse(id) {
  await ElMessageBox.confirm(`确定删除仓库 ${id}？`, '警告', { type: 'warning' })
  await configStore.deleteWarehouse(id)
}

// ── 大促日历 ──────────────────────────────────────────────────────────────
const calendarDialogVisible = ref(false)
const calendarIsEdit = ref(false)
const calendarEditId = ref(null)
const calendarForm = reactive({ name: '', campaign_type: '', start_date: '', end_date: '' })

function openCalendarDialog(c = null) {
  calendarIsEdit.value = !!c
  calendarEditId.value = c?.id ?? null
  if (c) {
    calendarForm.name = c.name || ''
    calendarForm.campaign_type = c.campaign_type || ''
    calendarForm.start_date = c.start_date || ''
    calendarForm.end_date = c.end_date || ''
  } else {
    calendarForm.name = ''
    calendarForm.campaign_type = ''
    calendarForm.start_date = ''
    calendarForm.end_date = ''
  }
  calendarDialogVisible.value = true
}

async function submitCalendar() {
  submitting.value = true
  try {
    const data = { ...calendarForm }
    if (calendarIsEdit.value) {
      await configStore.updateCalendar(calendarEditId.value, data)
    } else {
      await configStore.createCalendar(data)
    }
    calendarDialogVisible.value = false
  } finally {
    submitting.value = false
  }
}

async function deleteCalendar(id) {
  await ElMessageBox.confirm('确定删除该大促记录？', '警告', { type: 'warning' })
  await configStore.deleteCalendar(id)
}

onMounted(() => configStore.fetchAll())
</script>
