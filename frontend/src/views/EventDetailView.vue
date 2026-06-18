<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { GaugeChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getEventDetail, getEventEvaluations } from '../api/index.js'
import FileEvaluationBlock from '../components/dashboard/FileEvaluationBlock.vue'

use([GaugeChart, TooltipComponent, CanvasRenderer])

const props = defineProps({ id: { type: String, required: true } })
const loading = ref(true)
const event = ref(null)
const evaluations = ref([])
const assessment = ref(null)
const activeTab = ref('dim-a')

async function fetchData() {
  try {
    const id = parseInt(props.id, 10)
    const [detailRes, evalRes] = await Promise.all([
      getEventDetail(id, { include: 'assessment' }),
      getEventEvaluations(id),
    ])
    const data = detailRes.data
    event.value = data
    evaluations.value = evalRes.data || []
    assessment.value = data.assessment || {
      overall_score: data.overall_score,
      final_risk_level: data.final_risk_level,
      recommendation: data.recommendation,
      executive_summary: data.executive_summary,
      dimension_a_score: data.dimension_a_score,
      dimension_b_score: data.dimension_b_score,
      rounds: evalRes.data || [],
      total_tokens_used: (evalRes.data || []).reduce((s, r) => s + (r.tokens_used || 0), 0),
      total_duration_ms: (evalRes.data || []).reduce((s, r) => s + (r.duration_ms || 0), 0),
    }
  } catch (e) {
    ElMessage.error('加载事件详情失败')
  } finally {
    loading.value = false
  }
}

// Filter evaluation rounds by type
const dimARounds = computed(() =>
  evaluations.value.filter(r => r.round_type === 'functionality_best_practices')
)
const dimBRounds = computed(() =>
  evaluations.value.filter(r => r.round_type === 'security_performance_health')
)

// Diff view with line numbers
const diffLines = computed(() => {
  if (!event.value?.diff_content) return []
  return event.value.diff_content.split('\n').map((line, i) => ({
    lineNumber: i + 1,
    content: line,
    type: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : line.startsWith('@@') ? 'header' : 'normal',
    marker: line.startsWith('+') ? '+' : line.startsWith('-') ? '-' : ' ',
  }))
})

