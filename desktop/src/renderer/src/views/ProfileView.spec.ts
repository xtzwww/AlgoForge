// @vitest-environment jsdom
/**
 * 能力画像页的视图测试（T37）。
 *
 * 这是 `views/` 下的第二个 spec（第一个是 T31 的 `MistakesView.spec.ts`）。
 * 挑它先写不是因为它最短，而是因为**它的失败模式全是静默的**：
 *
 * * **雷达图的闸门算错了对象。** 门槛是「总提交数 ≥ 3」，不是「知识点数 ≥ 3」。
 *   写成后者时，一个只提交过两次的学生也会拿到一张图——图上只有两个点，
 *   看着像数据，其实什么也没说。不报错、不空白，只是多了一张图。
 * * **总正确率是加权还是平均。** 页面用的是「通过次数 ÷ 提交次数」；换成
 *   「各知识点正确率的平均」之后，一个只提交过一次的知识点与一个提交过九次
 *   的等权。两者在**多数用例下恰好相等**，只有样本极不均衡时才分得开——
 *   所以下面专门造了 1/1 与 0/9 这一对。
 * * **「加载中」与「还没有数据」长得一样。** 请求回来之前 `stats` 就是空数组，
 *   空状态很容易被提前画出来：学生先看到「还没有可分析的数据」，再看着它消失。
 * * **进度条 0% 的可见性。** `width` 有个 2% 的下限；去掉它，正确率为 0 的
 *   那一行会退化成一条看不见的线，与「这一行没渲染」无法区分。
 *
 * 写这组用例时发现并修掉了一处**真实的误导**（见最后两条）：请求失败时
 * `stats` 被清空，而页面照着一份空数组算出了「总正确率 0.0% · 0 / 0 次通过」
 * ——那是在陈述一个学生并没有的事实。姊妹页 `MistakesView` 早就是「失败时
 * 只留错误横幅」的做法（它的列表挂在独立的 `v-if` 上，不在 `v-if/v-else` 链
 * 里），这里是链尾的 `v-else` 把它漏了进去，已改成 `v-else-if="!errorMsg"`。
 *
 * 两条本机踩过的坑（README §四）这里都绕开了：
 * `vi.mock` 的工厂会被提升到 import 之前，所以替身状态与常量都放 `vi.hoisted()`；
 * `onMounted` 里发起的那次加载是异步的，`mount` 之后必须 `await flushPromises()`
 * 才读得到数据，否则断言的是空数组**而且不会报错**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ProfileView from './ProfileView.vue'
import EmptyState from '../components/EmptyState.vue'
import type { KnowledgeStat, RadarResponse } from '../api/profile'

const { push, getKnowledgeStats, getRadar, RADAR_STUB } = vi.hoisted(() => ({
  push: vi.fn(),
  getKnowledgeStats: vi.fn(),
  getRadar: vi.fn(),
  RADAR_STUB: 'RadarChartStub'
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

/**
 * 换掉 api 模块里的**两个函数**，其余导出照原样保留。
 *
 * `importOriginal` 铺底而不是只返回这两个函数：把模块常量吃掉是 T34 踩过的
 * 坑（报错会指向组件而不是 mock）。这个模块目前只有函数，但铺底的写法不依赖
 * 「今天恰好没有常量」。
 */
vi.mock('../api/profile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/profile')>()),
  getKnowledgeStats,
  getRadar
}))

/**
 * `RadarChart` 整个换掉，而不是像它自己的 spec 那样只换 `VChart`。
 *
 * 两个理由：① ECharts 在 jsdom 里画不出 canvas，而这里要断言的不是它画了
 * 什么，是**页面把什么交给了它**；② 它内部的三个分支（加载中 / 无指标 /
 * 有图）已由 `components/RadarChart.spec.ts` 钉住，这里再渲染一遍只会让
 * 两条用例互为前提。替身声明全部 props，好让用例能读回页面传进去的值。
 */
vi.mock('../components/RadarChart.vue', () => ({
  default: {
    name: RADAR_STUB,
    props: ['indicators', 'values', 'loading', 'emptyTitle', 'emptyExplanation'],
    template: '<div class="radar-stub" />'
  }
}))

