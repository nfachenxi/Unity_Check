<script setup>
import { reactive, ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRepositories,
  createRepository,
  updateRepository,
  deleteRepository,
  scanRepository,
  getTasks,
  getTask,
} from '../api/index.js'

const loading = ref(false)
const repos = ref([])
const repoTasks = ref({})  // repo_id -> {taskId, status, progress_detail}
const _pollers = {}        // repo_id -> interval handle (non-reactive)

// Dialog state
const submitting = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEditing = ref(false)
const scanAfterCreate = ref(false)
const form = reactive({
  id: null,
  name: '',
  alias: '',
  clone_url: '',
  webhook_secret: '',
  ssh_key_path: '',
  branch_filter: '',
  is_active: true,
})

const emptyForm = () => ({
  id: null,
  name: '',
  alias: '',
  clone_url: '',
  webhook_secret: '',
  ssh_key_path: '',
  branch_filter: '',
  is_active: true,
})

// ---- Task polling helpers ----

function _stopPoll(repoId) {
  if (_pollers[repoId]) {
    clearInterval(_pollers[repoId])
    delete _pollers[repoId]
  }
}

function _startPoll(taskId, repoId) {
  _stopPoll(repoId)
  _pollers[repoId] = setInterval(async () => {
    try {
      const res = await getTask(taskId)
      const t = res.data
      repoTasks.value = { ...repoTasks.value, [repoId]: { taskId: t.id, status: t.status, progress_detail: t.progress_detail } }
      if (['completed', 'failed'].includes(t.status)) {
        _stopPoll(repoId)
        fetchRepos()
      }
    } catch {
      _stopPoll(repoId)
    }
  }, 3000)
}

// ---- Data loading ----

