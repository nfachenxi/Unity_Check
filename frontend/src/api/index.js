import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000,
})

// ---- Dashboard ----
export function getDashboardSummary(params) {
  return api.get('/dashboard/summary', { params })
}

export function getDashboardTrends(params) {
  return api.get('/dashboard/trends', { params })
}

export function getDashboardIssueDistribution(params) {
  return api.get('/dashboard/issue-distribution', { params })
}

// ---- Events ----
export function getEvents(params) {
  return api.get('/events', { params })
}

export function getEventDetail(id) {
  return api.get(`/events/${id}`)
}

export function getEventRules(eventId, params) {
  return api.get(`/events/${eventId}/rules`, { params })
}

export function getEventEvaluations(eventId) {
  return api.get(`/events/${eventId}/evaluations`)
}

export function getEventAssessment(eventId) {
  return api.get(`/events/${eventId}/assessment`)
}

// ---- Stats ----
export function getStatsScores(params) {
  return api.get('/stats/scores', { params })
}

export function getStatsHotspots(params) {
  return api.get('/stats/hotspots', { params })
}

// ---- Notifications ----
export function getNotifications(params) {
  return api.get('/notifications', { params })
}

export default api
