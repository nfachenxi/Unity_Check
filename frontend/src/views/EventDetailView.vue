<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getEventDetail, getEventRules, getEventEvaluations, getEventAssessment } from '../api/index.js'

const props = defineProps({ id: { type: String, required: true } })
const loading = ref(true)
const event = ref(null)
const rules = ref([])
const evaluations = ref([])
const assessment = ref(null)
const activeTab = ref('dim-a')

async function fetchData() {
  try {
    const id = parseInt(props.id, 10)
    const [evtRes, rulesRes, evalRes, assessRes] = await Promise.all([
      getEventDetail(id),
      getEventRules(id, { limit: 500 }),
      getEventEvaluations(id),
      getEventAssessment(id),
    ])
    event.value = evtRes.data
    // Rules & assessment now embedded via ?include=rules / ?include=assessment
    rules.value = rulesRes.data?.rules || []
    evaluations.value = evalRes.data || []
    assessment.value = assessRes.data?.assessment || assessRes.data
  } catch (e) {
    ElMessage.error('加载事件详情失败')
  } finally {
    loading.value = false
  }
}

// Filter evaluation rounds by type
const ruleCheckRound = computed(() =>
  evaluations.value.find(r => r.round_type === 'rule_check')
)
const dimARounds = computed(() =>
  evaluations.value.filter(r => r.round_type === 'functionality_best_practices')
)
const dimBRounds = computed(() =>
  evaluations.value.filter(r => r.round_type === 'security_performance_health')
)

