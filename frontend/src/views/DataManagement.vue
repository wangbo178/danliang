<template>
  <div>
    <h1 class="page-title"><el-icon class="page-title-icon"><FolderOpened /></el-icon>数据管理</h1>
    <p class="page-subtitle">渠道、仓库与抖音主播历史直接查询只读数据库；直播排期、佣金和预售历史继续单独维护。</p>

    <div v-if="persistentUploadError" class="persistent-upload-error">
      <div class="persistent-upload-error__header">
        <div>
          <strong>{{ dataTypeLabels[persistentUploadError.type] || '数据' }}{{ persistentUploadError.stage }}失败</strong>
          <span>{{ persistentUploadError.time }}</span>
        </div>
        <div class="persistent-upload-error__actions">
          <el-button size="small" type="danger" plain @click="copyUploadError">复制错误</el-button>
          <el-button size="small" @click="clearUploadError">关闭</el-button>
        </div>
      </div>
      <pre>{{ persistentUploadError.message }}</pre>
      <div class="persistent-upload-error__hint">该错误会一直保留，直到关闭、重新选择文件或操作成功。</div>
    </div>

    <el-tabs v-model="activeTab" class="data-tabs">
      <el-tab-pane label="数据库数据" name="source"><DirectSourceData /></el-tab-pane>
      <!-- ── 渠道订单 ── -->

      <!-- ── 仓库订单 ── -->

      <!-- ── 直播排期 ── -->
      <el-tab-pane label="直播排期" name="schedule" lazy>
        <div class="feishu-sync-card">
          <div class="sync-icon">飞</div>
          <div class="sync-main">
            <div class="sync-title">
              飞书排期自动同步
              <el-tag :type="feishuSyncStatus.last_error ? 'danger' : feishuSyncStatus.last_success_at ? 'success' : 'warning'" size="small">
                {{ feishuSyncStatus.running ? '同步中' : !feishuSyncStatus.app_id_configured || !feishuSyncStatus.app_secret_configured ? '待配置 App ID' : !feishuSyncStatus.binding_count ? '待绑定表格' : feishuSyncStatus.last_error ? '同步异常' : feishuSyncStatus.last_success_at ? '自动同步中' : '等待首次同步' }}
              </el-tag>
            </div>
            <p>通过飞书开放平台 App ID 应用身份，每 {{ syncIntervalText }} 读取 {{ feishuSyncStatus.binding_count || 0 }} 个已启用表格。同步时按绑定补齐店铺，只覆盖各表自己的未来记录。</p>
            <div class="sync-meta">
              <span>鉴权：App ID {{ feishuSyncStatus.app_id_masked || '未配置' }}</span>
              <span>最近成功：{{ formatSyncTime(feishuSyncStatus.last_success_at) }}</span>
              <span>飞书读取 {{ Number(feishuSyncStatus.rows_read || 0).toLocaleString('zh-CN') }} 条</span>
              <span>未来有效 {{ Number(feishuSyncStatus.future_rows || 0).toLocaleString('zh-CN') }} 条</span>
              <span v-if="feishuSyncStatus.last_error" class="sync-error">{{ feishuSyncStatus.last_error }}</span>
            </div>
          </div>
          <div class="sync-actions">
            <el-button @click="openFeishuConfig">同步设置</el-button>
            <el-button type="primary" :disabled="!feishuCredentialsReady" :loading="feishuSyncing || feishuSyncStatus.running" @click="syncFeishuNow">
              <el-icon><Refresh /></el-icon>立即同步
            </el-button>
          </div>
        </div>
        <div class="card feishu-binding-card">
          <div class="binding-header">
            <div>
              <h3>云表格与店铺绑定</h3>
              <p>飞书表格无需填写店铺。系统同步时会将该表全部排期归到这里绑定的店铺；新增其他店铺时继续添加绑定即可。</p>
            </div>
            <el-button type="primary" @click="openBindingDialog()">新增绑定</el-button>
          </div>
          <el-table :data="feishuBindings" border stripe v-loading="feishuBindingsLoading" empty-text="尚未绑定飞书表格">
            <el-table-column prop="name" label="绑定名称" min-width="140">
              <template #default="{ row }">{{ row.name || '未命名' }}</template>
            </el-table-column>
            <el-table-column label="店铺" min-width="170">
              <template #default="{ row }">{{ row.shop_name || row.shop_id }} <small>({{ row.shop_id }})</small></template>
            </el-table-column>
            <el-table-column prop="base_token" label="Base Token" min-width="220" show-overflow-tooltip />
            <el-table-column prop="table_id" label="Table ID" min-width="190" show-overflow-tooltip />
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openBindingDialog(row)">编辑</el-button>
                <el-button link type="danger" @click="removeBinding(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="card">
          <div class="upload-header">
            <span class="upload-tip">最新模板支持店铺、日期、平台、渠道、主播、场次、预估目标、预估单量、MCN及预售字段。渠道可直接填写实际名称。</span>
            <div class="schedule-upload-actions">
              <el-button size="small" type="primary" plain @click="openManualSchedule">手动新增排期</el-button>
              <el-button size="small" @click="downloadTemplate('schedule')">
                <el-icon><Download /></el-icon> 下载最新模板
              </el-button>
            </div>
          </div>
          <el-upload
            drag :auto-upload="false"
            :on-change="(f) => handleFileChange(f, 'schedule')"
            accept=".csv,.xlsx,.xls" :show-file-list="false"
          >
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽文件到此处或 <em>点击上传</em></div>
          </el-upload>
          <template v-if="scheduleData">
            <div class="preview-heading" style="margin-top:16px"><span>快速预览（前 20 行）</span><el-button size="small" type="primary" plain @click="openImportPreview('schedule')">预览全部明细</el-button></div>
            <el-table :data="scheduleData.preview" size="small" stripe border max-height="300" style="margin:16px 0">
              <el-table-column v-for="col in previewCols.schedule" :key="col" :prop="col" :label="col" width="130" />
            </el-table>
            <SaveActionBar
              :status="saveStates.schedule.status" :receipt="saveStates.schedule.receipt"
              :record-count="scheduleData.rows" @save="saveData('schedule')" @reset="resetUpload('schedule')"
            />
          </template>
        </div>
      </el-tab-pane>

      <!-- ── 联盟佣金 ── -->
      <el-tab-pane label="联盟佣金" name="commission" lazy>
        <div class="card">
          <div class="upload-header">
            <span class="upload-tip">必须包含 shop_id / month（YYYY-MM）/ commission_rate / active_affiliates 列</span>
            <el-button size="small" @click="downloadTemplate('commission')">
              <el-icon><Download /></el-icon> 下载模板
            </el-button>
          </div>
          <el-upload
            drag :auto-upload="false"
            :on-change="(f) => handleFileChange(f, 'commission')"
            accept=".csv,.xlsx,.xls" :show-file-list="false"
          >
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽文件到此处或 <em>点击上传</em></div>
          </el-upload>
          <template v-if="commissionData">
            <div class="preview-heading" style="margin-top:16px"><span>快速预览（前 20 行）</span><el-button size="small" type="primary" plain @click="openImportPreview('commission')">预览全部明细</el-button></div>
            <el-table :data="commissionData.preview" size="small" stripe border max-height="300" style="margin:16px 0">
              <el-table-column v-for="col in previewCols.commission" :key="col" :prop="col" :label="col" width="130" />
            </el-table>
            <SaveActionBar
              :status="saveStates.commission.status" :receipt="saveStates.commission.receipt"
              :record-count="commissionData.rows" @save="saveData('commission')" @reset="resetUpload('commission')"
            />
          </template>
        </div>
      </el-tab-pane>

      <!-- ── 达人直播数据 ── -->

      <!-- ── 预售历史 ── -->
      <el-tab-pane label="预售历史" name="presale" lazy>
        <div class="card">
          <div class="upload-header">
            <span class="upload-tip">支持列：直播时间 / 尾款时间 / 店铺ID / 单量；是否预售单（选填）</span>
            <el-button size="small" @click="downloadTemplate('presale')">
              <el-icon><Download /></el-icon> 下载模板
            </el-button>
          </div>
          <el-upload
            drag :auto-upload="false"
            :on-change="(f) => handlePresaleFileChange(f)"
            accept=".xlsx,.xls,.csv" :show-file-list="false"
          >
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽预售数据文件到此处或 <em>点击上传</em></div>
          </el-upload>

          <template v-if="presaleData">
            <!-- 摘要 -->
            <el-row :gutter="16" style="margin:16px 0 8px">
              <el-col :span="6">
                <div class="stat-box">
                  <div class="stat-num">{{ presaleData.rows }}</div>
                  <div class="stat-label">总行数</div>
                </div>
              </el-col>
              <el-col :span="6">
                <div class="stat-box">
                  <div class="stat-num" :style="presaleData.nan_shop_count > 0 ? 'color:#ff4d4f' : ''">
                    {{ presaleData.nan_shop_count }}
                  </div>
                  <div class="stat-label">缺失店铺行数</div>
                </div>
              </el-col>
              <el-col :span="12">
                <div class="stat-box">
                  <div style="font-size:12px;color:#666;line-height:1.8">
                    <span v-for="s in presaleData.campaign_summary" :key="s.campaign_type" style="margin-right:12px">
                      {{ s.campaign_type }}: {{ s.count }}行 / {{ (s.sum || 0).toLocaleString('zh-CN') }}单
                    </span>
                  </div>
                  <div class="stat-label">活动类型分布</div>
                </div>
              </el-col>
            </el-row>

            <!-- 缺失店铺警告 -->
            <el-alert
              v-if="presaleData.nan_shop_count > 0"
              :title="`有 ${presaleData.nan_shop_count} 行店铺信息缺失，请在预览表格中补全（红色行）`"
              type="warning" show-icon style="margin-bottom:12px"
            />

            <!-- 预览表格 -->
            <div class="preview-heading"><span>快速预览（前 20 行）</span><el-button size="small" type="primary" plain @click="openImportPreview('presale')">预览全部明细</el-button></div>
            <el-table
              :data="presalePreviewRows"
              size="small" stripe border max-height="360"
              style="margin-bottom:16px"
              :row-class-name="presaleRowClass"
            >
              <el-table-column prop="broadcast_date" label="直播时间" width="110" />
              <el-table-column prop="payment_date" label="尾款时间" width="110" />
              <el-table-column prop="shop_id" label="店铺 ID" width="130">
                <template #default="{ row, $index }">
                  <template v-if="row._shop_missing">
                    <el-select
                      v-model="presaleShopFix[$index]"
                      placeholder="请选择店铺"
                      size="small" style="width:140px"
                    >
                      <el-option
                        v-for="s in presaleData.shops"
                        :key="s.shop_id"
                        :label="`${s.name}（${s.shop_id}）`"
                        :value="s.shop_id"
                      />
                    </el-select>
                  </template>
                  <template v-else>{{ row.shop_id }}</template>
                </template>
              </el-table-column>
              <el-table-column prop="channel" label="渠道" width="90" />
              <el-table-column prop="campaign_type" label="活动类型" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="campaignTagType(row.campaign_type)">{{ row.campaign_type }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="order_count" label="单量" align="right" width="100">
                <template #default="{ row }">{{ (row.order_count || 0).toLocaleString('zh-CN') }}</template>
              </el-table-column>
            </el-table>

            <!-- 保存按钮 -->
            <SaveActionBar
              :status="saveStates.presale.status" :receipt="saveStates.presale.receipt"
              :record-count="presaleData.rows"
              :disabled="presaleHasUnfixedShops"
              :disabled-reason="`请先补全全部店铺信息（共 ${presaleData.nan_shop_count} 行缺失）`"
              @save="savePresale" @reset="resetUpload('presale')"
            />
          </template>
        </div>
      </el-tab-pane>

      <!-- ── 历史看板 ── -->
      <el-tab-pane label="历史看板" name="dashboard" lazy>
        <div class="card" v-loading="dashLoading">
          <!-- 筛选栏 -->
          <div class="card-header" style="margin-bottom:16px">
            <span class="card-title">分仓历史单量</span>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <el-button size="small" @click="setDashQuickRange(7)">近7天</el-button>
              <el-button size="small" @click="setDashQuickRange(30)">近30天</el-button>
              <el-button size="small" @click="setDashQuickRange(90)">近90天</el-button>
              <el-date-picker
                v-model="dashRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                value-format="YYYY-MM-DD"
                size="small"
                style="width:240px"
                :disabled-date="() => false"
              />
              <el-button class="btn-primary" size="small" type="primary" @click="loadDashboard">查询</el-button>
              <el-button size="small" @click="resetDashRange">全部</el-button>
            </div>
          </div>

          <!-- 无数据提示 -->
          <el-empty v-if="!dashLoading && dashData.dates.length === 0" description="暂无仓库历史订单数据" />

          <template v-else>
            <!-- KPI 小卡片 -->
            <el-row :gutter="16" style="margin-bottom:20px">
              <el-col :span="6" v-for="kpi in dashKpis" :key="kpi.label">
                <div class="dash-kpi">
                  <div class="dash-kpi-val" :style="{ color: kpi.color }">
                    {{ kpi.value.toLocaleString('zh-CN') }}
                  </div>
                  <div class="dash-kpi-label">{{ kpi.label }}</div>
                </div>
              </el-col>
            </el-row>

            <!-- 分仓堆叠柱状图 + 汇总折线 -->
            <v-chart :option="dashChartOption" style="height:380px;margin-bottom:24px" autoresize />

            <!-- 每日明细表（日期 × 仓库 + 合计） -->
            <div style="font-size:13px;font-weight:600;color:#333;margin-bottom:10px">每日明细</div>
            <el-table
              :data="dashTableData"
              stripe border size="small"
              max-height="420"
              show-summary
              :summary-method="dashSummary"
            >
              <el-table-column prop="date" label="日期" width="115" fixed sortable />
              <el-table-column
                v-for="wh in dashData.warehouses"
                :key="wh"
                :prop="wh"
                :label="wh"
                align="right"
                width="110"
              >
                <template #default="{ row }">
                  {{ row[wh] ? row[wh].toLocaleString('zh-CN') : '—' }}
                </template>
              </el-table-column>
              <el-table-column prop="total" label="合计" align="right" width="110" fixed="right" sortable>
                <template #default="{ row }">
                  <span style="font-weight:600;color:#2563eb">{{ row.total.toLocaleString('zh-CN') }}</span>
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top:8px;color:#999;font-size:12px">共 {{ dashData.dates.length }} 天</div>
          </template>
        </div>
      </el-tab-pane>

      <!-- ── 数据概览 & 删除 ── -->
      <el-tab-pane label="数据概览" name="overview" lazy>
        <!-- 统计卡片 -->
        <div class="card" v-loading="overviewLoading">
          <div class="card-header">
            <span class="card-title">数据统计</span>
            <el-button size="small" text @click="loadOverview">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
          <el-row :gutter="20" style="margin-bottom:20px">
            <el-col :span="6">
              <div class="stat-box">
                <div class="stat-num">{{ overviewData.channel_orders || 0 }}</div>
                <div class="stat-label">渠道订单</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-box">
                <div class="stat-num">{{ overviewData.warehouse_orders || 0 }}</div>
                <div class="stat-label">仓库订单</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-box">
                <div class="stat-num">{{ overviewData.live_schedules || 0 }}</div>
                <div class="stat-label">直播排期</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-box">
                <div class="stat-num">{{ overviewData.commissions || 0 }}</div>
                <div class="stat-label">联盟佣金</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-box">
                <div class="stat-num">{{ overviewData.anchor_live_orders || 0 }}</div>
                <div class="stat-label">达人直播</div>
              </div>
            </el-col>
          </el-row>
          <div style="font-size:13px;color:#666">
            渠道订单日期范围：{{ overviewData.date_range?.min || '—' }} ~ {{ overviewData.date_range?.max || '—' }}
          </div>
        </div>

        <!-- 批次管理 -->
        <div class="card" v-loading="batchLoading">
          <div class="card-header">
            <span class="card-title">上传批次管理</span>

          </div>

          <el-tabs v-model="batchTab" type="card" size="small">
            <el-tab-pane
              v-for="(label, key) in batchTypeLabels"
              :key="key"
              :label="label"
              :name="key"
            >
              <el-table
                :data="batches[key] || []"
                size="small"
                stripe
                border
                style="margin-top:8px"
              >
                <el-table-column prop="batch_id" label="批次 ID" width="130" />
                <el-table-column prop="count" label="记录数" width="90" />
                <el-table-column prop="source_filename" label="文件名" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.source_filename || '历史批次' }}</template>
                </el-table-column>
                <el-table-column label="数据日期范围" width="210">
                  <template #default="{ row }">
                    {{ row.data_start && row.data_end ? `${row.data_start} 至 ${row.data_end}` : '—' }}
                  </template>
                </el-table-column>
                <el-table-column prop="created_at" label="上传时间" width="175" />
                <el-table-column label="时间来源" width="105" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.time_source === 'exact' ? 'success' : 'warning'" size="small">
                      {{ row.time_source === 'exact' ? '准确记录' : '历史推断' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="150" fixed="right">
                  <template #default="{ row }">
                    <el-button
                      size="small"
                      type="primary"
                      text
                      @click="previewBatch(key, row.batch_id)"
                    >
                      预览
                    </el-button>
                    <el-button
                      size="small"
                      type="danger"
                      text
                      :loading="deletingKey === `${key}:${row.batch_id}`"
                      :disabled="Boolean(deletingKey)"
                      @click="deleteBatch(key, row.batch_id, row.count)"
                    >
                      删除
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div
                v-if="(batches[key] || []).length === 0"
                style="text-align:center;padding:24px 0;color:#bbb;font-size:13px"
              >
                暂无数据
              </div>

              <!-- 清空全部 -->
              <div style="margin-top:12px;text-align:right">
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="!(batches[key] || []).length"
                  :loading="deletingKey === `${key}:all`"
                  @click="deleteAll(key, label)"
                >
                  清空全部 {{ label }}
                </el-button>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </el-tab-pane>

      <!-- ── 已入库原始明细 ── -->
      <el-tab-pane label="排期及补充数据明细" name="details" lazy>
        <div class="card detail-browser">
          <div class="card-header detail-browser-header">
            <div>
              <span class="card-title">排期及补充数据明细</span>
              <div class="detail-hint">按批次读取原始记录，使用索引游标翻页，避免深分页变慢。</div>
            </div>
            <div class="query-performance" v-if="detailQueryMs != null">
              <el-tag :type="detailQueryMs <= 1 ? 'success' : detailQueryMs <= 5 ? 'warning' : 'danger'" effect="light">
                数据库往返 {{ detailQueryMs.toFixed(3) }} ms
              </el-tag>
              <span>接口 {{ detailRequestMs.toFixed(1) }} ms</span>
            </div>
          </div>

          <div class="detail-filters">
            <el-select v-model="detailType" style="width:150px" @change="handleDetailTypeChange">
              <el-option v-for="(label, key) in batchTypeLabels" :key="key" :label="label" :value="key" />
            </el-select>
            <el-select v-model="detailBatchId" filterable placeholder="请选择上传批次" style="width:220px" @change="handleDetailBatchChange">
              <el-option
                v-for="item in detailBatchOptions"
                :key="item.batch_id"
                :label="`${item.batch_id}（${Number(item.count).toLocaleString('zh-CN')} 条）`"
                :value="item.batch_id"
              />
            </el-select>
            <el-select
              v-model="detailShopId"
              filterable
              clearable
              :loading="detailShopOptionsLoading"
              placeholder="搜索店铺名称或 ID"
              style="width:240px"
              @change="resetAndLoadDetails"
            >
              <el-option
                v-for="shop in detailShopOptions"
                :key="shop.shop_id"
                :label="shop.name ? `${shop.name}（${shop.shop_id}）` : shop.shop_id"
                :value="shop.shop_id"
              />
            </el-select>
            <el-input
              v-if="detailSupportsChannel"
              v-model="detailChannel"
              clearable
              placeholder="渠道（精确）"
              style="width:170px"
              @keyup.enter="resetAndLoadDetails"
            />
            <el-date-picker
              v-if="detailSupportsDate"
              v-model="detailDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              style="width:250px"
            />
            <el-select v-model="detailPageSize" style="width:110px" @change="resetAndLoadDetails">
              <el-option :value="20" label="20 条/页" />
              <el-option :value="50" label="50 条/页" />
              <el-option :value="100" label="100 条/页" />
              <el-option :value="200" label="200 条/页" />
            </el-select>
            <el-button type="primary" :loading="detailLoading" @click="resetAndLoadDetails">查询</el-button>
            <el-button @click="clearDetailFilters">重置</el-button>
          </div>

          <el-alert
            v-if="!detailBatchId"
            type="info"
            title="请选择一个上传批次后查看明细"
            :closable="false"
            show-icon
            style="margin-bottom:14px"
          />
          <el-table
            v-else
            :data="detailRows"
            v-loading="detailLoading"
            border
            stripe
            height="520"
            empty-text="当前条件下没有数据"
          >
            <el-table-column
              v-for="column in detailColumns"
              :key="column"
              :prop="column"
              :label="detailColumnLabels[column] || column"
              :min-width="detailColumnWidth(column)"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <span v-if="column === 'order_count'" class="order-value">{{ Number(row[column] || 0).toLocaleString('zh-CN') }}</span>
                <span v-else-if="column === 'is_sample'">{{ row[column] ? '是' : '否' }}</span>
                <span v-else>{{ row[column] ?? '—' }}</span>
              </template>
            </el-table-column>
          </el-table>

          <div class="detail-pagination" v-if="detailBatchId">
            <span>第 {{ detailPage }} 页 · 本页 {{ detailRows.length }} 条</span>
            <div>
              <el-button :disabled="detailPage <= 1 || detailLoading" @click="loadPreviousDetailPage">上一页</el-button>
              <el-button type="primary" plain :disabled="!detailHasMore || detailLoading" @click="loadNextDetailPage">下一页</el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="feishuConfigVisible" title="飞书直播排期同步设置" width="560px" destroy-on-close>
      <el-alert type="info" :closable="false" show-icon>
        配置保存在数据库中；App Secret 加密存储且不会回传浏览器。Secret 留空表示保留原值。
      </el-alert>
      <el-form label-position="top" class="feishu-config-form" @submit.prevent>
        <el-form-item label="App ID" required>
          <el-input v-model.trim="feishuConfig.app_id" placeholder="例如 cli_xxxxxxxxxxxxxxxx" autocomplete="off" />
        </el-form-item>
        <el-form-item label="App Secret" :required="!feishuConfig.app_secret_configured">
          <el-input
            v-model="feishuConfig.app_secret" type="password" show-password autocomplete="new-password"
            :placeholder="feishuConfig.app_secret_configured ? '已配置；留空保持不变' : '请输入 App Secret'"
          />
        </el-form-item>
        <div class="feishu-form-grid">
          <el-form-item label="自动同步">
            <el-switch v-model="feishuConfig.enabled" active-text="启用" inactive-text="关闭" />
          </el-form-item>
          <el-form-item label="同步间隔（分钟）">
            <el-input-number v-model="feishuConfig.interval_minutes" :min="1" :max="1440" :step="5" controls-position="right" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="feishuConfigVisible = false">取消</el-button>
        <el-button :disabled="!feishuConfig.app_secret_configured || !feishuBindings.some(item => item.enabled)" :loading="feishuTesting" @click="testFeishuConfig">测试已保存配置</el-button>
        <el-button type="primary" :loading="feishuConfigSaving" @click="saveFeishuConfig">保存并生效</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bindingDialogVisible" :title="bindingForm.id ? '编辑表格店铺绑定' : '新增表格店铺绑定'" width="560px" destroy-on-close>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="绑定名称">
          <el-input v-model.trim="bindingForm.name" placeholder="例如：抖音旗舰店直播排期" />
        </el-form-item>
        <el-form-item label="归属店铺" required>
          <el-select v-model="bindingForm.shop_id" filterable placeholder="选择系统店铺" style="width:100%">
            <el-option v-for="shop in configStore.shops" :key="shop.shop_id" :label="`${shop.name || shop.shop_id} (${shop.shop_id})`" :value="shop.shop_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="Base Token" required>
          <el-input v-model.trim="bindingForm.base_token" placeholder="链接中 /base/ 后面的标识" />
        </el-form-item>
        <el-form-item label="Table ID" required>
          <el-input v-model.trim="bindingForm.table_id" placeholder="例如 tblxxxxxxxx" />
        </el-form-item>
        <el-form-item label="是否参与自动同步">
          <el-switch v-model="bindingForm.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bindingDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="bindingSaving" @click="saveBinding">保存绑定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manualScheduleVisible" title="手动新增直播排期" width="720px" destroy-on-close>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
        渠道可以从基础配置中选择，也可以直接输入新名称。保存后直播增量会归入该渠道并刷新最新预测。
      </el-alert>
      <el-form label-position="top" @submit.prevent>
        <div class="manual-schedule-grid">
          <el-form-item label="店铺" required>
            <el-select v-model="manualScheduleForm.shop_id" filterable placeholder="选择店铺" style="width:100%" @change="onManualScheduleShopChange">
              <el-option v-for="shop in configStore.shops" :key="shop.shop_id" :label="`${shop.name || shop.shop_id} (${shop.shop_id})`" :value="shop.shop_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="开播时间" required>
            <el-date-picker v-model="manualScheduleForm.scheduled_at" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" placeholder="选择日期和时间" style="width:100%" />
          </el-form-item>
          <el-form-item label="平台">
            <el-select v-model="manualScheduleForm.platform" filterable clearable placeholder="选择平台" style="width:100%">
              <el-option v-for="platform in configStore.platforms" :key="platform.platform_id" :label="platform.name || platform.platform_id" :value="platform.platform_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="渠道（可手动输入）" required>
            <el-select v-model.trim="manualScheduleForm.channel" filterable allow-create default-first-option placeholder="选择或输入实际渠道" style="width:100%">
              <el-option v-for="channel in manualScheduleChannelOptions" :key="`${channel.platform_id}-${channel.channel_id}`" :label="channel.name || channel.channel_id" :value="channel.channel_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="主播/达人" required>
            <el-input v-model.trim="manualScheduleForm.anchor_name" placeholder="须与达人直播历史名称一致" />
          </el-form-item>
          <el-form-item label="场次">
            <el-select v-model="manualScheduleForm.session_type" allow-create filterable clearable placeholder="专场、混场或手动输入" style="width:100%">
              <el-option label="专场" value="专场" />
              <el-option label="混场" value="混场" />
            </el-select>
          </el-form-item>
          <el-form-item label="预估单量">
            <el-input-number v-model="manualScheduleForm.expected_orders" :min="0" :precision="0" controls-position="right" style="width:100%" />
          </el-form-item>
          <el-form-item label="预估目标（GMV）">
            <el-input-number v-model="manualScheduleForm.expected_revenue" :min="0" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
          <el-form-item label="MCN">
            <el-input v-model.trim="manualScheduleForm.mcn" placeholder="选填" />
          </el-form-item>
          <el-form-item label="主播档级">
            <el-select v-model="manualScheduleForm.anchor_tier" clearable placeholder="选填" style="width:100%">
              <el-option label="A（头部）" value="A" />
              <el-option label="B（腰部）" value="B" />
              <el-option label="C（尾部）" value="C" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="manualScheduleVisible = false">取消</el-button>
        <el-button type="primary" :loading="manualScheduleSaving" @click="saveManualSchedule">保存并刷新预测</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importPreviewVisible" :title="`${dataTypeLabels[importPreviewType] || ''} · 导入明细预览`" width="92%" top="5vh" destroy-on-close>
      <div class="import-preview-meta">
        <span>共 {{ importPreviewRecords.length.toLocaleString('zh-CN') }} 条</span>
        <span>第 {{ importPreviewPage }} / {{ importPreviewPageCount }} 页</span>
        <span>当前仅预览，点击保存后才会写入数据库</span>
        <span v-if="importPreviewLimited">大文件模式仅展示前 20 行，完整数据将分批保存</span>
      </div>
      <el-table :data="importPreviewPageRows" border stripe height="62vh" empty-text="没有可预览的数据">
        <el-table-column
          v-for="column in importPreviewColumns"
          :key="column"
          :prop="column"
          :label="detailColumnLabels[column] || column"
          :min-width="detailColumnWidth(column)"
          show-overflow-tooltip
        />
      </el-table>
      <template #footer>
        <el-pagination
          v-model:current-page="importPreviewPage"
          v-model:page-size="importPreviewPageSize"
          :total="importPreviewRecords.length"
          :page-sizes="[50, 100, 200, 500]"
          layout="total, sizes, prev, pager, next, jumper"
          background
        />
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import DirectSourceData from '../components/DirectSourceData.vue'
import { dataApi, configApi } from '../api/index.js'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Refresh } from '@element-plus/icons-vue'
import SaveActionBar from '../components/SaveActionBar.vue'
import { useConfigStore } from '../stores/config.js'

const configStore = useConfigStore()

// ── 历史看板 ────────────────────────────────────────────────────────────────
const dashLoading = ref(false)

function _dateStr(d) { return d.toISOString().slice(0, 10) }
function _recentRange(days) {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - (days - 1))
  return [_dateStr(start), _dateStr(end)]
}
const dashRange = ref(_recentRange(7))
const dashData = ref({ dates: [], warehouses: [], series: {}, totals: [], date_range: {} })

