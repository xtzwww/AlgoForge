// @vitest-environment jsdom
/**
 * 错题本页的视图测试（T31 / P2-21）。
 *
 * 这是 `views/` 下的第一个 spec。写它的理由不是「把视图也纳入覆盖」这种
 * 数字上的整齐，而是错因筛选有一个**坏了看不出来**的失败模式：下拉框的
 * 选项是从已加载的条目里推出来的，推导一旦写错（例如把一个「没有标签」的
 * 条目也当成了有标签），页面不会报错、不会空白，只是多出一个「选了就空」
 * 的选项——而空列表在这一页是**合法状态**，所以人眼也分辨不出。
 *
 * 覆盖四条：
 *   1. 选中一个错因，列表收敛到它；
 *   2. 知识点与错因是**与**关系，不是后者覆盖前者；
 *   3. 一条带标签的条目都没有时**不渲染**筛选器（否则就是死控件）；
 *   4. 筛到空时提示里写清是哪几个条件，且一个按钮能全清掉。
 *
 * 两条本机踩过的坑，这里都绕开了：
 * * `vi.mock` 的工厂被提升到 import 之前，所以替身状态放 `vi.hoisted()`；
 * * `onMounted` 里发起的那次加载是异步的，`mount` 之后必须
 *   `await flushPromises()` 才读得到列表，否则断言的是空列表**而且不会报错**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import MistakesView from './MistakesView.vue'
import type { MistakeEntry } from '../api/mistake_book'

const { push, listMistakes } = vi.hoisted(() => ({
  push: vi.fn(),
  listMistakes: vi.fn()
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

/**
 * 整个 api 模块都换掉。
 *
 * 逐个 mock 而不是只 mock `listMistakes`：视图 import 的是同一个模块的
 * 五个函数，留真的那四个就等于让这个 spec 有可能去碰真的 HTTP 客户端。
 */
vi.mock('../api/mistake_book', () => ({
  listMistakes,
  getMistake: vi.fn(),
  createMistake: vi.fn(),
  updateMistake: vi.fn(),
  deleteMistake: vi.fn(),
  analyzeMistake: vi.fn()
}))

const mounted: VueWrapper[] = []

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

beforeEach(() => {
  push.mockReset()
  listMistakes.mockReset()
})

/** 一条错题。字段写全，而不是 `Partial` 拼一个残缺对象——`MistakeEntry`
 *  是接口，缺字段会被 `vue-tsc` 直接报出来（spec 也在类型检查范围内）。 */
function entry(overrides: Partial<MistakeEntry> = {}): MistakeEntry {
  return {
    id: 1,
    submission_id: 1,
    problem_id: 1,
    error_cause: '',
    knowledge_tags: [],
    reviewed: false,
    review_count: 0,
    last_reviewed_at: null,
    created_at: '2026-09-21T10:00:00',
    agent_error_type: null,
    agent_suggestion: '',
    analysis_status: 'pending',
    error_detail: '',
    hint: '',
    suggested_fix: '',
    error_type_description: '',
    ...overrides
  }
}

/** 一条已被分析为「整数溢出」的条目——多数用例只需要这一个标签。 */
function overflowEntry(id: number, problemId: number): MistakeEntry {
  return entry({
    id,
    problem_id: problemId,
    agent_error_type: 'overflow',
    error_type_description: '整数溢出'
  })
}

