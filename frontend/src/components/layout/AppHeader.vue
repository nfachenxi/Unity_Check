<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()

const pageTitle = computed(() => {
  const titles = { Dashboard: '概览看板', EventList: '事件列表', EventDetail: '事件详情', Stats: '统计中心' }
  return titles[route.name] || 'Unity Check'
})
</script>

<template>
  <div class="app-header-content">
    <span class="page-breadcrumb">{{ pageTitle }}</span>
    <div class="header-spacer"></div>
    <el-tag type="info" size="small" effect="dark" round>Beta</el-tag>
  </div>
</template>

<style scoped>
.app-header-content {
  width: 100%;
  display: flex;
  align-items: center;
}

.page-breadcrumb {
  font-family: var(--font-heading);
  font-size: 15px;
  color: var(--color-text-secondary);
}

.header-spacer {
  flex: 1;
}
</style>