const CHART_COLORS = ['#2563eb', '#0ea5e9', '#12b76a', '#f79009', '#7c3aed', '#14b8a6', '#475467', '#e11d48']

const dashKpis = computed(() => {
  const { dates, warehouses, totals } = dashData.value
  if (!dates.length) return []
  const grandTotal = totals.reduce((s, v) => s + v, 0)
  const avgDaily = dates.length ? Math.round(grandTotal / dates.length) : 0
  const maxDay = Math.max(...totals)
  return [
    { label: '累计总单量', value: grandTotal, color: '#2563eb' },
    { label: '日均单量', value: avgDaily, color: '#f79009' },
    { label: '峰值单量', value: maxDay, color: '#12b76a' },
    { label: '仓库数', value: warehouses.length, color: '#0e9384' },
  ]
})

const dashChartOption = computed(() => {
  const { dates, warehouses, series, totals } = dashData.value
  if (!dates.length) return {}
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter(params) {
        let str = `<b>${params[0].axisValue}</b><br/>`
        let barTotal = 0
        for (const p of params) {
          if (p.seriesType === 'bar') {
            str += `${p.marker}${p.seriesName}：${(p.value || 0).toLocaleString('zh-CN')}<br/>`
            barTotal += p.value || 0
          }
        }
        str += `<b>合计：${barTotal.toLocaleString('zh-CN')}</b>`
        return str
      },
    },
    legend: {
      data: [...warehouses, '每日合计'],
      bottom: 0,
      textStyle: { color: '#666', fontSize: 12 },
    },
    grid: { left: 16, right: 16, top: 16, bottom: 56, containLabel: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 30 }],
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#666', fontSize: 11, rotate: 30 },
    },
    yAxis: [
      {
        type: 'value',
        name: '单量',
        axisLabel: { color: '#666', formatter: v => v.toLocaleString('zh-CN') },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
    ],
    series: [
      ...warehouses.map((wh, i) => ({
        name: wh,
        type: 'bar',
        stack: 'wh',
        data: series[wh] || [],
        itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
        barMaxWidth: 40,
      })),
      {
        name: '每日合计',
        type: 'line',
        data: totals,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        itemStyle: { color: '#333' },
        lineStyle: { color: '#333', width: 2, type: 'dashed' },
        z: 10,
      },
    ],
  }
})

