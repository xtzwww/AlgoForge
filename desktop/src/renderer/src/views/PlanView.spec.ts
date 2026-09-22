// @vitest-environment jsdom
/**
 * 复习计划页的视图测试（T38）。
 *
 * `views/` 下的第三个 spec（前两个是 T31 的 `MistakesView` 与 T37 的
 * `ProfileView`）。挑的仍是**失败模式静默**的规则，而不是把渲染抄一遍：
 *
 * * **请求失败与「今天没有待复习」长得一样。** 空态挂在
 *   `v-else-if="!errorMsg && dueCount === 0"` 上。少了 `!errorMsg`，一次
 *   数据库没起来的请求会渲染出「今天没有待复习的题目 · 复习队列由错题本驱动」
 *   ——那是在陈述一个学生并没有的事实，而且正好出现在最常见的失败场景。
 *   这与 T37 在 `ProfileView` 里修掉的是同一类错误，所以钉住它。
 * * **打分的乐观更新把卡片弄丢了。** PU-9 之前是「先从列表里删掉，再调接口」：
 *   调用失败时卡片已经消失，只剩一条错误信息，而条目在下一次刷新时仍然是
 *   逾期状态。现在卡片**留在原地**并显示自己的进行中状态——这条要是退回去，
 *   界面会告诉学生「复习成功了」，而服务端什么都没记。
 * * **间隔的措辞。** `intervalLabel` 把天数翻成「今天 / 明天 / N 天后」；
 *   边界写错会显示成「0 天后」或「1 天后」这种读起来像 bug 的句子。
 * * **重复点击。** 打分期间按钮禁用，且处理函数自己也有守卫——两个都没了
 *   就会给同一条记录打两次分，而 SM-2 的间隔是累进的，重复一次就错一档。
 *
 * 两条本机踩过的坑这里都绕开：`vi.mock` 工厂被提升到 import 之前，替身状态
 * 放 `vi.hoisted()`；`onMounted` 里那次加载是异步的，`mount` 之后必须
 * `await flushPromises()` 才读得到内容，否则断言的是空数组**而且不会报错**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PlanView from './PlanView.vue'
import type { ReviewState } from '../api/review'
import type { MistakeEntry } from '../api/mistake_book'

const { push, listDueReviews, recordReview, getMistake } = vi.hoisted(() => ({
  push: vi.fn(),
  listDueReviews: vi.fn(),
  recordReview: vi.fn(),
  getMistake: vi.fn()
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

/**
 * 只换掉用到的函数，其余导出用 `importOriginal` 铺底。
 *
 * 不铺底会把模块常量一起吃掉，报错还会指向组件而不是 mock（T34 踩过）。
 * 这两个模块目前导出的常量（如 `FEEDBACK_SECTION_TITLES`）本页没用到，但
 * 铺底写法不依赖「今天恰好没用到」。
 */
vi.mock('../api/review', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/review')>()),
  listDueReviews,
  recordReview
}))

vi.mock('../api/mistake_book', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/mistake_book')>()),
  getMistake
}))

const mounted: VueWrapper[] = []

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

beforeEach(() => {
  push.mockReset()
  listDueReviews.mockReset()
  recordReview.mockReset()
  getMistake.mockReset()
  listDueReviews.mockResolvedValue([])
  getMistake.mockResolvedValue(entry())
})

function mountView(): VueWrapper {
  const wrapper = mount(PlanView)
  mounted.push(wrapper)
  return wrapper
}

/** 一条待复习记录。默认「从未复习过」。 */
function review(over: Partial<ReviewState> = {}): ReviewState {
  return {
    id: 1,
    entry_id: 11,
    repetitions: 0,
    easiness_factor: 2.5,
    interval_days: 0,
    last_reviewed_at: null,
    due_at: null,
    created_at: null,
    ...over
  }
}

function entry(over: Partial<MistakeEntry> = {}): MistakeEntry {
  return {
    id: 11,
    submission_id: 1,
    problem_id: 7,
    error_cause: '二分的边界写错了',
    knowledge_tags: ['binary_search'],
    reviewed: false,
    review_count: 0,
    last_reviewed_at: null,
    created_at: null,
    agent_error_type: null,
    agent_suggestion: '',
    analysis_status: 'done',
    error_detail: '',
    hint: '',
    suggested_fix: '',
    error_type_description: '',
    hint_ladder: null,
    variants: [],
    ...over
  }
}

