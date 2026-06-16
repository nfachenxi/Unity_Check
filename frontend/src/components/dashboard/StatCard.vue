<script setup>
defineProps({
  title: { type: String, required: true },
  value: { type: [String, Number], default: '-' },
  unit: { type: String, default: '' },
  color: { type: String, default: '#3B82F6' },
  icon: { type: String, default: 'HelpFilled' },
})
</script>

<template>
  <div class="stat-card" :style="{ '--card-color': color }">
    <div class="stat-body">
      <div class="stat-title">{{ title }}</div>
      <div class="stat-value">
        <span class="value-num">{{ value }}</span>
        <span v-if="unit" class="value-unit">{{ unit }}</span>
      </div>
    </div>
    <div class="stat-icon-box" :style="{ background: color + '18' }">
      <el-icon :size="26" :color="color">
        <component :is="icon" />
      </el-icon>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition: border-color 200ms ease, box-shadow 200ms ease, transform 200ms ease;
  box-shadow: var(--shadow-sm);
}

.stat-card:hover {
  border-color: var(--card-color);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(59, 130, 246, 0.06);
  transform: translateY(-1px);
}

.stat-title {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.stat-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.value-num {
  font-family: var(--font-heading);
  font-size: 28px;
  font-weight: 700;
  color: var(--color-text);
}

.value-unit {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.stat-icon-box {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
