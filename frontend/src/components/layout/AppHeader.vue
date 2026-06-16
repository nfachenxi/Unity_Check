<script setup>
import { useRoute, useRouter } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
const router = useRouter()

const pageTitle = computed(() => {
  const titles = { Dashboard: '概览看板', EventList: '事件列表', EventDetail: '事件详情', Stats: '统计中心' }
  return titles[route.name] || 'Unity Check'
})

const breadcrumb = computed(() => {
  const parts = []
  if (route.name === 'Dashboard') parts.push('概览看板')
  else if (route.name === 'EventList') parts.push('事件列表')
  else if (route.name === 'EventDetail') parts.push('事件列表', '事件详情')
  else if (route.name === 'Stats') parts.push('统计中心')
  else parts.push('Unity Check')
  return parts
})

function refresh() {
  router.go(0)
}
</script>

<template>
  <div class="app-header-content">
    <div class="breadcrumb-wrap">
      <span
        v-for="(crumb, i) in breadcrumb"
        :key="i"
        class="breadcrumb-item"
        :class="{ active: i === breadcrumb.length - 1 }"
      >
        <span v-if="i > 0" class="breadcrumb-sep">/</span>
        {{ crumb }}
      </span>
    </div>
    <div class="header-spacer"></div>
    <div class="header-actions">
      <el-tooltip content="刷新页面" placement="bottom">
        <el-icon :size="18" class="action-btn" @click="refresh">
          <Refresh />
        </el-icon>
      </el-tooltip>
      <el-tag type="info" size="small" effect="dark" round>Beta</el-tag>
    </div>
  </div>
</template>

<style scoped>
.app-header-content {
  width: 100%;
  display: flex;
  align-items: center;
}

.breadcrumb-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
}

.breadcrumb-item {
  font-family: var(--font-heading);
  font-size: 13px;
  color: var(--color-text-muted);
}

.breadcrumb-item.active {
  color: var(--color-text);
  font-weight: 500;
}

.breadcrumb-sep {
  margin: 0 4px;
  color: var(--color-border-light);
}

.header-spacer {
  flex: 1;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.action-btn {
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color 200ms ease, transform 200ms ease;
}

.action-btn:hover {
  color: var(--color-primary);
  transform: rotate(90deg);
}
</style>
