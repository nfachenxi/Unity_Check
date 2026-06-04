<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
defineProps({
  events: { type: Array, default: () => [] },
})

function getRiskType(level) {
  return level === 'critical' || level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'success'
}

function formatTime(date) {
  if (!date) return '-'
  const d = new Date(date)
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function goDetail(id) {
  router.push(`/events/${id}`)
}
</script>

<template>
  <div>
    <el-table
      :data="events.slice(0, 5)"
      size="small"
      style="width: 100%"
      @row-click="({ id }) => goDetail(id)"
      row-class-name="clickable-row"
    >
      <el-table-column prop="event_type" label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.event_type === 'push' ? '' : 'info'">
            {{ row.event_type === 'push' ? 'Push' : 'PR' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="repository" label="仓库" min-width="120">
        <template #default="{ row }">
          <span class="repo-name">{{ row.repository }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="overall_score" label="评分" width="60" align="center">
        <template #default="{ row }">
          <span :class="(row.overall_score ?? 0) >= 80 ? 'score-good' : (row.overall_score ?? 0) >= 60 ? 'score-ok' : 'score-bad'">
            {{ row.overall_score?.toFixed(0) ?? '-' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="final_risk_level" label="风险" width="70" align="center">
        <template #default="{ row }">
          <el-tag :type="getRiskType(row.final_risk_level)" size="small" effect="dark">
            {{ row.final_risk_level || '-' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="100">
        <template #default="{ row }">
          <span class="time-text">{{ formatTime(row.created_at) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="view-all" @click="$emit('viewAll')">
      查看全部事件 →
    </div>
  </div>
</template>

<style scoped>
.repo-name {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.score-good { color: var(--color-success); font-family: var(--font-heading); font-weight: 600; }
.score-ok { color: var(--color-warning); font-family: var(--font-heading); font-weight: 600; }
.score-bad { color: var(--color-critical); font-family: var(--font-heading); font-weight: 600; }

.time-text {
  color: var(--color-text-secondary);
  font-size: 12px;
}

:deep(.clickable-row) {
  cursor: pointer;
}

.view-all {
  margin-top: 12px;
  text-align: center;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
  transition: opacity 200ms ease;
}

.view-all:hover {
  opacity: 0.8;
}
</style>
