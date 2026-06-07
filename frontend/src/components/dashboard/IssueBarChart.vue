<script setup>
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { computed } from 'vue'

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  distribution: { type: Object, default: null },
  height: { type: String, default: '280px' },
})

const option = computed(() => {
  const semantic = props.distribution?.semantic?.by_category || {}

  const cats = Object.keys(semantic)

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 30, right: 20, top: 20, bottom: 30 },
    xAxis: {
      type: 'category',
      data: cats,
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
        name: '语义评估',
        type: 'bar',
        data: cats.map(c => semantic[c] || 0),
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