const dashTableData = computed(() => {
  const { dates, warehouses, series, totals } = dashData.value
  return dates.map((d, i) => {
    const row = { date: d, total: totals[i] || 0 }
    for (const wh of warehouses) row[wh] = (series[wh] || [])[i] || 0
    return row
  })
})

function dashSummary({ columns }) {
  return columns.map((col, idx) => {
    if (idx === 0) return '合计'
    const key = col.property
    if (!key || key === 'date') return ''
    const sum = dashTableData.value.reduce((s, r) => s + (r[key] || 0), 0)
    return sum.toLocaleString('zh-CN')
  })
}

async function loadDashboard() {
  dashLoading.value = true
  try {
    const params = {}
    if (dashRange.value?.[0]) params.date_start = dashRange.value[0]
    if (dashRange.value?.[1]) params.date_end = dashRange.value[1]
    dashData.value = await dataApi.getWarehouseDaily(params)
  } finally {
    dashLoading.value = false
  }
}

async function resetDashRange() {
  dashRange.value = null
  await loadDashboard()
}

async function setDashQuickRange(days) {
  dashRange.value = _recentRange(days)
  await loadDashboard()
}

const activeTab = ref('source')

// ── 上传状态 ──────────────────────────────────────────────────────────────
const channelData = ref(null)
const warehouseData = ref(null)
const scheduleData = ref(null)
const feishuSyncStatus = ref({})
const feishuSyncing = ref(false)
const feishuConfigVisible = ref(false)
const feishuConfigSaving = ref(false)
const feishuTesting = ref(false)
const feishuConfig = reactive({
  app_id: '', app_secret: '',
  enabled: true, interval_minutes: 15, app_secret_configured: false,
})
const feishuBindings = ref([])
const feishuBindingsLoading = ref(false)
const bindingDialogVisible = ref(false)
const bindingSaving = ref(false)
const bindingForm = reactive({ id: null, name: '', shop_id: '', base_token: '', table_id: '', enabled: true })
const manualScheduleVisible = ref(false)
const manualScheduleSaving = ref(false)
const manualScheduleForm = reactive({
  shop_id: '', scheduled_at: '', platform: '', channel: '', anchor_name: '',
  session_type: '', expected_orders: null, expected_revenue: null, mcn: '', anchor_tier: '',
})
const manualScheduleChannelOptions = computed(() => {
  const channels = configStore.allChannels || []
  if (!manualScheduleForm.platform) return channels
  return channels.filter(item => item.platform_id === manualScheduleForm.platform)
})
const feishuCredentialsReady = computed(() => Boolean(
  feishuSyncStatus.value.app_id_configured && feishuSyncStatus.value.app_secret_configured
  && Number(feishuSyncStatus.value.binding_count || 0) > 0
))
const syncIntervalText = computed(() => {
  const seconds = Number(feishuSyncStatus.value.interval_seconds || 900)
  return seconds >= 3600 ? `${Math.round(seconds / 3600)} 小时` : `${Math.round(seconds / 60)} 分钟`
})

