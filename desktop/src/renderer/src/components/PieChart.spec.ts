// @vitest-environment jsdom
/**
 * PieChart 的组件测试。
 *
 * 与 BarChart.spec 同一套替身思路。钉住的点：
 *   1. 环形（donut）：radius 是 ['52%','74%']，不是实心饼；
 *   2. 数据原样进 series（name/value 不转换）；
 *   3. tooltip 输出 name + 数值 + 占比（调用方可以只传次数，占比由 ECharts 给）；
 *   4. centerText 进 title.text，null 时不占位；
 *   5. showLegend 开关；
 *   6. 空态 / 加载态 / 透明背景。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import PieChart from './PieChart.vue'
import EmptyState from './EmptyState.vue'

const CHART_STUB = 'VChartStub'

vi.mock('vue-echarts', async () => {
  const { defineComponent } = await import('vue')
  return {
    default: defineComponent({
      name: 'VChartStub',
      props: {
        option: { type: Object, required: true },
        autoresize: { type: Boolean, default: false }
      },
      template: '<div class="vchart-stub" />'
    })
  }
})

interface PieProps {
  items: { name: string; value: number }[]
  height?: number
  color?: string[]
  valueFormatter?: (value: number, name: string) => string
  centerText?: string | null
  showLegend?: boolean
  loading?: boolean
  emptyTitle?: string
  emptyExplanation?: string
}

interface PieOption {
  backgroundColor: string
  tooltip: {
    formatter: (p: { name: string; value: number; percent: number }) => string
  }
  legend?: { bottom: number }
  title?: { text: string }
  series: {
    type: string
    radius: string[]
    data: { name: string; value: number }[]
    itemStyle: { borderColor: string }
  }[]
}

async function mountPie(props: PieProps): Promise<VueWrapper> {
  const wrapper = mount(PieChart, { props })
  await import('vue').then(({ nextTick }) => nextTick())
  return wrapper
}

function optionOf(wrapper: VueWrapper): PieOption {
  const stub = wrapper.findComponent({ name: CHART_STUB })
  return stub.props('option') as PieOption
}

const ITEMS = [
  { name: '边界条件', value: 3 },
  { name: '复杂度分析', value: 1 }
]

describe('PieChart', () => {
  it('环形：radius 是双值 donut，数据原样进 series', async () => {
    const w = await mountPie({ items: ITEMS })
    const o = optionOf(w)
    expect(o.series[0].type).toBe('pie')
    expect(o.series[0].radius).toEqual(['52%', '74%'])
    expect(o.series[0].data).toEqual(ITEMS)
  })

  it('tooltip 带占比：name + 格式化数值 + percent', async () => {
    const w = await mountPie({ items: ITEMS, valueFormatter: (v) => `${v} 次` })
    const out = optionOf(w).tooltip.formatter({ name: '边界条件', value: 3, percent: 75 })
    expect(out).toContain('边界条件: 3 次')
    expect(out).toContain('75%')
  })

  it('centerText 进 title.text；不传则不占位', async () => {
    const w1 = await mountPie({ items: ITEMS, centerText: '共 4 次错因' })
    expect(optionOf(w1).title?.text).toBe('共 4 次错因')

    const w2 = await mountPie({ items: ITEMS })
    expect(optionOf(w2).title).toBeUndefined()
  })

  it('showLegend 控制底部图例', async () => {
    const w = await mountPie({ items: ITEMS, showLegend: true })
    expect(optionOf(w).legend?.bottom).toBe(0)
  })

  it('空态 / 加载态：不给图', async () => {
    const wEmpty = await mountPie({ items: [], emptyTitle: '暂无错题记录' })
    const empty = wEmpty.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('title')).toBe('暂无错题记录')
    expect(wEmpty.findComponent({ name: CHART_STUB }).exists()).toBe(false)

    const wLoading = await mountPie({ items: ITEMS, loading: true })
    expect(wLoading.text()).toContain('加载中…')
    expect(wLoading.findComponent({ name: CHART_STUB }).exists()).toBe(false)
  })

  it('画布背景透明，片间用 surface 色分隔', async () => {
    const w = await mountPie({ items: ITEMS })
    const o = optionOf(w)
    expect(o.backgroundColor).toBe('transparent')
    expect(o.series[0].itemStyle.borderColor).toBeTruthy()
  })
})
