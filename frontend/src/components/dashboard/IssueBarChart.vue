<script setup>
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { computed } from 'vue'

use([BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps({
  distribution: { type: Object, default: null },
  height: { type: String, default: '280px' },
})

const option = computed(() => {
  const rules = props.distribution?.rules?.by_category || {}
  const semantic = props.distribution?.semantic?.by_category || {}

  const allCats = [...new Set([...Object.keys(rules), ...Object.keys(semantic)])]

  return {
    tooltip: { trigger: 'axis' },
    legend: {
      data: ['规则检测', '语义评估'],
      textStyle: { color: '#94A3B8' },
      top: 0,
    },
    grid: { left: 30, right: 20, top: 40, bottom: 30 },
    xAxis: {
      type: 'category',
      data: allCats,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748B', fontSize: 10, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#1E293B' } },
      axisLabel: { color: '#64748B', fontSize: 11 },
    },
    series: [
      {
        name: '规则检测',
        type: 'bar',
        data: allCats.map(c => rules[c] || 0),
        itemStyle: { color: '#3B82F6', borderRadius: [4, 4, 0, 0] },
      },
      {
        name: '语义评估',
        type: 'bar',
        data: allCats.map(c => semantic[c] || 0),
        itemStyle: { color: '#6366F1', borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <VChart v-if="distribution" :option="option" :style="{ height: height }" autoresize />
  <el-empty v-else description="暂无数据" />
</template>