vi.mock('../components/BarChart.vue', () => ({
  default: {
    name: 'BarChartStub',
    props: [
      'items',
      'height',
      'horizontal',
      'max',
      'color',
      'valueFormatter',
      'loading',
      'emptyTitle',
      'emptyExplanation'
    ],
    template: '<div class="bar-stub" />'
  }
}))

const mounted: VueWrapper[] = []

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

beforeEach(() => {
  push.mockReset()
  getKnowledgeStats.mockReset()
  getRadar.mockReset()
})

function mountView(): VueWrapper {
  const wrapper = mount(ProfileView)
  mounted.push(wrapper)
  return wrapper
}

/** 一个知识点统计。`accuracy` 由 total/passed 算出，与后端同一口径。 */
function stat(tag: string, total: number, passed: number): KnowledgeStat {
  return { tag, total, passed, accuracy: total === 0 ? 0 : passed / total }
}

/** 一份 ECharts-ready 的雷达数据。 */
function radar(names: string[], values: number[]): RadarResponse {
  return { indicators: names.map((name) => ({ name, max: 1 })), values, tags: names }
}

/** 两个请求都成功，并返回挂载好的页面。 */
async function mountWith(stats: KnowledgeStat[], payload: RadarResponse): Promise<VueWrapper> {
  getKnowledgeStats.mockResolvedValue(stats)
  getRadar.mockResolvedValue(payload)
  const wrapper = mountView()
  await flushPromises()
  return wrapper
}