// Collect all findings from dimension rounds
const dimAFindings = computed(() => {
  const f = []
  for (const r of dimARounds.value) {
    for (const finding of (r.output_data?.findings || [])) {
      f.push({ ...finding, file_path: r.file_path, round_id: r.id })
    }
  }
  return f
})
const dimBFindings = computed(() => {
  const f = []
  for (const r of dimBRounds.value) {
    for (const finding of (r.output_data?.findings || [])) {
      f.push({ ...finding, file_path: r.file_path, round_id: r.id })
    }
  }
  return f
})

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
        <el-descriptions-item label="仓库">{{ event.repository || '-' }}</el-descriptions-item>
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

    <!-- Score Display -->
    <div v-if="assessment" class="score-bar">
      <div class="score-circle">
        <div class="score-number">{{ assessment.overall_score?.toFixed(0) ?? '-' }}</div>
        <div class="score-label">综合评分</div>
      </div>
      <div class="score-meta">
        <div><span class="meta-label">建议：</span><span class="rec-text">{{ (assessment.recommendation || '').replace('_', ' ') }}</span></div>
        <div><span class="meta-label">维度A(功能/最佳实践)：</span>{{ assessment.dimension_a_score?.toFixed(1) ?? '-' }}</div>
        <div><span class="meta-label">维度B(安全/性能/健康度)：</span>{{ assessment.dimension_b_score?.toFixed(1) ?? '-' }}</div>
        <div><span class="meta-label">总 Token：</span>{{ assessment.total_tokens_used?.toLocaleString() ?? '-' }}</div>
        <div><span class="meta-label">总耗时：</span>{{ formatMs(assessment.total_duration_ms) }}</div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="dashboard-card" style="margin-top: 16px;">
      <el-tabs v-model="activeTab" type="border-card">
        <!-- Rules Tab (Roslyn) -->
        <el-tab-pane label="规则检测" name="rules">
          <div class="round-header">
            <span class="round-badge">共 {{ rules.length }} 条违规</span>
          </div>
          <el-table :data="rules" size="small" max-height="400" style="width:100%">
            <el-table-column prop="rule_id" label="规则" width="110" />
            <el-table-column prop="severity" label="严重度" width="80">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'Error' ? 'danger' : row.severity === 'Warning' ? 'warning' : 'info'" size="small" effect="dark">
                  {{ row.severity }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="类别" width="100" />
            <el-table-column prop="file_path" label="文件" min-width="150">
              <template #default="{ row }">
                <code class="file-path">{{ row.file_path }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="line_number" label="行" width="50" align="center" />
            <el-table-column prop="message" label="描述" min-width="250" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <!-- Dimension A Tab -->
        <el-tab-pane label="维度A · 功能与最佳实践" name="dim-a">
          <div class="round-header">
            <span class="round-badge badge-ok">已评估 {{ dimARounds.length }} 文件</span>
            <span class="round-info" v-if="dimARounds.length">
              均分: {{ assessment?.dimension_a_score?.toFixed(1) ?? '-' }} |
              Token: {{ dimARounds.reduce((s,r) => s + (r.tokens_used || 0), 0) }}
            </span>
          </div>
          <div v-for="r in dimARounds" :key="r.id" class="file-eval-block">
            <h4 class="file-eval-title">📄 {{ r.file_path }} — 评分: {{ r.score?.toFixed(0) ?? 'N/A' }}</h4>
            <p class="file-eval-summary">{{ r.output_data?.summary || '无摘要' }}</p>
            <div v-if="r.status === 'failed'" class="error-block">
              <el-alert :title="r.error_message" type="error" show-icon :closable="false" />
            </div>
            <div v-else class="findings-list">
              <div v-for="(f, i) in (r.output_data?.findings || [])" :key="i" class="finding-item">
                <div class="finding-title">
                  <el-tag :type="f.severity === 'critical' || f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'info'" size="small" effect="dark">
                    {{ f.severity || '?' }}
                  </el-tag>
                  <span class="finding-name">{{ f.title }}</span>
                  <el-tag size="small" type="info">{{ f.category }}</el-tag>
                </div>
                <div class="finding-desc">{{ f.description }}</div>
                <div v-if="f.suggestion" class="finding-suggestion">💡 {{ f.suggestion }}</div>
                <div v-if="f.line_hint" class="finding-file">📍 {{ f.line_hint }}</div>
              </div>
              <el-empty v-if="!r.output_data?.findings?.length" description="此文件未发现问题" />
            </div>
          </div>
          <el-empty v-if="!dimARounds.length" description="未执行维度A评估" />
        </el-tab-pane>

        <!-- Dimension B Tab -->
        <el-tab-pane label="维度B · 安全与性能" name="dim-b">
          <div class="round-header">
            <span class="round-badge badge-ok">已评估 {{ dimBRounds.length }} 文件</span>
            <span class="round-info" v-if="dimBRounds.length">
              均分: {{ assessment?.dimension_b_score?.toFixed(1) ?? '-' }} |
              Token: {{ dimBRounds.reduce((s,r) => s + (r.tokens_used || 0), 0) }}
            </span>
          </div>
          <div v-for="r in dimBRounds" :key="r.id" class="file-eval-block">
            <h4 class="file-eval-title">📄 {{ r.file_path }} — 评分: {{ r.score?.toFixed(0) ?? 'N/A' }}</h4>
            <p class="file-eval-summary">{{ r.output_data?.summary || '无摘要' }}</p>
            <div v-if="r.status === 'failed'" class="error-block">
              <el-alert :title="r.error_message" type="error" show-icon :closable="false" />
            </div>
            <div v-else class="findings-list">
              <div v-for="(f, i) in (r.output_data?.findings || [])" :key="i" class="finding-item">
                <div class="finding-title">
                  <el-tag :type="f.severity === 'critical' || f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'info'" size="small" effect="dark">
                    {{ f.severity || '?' }}
                  </el-tag>
                  <span class="finding-name">{{ f.title }}</span>
                  <el-tag size="small" type="info">{{ f.category }}</el-tag>
                </div>
                <div class="finding-desc">{{ f.description }}</div>
                <div v-if="f.suggestion" class="finding-suggestion">💡 {{ f.suggestion }}</div>
                <div v-if="f.line_hint" class="finding-file">📍 {{ f.line_hint }}</div>
              </div>
              <el-empty v-if="!r.output_data?.findings?.length" description="此文件未发现问题" />
            </div>
          </div>
          <el-empty v-if="!dimBRounds.length" description="未执行维度B评估" />
        </el-tab-pane>

        <!-- Diff Tab -->
        <el-tab-pane label="Diff 视图" name="diff">
          <div class="diff-view" v-if="event?.diff_content">
            <pre>{{ event.diff_content }}</pre>
          </div>
          <el-empty v-else description="无 Diff 内容" />
        </el-tab-pane>

        <!-- Summary Assessment Tab -->
        <el-tab-pane label="评估摘要" name="assessment">
          <div class="score-display">
            <div class="big-score">{{ assessment?.overall_score?.toFixed(0) ?? '-' }}</div>
            <div class="big-score-label">/100</div>
          </div>
          <div class="dim-scores" style="display:flex;gap:24px;justify-content:center;margin:8px 0;">
            <div>维度A (功能/最佳实践): <strong>{{ assessment?.dimension_a_score?.toFixed(1) ?? '-' }}</strong></div>
            <div>维度B (安全/性能/健康度): <strong>{{ assessment?.dimension_b_score?.toFixed(1) ?? '-' }}</strong></div>
          </div>
          <div class="summary-block" style="margin-top: 16px;">
            <p>{{ event?.executive_summary || '暂无评估摘要' }}</p>
          </div>
          <div v-if="event?.dimension_a_summary" class="summary-block" style="margin-top: 12px;">
            <h4>维度A 摘要</h4>
            <p>{{ event.dimension_a_summary }}</p>
          </div>
          <div v-if="event?.dimension_b_summary" class="summary-block" style="margin-top: 12px;">
            <h4>维度B 摘要</h4>
            <p>{{ event.dimension_b_summary }}</p>
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

.score-bar {
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 24px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  margin-bottom: 16px;
}

.score-circle {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  background: var(--el-color-primary-light-9);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 3px solid var(--el-color-primary);
}

.score-number {
  font-size: 28px;
  font-weight: 700;
  color: var(--el-color-primary);
  line-height: 1.2;
}

.score-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.score-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
}

.meta-label {
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.rec-text {
  text-transform: capitalize;
}

.round-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.round-badge {
  background: var(--el-color-info-light-5);
  color: var(--el-color-info);
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
}

.round-badge.badge-ok {
  background: var(--el-color-success-light-5);
  color: var(--el-color-success);
}

.round-badge.badge-fail {
  background: var(--el-color-danger-light-5);
  color: var(--el-color-danger);
}

.round-info {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.file-eval-block {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.file-eval-title {
  margin: 0 0 4px;
  font-size: 14px;
}

.file-eval-summary {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.error-block {
  margin: 8px 0;
}

.findings-list {
  margin-top: 8px;
}

.finding-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.finding-item:last-child {
  border-bottom: none;
}

.finding-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.finding-name {
  font-weight: 600;
  font-size: 13px;
}

.finding-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin: 4px 0;
}

.finding-suggestion {
  font-size: 12px;
  color: var(--el-color-success);
  margin-top: 2px;
}

.finding-file {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.file-path {
  font-size: 12px;
  background: var(--el-fill-color);
  padding: 1px 4px;
  border-radius: 3px;
}

.diff-view {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 16px;
  border-radius: 6px;
  max-height: 500px;
  overflow: auto;
}

.diff-view pre {
  margin: 0;
  font-size: 12px;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  white-space: pre-wrap;
  word-break: break-all;
}

.big-score {
  font-size: 48px;
  font-weight: 800;
  color: var(--el-color-primary);
}

.big-score-label {
  font-size: 18px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.score-display {
  text-align: center;
}

.summary-block p {
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.summary-block h4 {
  margin: 0 0 4px;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}
</style>