function formatSyncTime(value) {
  if (!value) return '—'
  return new Date(String(value).replace(' ', 'T')).toLocaleString('zh-CN', { hour12: false })
}

async function loadFeishuSyncStatus() {
  try {
    feishuSyncStatus.value = await dataApi.getFeishuScheduleStatus()
  } catch (_) {
    feishuSyncStatus.value = { last_error: '无法读取同步状态' }
  }
}

async function loadFeishuBindings() {
  feishuBindingsLoading.value = true
  try {
    feishuBindings.value = await configApi.listFeishuScheduleBindings()
  } finally {
    feishuBindingsLoading.value = false
  }
}

async function syncFeishuNow() {
  feishuSyncing.value = true
  try {
    const result = await dataApi.syncFeishuSchedule()
    ElMessage.success(result.message || '飞书排期同步完成')
    await Promise.all([loadFeishuSyncStatus(), loadOverview(), loadBatches()])
  } finally {
    feishuSyncing.value = false
  }
}

async function openManualSchedule() {
  await configStore.fetchAll()
  Object.assign(manualScheduleForm, {
    shop_id: '', scheduled_at: '', platform: '', channel: '', anchor_name: '',
    session_type: '', expected_orders: null, expected_revenue: null, mcn: '', anchor_tier: '',
  })
  manualScheduleVisible.value = true
}

function onManualScheduleShopChange(shopId) {
  const shop = configStore.shops.find(item => item.shop_id === shopId)
  if (shop?.platform_id) manualScheduleForm.platform = shop.platform_id
}