async function fetchRepos() {
  loading.value = true
  try {
    const [reposRes, tasksRes] = await Promise.all([
      getRepositories(),
      getTasks({ limit: 100 }),
    ])
    repos.value = reposRes.data

    // Merge task statuses into map (only pending/processing overwrite stale states)
    const fresh = {}
    for (const t of tasksRes.data) {
      if (t.repository_id && !fresh[t.repository_id]) {
        fresh[t.repository_id] = { taskId: t.id, status: t.status, progress_detail: t.progress_detail }
      }
    }
    const current = { ...repoTasks.value }
    for (const [rid, info] of Object.entries(fresh)) {
      if (!current[rid] || ['pending', 'processing'].includes(info.status)) {
        current[rid] = info
      }
    }
    repoTasks.value = current

    // Start pollers for any in-flight tasks
    for (const [rid, info] of Object.entries(fresh)) {
      if (info.status === 'processing' || info.status === 'pending') {
        _startPoll(info.taskId, Number(rid))
      }
    }
  } catch (e) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openAddDialog() {
  isEditing.value = false
  dialogTitle.value = '添加仓库'
  scanAfterCreate.value = false
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEditDialog(repo) {
  isEditing.value = true
  dialogTitle.value = '编辑仓库'
  form.id = repo.id
  form.name = repo.name
  form.alias = repo.alias || ''
  form.clone_url = repo.clone_url || ''
  form.webhook_secret = ''
  form.ssh_key_path = repo.ssh_key_path || ''
  form.branch_filter = repo.branch_filter || ''
  form.is_active = repo.is_active
  dialogVisible.value = true
}

async function handleScan(repo) {
  try {
    const res = await scanRepository(repo.id)
    const data = res.data
    if (data.status === 'accepted') {
      ElMessage.success('扫描任务已加入队列')
      repoTasks.value = {
        ...repoTasks.value,
        [repo.id]: { taskId: data.task_id, status: 'pending', progress_detail: '排队中' },
      }
      _startPoll(data.task_id, repo.id)
    }
  } catch (e) {
    const detail = e.response?.data?.detail || e.message
    if (e.response?.status === 409) {
      ElMessage.warning(detail)
    } else {
      ElMessage.error('扫描请求失败: ' + detail)
    }
    await fetchRepos()
  }
}

async function handleSubmit() {
  submitting.value = true
  try {
    if (isEditing.value) {
      const payload = {
        alias: form.alias || null,
        clone_url: form.clone_url || null,
        ssh_key_path: form.ssh_key_path || null,
        branch_filter: form.branch_filter || null,
        is_active: form.is_active,
      }
      if (form.webhook_secret) {
        payload.webhook_secret = form.webhook_secret
      }
      await updateRepository(form.id, payload)
      ElMessage.success('仓库配置已更新')
    } else {
      const res = await createRepository({
        name: form.name,
        alias: form.alias || null,
        clone_url: form.clone_url || null,
        webhook_secret: form.webhook_secret || null,
        ssh_key_path: form.ssh_key_path || null,
        branch_filter: form.branch_filter || null,
        is_active: form.is_active,
      }, { params: { scan: scanAfterCreate.value } })
      const data = res.data
      let msg = '仓库已添加'
      if (scanAfterCreate.value && data.task) {
        msg = '仓库已添加，扫描任务已加入队列'
        repoTasks.value = {
          ...repoTasks.value,
          [data.id]: { taskId: data.task.id, status: 'pending', progress_detail: '排队中' },
        }
        _startPoll(data.task.id, data.id)
      }
      ElMessage.success(msg)
    }
    dialogVisible.value = false
    await fetchRepos()
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

async function handleDelete(repo) {
  try {
    await ElMessageBox.confirm(
      `确定要删除仓库 "${repo.name}" 吗？关联的事件记录将继续保留。`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await deleteRepository(repo.id)
    ElMessage.success('仓库已删除')
    await fetchRepos()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

function formatTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function statusTagType(status) {
  const map = { active: 'success', error: 'danger', disabled: 'info' }
  return map[status] || 'info'
}

function taskTagInfo(repoId) {
  const t = repoTasks.value[repoId]
  if (!t) return { show: false }
  switch (t.status) {
    case 'pending':  return { show: true, type: 'warning', label: '排队中' }
    case 'processing': return { show: true, type: 'primary', label: '扫描中' }
    case 'completed': return { show: true, type: 'success', label: '完成' }
    case 'failed':   return { show: true, type: 'danger', label: '失败' }
    default:         return { show: false }
  }
}

function isTaskRunning(repoId) {
  const t = repoTasks.value[repoId]
  return t && ['pending', 'processing'].includes(t.status)
}

onMounted(fetchRepos)
onUnmounted(() => {
  Object.values(_pollers).forEach(clearInterval)
})
</script>

<template>
  <div>
    <h1 class="page-title">仓库管理</h1>

    <div class="dashboard-card" style="margin-top: 16px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <span style="color: var(--color-text-secondary); font-size: 13px;">
          管理已注册的 Git 仓库
        </span>
        <el-button type="primary" @click="openAddDialog">
          添加仓库
        </el-button>
      </div>

      <el-table
        :data="repos"
        v-loading="loading"
        size="default"
        style="width: 100%"
      >
        <el-table-column label="仓库名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.alias" style="font-weight: 500;">{{ row.alias }}</span>
            <span v-else style="font-weight: 500;">{{ row.name }}</span>
            <span v-if="row.alias" style="color: var(--color-text-muted); font-size: 12px; margin-left: 6px;">({{ row.name }})</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="扫描状态" width="100" align="center">
          <template #default="{ row }">
            <template v-if="taskTagInfo(row.id).show">
              <el-tag :type="taskTagInfo(row.id).type" size="small" effect="dark">
                {{ taskTagInfo(row.id).label }}
              </el-tag>
            </template>
            <span v-else style="color: var(--color-text-muted); font-size: 12px;">-</span>
          </template>
        </el-table-column>
        <el-table-column label="活跃" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="plain">
              {{ row.is_active ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后同步" width="180" align="center">
          <template #default="{ row }">
            <span style="color: var(--color-text-secondary); font-size: 12px;">
              {{ formatTime(row.last_synced_at) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="错误信息" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error_message" style="color: var(--color-critical); font-size: 12px;">
              {{ row.error_message }}
            </span>
            <span v-else style="color: var(--color-text-muted); font-size: 12px;">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              text
              type="primary"
              :disabled="!row.clone_url || isTaskRunning(row.id)"
              @click="handleScan(row)"
            >
              {{ isTaskRunning(row.id) ? '扫描中' : '扫描' }}
            </el-button>
            <el-button size="small" text type="primary" @click="openEditDialog(row)">
              编辑
            </el-button>
            <el-button size="small" text type="danger" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Add / Edit Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="560px"
      :close-on-click-modal="false"
      :destroy-on-close="true"
    >
      <el-form :model="form" label-width="120px" label-position="left">
        <el-form-item label="仓库名称" required>
          <el-input
            v-model="form.name"
            placeholder="owner/repo"
            :disabled="isEditing"
          />
        </el-form-item>
        <el-form-item label="仓库别名">
          <el-input
            v-model="form.alias"
            placeholder="友好名称，如「主游戏项目」（可选）"
          />
        </el-form-item>
        <el-form-item label="Clone URL">
          <el-input
            v-model="form.clone_url"
            placeholder="https://github.com/owner/repo.git"
          />
        </el-form-item>
        <el-form-item label="Webhook Secret">
          <el-input
            v-model="form.webhook_secret"
            type="password"
            show-password
            :placeholder="isEditing ? '留空则不修改' : '可选'"
          />
        </el-form-item>
        <el-form-item label="SSH Key 路径">
          <el-input
            v-model="form.ssh_key_path"
            placeholder="/path/to/id_ed25519"
          />
        </el-form-item>
        <el-form-item label="分支过滤">
          <el-input
            v-model="form.branch_filter"
            placeholder='["main", "release/*"] — 留空监听所有分支'
          />
        </el-form-item>
        <el-form-item label="活跃">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item v-if="!isEditing" label="初始扫描">
          <el-checkbox v-model="scanAfterCreate">
            注册后立即扫描所有 <code>.cs</code> 文件
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :loading="submitting" :disabled="submitting" type="primary" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* Scoped styles follow existing frontend patterns */
</style>
