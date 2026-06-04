<script setup>
import { ref, onMounted, computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ElMessage } from 'element-plus'
import { getDashboardTrends, getStatsScores, getStatsHotspots } from '../api/index.js'

use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer])

const dateRange = ref([
  new Date(Date.now() - 30 * 86400000),
  new Date(),
])
const trends = ref([])
const scores = ref(null)
const hotspots = ref([])
const loading = ref(false)

function toISODate(d) {
  if (!d) return ''
  return d.toISOString().slice(0, 10)
}

async function fetchStats() {
  loading.value = true
  try {
    const params = {}
    if (dateRange.value[0]) params.from_date = toISODate(dateRange.value[0])
    if (dateRange.value[1]) params.to_date = toISODate(dateRange.value[1])

    const [trendsRes, scoresRes, hotspotsRes] = await Promise.all([
      getDashboardTrends({ days: 90 }),
      getStatsScores(params),
      getStatsHotspots({ limit: 10, days: 90 }),
    ])
    trends.value = trendsRes.data
    scores.value = scoresRes.data
    hotspots.value = hotspotsRes.data
  } catch (e) {
    ElMessage.error('加载统计数据失败')
  } finally {
    loading.value = false
  }
}

const scoreTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 20, top: 20, bottom: 30 },
  xAxis: {
    type: 'category',
    data: scores.value?.scores?.map((_, i) => i + 1) || [],
    axisLabel: { color: '#64748B' },
  },
  yAxis: {
    type: 'value', min: 0, max: 100,
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E293B' } },
  },
  series: [{
    type: 'line',
    data: scores.value?.scores || [],
    itemStyle: { color: '#3B82F6' },
    areaStyle: { color: 'rgba(59,130,246,0.1)' },
    smooth: true,
  }],
}))

const hotspotBarOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 140, right: 40, top: 10, bottom: 20 },
  xAxis: {
    type: 'value',
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E293B' } },
  },
  yAxis: {
    type: 'category',
    data: (hotspots.value || []).map(h => h.file?.split('/').pop() || h.file),
    axisLabel: { color: '#64748B', fontSize: 11 },
    inverse: true,
  },
  series: [{
    type: 'bar',
    data: (hotspots.value || []).map(h => h.count),
    itemStyle: {
      color: '#F59E0B',
      borderRadius: [0, 4, 4, 0],
    },
  }],
}))

onMounted(fetchStats)
</script>

<template>
  <div v-loading="loading">
    <h1 class="page-title">统计中心</h1>

    <!-- Date Picker -->
    <div class="dashboard-card" style="margin-bottom: 16px; padding: 16px 24px;">
      <el-row align="middle">
        <el-col :span="8">
          <span class="filter-label">日期范围</span>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            size="default"
          />
        </el-col>
        <el-col :span="4">
          <el-button type="primary" @click="fetchStats">查询</el-button>
        </el-col>
        <el-col :span="12" style="text-align: right">
          <span class="stats-summary" v-if="scores">
            评分: 最低 {{ scores.min }} / 平均 {{ scores.avg }} / 最高 {{ scores.max }}
          </span>
        </el-col>
      </el-row>
    </div>

    <el-row :gutter="16">
      <!-- Score Trend -->
      <el-col :span="16">
        <div class="dashboard-card chart-card">
          <h3 class="chart-title">评分趋势</h3>
          <VChart v-if="scores?.count" :option="scoreTrendOption" style="height:360px" autoresize />
          <el-empty v-else description="暂无数据" />
        </div>
      </el-col>

      <!-- Hotspots -->
      <el-col :span="8">
        <div class="dashboard-card chart-card">
          <h3 class="chart-title">文件热点 TOP 10</h3>
          <VChart v-if="hotspots.length" :option="hotspotBarOption" style="height:360px" autoresize />
          <el-empty v-else description="暂无数据" />
        </div>
      </el-col>
    </el-row>

    <!-- Summary Stats Cards -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="8">
        <div class="dashboard-card summary-card">
          <div class="summary-label">总事件数</div>
          <div class="summary-value">{{ scores?.count ?? '-' }}</div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="dashboard-card summary-card">
          <div class="summary-label">最高评分</div>
          <div class="summary-value" style="color: var(--color-success);">{{ scores?.max ?? '-' }}</div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="dashboard-card summary-card">
          <div class="summary-label">最低评分</div>
          <div class="summary-value" style="color: var(--color-critical);">{{ scores?.min ?? '-' }}</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.filter-label {
  margin-right: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.stats-summary {
  color: var(--color-text-secondary);
  font-family: var(--font-heading);
  font-size: 13px;
}

.chart-title {
  font-family: var(--font-heading);
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-secondary);
  margin-bottom: 12px;
}

.chart-card {
  min-height: 400px;
}

.summary-card {
  text-align: center;
  padding: 24px;
}

.summary-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}

.summary-value {
  font-family: var(--font-heading);
  font-size: 36px;
  font-weight: 700;
  color: var(--color-text);
}
</style>
