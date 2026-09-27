<script setup lang="ts">
/**
 * BarChart — 通用单系列条形图（默认横向），知识点掌握、错因次数等都用它。
 *
 * Props:
 *   items:          [{label, value}, ...]  — 调用方定顺序（最弱的在前就是
 *                                             调用方排的序，这里不重排）
 *   horizontal:     默认 true（横向条形）。纵向留给未来需要
 *   max:            y/x 轴上限；null = 自动
 *   color:          字面量或 (value, index) => color。默认取主题 accent；
 *                   画像页传 masteryColorFor(accuracy) 按掌握度分级着色
 *   valueFormatter: tooltip / 轴标签的格式化
 *
 * 主题逻辑全部来自 `useChartPalette`（见其文件头）：运行时解析 CSS 变量、
 * 监听 themeVersion 重绘。失败模式与 RadarChart 相同——颜色错但可见，
 * 而不是画一张黑图。
 */

import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart as EBarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import EmptyState from './EmptyState.vue'
import { useChartPalette } from '../composables/useChartPalette'

use([CanvasRenderer, EBarChart, GridComponent, TooltipComponent])

interface BarItem {
  label: string
  value: number
}

const props = withDefaults(
  defineProps<{
    items: BarItem[]
    height?: number
    horizontal?: boolean
    /** 轴上限；null = 让 ECharts 自动取整。正确率图表传 1。 */
    max?: number | null
    /** 字面量或按 (value, index) 返回颜色的函数。 */
    color?: string | ((value: number, index: number) => string)
    valueFormatter?: (value: number) => string
    loading?: boolean
    emptyTitle?: string
    emptyExplanation?: string
  }>(),
  {
    height: 320,
    horizontal: true,
    max: null,
    color: '',
    valueFormatter: (value: number) => `${value}`,
    loading: false,
    emptyTitle: '暂无数据',
    emptyExplanation: ''
  }
)

const { palette } = useChartPalette()

function barColor(value: number, index: number): string {
  if (typeof props.color === 'function') return props.color(value, index)
  if (props.color) return props.color
  return palette.value?.accent ?? '#6d8dff'
}

const option = computed(() => {
  const p = palette.value
  if (!p) return {}

  const names = props.items.map((it) => it.label)
  const values = props.items.map((it) => it.value)
  // 横向：类目在 y 轴、数值在 x 轴；纵向则反过来。
  const categoryAxis = {
    type: 'category' as const,
    data: names,
    axisLabel: { color: p.textSecondary, fontSize: 12 },
    axisLine: { lineStyle: { color: p.grid } },
    axisTick: { show: false }
  }
  const valueAxis = {
    type: 'value' as const,
    max: props.max === null ? undefined : props.max,
    axisLabel: { color: p.textMuted, fontSize: 11 },
    splitLine: { lineStyle: { color: p.grid } }
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: p.band,
      borderColor: p.grid,
      textStyle: { color: p.textPrimary, fontSize: 12 },
      formatter: (params: { name: string; value: number }[]) => {
        const row = params[0]
        if (!row) return ''
        return `${row.name}: ${props.valueFormatter(row.value)}`
      }
    },
    grid: {
      left: 8,
      right: 24,
      top: 12,
      bottom: 8,
      containLabel: true
    },
    xAxis: props.horizontal ? valueAxis : categoryAxis,
    yAxis: props.horizontal ? categoryAxis : valueAxis,
    series: [
      {
        type: 'bar' as const,
        data: values.map((v, i) => ({
          value: v,
          itemStyle: {
            color: barColor(v, i),
            borderRadius: props.horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0]
          }
        })),
        // 细一点更像"能量条"，与 AI 强度轴同语言。
        barWidth: 14,
        showBackground: true,
        backgroundStyle: { color: p.band, borderRadius: 6 },
        label: {
          show: true,
          position: props.horizontal ? ('right' as const) : ('top' as const),
          color: p.textMuted,
          fontSize: 11,
          formatter: (params: { value: number }) => props.valueFormatter(params.value)
        }
      }
    ]
  }
})
</script>

<template>
  <div class="bar-chart-container" :style="{ height: `${props.height}px` }">
    <div v-if="props.loading" class="chart-placeholder">加载中…</div>
    <EmptyState
      v-else-if="props.items.length === 0"
      class="chart-empty"
      variant="search"
      icon="chart"
      :title="props.emptyTitle"
      :explanation="props.emptyExplanation"
    />
    <VChart v-else class="chart" :option="option" autoresize />
  </div>
</template>

<style scoped>
.bar-chart-container {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.chart {
  width: 100%;
  height: 100%;
}

.chart-placeholder {
  font-size: 14px;
  color: var(--af-text-muted);
}

/* The EmptyState brings its own surface and border; inside this already
   bordered container that would double up. */
.chart-empty {
  width: 100%;
  height: 100%;
  background: transparent;
  border: none;
}
</style>
