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
  {
    path: '/lock',
    name: 'LockScreen',
    component: () => import('../views/LockScreen.vue'),
    meta: { title: '验证' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

const AUTH_KEY = 'unity_check_auth'

function isAuthenticated() {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    if (!raw) return false
    const data = JSON.parse(raw)
    return data.unlocked === true && data.expires_at > Date.now()
  } catch {
    return false
  }
}

router.beforeEach((to, from, next) => {
  // LockScreen is always accessible
  if (to.name === 'LockScreen') {
    // Already authenticated → redirect to home
    if (isAuthenticated()) {
      next('/')
      return
    }
    next()
    return
  }

  // All other routes require authentication
  if (!isAuthenticated()) {
    next({ name: 'LockScreen', query: { redirect: to.fullPath } })
    return
  }

  next()
})

export default router
