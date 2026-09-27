<script setup lang="ts">
/**
 * RadarChart — a reusable ECharts radar wrapper (P1-22, rethemed in PU-9).
 *
 * Props:
 *   indicators: [{name, max}, ...]  — axis definitions
 *   values:     [0.65, 0.80, ...]   — one value per indicator
 *   loading:    boolean             — shows a loading placeholder
 *   emptyTitle / emptyExplanation   — the empty state's copy, so the
 *                                     caller owns wording and this
 *                                     component owns presentation
 *
 * The component is intentionally generic — it doesn't know about
 * knowledge tags or accuracy. ProfileView is responsible for
 * fetching the data and passing it in.
 *
 * ---------------------------------------------------------------------
 * **PU-9: why this file reads CSS variables at runtime.**
 *
 * ECharts' option object takes literal colours. It does not resolve
 * `var(--af-surface)`, and unlike a stylesheet there is no point at which
 * the browser substitutes one — the value is handed to a canvas painter.
 * So "use the PU-0 tokens" cannot mean writing `var()` here; it has to
 * mean *reading* the tokens and passing the resolved values through.
 *
 * Two alternatives were rejected:
 *
 *   1. Importing `assets/tokens.ts` directly. It would work, and it is
 *      what this file roughly did before (via hex literals that happened
 *      to match). But it makes the chart's colours independent of the
 *      running theme: if a `prefers-color-scheme: light` block ever
 *      lands in `tokens.css`, the CSS-driven components would follow it
 *      and the radar would not, silently.
 *   2. A duplicated hex table in this file. That is what PU-0 existed to
 *      delete.
 *
 * `getComputedStyle` is read when the component mounts **and** whenever
 * the user switches visual style (the `themeVersion` watcher re-resolves
 * it). It is no longer cached across the whole module: the app offers a
 * live theme switcher, so the stylesheet *can* change at runtime, and a
 * cached palette would freeze the chart in the theme it was first drawn
 * in. Re-resolving on each switch is cheap — one style resolution for the
 * document element — and the `option` computed rebuilds from the new
 * ref.
 *
 * **Degradation matters here.** `getComputedStyle(...).getPropertyValue()`
 * returns `''` for a name that is not defined — no exception, no warning.
 * A typo in a token name would therefore hand ECharts an empty string and
 * paint the chart black or transparent, which is exactly the class of
 * silent failure the PU-0 notes keep coming back to. `readToken` falls
 * back to a literal per token instead, so a missing variable is a wrong
 *-but-visible colour rather than an invisible chart.
 */

import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import EmptyState from './EmptyState.vue'
import { useChartPalette, withAlpha } from '../composables/useChartPalette'

use([CanvasRenderer, RadarChart, TooltipComponent])

interface Indicator {
  name: string
  max: number
}

const props = withDefaults(
  defineProps<{
    indicators: Indicator[]
    values: number[]
    loading?: boolean
    /** Copy for the no-indicators state; the caller owns the wording. */
    emptyTitle?: string
    emptyExplanation?: string
  }>(),
  {
    loading: false,
    emptyTitle: '暂无数据',
    emptyExplanation: ''
  }
)

/**
 * Legend is no longer registered: a single-series radar with one implicit
 * legend entry ("正确率") was taking a row of vertical space above the
 * chart to restate the axis labels. PU-9 dropped it rather than restyling
 * it — see the series below, which now names its own data point.
 */

const { palette: chartPalette } = useChartPalette()

const option = computed(() => {
  const p = chartPalette.value
  // Before mount there is nothing to draw for; the template's `v-else`
  // branch renders, and this option is never passed to a live chart.
  if (!p) return {}

  return {
    // The canvas is transparent so the panel's own surface shows through;
    // ECharts would otherwise paint its default white background and the
    // chart would be a light rectangle on a dark page.
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: p.band,
      borderColor: p.grid,
      textStyle: { color: p.textPrimary, fontSize: 12 },
      formatter: (params: { name: string; value: number[] }) => {
        const lines = params.value.map(
          (v, i) => `${props.indicators[i]?.name ?? ''}: ${(v * 100).toFixed(1)}%`
        )
        return lines.join('<br/>')
      }
    },
    radar: {
      indicator: props.indicators,
      shape: 'polygon' as const,
      splitNumber: 5,
      // A little inset so long axis labels ("动态规划") are not clipped at
      // the container edge; the default is 0 and the labels sit exactly on it.
      radius: '68%',
      center: ['50%', '52%'],
      axisName: {
        color: p.textSecondary,
        fontSize: 13
      },
      splitArea: {
        areaStyle: {
          // Alternating bands, both from tokens, so the grid reads as
          // concentric rings without introducing a colour of its own.
          color: [p.band, p.bandAlt, p.band, p.bandAlt, p.band]
        }
      },
      splitLine: {
        lineStyle: { color: p.grid }
      },
      axisLine: {
        lineStyle: { color: p.grid }
      }
    },
    series: [
      {
        name: '掌握度',
        type: 'radar',
        symbolSize: 5,
        data: [
          {
            value: props.values,
            name: '正确率',
            areaStyle: { color: withAlpha(p.accent, 0.28) },
            lineStyle: { color: p.accent, width: 2 },
            itemStyle: { color: p.accent },
            label: {
              // Off by default: five percentages around a pentagon is
              // noise. The tooltip carries them on demand.
              show: false,
              color: p.textPrimary
            }
          }
        ]
      }
    ]
  }
})
</script>

<template>
  <div class="radar-chart-container">
    <div v-if="props.loading" class="radar-placeholder">加载中…</div>
    <EmptyState
      v-else-if="props.indicators.length === 0"
      class="radar-empty"
      variant="search"
      icon="chart"
      :title="props.emptyTitle"
      :explanation="props.emptyExplanation"
    />
    <VChart v-else class="chart" :option="option" autoresize />
  </div>
</template>

<style scoped>
.radar-chart-container {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 420px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.chart {
  width: 100%;
  height: 100%;
}

.radar-placeholder {
  font-size: 14px;
  color: var(--af-text-muted);
}

/* The EmptyState brings its own surface and border; inside this already
 * bordered container that would double up. */
.radar-empty {
  width: 100%;
  height: 100%;
  background: transparent;
  border: none;
}
</style>
