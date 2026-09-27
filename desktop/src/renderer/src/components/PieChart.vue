<script setup lang="ts">
/**
 * PieChart — 通用环形图（donut），错因分布等占比数据用它。
 *
 * Props:
 *   items:        [{name, value}, ...]  — 调用方定顺序与文案
 *   color:        颜色数组；缺省时从主题色派生一组（accent/success/warning/
 *                 danger + 各降透明度一档），超过 8 项让 ECharts 自己循环
 *   valueFormatter: tooltip 里的数值格式化（调用方通常补一个"占比"）
 *   centerText:   环形中心的静态文本（如"共 12 次错因"）；null 不显示
 *   showLegend:   是否显示底部图例（默认不显示——tooltip 已带占比）
 *
 * 主题逻辑与 BarChart 一样来自 `useChartPalette`。
 */

import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart as EPieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import EmptyState from './EmptyState.vue'
import { useChartPalette, withAlpha } from '../composables/useChartPalette'

use([CanvasRenderer, EPieChart, LegendComponent, TooltipComponent])

interface PieItem {
  name: string
  value: number
}

const props = withDefaults(
  defineProps<{
    items: PieItem[]
    height?: number
    color?: string[]
    valueFormatter?: (value: number, name: string) => string
    centerText?: string | null
    showLegend?: boolean
    loading?: boolean
    emptyTitle?: string
    emptyExplanation?: string
  }>(),
  {
    height: 320,
    color: () => [],
    valueFormatter: (value: number) => `${value}`,
    centerText: null,
    showLegend: false,
    loading: false,
    emptyTitle: '暂无数据',
    emptyExplanation: ''
  }
)

const { palette } = useChartPalette()

/** 缺省色板：主题四色 + 各 55% 透明度一档，最多 8 种。 */
function defaultColors(p: NonNullable<ReturnType<typeof useChartPalette>['palette']['value']>): string[] {
  return [
    p.accent,
    p.success,
    p.warning,
    p.danger,
    withAlpha(p.accent, 0.55),
    withAlpha(p.success, 0.55),
    withAlpha(p.warning, 0.55),
    withAlpha(p.danger, 0.55)
  ]
}

const option = computed(() => {
  const p = palette.value
  if (!p) return {}

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item' as const,
      backgroundColor: p.band,
      borderColor: p.grid,
      textStyle: { color: p.textPrimary, fontSize: 12 },
      formatter: (params: { name: string; value: number; percent: number }) =>
        `${params.name}: ${props.valueFormatter(params.value, params.name)}（${params.percent}%）`
    },
    legend: props.showLegend
      ? {
          bottom: 0,
          textStyle: { color: p.textSecondary, fontSize: 11 },
          itemWidth: 10,
          itemHeight: 10
        }
      : undefined,
    series: [
      {
        type: 'pie' as const,
        color: props.color.length > 0 ? props.color : defaultColors(p),
        radius: ['52%', '74%'],
        center: ['50%', props.showLegend ? '46%' : '50%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderColor: p.surface,
          borderWidth: 2,
          borderRadius: 4
        },
        label: { show: false },
        emphasis: {
          label: { show: true, color: p.textPrimary, fontSize: 13, fontWeight: 600 },
          itemStyle: {
            // 悬停时把那一片提亮，而不是让别的片变暗——在深色主题上
            // "高亮"比"调暗"更可读。
            shadowBlur: 12,
            shadowColor: withAlpha(p.accent, 0.45)
          }
        },
        data: props.items.map((it) => ({ name: it.name, value: it.value }))
      }
    ],
    // 环形中心文本用 title 的副标题放；主标题留空，避免占位。
    title: props.centerText
      ? {
          text: props.centerText,
          left: 'center',
          top: '42%',
          textStyle: {
            color: p.textSecondary,
            fontSize: 13,
            fontWeight: 500
          }
        }
      : undefined
  }
})
</script>

<template>
  <div class="pie-chart-container" :style="{ height: `${props.height}px` }">
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
.pie-chart-container {
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

.chart-empty {
  width: 100%;
  height: 100%;
  background: transparent;
  border: none;
}
</style>
