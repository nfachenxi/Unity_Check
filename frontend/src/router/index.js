import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: '概览看板' },
  },
  {
    path: '/events',
    name: 'EventList',
    component: () => import('../views/EventListView.vue'),
    meta: { title: '事件列表' },
  },
  {
    path: '/events/:id',
    name: 'EventDetail',
    component: () => import('../views/EventDetailView.vue'),
    meta: { title: '事件详情' },
    props: true,
  },
  {
    path: '/stats',
    name: 'Stats',
    component: () => import('../views/StatsView.vue'),
    meta: { title: '统计中心' },
  },
  {
    path: '/repositories',
    name: 'RepositoryList',
    component: () => import('../views/RepositoryListView.vue'),
    meta: { title: '仓库管理' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue'),
    meta: { title: '全局配置' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
