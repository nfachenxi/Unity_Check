<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getEvents } from '../api/index.js'

const router = useRouter()
const loading = ref(false)
const events = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const filters = reactive({
  event_type: '',
  risk_level: '',
  status: '',
})

function getRiskType(level) {
  return level === 'critical' || level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'success'
}

function getStatusType(s) {
  return s === 'success' ? 'success' : s === 'failed' ? 'danger' : s === 'running' ? 'warning' : 'info'
}

function formatTime(date) {
  if (!date) return '-'
  const d = new Date(date)
  return d.toLocaleString('zh-CN', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function shortSha(sha) {
  return sha ? sha.slice(0, 7) : '-'
}

async function fetchEvents() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.event_type) params.event_type = filters.event_type
    if (filters.risk_level) params.risk_level = filters.risk_level
    if (filters.status) params.status = filters.status

    const res = await getEvents(params)
    events.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载事件列表失败')
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  fetchEvents()
}

function resetFilters() {
  filters.event_type = ''
  filters.risk_level = ''
  filters.status = ''
  page.value = 1
  fetchEvents()
}

function goDetail(id) {
  router.push(`/events/${id}`)
}

function handlePageChange(p) {
  page.value = p
  fetchEvents()
}

onMounted(fetchEvents)
</script>

<template>
  <div>
    <h1 class="page-title">事件列表</h1>

    <!-- Filters -->
    <div class="dashboard-card filter-bar">
      <el-row :gutter="12" align="middle">
        <el-col :span="4">
          <el-select v-model="filters.event_type" placeholder="事件类型" clearable size="default" style="width:100%">
            <el-option label="Push" value="push" />
            <el-option label="Pull Request" value="pull_request" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.risk_level" placeholder="风险等级" clearable size="default" style="width:100%">
            <el-option label="Low" value="low" />
            <el-option label="Medium" value="medium" />
            <el-option label="High" value="high" />
            <el-option label="Critical" value="critical" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.status" placeholder="状态" clearable size="default" style="width:100%">
            <el-option label="Queued" value="queued" />
            <el-option label="Running" value="running" />
            <el-option label="Success" value="success" />
            <el-option label="Failed" value="failed" />
          </el-select>
        </el-col>
        <el-col :span="6">
          <el-button type="primary" @click="applyFilters">筛选</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-col>
        <el-col :span="6" style="text-align: right;">
          <span class="total-hint">共 {{ total }} 条</span>
        </el-col>
      </el-row>
    </div>

    <!-- Table -->
    <div class="dashboard-card" style="margin-top: 16px;">
      <el-table
        :data="events"
        v-loading="loading"
        size="default"
        style="width: 100%"
        @row-click="({ id }) => goDetail(id)"
        row-class-name="clickable-row"
      >
        <el-table-column prop="event_type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="row.event_type === 'push' ? '' : 'info'" size="small">
              {{ row.event_type === 'push' ? 'Push' : 'PR' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="repository" label="仓库" min-width="160">
          <template #default="{ row }">
            <span class="mono-text">{{ row.repository || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="after_sha" label="Commit" width="110">
          <template #default="{ row }">
            <code class="sha-code">{{ shortSha(row.after_sha) }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="overall_score" label="评分" width="75" align="center">
          <template #default="{ row }">
            <span v-if="row.overall_score != null" class="score-value">{{ row.overall_score.toFixed(0) }}</span>
            <span v-else class="no-score">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="final_risk_level" label="风险" width="85" align="center">
          <template #default="{ row }">
            <el-tag :type="getRiskType(row.final_risk_level)" size="small" effect="dark">
              {{ row.final_risk_level || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="recommendation" label="建议" width="110">
          <template #default="{ row }">
            <span class="rec-text">{{ (row.recommendation || '').replace('_', ' ') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="85">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small" effect="plain">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="140">
          <template #default="{ row }">
            <span class="time-text">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          small
          @current-change="handlePageChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.filter-bar {
  padding: 16px 20px;
}

.total-hint {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.mono-text {
  font-family: var(--font-heading);
  font-size: 13px;
  color: var(--color-text-secondary);
}

.sha-code {
  font-family: var(--font-heading);
  font-size: 12px;
  color: var(--color-primary);
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
}

.score-value {
  font-family: var(--font-heading);
  font-weight: 600;
  color: var(--color-success);
}

.no-score {
  color: var(--color-text-secondary);
}

.rec-text {
  color: var(--color-text-secondary);
  font-size: 12px;
  text-transform: capitalize;
}

.time-text {
  color: var(--color-text-secondary);
  font-size: 12px;
}

:deep(.clickable-row) {
  cursor: pointer;
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
