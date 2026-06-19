<script setup>
import { computed } from 'vue'

const props = defineProps({
  level: { type: String, default: 'unknown' },
  size: { type: String, default: 'small' },
  effect: { type: String, default: 'dark' },
})

const LEVEL_MAP = {
  critical: { type: 'danger', label: '严重', icon: '🔴' },
  high: { type: 'danger', label: '高危', icon: '🟠' },
  medium: { type: 'warning', label: '中等', icon: '🟡' },
  low: { type: 'success', label: '低', icon: '🟢' },
  unknown: { type: 'info', label: '未知', icon: '⚪' },
}

const config = computed(() => {
  const key = (props.level || 'unknown').toLowerCase()
  return LEVEL_MAP[key] || LEVEL_MAP.unknown
})
</script>

<template>
  <el-tag :type="config.type" :size="size" :effect="effect" class="risk-tag">
    <span class="risk-tag-content">
      <span class="risk-tag-icon">{{ config.icon }}</span>
      <span class="risk-tag-label">{{ config.label }}</span>
    </span>
  </el-tag>
</template>

<style scoped>
.risk-tag-content {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.risk-tag-icon {
  font-size: 1em;
  line-height: 1;
}
.risk-tag-label {
  font-weight: 600;
}
</style>
