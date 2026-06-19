<script setup>
import { ref, computed } from 'vue'
import RiskTag from '../common/RiskTag.vue'

const props = defineProps({
  evaluations: { type: Array, default: () => [] },
  dimensionName: { type: String, default: '' },
  dimensionScore: { type: [Number, String], default: '-' },
  eventId: { type: [Number, String], default: null },
})

// ---- Collapse state (localStorage-backed) ----
const storageKey = computed(() => `file-collapse-${props.eventId || 'default'}`)

function loadCollapsed() {
  try {
    const raw = localStorage.getItem(storageKey.value)
    return raw ? new Set(JSON.parse(raw)) : new Set()
  } catch { return new Set() }
}

const collapsedFiles = ref(loadCollapsed())

function toggleCollapse(filePath) {
  const s = new Set(collapsedFiles.value)
  if (s.has(filePath)) s.delete(filePath)
  else s.add(filePath)
  collapsedFiles.value = s
  try {
    localStorage.setItem(storageKey.value, JSON.stringify([...s]))
  } catch { /* localStorage full or unavailable */ }
}

function isCollapsed(filePath) {
  return collapsedFiles.value.has(filePath)
}

// ---- Sorting: by highest severity desc, then score asc ----
const SEVERITY_RANK = { critical: 4, high: 3, medium: 2, low: 1 }

function maxSeverityRank(evalRound) {
  const findings = evalRound.output_data?.findings
  if (!findings?.length) return 0
  return Math.max(...findings.map(f => SEVERITY_RANK[f.severity?.toLowerCase()] || 0))
}

const sortedEvaluations = computed(() => {
  return [...props.evaluations].sort((a, b) => {
    const rankA = maxSeverityRank(a)
    const rankB = maxSeverityRank(b)
    if (rankB !== rankA) return rankB - rankA
    return (a.score ?? 0) - (b.score ?? 0)
  })
})

// ---- Severity color (for card left border) ----
function getSeverityColor(severity) {
  if (severity === 'critical' || severity === 'high') return 'var(--color-critical)'
  if (severity === 'medium') return 'var(--color-warning)'
  return 'var(--color-info)'
}
</script>

<template>
  <div>
    <!-- Dimension header -->
    <div class="dim-header">
      <div class="dim-header-left">
        <span class="dim-badge">
          已评估 {{ evaluations.length }} 文件
        </span>
        <span class="dim-summary" v-if="evaluations.length">
          均分: {{ dimensionScore ?? '-' }} |
          Token: {{ evaluations.reduce((s, r) => s + (r.tokens_used || 0), 0).toLocaleString() }}
        </span>
      </div>
    </div>

    <!-- File evaluation blocks -->
    <div v-for="r in sortedEvaluations" :key="r.id" class="file-eval-card">
      <!-- File header — clickable toggle -->
      <div class="file-eval-header" @click="toggleCollapse(r.file_path)" role="button" tabindex="0" @keydown.enter="toggleCollapse(r.file_path)">
        <div class="file-eval-header-left">
          <span class="collapse-arrow" :class="{ collapsed: isCollapsed(r.file_path) }">▼</span>
          <span class="file-icon">📄</span>
          <span class="file-path-text">{{ r.file_path }}</span>
        </div>
        <div class="file-eval-score" :style="{ color: r.score >= 80 ? 'var(--color-success)' : r.score >= 60 ? 'var(--color-warning)' : 'var(--color-critical)' }">
          {{ r.score?.toFixed(0) ?? 'N/A' }}
        </div>
      </div>

      <!-- Collapsible content -->
      <div v-show="!isCollapsed(r.file_path)">
        <!-- Summary -->
        <p v-if="r.output_data?.summary" class="file-eval-summary">{{ r.output_data.summary }}</p>

        <!-- Error state -->
        <div v-if="r.status === 'failed'" class="error-block">
          <el-alert :title="r.error_message" type="error" show-icon :closable="false" />
        </div>

        <!-- Findings list -->
        <div v-else-if="r.output_data?.findings?.length" class="findings-list">
          <div
            v-for="(f, i) in r.output_data.findings"
            :key="i"
            class="finding-card"
            :style="{ borderLeftColor: getSeverityColor(f.severity) }"
          >
            <div class="finding-header">
              <RiskTag :level="f.severity" size="small" />
              <span class="finding-title-text">{{ f.title }}</span>
              <el-tag size="small" type="info" effect="plain">{{ f.category }}</el-tag>
            </div>
            <div class="finding-desc">{{ f.description }}</div>
            <div v-if="f.suggestion" class="finding-suggestion">
              <span class="suggestion-icon">💡</span>
              {{ f.suggestion }}
            </div>
            <div v-if="f.line_hint" class="finding-location">📍 {{ f.line_hint }}</div>
          </div>
        </div>

        <!-- Empty state -->
        <div v-else class="findings-empty">
          <el-empty description="此文件未发现问题" :image-size="60" />
        </div>
      </div>
    </div>

    <!-- No evaluations state -->
    <div v-if="!evaluations.length" class="dim-empty">
      <el-empty :description="`未执行 ${dimensionName} 评估`" :image-size="80" />
    </div>
  </div>
</template>

<style scoped>
.dim-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
}

.dim-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.dim-badge {
  background: var(--color-success-bg);
  color: var(--color-success);
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--font-heading);
}

.dim-summary {
  font-size: 13px;
  color: var(--color-text-muted);
}

.file-eval-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
  overflow: hidden;
  transition: border-color 200ms ease;
}

.file-eval-card:hover {
  border-color: var(--color-border-light);
}

.file-eval-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--color-bg-elevated);
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
  user-select: none;
  transition: background 150ms ease;
}

.file-eval-header:hover {
  background: var(--color-bg-card);
}

.file-eval-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.collapse-arrow {
  font-size: 10px;
  color: var(--color-text-muted);
  transition: transform 200ms ease;
  flex-shrink: 0;
  width: 12px;
  text-align: center;
}

.collapse-arrow.collapsed {
  transform: rotate(-90deg);
}

.file-icon {
  font-size: 15px;
  flex-shrink: 0;
}

.file-path-text {
  font-family: var(--font-heading);
  font-size: 13px;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-eval-score {
  font-family: var(--font-heading);
  font-size: 18px;
  font-weight: 700;
  flex-shrink: 0;
  margin-left: 12px;
}

.file-eval-summary {
  margin: 0;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  border-bottom: 1px solid var(--color-border);
}

.error-block {
  margin: 12px 16px;
}

.findings-list {
  padding: 8px;
}

.finding-card {
  background: rgba(255, 255, 255, 0.02);
  border-left: 3px solid var(--color-info);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  padding: 10px 14px;
  margin-bottom: 6px;
  transition: background 200ms ease;
}

.finding-card:hover {
  background: rgba(255, 255, 255, 0.04);
}

.finding-card:last-child {
  margin-bottom: 0;
}

.finding-header {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.finding-title-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.finding-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  margin-bottom: 4px;
}

.finding-suggestion {
  font-size: 12px;
  color: var(--color-success);
  margin-top: 4px;
  padding: 4px 8px;
  background: var(--color-success-bg);
  border-radius: 4px;
  display: inline-block;
}

.suggestion-icon {
  margin-right: 4px;
}

.finding-location {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 4px;
  font-family: var(--font-heading);
}

.findings-empty {
  padding: 8px;
}

.dim-empty {
  padding: 24px 0;
}
</style>
