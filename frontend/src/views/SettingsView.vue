<script setup>
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSystemSettings, updateSystemSettings } from '../api/index.js'

const loading = ref(false)
const saving = ref(false)
const form = reactive({
  webhook_secret: '',
  ssh_key_path: '',
})

async function fetchSettings() {
  loading.value = true
  try {
    const res = await getSystemSettings()
    form.ssh_key_path = res.data.generic_ssh_key_path || ''
    // webhook_secret is not returned; we only know if it's set
    form.webhook_secret_set = res.data.generic_webhook_secret_set
  } catch (e) {
    ElMessage.error('加载配置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const payload = {}
    // Only send webhook_secret if the user entered something
    if (form.webhook_secret) {
      payload.generic_webhook_secret = form.webhook_secret
    }
    payload.generic_ssh_key_path = form.ssh_key_path || ''
    await updateSystemSettings(payload)
    ElMessage.success('全局配置已更新')
    form.webhook_secret = ''  // clear input after save
    await fetchSettings()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div>
    <h1 class="page-title">全局配置</h1>

    <div class="dashboard-card" style="margin-top: 16px; max-width: 640px;">
      <div style="margin-bottom: 24px;">
        <p style="color: var(--color-text-secondary); font-size: 13px; line-height: 1.6;">
          全局配置作为默认值，当仓库级别未单独设置时将自动沿用。
        </p>
      </div>

      <el-form
        v-loading="loading"
        label-position="left"
        label-width="160px"
        @submit.prevent="handleSave"
      >
        <el-form-item label="通用 Webhook Secret">
          <el-input
            v-model="form.webhook_secret"
            type="password"
            show-password
            :placeholder="form.webhook_secret_set ? '已设置（填入新值替换）' : '未设置（可选）'"
            style="width: 100%;"
          />
          <div style="color: var(--color-text-muted); font-size: 12px; margin-top: 4px;">
            {{ form.webhook_secret_set ? '当前状态：已配置' : '当前状态：未配置' }}
          </div>
        </el-form-item>

        <el-form-item label="通用 SSH Key 路径">
          <el-input
            v-model="form.ssh_key_path"
            placeholder="/home/user/.ssh/id_ed25519"
            style="width: 100%;"
          />
          <div style="color: var(--color-text-muted); font-size: 12px; margin-top: 4px;">
            仓库未单独指定 SSH Key 时将使用此路径
          </div>
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="saving"
            @click="handleSave"
          >
            保存配置
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>
