import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000,
})

// ---- Dashboard (unified endpoint) ----
export function getDashboardSummary(params) {
  return api.get('/dashboard', { params: { ...params, section: 'summary' } })
}

export function getDashboardTrends(params) {
  return api.get('/dashboard', { params: { ...params, section: 'trends' } })
}

export function getDashboardIssueDistribution(params) {
  return api.get('/dashboard', { params: { ...params, section: 'distribution' } })
}

// ---- Events ----
export function getEvents(params) {
  return api.get('/events', { params })
}

export function getEventDetail(id, params = {}) {
  return api.get(`/events/${id}`, { params })
}

export function getEventEvaluations(eventId) {
  return api.get(`/events/${eventId}/evaluations`)
}

// ---- Stats ----
export function getStatsScores(params) {
  return api.get('/dashboard', { params: { ...params, section: 'scores' } })
}

export function getStatsHotspots(params) {
  return api.get('/dashboard', { params: { ...params, section: 'hotspots' } })
}

// ---- Repositories ----
export function getRepositories(params) {
  return api.get('/repositories', { params })
}

export function getRepositoryDetail(id) {
  return api.get(`/repositories/${id}`)
}

export function createRepository(data) {
  return api.post('/repositories', data)
}

export function updateRepository(id, data) {
  return api.put(`/repositories/${id}`, data)
}

export function deleteRepository(id) {
  return api.delete(`/repositories/${id}`)
}

export default api
