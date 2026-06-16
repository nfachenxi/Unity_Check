<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getDashboardSummary, getDashboardTrends, getDashboardIssueDistribution } from '../api/index.js'
import StatCard from '../components/dashboard/StatCard.vue'
import TrendChart from '../components/dashboard/TrendChart.vue'
import RiskDonut from '../components/dashboard/RiskDonut.vue'
import IssueBarChart from '../components/dashboard/IssueBarChart.vue'
import RecentEventsTable from '../components/dashboard/RecentEventsTable.vue'

const router = useRouter()
const loading = ref(true)
const summary = ref(null)
const trends = ref([])
const distribution = ref(null)

async function fetchData() {
  try {
    const [s, t, d] = await Promise.all([
      getDashboardSummary({ days: 30 }),
      getDashboardTrends({ days: 30 }),
      getDashboardIssueDistribution({ days: 30 }),
    ])
    summary.value = s.data
    trends.value = t.data
    distribution.value = d.data
  } catch (e) {
    ElMessage.error('加载仪表盘数据失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function goToEvents() {
  router.push('/events')
}

onMounted(fetchData)
</script>

<template>
  <div>
    <h1 class="page-title">概览看板</h1>

    <!-- Loading Skeleton -->
    <template v-if="loading">
      <el-row :gutter="16" class="stat-row">
        <el-col v-for="i in 3" :key="i" :xs="12" :sm="8">
          <div class="dashboard-card skeleton-card skeleton-pulse">
            <div class="skeleton-line w-40"></div>
            <div class="skeleton-line w-80 h-32 mt-12"></div>
          </div>
        </el-col>
      </el-row>
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :span="16">
          <div class="dashboard-card skeleton-card skeleton-pulse">
            <div class="skeleton-line w-60"></div>
            <div class="skeleton-chart mt-16"></div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="dashboard-card skeleton-card skeleton-pulse">
            <div class="skeleton-line w-40"></div>
            <div class="skeleton-chart mt-16"></div>
          </div>
        </el-col>
      </el-row>
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :span="12">
          <div class="dashboard-card skeleton-card skeleton-pulse">
            <div class="skeleton-line w-50"></div>
            <div class="skeleton-chart mt-16"></div>
          </div>
        </el-col>
        <el-col :span="12">
          <div class="dashboard-card skeleton-card skeleton-pulse">
            <div class="skeleton-line w-40"></div>
            <div class="skeleton-chart mt-16"></div>
          </div>
        </el-col>
      </el-row>
    </template>

    <!-- Actual Content -->
    <template v-else>
      <!-- KPI Cards Row (3 columns) -->
      <el-row :gutter="16" class="stat-row">
        <el-col :xs="12" :sm="8">
          <StatCard
            title="总提交数"
            :value="summary?.total_events ?? '-'"
            unit="次"
            color="#3B82F6"
            icon="Document"
          />
        </el-col>
        <el-col :xs="12" :sm="8">
          <StatCard
            title="平均评分"
            :value="summary?.average_score ?? '-'"
            unit="/100"
            color="#22C55E"
            icon="TrendCharts"
          />
        </el-col>
        <el-col :xs="12" :sm="8">
          <StatCard
            title="高风险事件"
            :value="(summary?.risk_distribution?.critical || 0) + (summary?.risk_distribution?.high || 0)"
            unit="次"
            color="#EF4444"
            icon="WarningFilled"
          />
        </el-col>
      </el-row>

      <!-- Charts Row -->
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :span="16">
          <div class="dashboard-card chart-card">
            <h3 class="chart-title">提交趋势（近30天）</h3>
            <TrendChart :data="trends" height="320px" />
          </div>
        </el-col>
        <el-col :span="8">
          <div class="dashboard-card chart-card">
            <h3 class="chart-title">风险分布</h3>
            <RiskDonut :distribution="summary?.risk_distribution || {}" height="320px" />
          </div>
        </el-col>
      </el-row>

      <!-- Bottom Row -->
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :span="12">
          <div class="dashboard-card chart-card">
            <h3 class="chart-title">问题类型分布</h3>
            <IssueBarChart :distribution="distribution" height="280px" />
          </div>
        </el-col>
        <el-col :span="12">
          <div class="dashboard-card chart-card">
            <h3 class="chart-title">最近事件</h3>
            <RecentEventsTable
              :events="summary?.recent_events || []"
              @view-all="goToEvents"
            />
          </div>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.stat-row {
  margin-bottom: 0;
}

.chart-title {
  font-family: var(--font-heading);
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-secondary);
  margin-bottom: 16px;
}

.chart-card {
  height: 100%;
  min-height: 380px;
}

/* Skeleton styles */
.skeleton-card {
  min-height: 100px;
}

.skeleton-line {
  height: 14px;
  border-radius: 6px;
  background: var(--color-bg-elevated);
}

.skeleton-line.w-40 { width: 40%; }
.skeleton-line.w-50 { width: 50%; }
.skeleton-line.w-60 { width: 60%; }
.skeleton-line.w-80 { width: 80%; }

.skeleton-line.h-32 { height: 32px; }

.mt-12 { margin-top: 12px; }
.mt-16 { margin-top: 16px; }

.skeleton-chart {
  height: 240px;
  border-radius: 8px;
  background: var(--color-bg-elevated);
}
</style>
