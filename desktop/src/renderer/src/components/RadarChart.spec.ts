// @vitest-environment jsdom
/**
 * RadarChart 的组件测试。
 *
 * 它是 `ProfileView` 的雷达图包装：页面负责取数，它负责把 `indicators` /
 * `values` 变成 ECharts 的 option。所以这里**不渲染 canvas**，只断言
 * 「传进去的 option 对不对」——canvas 在 jsdom 里画不出来，而画不出来
 * 并不等于 option 是对的；`VChart` 被换成一个只把 `option` 暴露出来的替身。
 *
 * 为什么值得测：这个组件的失败模式全是**静默**的。
 *   - `getComputedStyle` 读不到 token 时返回 `''`，不抛错；直接把它交给
 *     ECharts 就是一张黑图或透明图。`readToken` 的回退表就是为了吸收这个，
 *     但回退表本身没有测试保护，改坏了没人知道。
 *   - 三个分支（加载中 / 无指标 / 有图）里，**无指标**那条最容易被写成
 *     「画一张空图」——空图看着像数据加载失败，而实际是「这个学生还没有
 *     任何知识点统计」。
 *   - tooltip 的百分比格式化只在这里做一次，页面不参与；写错就是全站
 *     的雷达图数字都错。
 *
 * 钉住的七条：
 *   1. 三个分支互斥：加载中不给图、无指标给空状态（用调用方给的文案）、
 *      有指标才给图；
 *   2. 轴与数值原样进 option（页面给什么，图就画什么，中间不做转换）；
 *   3. tooltip 把比例转成一位小数的百分比、用 `<br/>` 连接，并且**按轴序**
 *      取名字；
 *   4. 值比轴多时不抛错——数据源加了新维度而轴还没跟上是必然会发生的事；
 *   5. 颜色来自 token 的回退字面量（jsdom 里没有 tokens.css，正好走回退分支）
 *      ——这条是回退表唯一的保护；
 *   6. 画布背景必须透明，否则 ECharts 默认的白底会在深色页面上画出一个
 *      白方块；
 *   7. **CSS Color 4 的 `rgb(r g b / a)` 必须先归一化**，否则 zrender 解析
 *      不了、网格线静默丢失。这条是 2026-09-25 修完一个真实 bug 后补的：
 *      `--af-border` 改成半透明写法的那一刻，雷达图的网格就没了，而屏幕上
 *      没有任何报错。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import RadarChart from './RadarChart.vue'
import EmptyState from './EmptyState.vue'

/**
 * `VChart` 替身：只保留 `option` 这个入参，不碰 canvas。
 *
 * 定义在工厂**里面**而不是外面：`vi.mock` 的工厂会被提升到所有 import 之前，
 * 引用外层的 `const` 会报 `Cannot access 'X' before initialization`。
 * 拿回它的方式是 `findComponent({ name: CHART_STUB })`。
 */
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

const INDICATORS = [
  { name: '动态规划', max: 1 },
  { name: '贪心', max: 1 }
]

/**
 * 挂载后必须 `await nextTick()` 才能读到 option。
 *
 * 调色板是 `onMounted` 里解析的，而首次渲染发生在 mounted 之前——那一刻
 * `chartPalette` 还是 null，option 是 `{}`。设置 ref 引发的重渲染是异步的，
 * 所以不 await 只会读到空对象，而且**不会报错**（`{}.radar` 只是 undefined，
 * 要等断言才炸）。这是本文件唯一一个非显然的写法。
 */
/**
 * 参数按组件自己的 props 类型写，而不是 `Record<string, unknown>`。
 *
 * 类型检查覆盖测试文件（`tsconfig.web.json` 含 `src/renderer/src/**`），
 * 所以写宽了会编译失败——这正是这套配置想要的：测试代码和被测代码受同一套
 * 约束，不存在「测试里可以随便写」的口子。
 */
interface ChartProps {
  indicators: { name: string; max: number }[]
  values: number[]
  loading?: boolean
  emptyTitle?: string
  emptyExplanation?: string
}

/**
 * 断言用到的 option 片段。不引 ECharts 自己的类型：这里要钉的是「我们传了什么」，
 * 不是「ECharts 接受什么」，用宽类型反而会把 ECharts 的版本差异引进来。
 */
interface RadarOption {
  backgroundColor: string
  tooltip: {
    formatter: (p: { name: string; value: number[] }) => string
    borderColor: string
  }
  radar: {
    indicator: { name: string; max: number }[]
    splitArea: { areaStyle: { color: string[] } }
    splitLine: { lineStyle: { color: string } }
    axisLine: { lineStyle: { color: string } }
  }
  series: {
    type: string
    data: {
      value: number[]
      lineStyle: { color: string }
      areaStyle: { color: string }
    }[]
  }[]
}

/** 挂载，但不假定图渲染了——用于「不该有图」的那两个分支。 */
async function mountRadar(
  props: ChartProps
): Promise<{ w: VueWrapper; option: RadarOption | undefined }> {
  const w = mount(RadarChart, { props })
  await w.vm.$nextTick()

  const chart = w.findComponent({ name: CHART_STUB })
  return { w, option: chart.exists() ? (chart.props('option') as RadarOption) : undefined }
}

/**
 * 挂载并取回 option，**要求图确实渲染了**。
 *
 * 用抛错而不是非空断言：这样「本该有图却没有」会以一句说人话的失败信息出现，
 * 而不是 `Cannot read properties of undefined`。
 */
