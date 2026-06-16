<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRepositories,
  createRepository,
  updateRepository,
  deleteRepository,
} from '../api/index.js'

const loading = ref(false)
const repos = ref([])

// Dialog state
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEditing = ref(false)
const form = reactive({
  id: null,
  name: '',
  clone_url: '',
  webhook_secret: '',
  ssh_key_path: '',
  branch_filter: '',
  is_active: true,
})

const emptyForm = () => ({
  id: null,
  name: '',
  clone_url: '',
  webhook_secret: '',
  ssh_key_path: '',
  branch_filter: '',
  is_active: true,
})

async function fetchRepos() {
  loading.value = true
  try {
    const res = await getRepositories()
    repos.value = res.data
  } catch (e) {
    ElMessage.error('加载仓库列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openAddDialog() {
  isEditing.value = false
  dialogTitle.value = '添加仓库'
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEditDialog(repo) {
  isEditing.value = true
  dialogTitle.value = '编辑仓库'
  form.id = repo.id
  form.name = repo.name
  form.clone_url = repo.clone_url || ''
  form.webhook_secret = ''
  form.ssh_key_path = repo.ssh_key_path || ''
  form.branch_filter = repo.branch_filter || ''
  form.is_active = repo.is_active
  dialogVisible.value = true
}

async function handleSubmit() {
  try {
    if (isEditing.value) {
      const payload = {
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
      await createRepository({
        name: form.name,
        clone_url: form.clone_url || null,
        webhook_secret: form.webhook_secret || null,
        ssh_key_path: form.ssh_key_path || null,
        branch_filter: form.branch_filter || null,
        is_active: form.is_active,
      })
      ElMessage.success('仓库已添加')
    }
    dialogVisible.value = false
    await fetchRepos()
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
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

onMounted(fetchRepos)
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
        <el-table-column prop="name" label="仓库名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ row.status }}
            </el-tag>
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
        <el-table-column label="操作" width="140" align="center" fixed="right">
          <template #default="{ row }">
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
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* Scoped styles follow existing frontend patterns */
</style>
