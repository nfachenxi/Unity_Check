<script setup>
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { computed } from 'vue'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps({
  data: { type: Array, default: () => [] },
  height: { type: String, default: '320px' },
})

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: {
    data: ['事件数', '平均分'],
    textStyle: { color: '#94A3B8' },
    top: 0,
  },
  grid: { left: 40, right: 20, top: 40, bottom: 30 },
  xAxis: {
    type: 'category',
    data: props.data.map(d => d.date?.slice(5) || d.date),
    axisLine: { lineStyle: { color: '#334155' } },
    axisLabel: { color: '#64748B', fontSize: 11 },
  },
  yAxis: [
    {
      type: 'value',
      name: '事件数',
      nameTextStyle: { color: '#94A3B8', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#1E293B' } },
      axisLabel: { color: '#64748B', fontSize: 11 },
    },
    {
      type: 'value',
      name: '评分',
      min: 0,
      max: 100,
      nameTextStyle: { color: '#94A3B8', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { show: false },
      axisLabel: { color: '#64748B', fontSize: 11 },
    },
  ],
  series: [
    {
      name: '事件数',
      type: 'line',
      data: props.data.map(d => d.event_count || 0),
      itemStyle: { color: '#3B82F6' },
      lineStyle: { width: 2 },
      symbol: 'circle',
      symbolSize: 4,
      smooth: true,
    },
    {
      name: '平均分',
      type: 'line',
      yAxisIndex: 1,
      data: props.data.map(d => d.avg_score ?? null),
      itemStyle: { color: '#22C55E' },
      lineStyle: { type: 'dashed', width: 2 },
      symbol: 'diamond',
      symbolSize: 6,
      smooth: true,
    },
  ],
}))
</script>

<template>
  <VChart :option="option" :style="{ height: height }" autoresize />
</template>
