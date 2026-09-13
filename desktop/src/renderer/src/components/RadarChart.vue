<script setup lang="ts">
/**
 * RadarChart — a reusable ECharts radar chart wrapper (P1-22).
 *
 * Props:
 *   indicators: [{name, max}, ...]  — axis definitions
 *   values:     [0.65, 0.80, ...]    — one value per indicator
 *   loading:    boolean              — shows a loading placeholder
 *
 * The component is intentionally generic — it doesn't know about
 * knowledge tags or accuracy. ProfileView is responsible for
 * fetching the data and passing it in.
 */
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'

use([CanvasRenderer, RadarChart, TooltipComponent, LegendComponent])

interface Indicator {
  name: string
  max: number
}

const props = withDefaults(
  defineProps<{
    indicators: Indicator[]
    values: number[]
    loading?: boolean
  }>(),
  {
    loading: false,
  },
)

const option = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (params: { name: string; value: number[] }) => {
      const lines = params.value.map(
        (v, i) => `${props.indicators[i]?.name ?? ''}: ${(v * 100).toFixed(1)}%`,
      )
      return lines.join('<br/>')
    },
  },
  radar: {
    indicator: props.indicators,
    shape: 'polygon' as const,
    splitNumber: 5,
    axisName: {
      color: '#e5e7eb',
      fontSize: 13,
    },
    splitArea: {
      areaStyle: {
        color: ['#1e293b', '#16213e', '#1e293b', '#16213e', '#1e293b'],
      },
    },
    splitLine: {
      lineStyle: { color: '#374151' },
    },
    axisLine: {
      lineStyle: { color: '#374151' },
    },
  },
  series: [
    {
      name: '掌握度',
      type: 'radar',
      data: [
        {
          value: props.values,
          name: '正确率',
          areaStyle: { color: 'rgba(59, 130, 246, 0.3)' },
          lineStyle: { color: '#3b82f6', width: 2 },
          itemStyle: { color: '#3b82f6' },
        },
      ],
    },
  ],
}))
</script>

<template>
  <div class="radar-chart-container">
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="indicators.length === 0" class="empty">
      暂无数据。完成一些题目后再来看看能力画像。
    </div>
    <VChart v-else class="chart" :option="option" autoresize />
  </div>
</template>

<style scoped>
.radar-chart-container {
  width: 100%;
  height: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #16213e;
  border-radius: 8px;
}

.chart {
  width: 100%;
  height: 100%;
}

.loading,
.empty {
  color: #6b7280;
  font-size: 14px;
}
</style>
