<script setup>
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { computed } from 'vue'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps({
  distribution: { type: Object, default: () => ({}) },
  height: { type: String, default: '320px' },
})

const colors = { critical: '#EF4444', high: '#F59E0B', medium: '#3B82F6', low: '#22C55E', unknown: '#64748B' }

const option = computed(() => {
  const data = Object.entries(props.distribution || {}).map(([k, v]) => ({
    name: k,
    value: v,
    itemStyle: { color: colors[k] || '#64748B' },
  }))

  return {
    tooltip: {
      trigger: 'item',
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      orient: 'vertical',
      left: 'left',
      top: 'center',
      textStyle: { color: '#94A3B8' },
    },
    series: [{
      type: 'pie',
      radius: ['55%', '80%'],
      center: ['60%', '50%'],
      avoidLabelOverlap: false,
      label: { show: false },
      emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
      data: data.length > 0 ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#334155' } }],
    }],
  }
})
</script>

<template>
  <VChart :option="option" :style="{ height: height }" autoresize />
</template>
