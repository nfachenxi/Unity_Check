<script setup>
import SideNav from './SideNav.vue'
import AppHeader from './AppHeader.vue'
import { ref } from 'vue'

const sidebarCollapsed = ref(false)

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

// Component names for keep-alive (defined via defineOptions in each keep-alive view)
const keepAliveNames = ['DashboardPage', 'EventListPage']
</script>

<template>
  <el-container class="app-container">
    <el-aside :width="sidebarCollapsed ? '64px' : '220px'" class="app-aside">
      <SideNav :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
    </el-aside>
    <el-container>
      <el-header height="56px" class="app-header">
        <AppHeader />
      </el-header>
      <el-main class="app-main">
        <router-view v-slot="{ Component, route }">
          <transition name="fade" mode="out-in">
            <keep-alive :include="keepAliveNames">
              <component :is="Component" :key="route.name" />
            </keep-alive>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-container {
  height: 100vh;
}

.app-aside {
  background: var(--color-secondary);
  border-right: 1px solid var(--color-border);
  overflow: hidden;
  transition: width 250ms ease;
}

.app-header {
  background: var(--color-secondary);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  padding: 0 24px;
}

.app-main {
  background: var(--color-bg);
  padding: 24px;
  overflow-y: auto;
}
</style>