async function saveManualSchedule() {
  if (!manualScheduleForm.shop_id || !manualScheduleForm.scheduled_at || !manualScheduleForm.channel || !manualScheduleForm.anchor_name) {
    ElMessage.warning('请填写店铺、开播时间、渠道和主播/达人')
    return
  }
  manualScheduleSaving.value = true
  try {
    const payload = {
      ...manualScheduleForm,
      date: manualScheduleForm.scheduled_at.slice(0, 10),
      event_type: 'live',
      has_live: 1,
    }
    const result = await dataApi.saveSchedule([payload], { source_filename: '页面手动新增' })
    ElMessage.success(result.message || '直播排期已保存')
    manualScheduleVisible.value = false
    await Promise.all([loadFeishuSyncStatus(), loadOverview(), loadBatches()])
  } finally {
    manualScheduleSaving.value = false
  }
}

async function openFeishuConfig() {
  const saved = await configApi.getFeishuScheduleConfig()
  Object.assign(feishuConfig, {
    app_id: saved.app_id || '',
    app_secret: '',
    enabled: saved.enabled !== false,
    interval_minutes: Math.max(1, Math.round(Number(saved.interval_seconds || 900) / 60)),
    app_secret_configured: Boolean(saved.app_secret_configured),
  })
  feishuConfigVisible.value = true
}

function validateFeishuConfig() {
  if (!feishuConfig.app_id) {
    ElMessage.warning('请填写 App ID')
    return false
  }
  if (!feishuConfig.app_secret_configured && !feishuConfig.app_secret.trim()) {
    ElMessage.warning('首次配置必须填写 App Secret')
    return false
  }
  return true
}

async function saveFeishuConfig() {
  if (!validateFeishuConfig()) return
  feishuConfigSaving.value = true
  try {
    const saved = await configApi.saveFeishuScheduleConfig({
      app_id: feishuConfig.app_id,
      app_secret: feishuConfig.app_secret || null,
      enabled: feishuConfig.enabled,
      interval_seconds: Number(feishuConfig.interval_minutes || 15) * 60,
    })
    feishuConfig.app_secret = ''
    feishuConfig.app_secret_configured = Boolean(saved.app_secret_configured)
    ElMessage.success(saved.message || '飞书同步配置已保存')
    feishuConfigVisible.value = false
    await loadFeishuSyncStatus()
  } finally {
    feishuConfigSaving.value = false
  }
}

async function openBindingDialog(row = null) {
  await configStore.fetchAll()
  Object.assign(bindingForm, row ? {
    id: row.id, name: row.name || '', shop_id: row.shop_id,
    base_token: row.base_token, table_id: row.table_id, enabled: row.enabled,
  } : { id: null, name: '', shop_id: '', base_token: '', table_id: '', enabled: true })
  bindingDialogVisible.value = true
}

async function saveBinding() {
  if (!bindingForm.shop_id || !bindingForm.base_token || !bindingForm.table_id) {
    ElMessage.warning('请选择店铺并填写 Base Token、Table ID')
    return
  }
  bindingSaving.value = true
  try {
    const payload = {
      name: bindingForm.name || null, shop_id: bindingForm.shop_id,
      base_token: bindingForm.base_token, table_id: bindingForm.table_id,
      enabled: bindingForm.enabled,
    }
    if (bindingForm.id) await configApi.updateFeishuScheduleBinding(bindingForm.id, payload)
    else await configApi.createFeishuScheduleBinding(payload)
    ElMessage.success('表格与店铺绑定已保存')
    bindingDialogVisible.value = false
    await Promise.all([loadFeishuBindings(), loadFeishuSyncStatus()])
  } finally {
    bindingSaving.value = false
  }
}

async function removeBinding(row) {
  await ElMessageBox.confirm(
    `确定删除“${row.name || row.table_id}”绑定？已同步的排期数据不会删除。`,
    '删除表格绑定',
    { type: 'warning', confirmButtonText: '删除绑定' },
  )
  const result = await configApi.deleteFeishuScheduleBinding(row.id)
  ElMessage.success(result.message || '绑定已删除')
  await Promise.all([loadFeishuBindings(), loadFeishuSyncStatus()])
}

async function testFeishuConfig() {
  feishuTesting.value = true
  try {
    const result = await configApi.testFeishuScheduleConfig()
    ElMessage.success(result.message || '飞书连接成功')
  } finally {
    feishuTesting.value = false
  }
}
const commissionData = ref(null)
const manualMapping = ref({})
const channelSaveProgress = ref(0)
const channelSavedRows = ref(0)
const warehouseSaveProgress = ref(0)
const warehouseSavedRows = ref(0)
const channelUploadState = reactive({
  active: false,
  phase: 'uploading',
  percent: 0,
  filename: '',
  size: 0,
})

