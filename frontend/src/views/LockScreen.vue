<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login } from '../api/index.js'

const router = useRouter()
const route = useRoute()

const password = ref('')
const loading = ref(false)
const errorMsg = ref('')
const banned = ref(false)
const banMinutes = ref(0)

const AUTH_KEY = 'unity_check_auth'
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

async function handleLogin() {
  if (!password.value) {
    errorMsg.value = '请输入访问密码'
    return
  }

  loading.value = true
  errorMsg.value = ''

  try {
    const res = await login(password.value)
    if (res.data.success) {
      localStorage.setItem(AUTH_KEY, JSON.stringify({
        unlocked: true,
        expires_at: Date.now() + SEVEN_DAYS_MS,
      }))
      const redirect = route.query.redirect || '/'
      router.push(redirect)
    }
  } catch (e) {
    const status = e.response?.status
    const detail = e.response?.data?.detail || '密码错误'

    if (status === 429) {
      // Extract minutes from the message
      banned.value = true
      const match = detail.match(/(\d+)\s*分钟/)
      banMinutes.value = match ? parseInt(match[1], 10) : 60
      errorMsg.value = detail
    } else if (status === 401) {
      errorMsg.value = '密码错误'
    } else {
      errorMsg.value = detail
    }
  } finally {
    loading.value = false
  }
}

function onKeydown(e) {
  if (e.key === 'Enter' && !loading.value) {
    handleLogin()
  }
}
</script>

<template>
  <div class="lock-screen">
    <div class="lock-card">
      <div class="lock-header">
        <img class="lock-logo" src="/logo.png" alt="Unity Check" width="56" height="56" />
        <h1 class="lock-title">Unity Check</h1>
        <p class="lock-subtitle">代码质量智能评估系统</p>
      </div>

      <div class="lock-body">
        <div class="input-wrap" :class="{ 'has-error': errorMsg }">
          <el-input
            v-model="password"
            type="password"
            show-password
            placeholder="输入访问密码"
            size="large"
            :disabled="loading || banned"
            @keydown="onKeydown"
          />
        </div>

        <el-button
          type="primary"
          size="large"
          :loading="loading"
          :disabled="loading || banned || !password"
          class="login-btn"
          @click="handleLogin"
        >
          {{ loading ? '验证中...' : '验证' }}
        </el-button>

        <transition name="fade-slide">
          <p v-if="errorMsg" class="error-msg">
            {{ errorMsg }}
          </p>
        </transition>
      </div>

      <div class="lock-footer">
        <span class="version-tag">v0.1.0 Beta</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lock-screen {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg, #0f172a);
}

.lock-card {
  width: 400px;
  max-width: 90vw;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.lock-header {
  text-align: center;
  margin-bottom: 40px;
}

.lock-logo {
  border-radius: 14px;
  object-fit: cover;
  margin-bottom: 16px;
}

.lock-title {
  font-family: var(--font-heading, 'Inter', sans-serif);
  font-size: 24px;
  font-weight: 600;
  color: var(--color-text, #f1f5f9);
  margin: 0 0 8px;
}

.lock-subtitle {
  font-size: 13px;
  color: var(--color-text-muted, #64748b);
  margin: 0;
}

.lock-body {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.input-wrap {
  transition: all 200ms ease;
}

.input-wrap :deep(.el-input__wrapper) {
  background: var(--color-bg-elevated, #1e293b);
  box-shadow: 0 0 0 1px var(--color-border, #334155);
  transition: box-shadow 200ms ease;
}

.input-wrap :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px var(--color-border-hover, #475569);
}

.input-wrap.has-error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--color-critical, #ef4444);
}

.login-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
}

.error-msg {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--color-critical, #ef4444);
  text-align: center;
  line-height: 1.5;
}

.lock-footer {
  margin-top: 48px;
}

.version-tag {
  font-size: 11px;
  color: var(--color-text-muted, #64748b);
  font-family: var(--font-heading, 'Inter', sans-serif);
}

/* Transition */
.fade-slide-enter-active {
  transition: all 300ms ease-out;
}

.fade-slide-leave-active {
  transition: all 200ms ease-in;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}

.fade-slide-leave-to {
  opacity: 0;
}
</style>
