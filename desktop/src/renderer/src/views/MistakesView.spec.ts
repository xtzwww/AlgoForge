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
import type { HintLadder, MistakeEntry, Variant } from '../api/mistake_book'

const { push, listMistakes } = vi.hoisted(() => ({
  push: vi.fn(),
  listMistakes: vi.fn()
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

/**
 * 换掉 api 模块里的**函数**，保留其余导出。
 *
 * 逐个 mock 而不是只 mock `listMistakes`：视图 import 的是同一个模块的
 * 五个函数，留真的那四个就等于让这个 spec 有可能去碰真的 HTTP 客户端。
 *
 * 保留其余导出（`...actual`）不是偷懒：视图底下的 `LayeredFeedback` 从同一个
 * 模块读 `FEEDBACK_SECTION_TITLES` 等常量，只返回这六个函数会把常量一起吃掉
 * ——组件渲染到标题那一行才炸，报错指向的是组件而不是 mock，很难定位。
 * 而把常量照抄一份进 mock 更糟：那是同一份文案的第二副本，改了真的一份
 * 而这里没跟上，测试照样绿。用 `importOriginal` 铺底是唯一不用维护副本的做法
 * （T34 加变式题接线用例时才踩到，因为那之前没有用例真的渲染出诊断块）。
 */
vi.mock('../api/mistake_book', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/mistake_book')>()),
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
    // T34: S7's two outputs. `null` / `[]` is the ordinary case — the
    // default pipeline produces neither — so a fixture that omitted them
    // would be a fixture claiming an analysis type that does not exist.
    hint_ladder: null,
    variants: [],
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

// ------------------------------------------------------------ S7 产物接线

/**
 * 阶梯与变式题在页面上的接线（T34）。
 *
 * 两个组件各有自己的 spec，覆盖的是「拿到数据怎么渲染」；这几条覆盖的是
 * 「页面有没有把数据送进去」——而这一层的失败同样是静默的：少写一个 `v-if`
 * 或绑错字段名，页面不报错，只是这两块永远不出现，而「不出现」恰好也是
 * 默认（`direct`）编排下的**正常**状态。人眼分辨不出「没接」和「没数据」。
 */
describe('提示阶梯与变式题的接线（T34）', () => {
  const ladder: HintLadder = {
    levels: [{ level: 1, text: '先看看循环是从几开始的。' }],
    self_check: []
  }
  const variants: Variant[] = [
    {
      statement: '输入第一行改成 n，再求最大值。',
      sample_input: '3\n1 9 4',
      sample_output: '9',
      knowledge_points: ['循环边界'],
      difference: '把 n 挪到了第一行。'
    }
  ]

  it('分析完成且两种产物都有时，两块都渲染出来', async () => {
    const wrapper = await mountView([
      entry({ id: 1, problem_id: 101, analysis_status: 'done', hint_ladder: ladder, variants })
    ])

    expect(wrapper.find('.entry-ladder').exists()).toBe(true)
    expect(wrapper.find('.entry-variants').exists()).toBe(true)
    // 内容也进来了，而不是只挂了个空壳。
    expect(wrapper.text()).toContain('提示阶梯')
    expect(wrapper.text()).toContain('举一反三')
  })

  it('默认编排下两种产物都没有，两块都不渲染', async () => {
    // 这是绝大多数真实条目的状态：`direct` 不跑那两个 worker。
    const wrapper = await mountView([
      entry({ id: 1, problem_id: 101, analysis_status: 'done', error_detail: '下标从 1 开始。' })
    ])

    expect(wrapper.find('.entry-ladder').exists()).toBe(false)
    expect(wrapper.find('.entry-variants').exists()).toBe(false)
  })

  it('分析未完成时也不渲染——它们是同一次分析的产物', async () => {
    const wrapper = await mountView([
      entry({
        id: 1,
        problem_id: 101,
        analysis_status: 'pending',
        hint_ladder: ladder,
        variants
      })
    ])

    // 挂着「AI 分析中…」的同时摊开一张阶梯会自相矛盾。
    expect(wrapper.find('.entry-ladder').exists()).toBe(false)
    expect(wrapper.find('.entry-variants').exists()).toBe(false)
  })

  it('只有阶梯时只渲染阶梯，不因为缺变式题就一起不显示', async () => {
    const wrapper = await mountView([
      entry({ id: 1, problem_id: 101, analysis_status: 'done', hint_ladder: ladder })
    ])

    expect(wrapper.find('.entry-ladder').exists()).toBe(true)
    expect(wrapper.find('.entry-variants').exists()).toBe(false)
  })
})