// Gauge chart option
const gaugeOption = computed(() => {
  const score = assessment.value?.overall_score ?? 0
  const color = score >= 80 ? '#22C55E' : score >= 60 ? '#F59E0B' : '#EF4444'

  return {
    series: [{
      type: 'gauge',
      startAngle: 220,
      endAngle: -40,
      min: 0,
      max: 100,
      pointer: { show: false },
      progress: {
        show: true,
        width: 12,
        itemStyle: { color },
      },
      axisLine: {
        lineStyle: {
          width: 12,
          color: [[1, 'rgba(255,255,255,0.08)']],
        },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: { show: false },
      data: [{ value: score }],
      title: { show: false },
      center: ['50%', '55%'],
      radius: '80%',
    }],
  }
})

// Dimension mini-gauge options
function dimGaugeOption(score) {
  const color = score >= 80 ? '#22C55E' : score >= 60 ? '#F59E0B' : '#EF4444'
  return {
    series: [{
      type: 'gauge',
      startAngle: 220,
      endAngle: -40,
      min: 0,
      max: 100,
      pointer: { show: false },
      progress: {
        show: true,
        width: 6,
        itemStyle: { color },
      },
      axisLine: {
        lineStyle: { width: 6, color: [[1, 'rgba(255,255,255,0.06)']] },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: { show: false },
      data: [{ value: score }],
      title: { show: false },
      center: ['50%', '55%'],
      radius: '75%',
    }],
  }
}

function getRiskType(level) {
  return level === 'critical' || level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'success'
}

function formatMs(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatTime(date) {
  if (!date) return '-'
  return new Date(date).toLocaleString('zh-CN')
}

watch(() => props.id, fetchData)
onMounted(fetchData)
</script>

<template>
  <div v-loading="loading">
    <!-- Header -->
    <div class="detail-header">
      <h1 class="page-title" style="margin-bottom: 0;">事件详情</h1>
      <el-tag v-if="event" :type="getRiskType(event.final_risk_level)" size="large" effect="dark">
        {{ (event.final_risk_level || 'unknown').toUpperCase() }}
      </el-tag>
    </div>

    <!-- Event Meta Card -->
    <div v-if="event" class="dashboard-card meta-card">
      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="ID">{{ event.id }}</el-descriptions-item>
        <el-descriptions-item label="类型">
          <el-tag :type="event.event_type === 'push' ? '' : 'info'" size="small">
            {{ event.event_type === 'push' ? 'Push' : 'Pull Request' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="仓库">
          <span v-if="event.repository_alias" style="font-weight: 500;">{{ event.repository_alias }}</span>
          <span v-if="event.repository_alias" style="color: var(--color-text-muted); font-size: 12px; margin-left: 4px;">({{ event.repository }})</span>
          <span v-else>{{ event.repository || '-' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="event.status === 'success' ? 'success' : event.status === 'failed' ? 'danger' : 'info'" size="small">
            {{ event.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Commit SHA">{{ event.after_sha || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Diff 大小">{{ event.diff_size ? event.diff_size + ' bytes' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatTime(event.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="Delivery ID">{{ event.delivery_id || '-' }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <!-- Score Display with Gauge -->
    <div v-if="assessment" class="score-section">
      <div class="score-main">
        <VChart :option="gaugeOption" style="width: 130px; height: 130px" autoresize />
        <div class="score-main-text">
          <div class="score-big">{{ assessment.overall_score?.toFixed(0) ?? '-' }}</div>
          <div class="score-label">综合评分</div>
          <div class="score-rec">{{ (assessment.recommendation || '').replace('_', ' ') }}</div>
        </div>
      </div>
      <div class="score-dims">
        <div class="dim-item" v-if="assessment.dimension_a_score != null">
          <VChart :option="dimGaugeOption(assessment.dimension_a_score)" style="width: 72px; height: 72px" autoresize />
          <div class="dim-item-text">
            <div class="dim-item-label">维度A</div>
            <div class="dim-item-score">{{ assessment.dimension_a_score.toFixed(1) }}</div>
          </div>
        </div>
        <div class="dim-item" v-if="assessment.dimension_b_score != null">
          <VChart :option="dimGaugeOption(assessment.dimension_b_score)" style="width: 72px; height: 72px" autoresize />
          <div class="dim-item-text">
            <div class="dim-item-label">维度B</div>
            <div class="dim-item-score">{{ assessment.dimension_b_score.toFixed(1) }}</div>
          </div>
        </div>
      </div>
      <div class="score-meta">
        <div class="meta-row">
          <span class="meta-label">Token 消耗</span>
          <span class="meta-value">{{ assessment.total_tokens_used?.toLocaleString() ?? '-' }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">评估耗时</span>
          <span class="meta-value">{{ formatMs(assessment.total_duration_ms) }}</span>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="dashboard-card" style="margin-top: 16px;">
      <el-tabs v-model="activeTab" type="border-card">
        <!-- Dimension A Tab -->
        <el-tab-pane label="维度A · 功能与最佳实践" name="dim-a">
          <FileEvaluationBlock
            :evaluations="dimARounds"
            dimension-name="维度A"
            :dimension-score="assessment?.dimension_a_score"
          />
        </el-tab-pane>

        <!-- Dimension B Tab -->
        <el-tab-pane label="维度B · 安全与性能" name="dim-b">
          <FileEvaluationBlock
            :evaluations="dimBRounds"
            dimension-name="维度B"
            :dimension-score="assessment?.dimension_b_score"
          />
        </el-tab-pane>

        <!-- Diff View Tab -->
        <el-tab-pane label="Diff 视图" name="diff">
          <div v-if="diffLines.length" class="diff-view">
            <div
              v-for="(line, i) in diffLines"
              :key="i"
              class="diff-line"
              :class="line.type"
            >
              <span class="diff-line-num">{{ line.lineNumber }}</span>
              <span class="diff-marker">{{ line.marker }}</span>
              <span class="diff-line-content">{{ line.content }}</span>
            </div>
          </div>
          <el-empty v-else description="无 Diff 内容" />
        </el-tab-pane>

        <!-- Summary Assessment Tab -->
        <el-tab-pane label="评估摘要" name="assessment">
          <div class="assessment-content">
            <!-- Overall score -->
            <div class="assessment-score">
              <div class="big-score" :style="{ color: (assessment?.overall_score ?? 0) >= 80 ? 'var(--color-success)' : (assessment?.overall_score ?? 0) >= 60 ? 'var(--color-warning)' : 'var(--color-critical)' }">
                {{ assessment?.overall_score?.toFixed(0) ?? '-' }}
              </div>
              <div class="big-score-label">/ 100</div>
            </div>
            <!-- Dimension scores -->
            <div class="assessment-dims">
              <div class="assessment-dim">
                <span class="dim-indicator dim-a"></span>
                <span>维度A</span>
                <strong>{{ assessment?.dimension_a_score?.toFixed(1) ?? '-' }}</strong>
              </div>
              <div class="assessment-dim">
                <span class="dim-indicator dim-b"></span>
                <span>维度B</span>
                <strong>{{ assessment?.dimension_b_score?.toFixed(1) ?? '-' }}</strong>
              </div>
            </div>
            <!-- Summary text blocks -->
            <div class="summary-blocks">
              <div class="summary-block" v-if="event?.executive_summary">
                <div class="summary-block-title">执行摘要</div>
                <p>{{ event.executive_summary }}</p>
              </div>
              <div class="summary-block" v-if="event?.dimension_a_summary">
                <div class="summary-block-title">维度A · 功能与最佳实践</div>
                <p>{{ event.dimension_a_summary }}</p>
              </div>
              <div class="summary-block" v-if="event?.dimension_b_summary">
                <div class="summary-block-title">维度B · 安全与性能</div>
                <p>{{ event.dimension_b_summary }}</p>
              </div>
              <el-empty v-if="!event?.executive_summary && !event?.dimension_a_summary && !event?.dimension_b_summary" description="暂无评估摘要" />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.detail-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.meta-card {
  padding: 16px 24px;
  margin-bottom: 16px;
}

/* ---- Score Section ---- */
.score-section {
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 20px 28px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.score-main {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.score-main-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.score-big {
  font-family: var(--font-heading);
  font-size: 32px;
  font-weight: 700;
  line-height: 1;
  color: var(--color-text);
}

.score-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.score-rec {
  font-size: 12px;
  color: var(--color-primary);
  text-transform: capitalize;
  font-family: var(--font-heading);
}

.score-dims {
  display: flex;
  gap: 24px;
  flex-shrink: 0;
}

.dim-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dim-item-text {
  display: flex;
  flex-direction: column;
}

.dim-item-label {
  font-size: 11px;
  color: var(--color-text-muted);
}

.dim-item-score {
  font-family: var(--font-heading);
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}

.score-meta {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

.meta-row {
  display: flex;
  gap: 12px;
  font-size: 12px;
}

.meta-label {
  color: var(--color-text-muted);
  font-family: var(--font-heading);
}

.meta-value {
  color: var(--color-text);
  font-family: var(--font-heading);
}

/* ---- Assessment Tab ---- */
.assessment-content {
  max-width: 700px;
  margin: 0 auto;
  padding: 16px 0;
}

.assessment-score {
  text-align: center;
  margin-bottom: 16px;
}

.big-score {
  font-size: 56px;
  font-weight: 800;
  font-family: var(--font-heading);
  line-height: 1;
}

.big-score-label {
  font-size: 16px;
  color: var(--color-text-muted);
  font-family: var(--font-heading);
  margin-top: 4px;
}

.assessment-dims {
  display: flex;
  justify-content: center;
  gap: 32px;
  margin-bottom: 24px;
}

.assessment-dim {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--color-text-secondary);
}

.assessment-dim strong {
  color: var(--color-text);
  font-family: var(--font-heading);
}

.dim-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.dim-indicator.dim-a { background: var(--color-primary); }
.dim-indicator.dim-b { background: var(--color-info); }

.summary-blocks {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-block {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 16px 20px;
}

.summary-block-title {
  font-family: var(--font-heading);
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}

.summary-block p {
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

/* ---- Diff View Tab ---- */
.diff-view {
  background: #0d1117;
  border-radius: var(--radius-sm);
  padding: 12px 0;
  font-family: var(--font-heading);
  font-size: 13px;
  line-height: 1.6;
  overflow-x: auto;
  max-height: 500px;
  overflow-y: auto;
}

.diff-line {
  display: flex;
  padding: 0 12px;
  min-height: 22px;
  align-items: stretch;
}

.diff-line:hover {
  background: rgba(255, 255, 255, 0.03);
}

.diff-line-num {
  min-width: 40px;
  text-align: right;
  padding-right: 12px;
  color: var(--color-text-muted);
  user-select: none;
  font-size: 12px;
}

.diff-marker {
  width: 16px;
  user-select: none;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.diff-line-content {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-all;
}

.diff-line.add { background: rgba(34, 197, 94, 0.08); }
.diff-line.add .diff-line-num { color: var(--color-success); }
.diff-line.add .diff-marker { color: var(--color-success); }

.diff-line.remove { background: rgba(239, 68, 68, 0.08); }
.diff-line.remove .diff-line-num { color: var(--color-critical); }
.diff-line.remove .diff-marker { color: var(--color-critical); }

.diff-line.header { background: rgba(99, 102, 241, 0.06); }
.diff-line.header .diff-line-num { color: var(--color-info); }
.diff-line.header .diff-line-content { color: var(--color-info); }
</style>