function formatFileSize(bytes) {
  const size = Number(bytes || 0)
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const channelLatestAov = computed(() => {
  const rows = channelData.value?.aov_history || []
  return Number(rows.at(-1)?.avg_order_value || 0)
})

const channelPeriodAov = computed(() => {
  const rows = channelData.value?.aov_history || []
  const orders = rows.reduce((sum, row) => sum + Number(row.order_count || 0), 0)
  const amount = rows.reduce((sum, row) => sum + Number(row.payment_amount || 0), 0)
  return orders ? amount / orders : 0
})

const channelAovChartOption = computed(() => {
  const rows = channelData.value?.aov_history || []
  return {
    grid: { left: 56, right: 24, top: 28, bottom: 42 },
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        return `${row.date || ''}<br/>客单价：¥${Number(row.avg_order_value || 0).toFixed(2)}<br/>订单量：${Number(row.order_count || 0).toLocaleString('zh-CN')}<br/>付款金额：¥${Number(row.payment_amount || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
      },
    },
    xAxis: { type: 'category', data: rows.map(row => row.date), boundaryGap: false },
    yAxis: { type: 'value', name: '客单价（元）', scale: true },
    series: [{
      name: '客单价', type: 'line', smooth: true, symbolSize: 7,
      data: rows.map(row => row.avg_order_value),
      lineStyle: { width: 3, color: '#2563eb' },
      itemStyle: { color: '#2563eb' },
      areaStyle: { color: 'rgba(37,99,235,.10)' },
    }],
  }
})
const saveStates = reactive(Object.fromEntries(
  ['channel', 'warehouse', 'schedule', 'commission', 'anchor', 'presale']
    .map(type => [type, { status: 'ready', receipt: null }])
))
const dataTypeLabels = {
  channel: '渠道订单', warehouse: '仓库订单', schedule: '直播排期',
  commission: '联盟佣金', anchor: '达人直播数据', presale: '预售历史',
}
const UPLOAD_ERROR_STORAGE_KEY = 'ecom_forecast:last_upload_error'

function loadStoredUploadError() {
  try {
    return JSON.parse(localStorage.getItem(UPLOAD_ERROR_STORAGE_KEY) || 'null')
  } catch {
    return null
  }
}

const persistentUploadError = ref(loadStoredUploadError())

function formatRequestError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (detail != null) {
    try { return JSON.stringify(detail, null, 2) } catch { return String(detail) }
  }
  return error?.message || '未知错误，请检查后重试'
}

function clearUploadError() {
  persistentUploadError.value = null
  try { localStorage.removeItem(UPLOAD_ERROR_STORAGE_KEY) } catch {}
}

function recordUploadError(type, stage, error) {
  const record = {
    type,
    stage,
    message: formatRequestError(error),
    time: new Date().toLocaleString('zh-CN', { hour12: false }),
  }
  persistentUploadError.value = record
  try { localStorage.setItem(UPLOAD_ERROR_STORAGE_KEY, JSON.stringify(record)) } catch {}
}

async function copyUploadError() {
  if (!persistentUploadError.value) return
  const item = persistentUploadError.value
  const text = `[${item.time}] ${dataTypeLabels[item.type] || '数据'}${item.stage}失败\n${item.message}`
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('错误信息已复制')
  } catch {
    ElMessage.error('复制失败，请手动选中错误文字')
  }
}

function markReady(type) {
  saveStates[type].status = 'ready'
  saveStates[type].receipt = null
}

function markSaved(type, response, fallbackCount) {
  clearUploadError()
  const saved = Number(response?.saved ?? fallbackCount ?? 0)
  saveStates[type].status = 'saved'
  saveStates[type].receipt = {
    saved,
    skipped: Number(response?.skipped || 0),
    batchId: response?.batch_id || '',
    savedAt: new Date().toLocaleString('zh-CN', { hour12: false }),
  }
  const created = response?.created_config
  const createdCount = created
    ? (created.platforms?.length || 0) + (created.channels?.length || 0) + (created.shops?.length || 0)
    : 0
  ElMessage.success({
    message: `${dataTypeLabels[type]}已保存：${saved.toLocaleString('zh-CN')} 条${response?.batch_id ? `，批次 ${response.batch_id}` : ''}${createdCount ? `；自动新增 ${createdCount} 项基础配置` : ''}`,
    duration: 4500,
    showClose: true,
  })
  if (type === 'channel' && createdCount) configStore.fetchAll({ force: true })
  Promise.allSettled([loadOverview(), loadBatches()])
}

async function confirmSave(type, count) {
  try {
    await ElMessageBox.confirm(
      `确认将 ${Number(count || 0).toLocaleString('zh-CN')} 条${dataTypeLabels[type]}写入数据库？`,
      '确认保存',
      { type: 'info', confirmButtonText: '确认保存', cancelButtonText: '返回检查' }
    )
    return true
  } catch {
    return false
  }
}

function resetUpload(type) {
  clearUploadError()
  if (type === 'channel') {
    channelData.value = null
    manualMapping.value = {}
    channelSaveProgress.value = 0
    channelSavedRows.value = 0
  }
  if (type === 'warehouse') {
    warehouseData.value = null
    warehouseSaveProgress.value = 0
    warehouseSavedRows.value = 0
  }
  if (type === 'schedule') scheduleData.value = null
  if (type === 'commission') commissionData.value = null
  if (type === 'anchor') anchorData.value = null
  if (type === 'presale') { presaleData.value = null; presaleShopFix.value = {} }
  markReady(type)
}

// 上传文件完整明细在用户点击时才解析，避免平时渲染数万行数据。
const importPreviewVisible = ref(false)
const importPreviewType = ref('')
const importPreviewRecords = ref([])
const importPreviewPage = ref(1)
const importPreviewPageSize = ref(100)
const importPreviewLimited = ref(false)
const importPreviewColumns = computed(() =>
  importPreviewRecords.value[0] ? Object.keys(importPreviewRecords.value[0]).filter(key => !key.startsWith('_')) : []
)
const importPreviewPageCount = computed(() =>
  Math.max(1, Math.ceil(importPreviewRecords.value.length / importPreviewPageSize.value))
)
const importPreviewPageRows = computed(() => {
  const start = (importPreviewPage.value - 1) * importPreviewPageSize.value
  return importPreviewRecords.value.slice(start, start + importPreviewPageSize.value)
})

function openImportPreview(type) {
  const source = {
    channel: channelData.value,
    warehouse: warehouseData.value,
    schedule: scheduleData.value,
    commission: commissionData.value,
    anchor: anchorData.value,
    presale: presaleData.value,
  }[type]
  if (!source) return
  try {
    const records = ['channel', 'warehouse'].includes(type) && source.large_file_mode
      ? (source.preview || [])
      : type === 'presale'
      ? (source._records_json || [])
      : JSON.parse(source._df_json || '[]')
    importPreviewType.value = type
    importPreviewRecords.value = records
    importPreviewLimited.value = ['channel', 'warehouse'].includes(type) && source.large_file_mode
    importPreviewPage.value = 1
    importPreviewVisible.value = true
  } catch {
    ElMessage.error('完整预览数据解析失败，请重新上传文件')
  }
}

// ── 达人直播数据上传 ───────────────────────────────────────────────────────
const anchorData = ref(null)

const anchorPreviewCols = computed(() =>
  anchorData.value?.preview?.[0] ? Object.keys(anchorData.value.preview[0]) : []
)





// ── 预售历史上传 ───────────────────────────────────────────────────────────
const presaleData = ref(null)
const presaleShopFix = ref({})   // {rowIndex: shop_id} 记录前端手动修正的店铺

const presalePreviewRows = computed(() => presaleData.value?.preview || [])

const presaleHasUnfixedShops = computed(() => {
  if (!presaleData.value) return false
  const rows = presaleData.value._records_json || []
  return rows.some((r, i) => r._shop_missing && !presaleShopFix.value[i])
})

function presaleRowClass({ row }) {
  return row._shop_missing ? 'row-missing-shop' : ''
}

function campaignTagType(type) {
  const map = { '1111': 'danger', '618': 'warning', '38': 'success', '1212': 'info', 'default': '' }
  return map[type] || ''
}

async function handlePresaleFileChange(fileObj) {
  clearUploadError()
  const formData = new FormData()
  formData.append('file', fileObj.raw)
  try {
    const res = await dataApi.uploadPresale(formData)
    res.source_filename = fileObj.name || fileObj.raw?.name || ''
    presaleData.value = res
    presaleShopFix.value = {}
    markReady('presale')
    const converted = res?.shop_converted
    const tip = converted ? `解析成功，已自动匹配 ${converted} 个店铺名称→ID` : '预售文件解析成功'
    ElMessage.success(tip)
  } catch (error) {
    recordUploadError('presale', '上传', error)
  }
}

async function savePresale() {
  if (!presaleData.value || presaleHasUnfixedShops.value || saveStates.presale.status !== 'ready') return
  if (!await confirmSave('presale', presaleData.value.rows)) return
  saveStates.presale.status = 'saving'
  try {
    // 将前端手动补全的店铺 ID 合并到 records
    const records = (presaleData.value._records_json || []).map((r, i) => {
      if (r._shop_missing && presaleShopFix.value[i]) {
        return { ...r, shop_id: presaleShopFix.value[i], _shop_missing: false }
      }
      return r
    })
    const res = await dataApi.savePresale(records, { source_filename: presaleData.value.source_filename })
    markSaved('presale', res, records.length)
  } catch (error) {
    recordUploadError('presale', '入库', error)
    saveStates.presale.status = 'ready'
  }
}

const previewCols = computed(() => ({
  channel:    channelData.value?.columns?.slice(0, 8) || [],
  warehouse:  warehouseData.value?.columns?.slice(0, 8) || [],
  schedule:   scheduleData.value?.preview?.[0]    ? Object.keys(scheduleData.value.preview[0])    : [],
  commission: commissionData.value?.preview?.[0]  ? Object.keys(commissionData.value.preview[0])  : [],
}))

const channelHasUnresolvedMappings = computed(() =>
  (channelData.value?.need_manual || []).some(item => !manualMapping.value[item.key])
)

async function handleFileChange(fileObj, type) {
  if (!['schedule', 'commission'].includes(type)) return
  clearUploadError()
  const formData = new FormData()
  formData.append('file', fileObj.raw)
  try {
    const res = type === 'schedule' ? await dataApi.uploadSchedule(formData) : await dataApi.uploadCommission(formData)
    res.source_filename = fileObj.name || fileObj.raw?.name || ''
    if (type === 'schedule') scheduleData.value = res
    else commissionData.value = res
    markReady(type)
    ElMessage.success('文件解析成功')
  } catch (error) { recordUploadError(type, '上传', error) }
}

async function saveData(type) {
  if (!['schedule', 'commission'].includes(type)) return
  if (saveStates[type].status !== 'ready') return
  const source = type === 'schedule' ? scheduleData.value : commissionData.value
  if (!source || !await confirmSave(type, source.rows)) return
  saveStates[type].status = 'saving'
  try {
    const records = JSON.parse(source._df_json)
    const res = type === 'schedule'
      ? await dataApi.saveSchedule(records, { source_filename: source.source_filename })
      : await dataApi.saveCommission(records, { source_filename: source.source_filename })
    markSaved(type, res, records.length)
  } catch (error) {
    recordUploadError(type, '入库', error)
    saveStates[type].status = 'ready'
  }
}

// ── 概览 & 批次管理 ────────────────────────────────────────────────────────
const overviewLoading = ref(false)
const overviewData = ref({})
const batchLoading = ref(false)
const batches = ref({ channel: [], warehouse: [], schedule: [], commission: [], anchor: [], presale: [] })
const batchTab = ref('schedule')
const deleteRange = ref(null)
const deletingKey = ref('')

const batchTypeLabels = {
  schedule:   '直播排期',
  commission: '联盟佣金',
  presale:    '预售历史',
}

async function loadOverview() {
  overviewLoading.value = true
  try {
    overviewData.value = await dataApi.getOverview()
  } finally {
    overviewLoading.value = false
  }
}

async function loadBatches() {
  batchLoading.value = true
  try {
    batches.value = await dataApi.getBatches()
  } finally {
    batchLoading.value = false
  }
}

const overviewCountKeys = {
  channel: 'channel_orders', warehouse: 'warehouse_orders', schedule: 'live_schedules',
  commission: 'commissions', anchor: 'anchor_live_orders',
}

async function refreshAfterDelete(type, deleted) {
  const countKey = overviewCountKeys[type]
  if (countKey && Number.isFinite(Number(deleted))) {
    overviewData.value[countKey] = Math.max(
      0, Number(overviewData.value[countKey] || 0) - Number(deleted)
    )
  }
  // 批次列表现在读取小型汇总表，优先完成它；较重的概览聚合随后后台刷新。
  await loadBatches()
  loadOverview().catch(() => {})
}

async function deleteBatch(type, batchId, count) {
  await ElMessageBox.confirm(
    `确定删除批次 ${batchId} 的 ${count} 条${batchTypeLabels[type]}？此操作不可恢复。`,
    '确认删除',
    { type: 'warning', confirmButtonText: '确定删除', confirmButtonClass: 'el-button--danger' }
  )
  deletingKey.value = `${type}:${batchId}`
  try {
    const res = await dataApi.deleteData(type, { batch_id: batchId })
    ElMessage.success(res.message || '删除成功')
    await refreshAfterDelete(type, res.deleted)
  } finally {
    deletingKey.value = ''
  }
}

async function deleteAll(type, label) {
  await ElMessageBox.confirm(
    `确定清空全部 ${label}？此操作不可恢复！`,
    '危险操作',
    { type: 'error', confirmButtonText: '确定清空', confirmButtonClass: 'el-button--danger' }
  )
  deletingKey.value = `${type}:all`
  try {
    const res = await dataApi.deleteData(type, { delete_all: true })
    ElMessage.success(res.message || '清空成功')
    await refreshAfterDelete(type, res.deleted)
  } finally {
    deletingKey.value = ''
  }
}



// ── 已入库明细：索引游标分页 ──────────────────────────────────────────────
const detailType = ref('schedule')
const detailBatchId = ref('')
const detailShopId = ref('')
const detailShopOptions = ref([])
const detailShopOptionsLoading = ref(false)
const detailChannel = ref('')
const detailDateRange = ref(null)
const detailPageSize = ref(50)
const detailRows = ref([])
const detailColumns = ref([])
const detailLoading = ref(false)
const detailHasMore = ref(false)
const detailNextCursor = ref(null)
const detailCursor = ref(null)
const detailCursorStack = ref([])
const detailQueryMs = ref(null)
const detailRequestMs = ref(0)
let detailRequestSequence = 0
let detailShopOptionsSequence = 0

const detailBatchOptions = computed(() => batches.value[detailType.value] || [])
const detailPage = computed(() => detailCursorStack.value.length + 1)
const detailSupportsChannel = computed(() => ['channel', 'warehouse', 'schedule', 'presale'].includes(detailType.value))
const detailSupportsDate = computed(() => detailType.value !== 'commission')

const detailColumnLabels = {
  id: 'ID', date: '日期', shop_id: '店铺', platform: '平台', channel: '渠道',
  warehouse_id: '仓库', order_count: '订单量', payment_amount: '付款金额', is_sample: '派样单',
  event_type: '事件类型', anchor_tier: '达人档级', anchor_name: '达人名称',
  broadcast_date: '直播日期', payment_date: '尾款日期', expected_viewers: '预计观看',
  expected_orders: '预估单量', expected_revenue: '预估目标', session_type: '场次',
  products: '直播商品及规格', sales_amount: '销售金额', sales_basis: '金额口径',
  mcn: 'MCN', scheduled_at: '开播时间', source_type: '来源',
  month: '月份', commission_rate: '佣金率', active_affiliates: '活跃联盟数',
  campaign_type: '大促类型', upload_batch: '上传批次', created_at: '入库时间',
}

function detailColumnWidth(column) {
  if (['id', 'order_count', 'is_sample', 'anchor_tier'].includes(column)) return 90
  if (column === 'payment_amount') return 130
  if (['date', 'month', 'broadcast_date', 'payment_date'].includes(column)) return 120
  if (['upload_batch', 'created_at'].includes(column)) return 170
  return 130
}

async function ensureDetailBatch() {
  // 飞书自动同步会用新批次替换同一数据源的未来排期，页面内存中的旧批次
  // 可能已失效；进入明细时始终刷新一次，避免继续查询已被替换的批次。
  await loadBatches()
  if (!detailBatchOptions.value.some(item => item.batch_id === detailBatchId.value)) {
    detailBatchId.value = detailBatchOptions.value[0]?.batch_id || ''
  }
}

async function loadDetailShopOptions() {
  const requestSequence = ++detailShopOptionsSequence
  detailShopOptions.value = []
  detailShopId.value = ''
  if (!detailBatchId.value) {
    detailShopOptionsLoading.value = false
    return
  }
  detailShopOptionsLoading.value = true
  try {
    const result = await dataApi.getRecordOptions({
      data_type: detailType.value,
      batch_id: detailBatchId.value,
    })
    if (requestSequence === detailShopOptionsSequence) {
      detailShopOptions.value = result.shops || []
    }
  } finally {
    if (requestSequence === detailShopOptionsSequence) {
      detailShopOptionsLoading.value = false
    }
  }
}

async function loadDetailRecords() {
  if (!detailBatchId.value) {
    detailRows.value = []
    detailColumns.value = []
    return
  }
  detailLoading.value = true
  const requestSequence = ++detailRequestSequence
  const started = performance.now()
  try {
    const params = {
      data_type: detailType.value,
      batch_id: detailBatchId.value,
      page_size: detailPageSize.value,
    }
    if (detailCursor.value) params.after_id = detailCursor.value
    if (detailShopId.value.trim()) params.shop_id = detailShopId.value.trim()
    if (detailSupportsChannel.value && detailChannel.value.trim()) params.channel = detailChannel.value.trim()
    if (detailDateRange.value?.[0]) params.date_start = detailDateRange.value[0]
    if (detailDateRange.value?.[1]) params.date_end = detailDateRange.value[1]
    const result = await dataApi.getRecords(params)
    if (requestSequence !== detailRequestSequence) return
    detailRows.value = result.records || []
    detailColumns.value = result.columns || []
    detailHasMore.value = Boolean(result.has_more)
    detailNextCursor.value = result.next_cursor
    detailQueryMs.value = Number(result.query_ms)
  } finally {
    if (requestSequence === detailRequestSequence) {
      detailRequestMs.value = performance.now() - started
      detailLoading.value = false
    }
  }
}

async function resetAndLoadDetails() {
  detailCursor.value = null
  detailCursorStack.value = []
  await loadDetailRecords()
}

async function handleDetailTypeChange() {
  detailShopId.value = ''
  detailChannel.value = ''
  detailDateRange.value = null
  detailBatchId.value = ''
  await ensureDetailBatch()
  await Promise.all([loadDetailShopOptions(), resetAndLoadDetails()])
}

async function handleDetailBatchChange() {
  detailShopId.value = ''
  await Promise.all([loadDetailShopOptions(), resetAndLoadDetails()])
}

async function previewBatch(type, batchId) {
  detailType.value = type
  await loadBatches()
  const availableBatches = batches.value[type] || []
  const batchExists = availableBatches.some(item => item.batch_id === batchId)
  detailBatchId.value = batchExists ? batchId : (availableBatches[0]?.batch_id || '')
  if (!batchExists && batchId) {
    ElMessage.warning(
      `批次 ${batchId} 已被后续同步替换，已切换到最新批次 ${detailBatchId.value || '（无可用批次）'}`
    )
  }
  if (activeTab.value === 'details') {
    await Promise.all([loadDetailShopOptions(), resetAndLoadDetails()])
  }
  else activeTab.value = 'details'
}

async function loadNextDetailPage() {
  if (!detailHasMore.value || !detailNextCursor.value) return
  detailCursorStack.value.push(detailCursor.value)
  detailCursor.value = detailNextCursor.value
  await loadDetailRecords()
}

async function loadPreviousDetailPage() {
  if (!detailCursorStack.value.length) return
  detailCursor.value = detailCursorStack.value.pop() ?? null
  await loadDetailRecords()
}

async function clearDetailFilters() {
  detailShopId.value = ''
  detailChannel.value = ''
  detailDateRange.value = null
  await resetAndLoadDetails()
}

watch(activeTab, (tab) => {
  if (tab === 'overview') {
    loadOverview()
    loadBatches()
  }
  if (tab === 'dashboard') {
    loadDashboard()
  }
  if (tab === 'schedule') {
    loadFeishuSyncStatus()
    loadFeishuBindings()
  }
  if (tab === 'details') {
    ensureDetailBatch().then(() => Promise.all([loadDetailShopOptions(), resetAndLoadDetails()]))
  }
})

// ── 模板下载 ──────────────────────────────────────────────────────────────
// ── 模板定义：每列含字段名、描述、示例值 ────────────────────────────────────
const TEMPLATES = {
  
  
  schedule: {
    filename: '直播排期模板.xlsx',
    sheetName: '直播排期',
    fields: [
      { name: '店铺ID',       desc: '店铺ID，与基础配置一致；也支持填写系统中已有的店铺名称', examples: ['CS91', 'CS91', 'CS68'] },
      { name: '日期',         desc: '普通直播填开播日期；预售尾款填尾款日期（YYYY-MM-DD）', examples: ['2026-10-20', '2026-10-21', '2026-11-01'] },
      { name: '平台',         desc: '平台ID或平台名称，用于核对渠道归属', examples: ['DOUYIN', 'DOUYIN', 'TM'] },
      { name: '渠道',         desc: '手动指定直播增量归属的实际渠道；建议填写预测表中展示的渠道名称', examples: ['达人直播间', '自营直播间', '达播'] },
      { name: '主播',         desc: '达人/主播名称，须与达人直播历史中的名称一致', examples: ['达人A', '品牌自播', '达人B'] },
      { name: '场次',         desc: '场次类型，例如专场、混场', examples: ['专场', '混场', '专场'] },
      { name: '预估目标',     desc: '排期预估GMV，可留空', examples: [320000, 160000, 500000] },
      { name: '预估单量',     desc: '运营预估单量；模型会结合达人历史进行校准', examples: [1000, 500, 1800] },
      { name: 'MCN',          desc: '达人所属MCN，可留空', examples: ['示例MCN', '', '示例MCN'] },
      { name: '事件类型',     desc: 'live=普通直播；presale_payment=预售尾款', examples: ['live', 'live', 'presale_payment'] },
      { name: '主播档级',     desc: 'A=头部，B=腰部，C=尾部，可留空', examples: ['A', 'B', 'A'] },
      { name: '预计观看人数', desc: '预计直播间观看人数，可留空', examples: [500000, 120000, 800000] },
      { name: '对应直播日期', desc: '仅预售尾款行填写对应定金场直播日期；普通直播留空', examples: ['', '', '2026-10-20'] },
    ],
  },
  
  commission: {
    filename: '联盟佣金模板.xlsx',
    sheetName: '联盟佣金',
    fields: [
      { name: 'shop_id',           desc: '店铺ID',                         examples: ['CS01',   'CS01',   'CS02'] },
      { name: 'month',             desc: '月份（YYYY-MM）',                 examples: ['2024-01','2024-02','2024-01'] },
      { name: 'commission_rate',   desc: '联盟佣金率（小数，如5%填0.05）',  examples: [0.05,     0.06,     0.04] },
      { name: 'active_affiliates', desc: '当月活跃达人/推手数量（整数）',   examples: [320,      410,      180] },
    ],
  },
  presale: {
    filename: '预售历史模板.xlsx',
    sheetName: '预售历史',
    fields: [
      { name: 'broadcast_date', desc: '直播时间：定金场的播出日期（YYYY-MM-DD）',                          examples: ['2024-10-21', '2024-10-21', '2024-05-20'] },
      { name: 'payment_date',   desc: '尾款时间：买家付尾款的日期（YYYY-MM-DD）',                          examples: ['2024-11-01', '2024-11-01', '2024-06-01'] },
      { name: 'shop_id',        desc: '店铺ID（与系统配置一致）',                                           examples: ['CS01',       'CS02',       'CS01'] },
      { name: 'order_count',    desc: '该场预售的尾款单量（整数）',                                          examples: [3200,          1800,          2600] },
      { name: 'is_presale_order', desc: '是否预售单：1=是（填1即可，此列选填）',                            examples: [1,             1,             1] },
    ],
  },
}

async function downloadTemplate(type) {
  const tpl = TEMPLATES[type]
  if (!tpl) return

  const XLSX = await import('xlsx')

  // ── Sheet1：数据填写（可直接上传）────────────────────────────────────────
  const headerRow = tpl.fields.map(f => f.name)
  const exampleCount = tpl.fields[0].examples.length
  const dataRows = Array.from({ length: exampleCount }, (_, i) =>
    tpl.fields.map(f => f.examples[i] ?? '')
  )
  const ws1 = XLSX.utils.aoa_to_sheet([headerRow, ...dataRows])
  ws1['!cols'] = tpl.fields.map(f => ({ wch: Math.max(f.name.length + 4, 14) }))

  // ── Sheet2：字段说明（供查阅）────────────────────────────────────────────
  const descHeader = ['字段名', '说明', '示例值']
  const descRows = tpl.fields.map(f => [f.name, f.desc, f.examples[0] ?? ''])
  const ws2 = XLSX.utils.aoa_to_sheet([descHeader, ...descRows])
  ws2['!cols'] = [{ wch: 22 }, { wch: 46 }, { wch: 20 }]

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws1, '数据填写')
  XLSX.utils.book_append_sheet(wb, ws2, '字段说明')
  XLSX.writeFile(wb, tpl.filename)
}
</script>

<style scoped>
.data-tabs {
  --el-tabs-header-height: 44px;
}

.persistent-upload-error {
  margin: 0 0 18px;
  padding: 14px 16px;
  border: 1px solid #fda29b;
  border-left: 4px solid #d92d20;
  border-radius: 10px;
  background: #fff5f4;
  color: #7a271a;
}

.persistent-upload-error__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.persistent-upload-error__header strong {
  display: block;
  font-size: 15px;
}

.persistent-upload-error__header span,
.persistent-upload-error__hint {
  color: #b42318;
  font-size: 12px;
}

.persistent-upload-error__actions {
  display: flex;
  flex-shrink: 0;
}

.persistent-upload-error pre {
  margin: 12px 0 8px;
  padding: 12px;
  overflow: auto;
  border-radius: 7px;
  background: #fff;
  color: #912018;
  font: 13px/1.65 Consolas, "Microsoft YaHei", sans-serif;
  white-space: pre-wrap;
  word-break: break-word;
}

.upload-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.schedule-upload-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.manual-schedule-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

@media (max-width: 768px) {
  .upload-header { align-items: flex-start; gap: 10px; flex-wrap: wrap; }
  .schedule-upload-actions { width: 100%; flex-wrap: wrap; }
  .manual-schedule-grid { grid-template-columns: 1fr; }
  .persistent-upload-error__header { align-items: flex-start; flex-direction: column; }
  .stat-box, .dash-kpi { margin-bottom: 10px; }
  :deep(.el-upload), :deep(.el-upload-dragger) { width: 100%; }
}

.upload-tip {
  font-size: 13px;
  color: #8c8c8c;
}

.channel-upload-progress {
  margin-top: 14px;
  padding: 15px 16px;
  border: 1px solid #b9d5ff;
  border-radius: 10px;
  background: #f5f9ff;
}

.channel-upload-progress__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
  color: #1d4f91;
}

.channel-upload-progress__header strong,
.channel-upload-progress__header span {
  display: block;
}

.channel-upload-progress__header span,
.channel-upload-progress__hint {
  margin-top: 3px;
  color: #667085;
  font-size: 12px;
}

.channel-upload-progress__status {
  color: #2563eb !important;
  white-space: nowrap;
}

.channel-save-progress-text {
  margin-bottom: 18px;
  color: #667085;
  font-size: 12px;
  text-align: right;
}

.stat-box {
  text-align: center;
  padding: 20px;
  background: #f8f9ff;
  border-radius: 12px;
  border: 1px solid #e8edff;
}

.stat-num {
  font-size: 32px;
  font-weight: 700;
  color: #2563eb;
}

.stat-label {
  font-size: 13px;
  color: #8c8c8c;
  margin-top: 4px;
}

.dash-kpi {
  text-align: center;
  padding: 16px 12px;
  background: #f8f9ff;
  border-radius: 12px;
  border: 1px solid #e8edff;
}

.dash-kpi-val {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
}

.dash-kpi-label {
  font-size: 12px;
  color: #8c8c8c;
  margin-top: 4px;
}

.detail-browser-header {
  align-items: flex-start;
}

.detail-hint {
  margin-top: 5px;
  color: #8490a4;
  font-size: 12px;
}

.query-performance {
  display: flex;
  gap: 10px;
  align-items: center;
  color: #8490a4;
  font-size: 12px;
}

.detail-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 14px;
  margin-bottom: 14px;
  border: 1px solid #e8edf5;
  border-radius: 10px;
  background: #f8faff;
}

.detail-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
  color: #667085;
  font-size: 13px;
}

.order-value {
  color: #2563eb;
  font-weight: 600;
}

.preview-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  color: #667085;
  font-size: 14px;
}

.feishu-sync-card {
  margin-bottom: 18px;
  padding: 18px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  border: 1px solid #bfdbfe;
  border-radius: 13px;
  background: linear-gradient(120deg, #eff6ff, #f5f3ff);
}

.sync-icon {
  width: 46px;
  height: 46px;
  flex: 0 0 46px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  color: #fff;
  background: linear-gradient(135deg, #3370ff, #7c3aed);
  font-size: 20px;
  font-weight: 700;
}

.sync-main { min-width: 0; flex: 1; }
.sync-title { display: flex; align-items: center; gap: 10px; color: #25314d; font-size: 16px; font-weight: 700; }
.sync-main p { margin: 7px 0 8px; color: #667085; font-size: 13px; line-height: 1.55; }
.sync-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: #8490a4; font-size: 12px; }
.sync-error { color: #d92d20; }
.sync-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.sync-actions .el-button + .el-button { margin-left: 0; }
.feishu-config-form { margin-top: 18px; }
.feishu-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.feishu-binding-card { margin-bottom: 18px; }
.binding-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 16px; }
.binding-header h3 { margin: 0 0 6px; color: #25314d; font-size: 16px; }
.binding-header p { margin: 0; color: #667085; font-size: 13px; line-height: 1.55; }
.binding-header small { color: #98a2b3; }

.import-preview-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-bottom: 12px;
  color: #667085;
  font-size: 13px;
}

.aov-preview {
  margin: 16px 0;
  padding: 16px 18px 8px;
  border: 1px solid #dbe7ff;
  border-radius: 10px;
  background: linear-gradient(180deg, #f7faff 0%, #fff 100%);
}

.aov-preview-header, .aov-kpis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.aov-preview-header strong { margin-right: 10px; color: #1d2939; }
.aov-preview-header span { color: #667085; font-size: 12px; }
.aov-kpis b { color: #2563eb; font-size: 16px; margin-left: 5px; }

@media (max-width: 900px) {
  .detail-browser-header, .detail-pagination { align-items: flex-start; gap: 12px; flex-wrap: wrap; }
  .aov-preview-header, .aov-kpis { align-items: flex-start; flex-wrap: wrap; }
  .feishu-sync-card { align-items: flex-start; flex-wrap: wrap; }
  .sync-actions { width: 100%; }
  .sync-actions .el-button { flex: 1; }
  .feishu-form-grid { grid-template-columns: 1fr; gap: 0; }
  .binding-header { flex-direction: column; }
  .binding-header .el-button { width: 100%; }
}
</style>

<style>
/* 预售历史：缺失店铺行高亮（非 scoped，作用于 el-table 内部行） */
.el-table .row-missing-shop td {
  background-color: #fff2f0 !important;
}
</style>