describe('PlanView', () => {
  it('请求失败时不画「今天没有待复习」——那是学生并没有的事实', async () => {
    listDueReviews.mockRejectedValue(new Error('Network Error'))
    const wrapper = mountView()
    await flushPromises()

    // 空态与错误态是互斥的。这里退回成链尾的 v-else，就会同时出现
    // 「今天没有待复习的题目」与一条错误横幅。
    expect(wrapper.text()).toContain('Network Error')
    expect(wrapper.text()).not.toContain('今天没有待复习')
    expect(wrapper.find('.review-list').exists()).toBe(false)
  })

  it('真的没有待复习时，空态邀请去错题本', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('今天没有待复习的题目')
    expect(wrapper.text()).toContain('去错题本看看')
  })

  it('本次已清空时的空态换一套说法，且不再指路', async () => {
    listDueReviews.mockResolvedValue([review()])
    recordReview.mockResolvedValue(review({ interval_days: 3 }))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('.quality-btn')[3].trigger('click')
    await flushPromises()
    // 清空列表后重新加载，模拟「明天再来」之前的一次刷新。
    listDueReviews.mockResolvedValue([])
    await wrapper.findComponent({ name: 'PageHeader' }).vm.$emit('refresh')
    await flushPromises()

    expect(wrapper.text()).toContain('今日复习已经清空')
    // 这一份文案里不该再出现「去错题本看看」：队列是被自己清空的，
    // 不是「还没有错题」。
    expect(wrapper.text()).not.toContain('去错题本看看')
  })

  it('打分成功后卡片留在原地，并说出下次复习在什么时候', async () => {
    listDueReviews.mockResolvedValue([review()])
    recordReview.mockResolvedValue(
      review({
        interval_days: 1,
        repetitions: 1,
        due_at: '2026-09-23T00:00:00Z'
      })
    )
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('.quality-btn')[3].trigger('click')
    await flushPromises()

    // 卡片没有被移除：它是「这次点击产生了效果」的唯一证据。
    expect(wrapper.findAll('.review-card')).toHaveLength(1)
    expect(wrapper.find('.badge-done').text()).toContain('本次已复习')
    // 记录的是按下的那一档（索引 3 = 3 分「艰难」），不是别的档。
    expect(wrapper.find('.outcome').text()).toContain('3 分')
    // 1 天说「明天」，不说「1 天后」。
    expect(wrapper.find('.outcome').text()).toContain('明天')
    // 日期是服务端算出来的那个，不是本地猜的。
    expect(wrapper.find('.outcome').text()).toContain('2026')
    expect(recordReview).toHaveBeenCalledWith(11, 3)
  })

  it('间隔措辞的三个边界', async () => {
    const cases: [number, string][] = [
      [0, '今天'],
      [1, '明天'],
      [5, '5 天后']
    ]
    for (const [days, wording] of cases) {
      listDueReviews.mockResolvedValue([review()])
      recordReview.mockResolvedValue(review({ interval_days: days }))
      const wrapper = mountView()
      await flushPromises()

      await wrapper.findAll('.quality-btn')[3].trigger('click')
      await flushPromises()

      expect(wrapper.find('.outcome').text()).toContain(wording)
    }
  })

  it('打分失败时卡片必须留着，并把错误说出来', async () => {
    listDueReviews.mockResolvedValue([review()])
    recordReview.mockRejectedValue(new Error('Network Error'))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('.quality-btn')[3].trigger('click')
    await flushPromises()

    // 退回「先删卡片再调接口」的话，这里会是 0 张卡片加一条错误信息：
    // 学生看到题目消失了，会以为已经复习过了。
    expect(wrapper.findAll('.review-card')).toHaveLength(1)
    expect(wrapper.find('.outcome').exists()).toBe(false)
    expect(wrapper.text()).toContain('Network Error')
  })

  it('打分进行中不会重复提交同一条', async () => {
    listDueReviews.mockResolvedValue([review()])
    let settle: (value: ReviewState) => void = () => {}
    recordReview.mockImplementation(
      () =>
        new Promise<ReviewState>((resolve) => {
          settle = resolve
        })
    )
    const wrapper = mountView()
    await flushPromises()

    // 两次点击之间**不**等 nextTick。按钮的 `disabled` 要等一次渲染才生效，
    // 所以此刻能挡住第二次点击的只有处理函数里的守卫。
    //
    // 用 `await button.trigger('click')` 写会让按钮先变成 disabled，第二次
    // 点击被浏览器丢掉——测试照样绿，但**把守卫删掉也照样绿**（实测：注入
    // 「去掉守卫」时这条没有红）。同步 `element.click()` 才真的走到守卫。
    const element = wrapper.findAll('.quality-btn')[3].element as HTMLButtonElement
    element.click()
    element.click()

    // 少了守卫就会给同一条记录打两次分；SM-2 的间隔是累进的，多打一次错一档。
    expect(recordReview).toHaveBeenCalledTimes(1)

    settle(review({ interval_days: 1 }))
    await flushPromises()
  })

  it('错题条目取不到时，题目编号退化成问号而不是空白', async () => {
    listDueReviews.mockResolvedValue([review()])
    getMistake.mockRejectedValue(new Error('404'))
    const wrapper = mountView()
    await flushPromises()

    // 条目可能已经被删掉。一张写着「题目 #?」的卡片仍然能复习，
    // 而整页失败会连复习都做不了。
    expect(wrapper.find('.review-title').text()).toContain('题目 #?')
    expect(wrapper.findAll('.quality-btn').length).toBeGreaterThan(0)
  })

  it('顶部计数只说存在的部分', async () => {
    listDueReviews.mockResolvedValue([review(), review({ id: 2, entry_id: 12 })])
    recordReview.mockResolvedValue(review({ interval_days: 2 }))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'PageHeader' }).props('subtitle')).toBe('待复习 2 项')

    await wrapper.findAll('.quality-btn')[3].trigger('click')
    await flushPromises()

    const subtitle = wrapper.findComponent({ name: 'PageHeader' }).props('subtitle')
    expect(subtitle).toBe('待复习 2 项 · 本次已复习 1 项')
  })
})