async function mountView(entries: MistakeEntry[]): Promise<VueWrapper> {
  listMistakes.mockResolvedValue(entries)
  const wrapper = mount(MistakesView, { attachTo: document.body })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

/** 列表里每张卡片的标题（`题目 #<problem_id>`），用来看剩下哪几条。 */
function cardTitles(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.entry-title').map((b) => b.text().trim())
}

/** 错因下拉框里的选项文案。 */
function causeOptions(wrapper: VueWrapper): string[] {
  return wrapper.findAll('#cause-select option').map((o) => o.text().trim())
}

async function selectCause(wrapper: VueWrapper, label: string): Promise<void> {
  await wrapper.find('#cause-select').setValue(label)
}

/**
 * 按文案点一个知识点 chip。
 *
 * 不按下标取：chip 的顺序来自 `allTags` 的字符串排序（`'dp'` 与 `'贪心'`
 * 谁在前取决于码点，不是「看起来的顺序」），按下标写的测试会在有人改了
 * 排序规则时**静默**点错 chip。
 */
async function selectTag(wrapper: VueWrapper, tag: string): Promise<void> {
  const chip = wrapper.findAll('.tag-chip').find((c) => c.text().trim() === tag)
  if (!chip) throw new Error(`没有「${tag}」这个知识点 chip`)
  await chip.trigger('click')
}

// ---------------------------------------------------------------- 筛选生效

describe('错因筛选（P2-21）', () => {
  const overflow = entry({
    id: 1,
    problem_id: 101,
    knowledge_tags: ['dp'],
    agent_error_type: 'overflow',
    error_type_description: '整数溢出'
  })
  const offByOne = entry({
    id: 2,
    problem_id: 202,
    knowledge_tags: ['贪心'],
    agent_error_type: 'off_by_one',
    error_type_description: '边界差一'
  })

  it('选中一个错因，列表只留下该错因的条目', async () => {
    const wrapper = await mountView([overflow, offByOne])
    expect(cardTitles(wrapper)).toEqual(['题目 #101', '题目 #202'])

    await selectCause(wrapper, 'overflow')

    expect(cardTitles(wrapper)).toEqual(['题目 #101'])
  })

  it('选项是当前列表里真实存在的错因，用学生看的中文写', async () => {
    const wrapper = await mountView([overflow, offByOne])

    // 「全部错因」永远在，其余来自数据。用中文描述而不是机器标签：
    // 学生不认识 `off_by_one`，那是教师端统计用的标识。
    // 顺序按中文描述排（拼音：biānzhì… < zhěngshù…），不是按机器标签
    // ——`off_by_one` 排在 `overflow` 前面会是另一种顺序，而这正是
    // 学生读不出来的那种「看起来没坏」。
    expect(causeOptions(wrapper)).toEqual(['全部错因', '边界差一', '整数溢出'])
  })

  it('知识点与错因是与关系，不是互相覆盖', async () => {
    const wrapper = await mountView([overflow, offByOne])

    await selectTag(wrapper, 'dp')
    expect(cardTitles(wrapper)).toEqual(['题目 #101'])

    // 两条都满足：dp 的那条正是 overflow。这一条过，说明错因没有把知识点顶掉。
    await selectCause(wrapper, 'overflow')
    expect(cardTitles(wrapper)).toEqual(['题目 #101'])

    // 换成另一条错因就空了——#202 是 off_by_one 但标签是「贪心」。
    // 若实现是「后者覆盖前者」，这里会剩回 #202。
    await selectCause(wrapper, 'off_by_one')
    expect(cardTitles(wrapper)).toEqual([])
  })

  it('筛到空时提示写清两个条件，点一下全清掉', async () => {
    const wrapper = await mountView([overflow, offByOne])

    await selectTag(wrapper, '贪心')
    await selectCause(wrapper, 'overflow')
    expect(wrapper.find('.empty-title').text()).toBe('「贪心」 + 「整数溢出」下还没有错题')

    await wrapper.find('.empty-action').trigger('click')

    expect(cardTitles(wrapper)).toEqual(['题目 #101', '题目 #202'])
  })
})

// ------------------------------------------------------------ 不做死控件

describe('没有可筛的错因时', () => {
  it('不渲染错因筛选器，而不是渲染一个只有「全部」的下拉框', async () => {
    // 两条都没被分析过：`agent_error_type` 是 NULL，也就没有错因可筛。
    const wrapper = await mountView([
      entry({ id: 1, problem_id: 101, analysis_status: 'pending' }),
      entry({ id: 2, problem_id: 202, analysis_status: 'failed' })
    ])

    expect(wrapper.find('#cause-select').exists()).toBe(false)
    // 知识点栏照旧：它是另一个筛选，和错因有没有数据无关。
    expect(cardTitles(wrapper)).toHaveLength(2)
  })

  it('有一条带标签就渲染，且只把有标签的列进选项', async () => {
    const wrapper = await mountView([overflowEntry(1, 101), entry({ id: 2, problem_id: 202 })])

    expect(wrapper.find('#cause-select').exists()).toBe(true)
    expect(causeOptions(wrapper)).toEqual(['全部错因', '整数溢出'])
  })

  it('同一错因出现多次也只出一个选项', async () => {
    const wrapper = await mountView([overflowEntry(1, 101), overflowEntry(2, 202)])

    expect(causeOptions(wrapper)).toEqual(['全部错因', '整数溢出'])
  })
})
