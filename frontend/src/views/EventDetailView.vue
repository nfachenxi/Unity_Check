<script setup>
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getEventDetail, getEventRules, getEventEvaluations, getEventAssessment } from '../api/index.js'

const props = defineProps({ id: { type: String, required: true } })
const loading = ref(true)
const event = ref(null)
const rules = ref([])
const evaluations = ref([])
const assessment = ref(null)
const activeTab = ref('assessment')

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
    rules.value = rulesRes.data || []
    evaluations.value = evalRes.data || []
    assessment.value = assessRes.data
  } catch (e) {
    ElMessage.error('加载事件详情失败')
  } finally {
    loading.value = false
  }
}

function getRiskType(level) {
  return level === 'critical' || level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'success'
}

function getEvalRound(roundNum) {
  return evaluations.value.find(r => r.round_number === roundNum) || null
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
        <div><span class="meta-label">总 Token：</span>{{ assessment.total_tokens_used?.toLocaleString() ?? '-' }}</div>
        <div><span class="meta-label">总耗时：</span>{{ formatMs(assessment.total_duration_ms) }}</div>
      </div>
    </div>

    <!-- Tabs: Assessment / R1 / R2 / R3 / Diff -->
    <div class="dashboard-card" style="margin-top: 16px;">
      <el-tabs v-model="activeTab" type="border-card">
        <!-- Round 1 -->
        <el-tab-pane label="Round 1 · 规则检测" name="round1">
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

        <!-- Round 2 -->
        <el-tab-pane label="Round 2 · 语义评估" name="round2">
          <div class="round-header">
            <span class="round-badge" :class="getEvalRound(2)?.status === 'success' ? 'badge-ok' : 'badge-fail'">
              {{ getEvalRound(2)?.status === 'success' ? '成功' : '失败' }}
            </span>
            <template v-if="getEvalRound(2)">
              <span class="round-info">Token: {{ getEvalRound(2).tokens_used }} | 耗时: {{ formatMs(getEvalRound(2).duration_ms) }}</span>
            </template>
          </div>
          <div v-if="getEvalRound(2)?.status === 'failed'" class="error-block">
            <el-alert :title="getEvalRound(2)?.error_message" type="error" show-icon :closable="false" />
          </div>
          <el-empty v-else-if="!getEvalRound(2)" description="未执行" />
          <div v-else class="findings-list">
            <div v-for="(f, i) in (getEvalRound(2)?.output_data?.findings || [])" :key="i" class="finding-item">
              <div class="finding-title">
                <el-tag :type="f.severity === 'critical' || f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'info'" size="small" effect="dark">
                  {{ f.severity || '?' }}
                </el-tag>
                <span class="finding-name">{{ f.title }}</span>
                <el-tag size="small" type="info">{{ f.category }}</el-tag>
              </div>
              <div class="finding-desc">{{ f.description }}</div>
              <div v-if="f.suggestion" class="finding-suggestion">💡 {{ f.suggestion }}</div>
              <div v-if="f.file" class="finding-file">📄 {{ f.file }}{{ f.line_hint ? ` : ${f.line_hint}` : '' }}</div>
            </div>
            <el-empty v-if="!getEvalRound(2)?.output_data?.findings?.length" description="未发现语义问题" />
          </div>
        </el-tab-pane>

        <!-- Round 3 -->
        <el-tab-pane label="Round 3 · 综合评估" name="round3">
          <div class="round-header">
            <span class="round-badge" :class="getEvalRound(3)?.status === 'success' ? 'badge-ok' : 'badge-fail'">
              {{ getEvalRound(3)?.status === 'success' ? '成功' : '失败' }}
            </span>
          </div>
          <div v-if="getEvalRound(3)?.status === 'failed'" class="error-block">
            <el-alert :title="getEvalRound(3)?.error_message" type="error" show-icon :closable="false" />
          </div>
          <div v-else-if="getEvalRound(3)" class="assessment-detail">
            <div class="summary-block">
              <h4>执行摘要</h4>
              <p>{{ event?.executive_summary || '-' }}</p>
            </div>
            <div class="issues-block">
              <h4>关键问题</h4>
              <div v-for="(iss, i) in (getEvalRound(3)?.output_data?.top_issues || [])" :key="i" class="issue-line">
                <el-tag :type="iss.severity === 'critical' || iss.severity === 'high' ? 'danger' : 'warning'" size="small" effect="dark">
                  {{ iss.severity }}
                </el-tag>
                <span>{{ iss.title }}</span>
                <el-tag size="small" type="info">来源: {{ iss.source }}</el-tag>
              </div>
            </div>
            <div class="actions-block">
              <h4>行动项</h4>
              <el-timeline>
                <el-timeline-item
                  v-for="(act, i) in (getEvalRound(3)?.output_data?.action_items || [])"
                  :key="i"
                  :color="act.priority === 'high' ? '#EF4444' : act.priority === 'medium' ? '#F59E0B' : '#3B82F6'"
                >
                  {{ act.action }}
                  <el-tag size="small">{{ act.priority }}</el-tag>
                </el-timeline-item>
              </el-timeline>
            </div>
          </div>
        </el-tab-pane>

        <!-- Diff -->
        <el-tab-pane label="Diff 视图" name="diff">
          <div class="diff-view" v-if="event?.diff_content">
            <pre>{{ event.diff_content }}</pre>
          </div>
          <el-empty v-else description="无 Diff 内容" />
        </el-tab-pane>

        <!-- Summary Assessment -->
        <el-tab-pane label="评估摘要" name="assessment">
          <div class="score-display">
            <div class="big-score">{{ assessment?.overall_score?.toFixed(0) ?? '-' }}</div>
            <div class="big-score-label">/100</div>
          </div>
          <div class="summary-block" style="margin-top: 16px;">
            <p>{{ event?.executive_summary || '暂无评估摘要' }}</p>
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
}

.score-bar {
  display: flex;
  align-items: center;
  gap: 32px;
  margin-top: 16px;
  padding: 20px 32px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
}

.score-circle {
  text-align: center;
  min-width: 100px;
}

.score-number {
  font-family: var(--font-heading);
  font-size: 48px;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1;
}

.score-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}

.score-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.meta-label {
  color: var(--color-text);
}

.rec-text {
  text-transform: capitalize;
  color: var(--color-primary);
}

.round-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.round-badge {
  font-family: var(--font-heading);
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 4px;
  background: rgba(59,130,246,0.15);
  color: var(--color-primary);
}

.badge-ok { color: var(--color-success); background: rgba(34,197,94,0.15); }
.badge-fail { color: var(--color-critical); background: rgba(239,68,68,0.15); }

.round-info {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.file-path {
  font-family: var(--font-heading);
  font-size: 12px;
  color: var(--color-primary);
}

.finding-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}

.finding-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.finding-name {
  font-weight: 500;
}

.finding-desc {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: 6px;
}

.finding-suggestion {
  color: var(--color-warning);
  font-size: 12px;
}

.finding-file {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.issue-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}

.issue-line span {
  flex: 1;
}

.summary-block p {
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.score-display {
  display: flex;
  align-items: baseline;
  gap: 4px;
  justify-content: center;
  padding: 16px 0;
}

.big-score {
  font-family: var(--font-heading);
  font-size: 64px;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1;
}

.big-score-label {
  font-size: 24px;
  color: var(--color-text-secondary);
}

.error-block {
  margin: 16px 0;
}

h4 {
  font-family: var(--font-heading);
  font-size: 14px;
  margin-bottom: 8px;
  margin-top: 16px;
  color: var(--color-text);
}
</style>