/** 把模板里的换行与缩进折成一个空格，方便对文案做包含断言。 */
function flat(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

describe('ProfileView', () => {
  it('请求未落地时给的是「加载中」，不是「还没有数据」', async () => {
    // 两个请求都不 resolve：这一条要断言的是**未决**状态本身。
    getKnowledgeStats.mockReturnValue(new Promise<KnowledgeStat[]>(() => {}))
    getRadar.mockReturnValue(new Promise<RadarResponse>(() => {}))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.placeholder').text()).toContain('加载画像中')
    // 反例：请求回来之前 `stats` 也是空数组，所以「空」与「还没回来」在模板里
    // 长得一模一样。空状态一旦提前渲染，学生会在加载中先读到一句结论。
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
  })

  it('总正确率按提交次数加权，不是各知识点正确率的平均', async () => {
    // 1/1 与 0/9：按次数是 1/10 = 10.0%；按标签平均是 (100% + 0%) / 2 = 50.0%。
    const wrapper = await mountWith(
      [stat('dp', 1, 1), stat('greedy', 9, 0)],
      radar(['dp', 'greedy'], [1, 0])
    )

    const firstCard = wrapper.find('.summary-card')
    expect(firstCard.find('.summary-value').text()).toBe('10.0%')
    expect(flat(firstCard.find('.summary-hint').text())).toBe('1 / 10 次通过')
    // 只提交过一次的知识点不该与提交过九次的等权。
    expect(wrapper.text()).not.toContain('50.0%')
  })

  it('三张摘要卡分别写正确率、知识点数与总提交数', async () => {
    const wrapper = await mountWith(
      [stat('dp', 3, 2), stat('greedy', 7, 1)],
      radar(['dp', 'greedy'], [2 / 3, 1 / 7])
    )

    expect(wrapper.findAll('.summary-card')).toHaveLength(3)
    // 知识点数是**标签数**，总提交数是**次数**：2 个标签 10 次提交。
    expect(wrapper.findAll('.summary-value').map((n) => n.text())).toEqual(['30.0%', '2', '10'])
    expect(wrapper.find('.page-subtitle').text()).toBe('共 10 次提交 · 2 个知识点')
  })

  it('提交数不足 3 次时不画雷达图，并说清还差几次', async () => {
    const wrapper = await mountWith(
      [stat('dp', 1, 1), stat('greedy', 1, 0)],
      radar(['dp', 'greedy'], [1, 0])
    )

    const block = wrapper.find('.radar-insufficient')
    expect(block.exists()).toBe(true)
    expect(flat(block.text())).toContain('已记录 2 次提交')
    expect(flat(block.text())).toContain('至少需要 3 次')
    expect(flat(block.text())).toContain('再提交 1 次即可解锁')
    expect(wrapper.findComponent({ name: RADAR_STUB }).exists()).toBe(false)
  })

  it('闸门算的是提交数，不是知识点数', async () => {
    // 3 个知识点但只有 2 次提交。按 `stats.length` 判断会误以为「够画了」，
    // 画出来只有两个点——看着像数据，其实是噪声，而且不报错。
    const wrapper = await mountWith(
      [stat('a', 1, 1), stat('b', 1, 0), stat('c', 0, 0)],
      radar(['a', 'b', 'c'], [1, 0, 0])
    )

    expect(wrapper.findAll('.stats-table tbody tr')).toHaveLength(3)
    expect(wrapper.find('.radar-insufficient').exists()).toBe(true)
    expect(wrapper.findComponent({ name: RADAR_STUB }).exists()).toBe(false)
  })

  it('提交数够了才把指标与数值原样交给雷达图', async () => {
    const payload = radar(['dp', 'greedy'], [0.33, 1])
    const wrapper = await mountWith([stat('dp', 3, 1), stat('greedy', 1, 1)], payload)

    const chart = wrapper.findComponent({ name: RADAR_STUB })
    expect(chart.exists()).toBe(true)
    // 页面不做转换：后端给什么，图就画什么。
    expect(chart.props('indicators')).toEqual(payload.indicators)
    expect(chart.props('values')).toEqual(payload.values)
    expect(wrapper.find('.radar-insufficient').exists()).toBe(false)
  })

  it('详细统计按正确率升序，最需要补的排在最上面', async () => {
    const wrapper = await mountWith(
      [stat('greedy', 10, 9), stat('dp', 10, 1), stat('math', 10, 5)],
      radar(['greedy', 'dp', 'math'], [0.9, 0.1, 0.5])
    )

    expect(wrapper.findAll('.stats-table tbody tr .tag-cell').map((n) => n.text())).toEqual([
      'dp',
      'math',
      'greedy'
    ])
  })

  it('进度条给 0% 留一条看得见的细线，并带上无障碍标签', async () => {
    const wrapper = await mountWith(
      [stat('dp', 10, 0), stat('math', 10, 5)],
      radar(['dp', 'math'], [0, 0.5])
    )

    // 顺序即升序，所以 0% 的那行在前。
    expect(wrapper.findAll('.progress-bar').map((n) => n.attributes('aria-label'))).toEqual([
      'dp 掌握度 0.0%',
      'math 掌握度 50.0%'
    ])
    // 2% 是下限：0% 若原样写进 width，这一行就没有任何可见痕迹。
    expect(wrapper.findAll('.progress-fill').map((n) => n.attributes('style'))).toEqual([
      expect.stringContaining('width: 2%'),
      expect.stringContaining('width: 50%')
    ])
  })

  it('没有任何统计时给空状态，并留一条去题库的路', async () => {
    const wrapper = await mountWith([], radar([], []))

    const empty = wrapper.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.find('.empty-title').text()).toBe('还没有可分析的数据')

    await empty.find('.empty-action').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'problems' })
  })

  it('请求失败时只留错误横幅，不画一份「0.0%」的假画像', async () => {
    getKnowledgeStats.mockRejectedValue(new Error('Network Error'))
    getRadar.mockRejectedValue(new Error('Network Error'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.page-error').text()).toContain('Network Error')
    // 「失败」与「没有数据」是两件事，错误横幅旁边不该再站一个空状态。
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    // 这一条钉的是本轮修掉的那处误导：失败时 `stats` 被清空，页面照着一份
    // 空数组算出「总正确率 0.0% · 0 / 0 次通过」——那是在陈述一个学生并没有
    // 的事实，而它恰好出现在最常见的失败场景（数据库没起来 → Network Error）。
    expect(wrapper.find('.summary').exists()).toBe(false)
    expect(wrapper.find('.radar-insufficient').exists()).toBe(false)
  })

  it('失败之后按刷新能自己恢复', async () => {
    getKnowledgeStats
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce([stat('dp', 3, 2)])
    getRadar
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce(radar(['dp'], [2 / 3]))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.page-error').exists()).toBe(true)

    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.page-error').exists()).toBe(false)
    expect(wrapper.find('.summary').exists()).toBe(true)
    expect(wrapper.findComponent({ name: RADAR_STUB }).exists()).toBe(true)
  })
})
