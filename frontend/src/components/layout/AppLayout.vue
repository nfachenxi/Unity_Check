<script setup>
import SideNav from './SideNav.vue'
import AppHeader from './AppHeader.vue'
</script>

<template>
  <el-container class="app-container">
    <el-aside width="220px" class="app-aside">
      <SideNav />
    </el-aside>
    <el-container>
      <el-header height="56px" class="app-header">
        <AppHeader />
      </el-header>
      <el-main class="app-main">
        <router-view />
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
  overflow-y: auto;
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