async function mountChart(props: ChartProps): Promise<{ w: VueWrapper; option: RadarOption }> {
  const mounted = await mountRadar(props)
  if (!mounted.option) {
    throw new Error('RadarChart 没有渲染图，但这个用例假定它渲染了')
  }
  return { w: mounted.w, option: mounted.option }
}

describe('RadarChart', () => {
  it('加载中只给占位文案，不给图也不给空状态', async () => {
    const { w } = await mountRadar({
      indicators: INDICATORS,
      values: [0.5, 0.5],
      loading: true
    })
    expect(w.find('.radar-placeholder').text()).toBe('加载中…')
    expect(w.findComponent({ name: CHART_STUB }).exists()).toBe(false)
    expect(w.findComponent(EmptyState).exists()).toBe(false)
  })

  it('没有指标时给空状态，并用调用方给的文案——空图会被读成加载失败', async () => {
    const { w } = await mountRadar({
      indicators: [],
      values: [],
      emptyTitle: '还没有知识点统计',
      emptyExplanation: '做完一次带标签的提交后就会出现'
    })
    expect(w.findComponent({ name: CHART_STUB }).exists()).toBe(false)

    const empty = w.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('title')).toBe('还没有知识点统计')
    expect(empty.props('explanation')).toBe('做完一次带标签的提交后就会出现')
    // 「你筛空了」语气，不是首次使用那种邀请语气。
    expect(empty.props('variant')).toBe('search')
  })

  it('轴与数值原样进 option', async () => {
    const { option } = await mountChart({ indicators: INDICATORS, values: [0.65, 0.8] })
    expect(option.radar.indicator).toEqual(INDICATORS)
    expect(option.series[0].type).toBe('radar')
    expect(option.series[0].data[0].value).toEqual([0.65, 0.8])
  })

  it('tooltip 按轴序取名字，比例转成一位小数的百分比', async () => {
    const { option } = await mountChart({ indicators: INDICATORS, values: [0.65, 0.8] })
    expect(option.tooltip.formatter({ name: '正确率', value: [0.65, 0.8] })).toBe(
      '动态规划: 65.0%<br/>贪心: 80.0%'
    )
  })

  it('值比轴多时多出来的部分不带名字，但不抛错', async () => {
    const { option } = await mountChart({ indicators: [INDICATORS[0]], values: [0.5, 0.9] })
    expect(option.tooltip.formatter({ name: '正确率', value: [0.5, 0.9] })).toBe(
      '动态规划: 50.0%<br/>: 90.0%'
    )
  })

  it('颜色走 token 回退字面量，画布背景透明', async () => {
    const { option } = await mountChart({ indicators: INDICATORS, values: [0.65, 0.8] })
    // jsdom 里没有 tokens.css，`getComputedStyle` 一律返回 ''，所以这里量到的
    // 正是回退表本身：把 accent 的回退改成空串，图就变成看不见的了。
    expect(option.series[0].data[0].lineStyle.color).toBe('#6d8dff')
    expect(option.series[0].data[0].areaStyle.color).toBe('rgba(109, 141, 255, 0.28)')
    expect(option.radar.splitArea.areaStyle.color).toHaveLength(5)
    expect(option.backgroundColor).toBe('transparent')
  })

  it('CSS Color 4 的 `rgb(r g b / a)` 归一化成 zrender 认得的写法', async () => {
    /*
     * 这条钉的是一个**真实的、静默的** bug，2026-09-25 修。
     *
     * `--af-border` 在 tokens.css 里写成 `rgb(122 162 255 / 0.14)`——细边框
     * 必须半透明才能透出背后的画布，不透明的 hex 表达不了。但 zrender 的颜色
     * 解析器只认 hex 和逗号写法，遇到空格斜杠写法不报错，只是**画成别的颜色**。
     * 雷达图的网格线、轴线、tooltip 边框都读这个 token，于是它们会一起消失，
     * 而屏幕上没有任何提示。
     *
     * 所以 `resolvePalette()` 里每个 token 都过一遍 `normalizeColor()`；这条
     * 用例就是防止有人哪天把那层归一化当成多余的。
     *
     * `vi.resetModules()` 重新加载组件，使前面的用例里对
     * `getComputedStyle` 的桩不影响这里读到的样式——这里要量的是新的
     * getComputedStyle 返回值，而不是别的用例留下的状态。
     */
    vi.resetModules()

    // 只让 border 返回真实值，其余仍走回退——这样断言里的 alpha 只可能来自
    // 归一化，不可能来自别处。
    vi.stubGlobal('getComputedStyle', () => ({
      getPropertyValue: (name: string) => (name === '--af-border' ? 'rgb(122 162 255 / 0.14)' : '')
    }))

    try {
      const { default: FreshRadarChart } = await import('./RadarChart.vue')
      const w = mount(FreshRadarChart, {
        props: { indicators: INDICATORS, values: [0.5, 0.5] }
      })
      await w.vm.$nextTick()

      const chart = w.findComponent({ name: CHART_STUB })
      expect(chart.exists()).toBe(true)
      const option = chart.props('option') as RadarOption

      const expected = 'rgba(122, 162, 255, 0.14)'
      expect(option.radar.splitLine.lineStyle.color).toBe(expected)
      expect(option.radar.axisLine.lineStyle.color).toBe(expected)
      expect(option.tooltip.borderColor).toBe(expected)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
