<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
const activePath = computed(() => route.path)

const navItems = [
  { path: '/', title: '概览看板', icon: 'Odometer' },
  { path: '/events', title: '事件列表', icon: 'List' },
  { path: '/stats', title: '统计中心', icon: 'DataAnalysis' },
]
</script>

<template>
  <div class="sidenav">
    <div class="sidenav-brand">
      <span class="brand-icon">🔍</span>
      <span class="brand-text">Unity Check</span>
    </div>
    <el-menu
      :default-active="activePath"
      router
      class="sidenav-menu"
      background-color="transparent"
      text-color="#94A3B8"
      active-text-color="#3B82F6"
    >
      <el-menu-item
        v-for="item in navItems"
        :key="item.path"
        :index="item.path"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span>{{ item.title }}</span>
      </el-menu-item>
    </el-menu>
  </div>
</template>

<style scoped>
.sidenav {
  padding: 16px 0;
}

.sidenav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px 24px;
  font-family: var(--font-heading);
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}

.brand-icon {
  font-size: 22px;
}

.sidenav-menu {
  border-right: none;
}
</style>
