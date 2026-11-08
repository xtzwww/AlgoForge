// @vitest-environment jsdom
/**
 * TeacherProblemsView 的视图测试（2026-09-27）。
 *
 * 这个面板是教师端第一块"写"型页面，值得钉的事：
 *   1. **列表与筛选都走服务端**。q / knowledge_tag / difficulty /
 *      include_inactive 是请求参数，不是客户端过滤——题库会长大，客户端
 *      全量拉下来迟早变成一次很慢的请求。
 *   2. **编辑 = 拉 detail + 拉全部用例两路合并**。getProblem 只回可见
 *      用例，隐藏用例必须走 test-cases 端点；两路一起失败就是编辑不了，
 *      所以「打开编辑会同时请求两路」值得单独钉住。
 *   3. **空态是提示，不是错误**。没有命中条件的题是一个陈述句。
 *
 * useDialog 的复制/停用交互依赖 NDialogProvider，jsdom 下不渲染弹层，
 * 这里不测（它们在 app 里有人眼验收路径）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { h, nextTick } from 'vue'
import { NDialogProvider, NSelect } from 'naive-ui'
import TeacherProblemsView from './TeacherProblemsView.vue'
import type { ProblemSummary } from '../../api/problems'

const resolve = vi.fn(() => ({ href: '#/login' }))
vi.mock('vue-router', () => ({ useRouter: () => ({ resolve }) }))

// TeacherGate / useTeacherPanel 读的是 session 的四个导出。
// `useTeacherPanel` 仍在模块作用域读 `sessionReady` / `isTeacher`：
// 守卫（`router/guards/auth.ts`）保证"能渲染这一页 = 是教师"，但**加载
// 时机**仍由这个 hook 负责——它要等会话往返回来才拉数据。所以这两个值
// 必须给真值，`logout` 已不再被任何教师面板引用（2026-10-04）。
vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/problems', () => ({
  listProblems: vi.fn(),
  createProblem: vi.fn(),
  duplicateProblem: vi.fn(),
  getProblem: vi.fn(),
  getProblemTestCases: vi.fn(),
  setProblemActive: vi.fn(),
  updateProblem: vi.fn()
}))

const api = vi.mocked(await import('../../api/problems'))

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
    source: '洛谷',
    time_limit: 2,
    memory_limit: 512,
    is_active: false
  }
]

function mockList(items: ProblemSummary[] = problems): void {
  api.listProblems.mockResolvedValue({
    items,
    total: items.length,
    page: 1,
    page_size: 20
  })
}

async function rawMount(): Promise<VueWrapper> {
  // useDialog 需要 provider；视图内部读的对话框由 `dialog.warning` 懒触发，
  // 测试里从不打开，这里只是让 setup 不抛错。
  const wrapper = mount({
    render: () => h(NDialogProvider, null, { default: () => h(TeacherProblemsView) })
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

async function mountView(): Promise<VueWrapper> {
  mockList()
  return rawMount()
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('题目列表', () => {
  it('渲染行、知识点与启用/停用状态', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('两数之和')
    expect(wrapper.text()).toContain('最长回文子串')
    expect(wrapper.text()).toContain('数组')
    expect(wrapper.text()).toContain('启用')
    expect(wrapper.text()).toContain('停用')
    expect(wrapper.text()).toContain('共 2 题')
  })

  it('难度筛选变化时重新拉第一页并带参数', async () => {
    const wrapper = await mountView()
    api.listProblems.mockClear()

    const selects = wrapper.findAllComponents(NSelect)
    const difficultySelect = selects[1]
    difficultySelect.vm.$emit('update:value', 'hard')
    await flushPromises()

    expect(api.listProblems).toHaveBeenCalledWith(
      expect.objectContaining({ difficulty: 'hard', page: 1 })
    )
  })

  it('搜索变化时防抖重拉（300ms 内只发一次）', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = await mountView()
      api.listProblems.mockClear()

      const input = wrapper.find('.filter-search input')
      await input.setValue('回文')
      await input.setValue('回文子串')
      vi.advanceTimersByTime(400)
      await flushPromises()

      expect(api.listProblems).toHaveBeenCalledTimes(1)
      expect(api.listProblems).toHaveBeenCalledWith(
        expect.objectContaining({ q: '回文子串' })
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('没有命中时显示空态提示', async () => {
    mockList([])
    const wrapper = await rawMount()

    expect(wrapper.text()).toContain('没有符合条件的题目')
  })
})

describe('编辑弹窗', () => {
  it('打开编辑会同时请求题目详情与全部用例（含隐藏）', async () => {
    api.getProblem.mockResolvedValue({
      id: 1,
      title: '两数之和',
      knowledge_tags: ['数组'],
      description: '两数相加',
      input_format: null,
      output_format: null,
      samples: [],
      time_limit: 1,
      memory_limit: 256,
      difficulty: 'easy',
      source: null,
      is_active: true,
      test_cases: [{ id: 10, ordinal: 1, input: '1 2', expected_output: '3', is_sample: true }]
    })
    api.getProblemTestCases.mockResolvedValue({
      problem_id: 1,
      total: 2,
      test_cases: [
        { id: 10, ordinal: 1, input: '1 2', expected_output: '3', is_sample: true },
        { id: 11, ordinal: 2, input: '9 9', expected_output: '18', is_sample: false }
      ]
    })

    const wrapper = await mountView()
    const editButtons = wrapper.findAll('button').filter((b) => b.text().includes('编辑'))
    await editButtons[0].trigger('click')
    await flushPromises()

    expect(api.getProblem).toHaveBeenCalledWith(1)
    expect(api.getProblemTestCases).toHaveBeenCalledWith(1)
  })

  it('新建题目重置表单并打开发送路径', async () => {
    api.createProblem.mockResolvedValue({
      id: 3,
      title: '新题',
      knowledge_tags: [],
      description: '',
      input_format: null,
      output_format: null,
      samples: [],
      time_limit: 10,
      memory_limit: 256,
      difficulty: null,
      source: null,
      is_active: true,
      test_cases: []
    })
    const wrapper = await mountView()

    const createButton = wrapper.findAll('button').find((b) => b.text().includes('新建题目'))
    expect(createButton).toBeDefined()
    await createButton!.trigger('click')
    await flushPromises()
    await nextTick()

    // naive 的 NModal 默认 teleport 到 body；弹窗标题与表单区块都在那里。
    expect(document.body.textContent).toContain('新建题目')
    expect(document.body.textContent).toContain('基本信息')
    // 只是打开了弹窗，什么都没提交。
    expect(api.createProblem).not.toHaveBeenCalled()
  })
})
