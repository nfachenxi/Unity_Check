<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
const activePath = computed(() => route.path)

defineEmits(['toggle'])

defineProps({
  collapsed: { type: Boolean, default: false },
})

const navItems = [
  { path: '/', title: '概览看板', icon: 'Odometer' },
  { path: '/events', title: '事件列表', icon: 'List' },
  { path: '/repositories', title: '仓库管理', icon: 'FolderOpened' },
  { path: '/stats', title: '统计中心', icon: 'DataAnalysis' },
  { path: '/settings', title: '全局配置', icon: 'Setting' },
]
</script>

<template>
  <div class="sidenav" :class="{ collapsed }">
    <!-- Brand -->
    <div class="sidenav-brand">
      <img class="brand-logo" src="/logo.png" alt="Unity Check" width="32" height="32" />
      <transition name="fade">
        <span v-if="!collapsed" class="brand-text">Unity Check</span>
      </transition>
    </div>

    <!-- Navigation -->
    <el-menu
      :default-active="activePath"
      router
      class="sidenav-menu"
      background-color="transparent"
      text-color="#94A3B8"
      active-text-color="#3B82F6"
      :collapse="collapsed"
    >
      <el-menu-item
        v-for="item in navItems"
        :key="item.path"
        :index="item.path"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <template #title>
          <span>{{ item.title }}</span>
        </template>
      </el-menu-item>
    </el-menu>

    <!-- Collapse Toggle -->
    <div class="sidenav-footer">
      <div class="toggle-btn" @click="$emit('toggle')">
        <el-icon :size="18">
          <component :is="collapsed ? 'Expand' : 'Fold'" />
        </el-icon>
      </div>
      <transition name="fade">
        <span v-if="!collapsed" class="version-text">v0.1.0 Beta</span>
      </transition>
    </div>
  </div>
</template>

<style scoped>
.sidenav {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0;
  transition: width 250ms ease;
  overflow: hidden;
}

.sidenav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 20px 28px;
  min-height: 68px;
}

.brand-logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
}

.brand-text {
  font-family: var(--font-heading);
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
}

.sidenav-menu {
  flex: 1;
  border-right: none;
  --el-menu-item-height: 44px;
}

.sidenav-menu .el-menu-item {
  border-left: 3px solid transparent;
  transition: border-color 200ms ease, background-color 200ms ease;
  margin: 2px 8px;
  border-radius: 6px;
}

.sidenav-menu .el-menu-item.is-active {
  border-left-color: var(--color-primary);
  background: rgba(59, 130, 246, 0.1) !important;
}

.sidenav-menu .el-menu-item:not(.is-active):hover {
  background: rgba(255, 255, 255, 0.04) !important;
}

.sidenav-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid var(--color-border);
}

.toggle-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 200ms ease, color 200ms ease;
  flex-shrink: 0;
}

.toggle-btn:hover {
  background: var(--color-bg-elevated);
  color: var(--color-text);
}

.version-text {
  font-size: 11px;
  color: var(--color-text-muted);
  font-family: var(--font-heading);
}

/* Collapsed state overrides */
.sidenav.collapsed .sidenav-brand {
  justify-content: center;
  padding: 20px 0 28px;
}

.sidenav.collapsed .sidenav-menu .el-menu-item {
  justify-content: center;
  padding: 0;
  margin: 2px 12px;
}

.sidenav.collapsed .sidenav-footer {
  justify-content: center;
  padding: 16px 0;
}

.sidenav.collapsed .toggle-btn {
  width: 32px;
  height: 32px;
}
</style>
