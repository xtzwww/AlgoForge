// @vitest-environment jsdom
/**
 * BarChart 的组件测试。
 *
 * 和 RadarChart.spec 同一套思路：VChart 换成一个只暴露 `option` 的替身，
 * 不渲染 canvas——这里要断言的是「我们把什么喂给了 ECharts」。
 *
 * 钉住的点：
 *   1. 默认横向：类目在 y 轴、数值在 x 轴；
 *   2. max 透传给数值轴（正确率图传 1，轴就不会自己取整到 1.2）；
 *   3. 每个条的颜色：函数式 color 按 (value, index) 逐条生效；
 *   4. valueFormatter 进 tooltip 与条上标签；
 *   5. 空态与加载态分支（与雷达图相同的三态约定）；
 *   6. 画布背景透明——深色主题上白底是必现的视觉 bug。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import BarChart from './BarChart.vue'
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

interface BarProps {
  items: { label: string; value: number }[]
  height?: number
  horizontal?: boolean
  max?: number | null
  color?: string | ((value: number, index: number) => string)
  valueFormatter?: (value: number) => string
  loading?: boolean
  emptyTitle?: string
  emptyExplanation?: string
}

interface BarOption {
  backgroundColor: string
  tooltip: {
    formatter: (p: { name: string; value: number }[]) => string
  }
  xAxis: Record<string, unknown>
  yAxis: Record<string, unknown>
  series: {
    type: string
    data: { value: number; itemStyle: { color: string } }[]
    label: { formatter: (p: { value: number }) => string }
  }[]
}

async function mountBar(props: BarProps): Promise<VueWrapper> {
  const wrapper = mount(BarChart, { props })
  await nextTickSafe()
  return wrapper
}

/** 调色板在 onMounted 里解析，首次渲染的 option 是 {}——必须等一个 tick。 */
function nextTickSafe(): Promise<void> {
  return import('vue').then(({ nextTick }) => nextTick())
}

function optionOf(wrapper: VueWrapper): BarOption {
  const stub = wrapper.findComponent({ name: CHART_STUB })
  return stub.props('option') as BarOption
}

describe('BarChart', () => {
  it('默认横向：类目在 y 轴、数值在 x 轴，max 透传', async () => {
    const w = await mountBar({
      items: [
        { label: '动态规划', value: 0.4 },
        { label: '贪心', value: 0.8 }
      ],
      max: 1
    })
    const o = optionOf(w)
    expect(o.xAxis.type).toBe('value')
    expect(o.xAxis.max).toBe(1)
    expect(o.yAxis.type).toBe('category')
    expect(o.yAxis.data).toEqual(['动态规划', '贪心'])
  })

  it('纵向模式：类目在 x 轴、数值在 y 轴', async () => {
    const w = await mountBar({ items: [{ label: 'a', value: 2 }], horizontal: false })
    const o = optionOf(w)
    expect(o.xAxis.type).toBe('category')
    expect(o.yAxis.type).toBe('value')
  })

  it('颜色函数按 (value, index) 逐条生效', async () => {
    const w = await mountBar({
      items: [
        { label: 'a', value: 0.2 },
        { label: 'b', value: 0.9 }
      ],
      color: (v: number) => (v < 0.5 ? '#ff0000' : '#00ff00')
    })
    const data = optionOf(w).series[0].data
    expect(data[0].itemStyle.color).toBe('#ff0000')
    expect(data[1].itemStyle.color).toBe('#00ff00')
  })

  it('valueFormatter 同时进 tooltip 和条上标签', async () => {
    const fmt = (v: number) => `${(v * 100).toFixed(1)}%`
    const w = await mountBar({ items: [{ label: 'x', value: 0.65 }], valueFormatter: fmt })
    const o = optionOf(w)
    expect(o.tooltip.formatter([{ name: 'x', value: 0.65 }])).toBe('x: 65.0%')
    expect(o.series[0].label.formatter({ value: 0.65 })).toBe('65.0%')
  })

  it('空态：无条目时渲染 EmptyState 而不是空图', async () => {
    const w = await mountBar({
      items: [],
      emptyTitle: '暂无知识点数据',
      emptyExplanation: '还没有提交记录'
    })
    const empty = w.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('title')).toBe('暂无知识点数据')
    expect(w.findComponent({ name: CHART_STUB }).exists()).toBe(false)
  })

  it('加载态：不画图，只显示占位', async () => {
    const w = await mountBar({ items: [{ label: 'a', value: 1 }], loading: true })
    expect(w.text()).toContain('加载中…')
    expect(w.findComponent({ name: CHART_STUB }).exists()).toBe(false)
  })

  it('画布背景必须透明', async () => {
    const w = await mountBar({ items: [{ label: 'a', value: 1 }] })
    expect(optionOf(w).backgroundColor).toBe('transparent')
  })
})
