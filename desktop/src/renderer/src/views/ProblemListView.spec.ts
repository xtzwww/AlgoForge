// @vitest-environment jsdom
/**
 * ProblemListView 的视图测试。
 *
 * 这个视图三件值得钉的事：
 *   1. **数据从哪来、挂在哪**。题库行来自 `GET /problems`，「已通过」列来自
 *      `fetchVerdictsByProblem` 的客户端折叠——两路请求是分开的，所以
 *      一条失败不能拖垮另一条。钉「判题状态拉取失败，题库照样列得出来」。
 *   2. **三个过滤器 + 搜索是客户端纯函数**（`filteredProblems`）。这是最容易
 *      在重构里被悄悄改成「每次改 chip 都打服务器」的地方，所以把每条过滤
 *      路径都单独钉住。
 *   3. **`practice(id)` 把 id 变成路由参数**，而不是在 history 里留一条。
 *
 * 子组件没有重的（不引 Monaco / ECharts），所以不需要桩，只桩三个 api 模块
 * 和 router。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import ProblemListView from './ProblemListView.vue'
import type { ProblemSummary } from '../api/problems'
import type { ProblemVerdict } from '../api/submissions'
import type { AssignmentOut } from '../api/assignments'

const push = vi.fn()

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
// 只 stub 本视图用到的导出；factory 整个替换模块，其它导出变 undefined 无所谓。
vi.mock('../api/problems', () => ({ listProblems: vi.fn() }))
vi.mock('../api/submissions', () => ({ fetchVerdictsByProblem: vi.fn() }))
vi.mock('../api/assignments', () => ({ getOpenAssignments: vi.fn() }))

const listProblems = vi.mocked(
  (await import('../api/problems')).listProblems
) as unknown as ReturnType<typeof vi.fn>
const fetchVerdictsByProblem = vi.mocked(
  (await import('../api/submissions')).fetchVerdictsByProblem
) as unknown as ReturnType<typeof vi.fn>
const getOpenAssignments = vi.mocked(
  (await import('../api/assignments')).getOpenAssignments
) as unknown as ReturnType<typeof vi.fn>

const problems: ProblemSummary[] = [
  {
    id: 1,
    title: '两数之和',
    knowledge_tags: ['数组', '哈希'],
    difficulty: 'easy',
    source: null,
    time_limit: 1,
    memory_limit: 256,
    is_active: true
  },
  {
    id: 2,
    title: '最长回文子串',
    knowledge_tags: ['字符串'],
    difficulty: 'hard',
    source: null,
    time_limit: 2,
    memory_limit: 256,
    is_active: true
  },
  {
    id: 3,
    title: '快速排序',
    knowledge_tags: ['排序', '数组'],
    difficulty: 'medium',
    source: '洛谷',
    time_limit: 1,
    memory_limit: 256,
    is_active: true
  }
]

// id1 通过、id2 没通过、id3 没有任何提交记录（verdicts 里没有）。
const verdicts = new Map<number, ProblemVerdict>([
  [1, { passed: true, attempts: 2 }],
  [2, { passed: false, attempts: 3 }]
])

const assignments: AssignmentOut[] = [
  {
    id: 5,
    title: '第一次作业',
    description: '',
    problem_ids: [1, 3],
    due_at: null,
    overdue: false,
    created_by: null,
    created_at: null,
    assistance_level: 3,
    // 与 `AssistancePolicy` 同形（服务端 `policy_to_dict` 的镜像）。
    // 少字段不是"省略"，而是类型不符 —— 视图读 `student_label` 时会拿到
    // undefined，静默渲染成空档位名。
    assistance: {
      level: 3,
      label: '三档 · 只给提示',
      student_label: 'AI 提示',
      description: '可对话，只给提示',
      completion_uses_model: true,
      allows_chat: true,
      chat_is_hint_only: true,
      allows_code_insertion: false,
      allows_full_solution: false
    }
  }
]

async function mountList(): Promise<VueWrapper> {
  listProblems.mockResolvedValue({ items: problems, total: problems.length, page: 1, page_size: 100 })
  fetchVerdictsByProblem.mockResolvedValue(verdicts)
  getOpenAssignments.mockResolvedValue(assignments)
  const wrapper = mount(ProblemListView)
  await flushPromises()
  await nextTick()
  return wrapper
}

function titles(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.problem-title').map((n) => n.text())
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ProblemListView', () => {
  it('把 /problems 的题目渲染成卡片，并标出通过状态', async () => {
    const w = await mountList()

    const cards = w.findAll('.problem-card')
    expect(cards).toHaveLength(3)
    expect(titles(w)).toEqual(['两数之和', '最长回文子串', '快速排序'])

    // 已通过列：id1 通过 → 显示「已通过」，id2 未通过 → 显示「未通过」。
    expect(w.text()).toContain('已通过')
    expect(w.text()).toContain('未通过')
  })

  it('顶部汇总显示「已通过数 / 题目总数」', async () => {
    const w = await mountList()
    // verdicts 里只有 id1 的 passed 为 true。
    expect(w.find('.solved-summary').text()).toContain('1 / 3')
  })

  it('难度过滤只保留匹配项', async () => {
    const w = await mountList()
    await w.findAll('.filter-select')[0].setValue('easy')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和'])
  })

  it('状态过滤：已通过只留 verdicts 里 passed 的', async () => {
    const w = await mountList()
    await w.findAll('.filter-select')[1].setValue('solved')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和'])
  })

  it('状态过滤：未通过含「没提交记录」的题（passed 为假值也算）', async () => {
    const w = await mountList()
    await w.findAll('.filter-select')[1].setValue('unsolved')
    await nextTick()
    // id2（未通过）+ id3（无记录，passed 为 undefined）→ 两条。
    expect(titles(w)).toEqual(['最长回文子串', '快速排序'])
  })

  it('知识点 chip 过滤只留带该标签的题', async () => {
    const w = await mountList()
    const arrayChip = w.findAll('.tag-chip').find((c) => c.text() === '数组')!
    await arrayChip.trigger('click')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和', '快速排序'])
  })

  it('标题搜索按子串过滤（大小写不敏感由后端语义保证，这里只验客户端）', async () => {
    const w = await mountList()
    await w.find('.filter-input').setValue('排序')
    await nextTick()
    expect(titles(w)).toEqual(['快速排序'])
  })

  it('点「开始练习」把题目 id 推进路由参数', async () => {
    const w = await mountList()
    await w.findAll('.practice-btn')[0].trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'problem', params: { id: 1 } })
  })

  it('判题状态拉取失败，题库照样列得出来（失败只作旁注）', async () => {
    listProblems.mockResolvedValue({
      items: problems,
      total: problems.length,
      page: 1,
      page_size: 100
    })
    fetchVerdictsByProblem.mockRejectedValue(new Error('verdict down'))
    getOpenAssignments.mockResolvedValue(assignments)

    const w = mount(ProblemListView)
    await flushPromises()
    await nextTick()

    // 列表没被拖垮。
    expect(w.findAll('.problem-card')).toHaveLength(3)
    // 失败作为旁注出现，且不是致命错误块。
    expect(w.find('.warn-msg').exists()).toBe(true)
    expect(w.find('.error-msg').exists()).toBe(false)
  })

  it('点作业 chip 把题目收窄到该作业的 problem_ids，再点一次恢复', async () => {
    const w = await mountList()
    const chip = w.find('.assignment-chip')
    expect(chip.exists()).toBe(true)

    await chip.trigger('click')
    await nextTick()
    // 作业只含 id1、id3。
    expect(titles(w)).toEqual(['两数之和', '快速排序'])

    await chip.trigger('click')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和', '最长回文子串', '快速排序'])
  })

  it('清除筛选把所有过滤器复位', async () => {
    const w = await mountList()
    await w.findAll('.filter-select')[0].setValue('easy')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和'])

    const reset = w.find('.reset-btn')
    expect(reset.exists()).toBe(true)
    await reset.trigger('click')
    await nextTick()
    expect(titles(w)).toEqual(['两数之和', '最长回文子串', '快速排序'])
  })
})
