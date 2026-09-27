// @vitest-environment jsdom
/**
 * VariantPracticeView 的视图测试（2026-09-27，#5 变式题闭环）。
 *
 * 钉的事：
 *   1. **题目来自错题本**：从 query 的 entry/index 拉那条变式题，渲染
 *      题干、样例、知识点、差异。样例是空白敏感的，必须原样呈现。
 *   2. **判题走 inline 自测**（变式题没有 problem 行，测试集就是它自带
 *      的样例）：运行发 `record:false`，提交发 `record:true`——PU-8 的
 *      草稿/留痕差别要在这里保持。
 *   3. **index 越界是错误不是崩溃**：显示"这道变式题不存在"，不给编辑器。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import VariantPracticeView from './VariantPracticeView.vue'
import type { MistakeEntry } from '../api/mistake_book'
import type { JudgeResponse } from '../api/judge'

const routerPush = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { entry: '7', index: '0' } }),
  useRouter: () => ({ push: routerPush })
}))

vi.mock('../api/mistake_book', () => ({ getMistake: vi.fn() }))
vi.mock('../api/judge', () => ({ judgeCodeWithCases: vi.fn() }))
vi.mock('../api/languages', () => ({
  getLanguageOptions: vi.fn(),
  pickDefaultLanguage: vi.fn()
}))

// 编辑器桩掉：Monaco 在 jsdom 下不可用，且这里不测编辑行为。
vi.mock('../components/CodeEditor.vue', () => ({
  default: { name: 'CodeEditorStub', template: '<div class="editor-stub" />' }
}))

const mistakesApi = vi.mocked(await import('../api/mistake_book'))
const judgeApi = vi.mocked(await import('../api/judge'))
const languagesApi = vi.mocked(await import('../api/languages'))

const ENTRY: MistakeEntry = {
  id: 7,
  submission_id: 101,
  problem_id: 12,
  error_cause: 'integer_overflow',
  knowledge_tags: ['数组'],
  reviewed: false,
  review_count: 0,
  last_reviewed_at: null,
  created_at: '2026-09-25T10:00:00Z',
  analysis_status: 'done',
  variants: [
    {
      statement: '输入第一行是 n，第二行是 n 个整数，求最大值。',
      sample_input: '3\n1 9 4',
      sample_output: '9',
      knowledge_points: ['循环边界'],
      difference: '把 n 挪到了第一行。'
    }
  ]
} as MistakeEntry

async function mountView(entry: MistakeEntry = ENTRY): Promise<VueWrapper> {
  mistakesApi.getMistake.mockResolvedValue(entry)
  languagesApi.getLanguageOptions.mockResolvedValue([
    {
      id: 'cpp',
      display_name: 'C++',
      monaco_language: 'cpp',
      default_template: '',
      available: true,
      version: null,
      detail: null
    }
  ])
  languagesApi.pickDefaultLanguage.mockReturnValue({
    id: 'cpp',
    display_name: 'C++',
    monaco_language: 'cpp',
    default_template: '',
    available: true,
    version: null,
    detail: null
  })
  judgeApi.judgeCodeWithCases.mockResolvedValue({
    status: 'AC',
    compile_output: '',
    passed: 1,
    total: 1,
    problem_id: null,
    test_cases: [
      {
        status: 'AC',
        input: '3\n1 9 4',
        expected_output: '9',
        actual_output: '9',
        stderr: '',
        exit_code: 0
      }
    ]
  } as JudgeResponse)
  const wrapper = mount(VariantPracticeView)
  await flushPromises()
  await nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  routerPush.mockReset()
})

describe('VariantPracticeView', () => {
  it('渲染变式题的题干、样例、知识点与差异', async () => {
    const wrapper = await mountView()

    expect(mistakesApi.getMistake).toHaveBeenCalledWith(7)
    expect(wrapper.text()).toContain('第二行是 n 个整数')
    expect(wrapper.text()).toContain('循环边界')
    expect(wrapper.text()).toContain('把 n 挪到了第一行')
    expect(wrapper.find('.editor-stub').exists()).toBe(true)
  })

  it('index 越界时显示不存在，不渲染编辑器', async () => {
    // variants 为空与 index 0 越界走同一条路径。
    mistakesApi.getMistake.mockResolvedValue({ ...ENTRY, variants: [] })
    const wrapper = mount(VariantPracticeView)
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('这道变式题不存在')
    expect(wrapper.find('.editor-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('加载失败显示错误信息', async () => {
    mistakesApi.getMistake.mockRejectedValue(new Error('连接被拒绝'))
    const wrapper = mount(VariantPracticeView)
    await flushPromises()
    await nextTick()

    // describeRequestError 对 Error 的渲染带原文。
    expect(wrapper.text()).toContain('连接被拒绝')
    wrapper.unmount()
  })

  it('运行 = 草稿：judge 带样例用例且 record:false', async () => {
    const wrapper = await mountView()
    const editorStub = wrapper.find('.editor-stub')
    void editorStub

    await wrapper.findAll('button').find((b) => b.text() === '运行')!.trigger('click')
    await flushPromises()

    expect(judgeApi.judgeCodeWithCases).toHaveBeenCalledWith(
      '',
      [{ input: '3\n1 9 4', expected_output: '9' }],
      { language: 'cpp', record: false }
    )
  })

  it('提交 = 留痕：judge 带样例用例且 record:true', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('button').find((b) => b.text() === '提交')!.trigger('click')
    await flushPromises()

    expect(judgeApi.judgeCodeWithCases).toHaveBeenCalledWith(
      '',
      [{ input: '3\n1 9 4', expected_output: '9' }],
      { language: 'cpp', record: true }
    )
  })

  it('判定结果渲染：徽章、通过数、你的输出与期望输出', async () => {
    // 不走 mountView：它预设的 AC mock 会覆盖这里的 WA。
    mistakesApi.getMistake.mockResolvedValue(ENTRY)
    languagesApi.getLanguageOptions.mockResolvedValue([])
    languagesApi.pickDefaultLanguage.mockReturnValue(null)
    judgeApi.judgeCodeWithCases.mockResolvedValue({
      status: 'WA',
      compile_output: '',
      passed: 0,
      total: 1,
      problem_id: null,
      test_cases: [
        {
          status: 'WA',
          input: '3\n1 9 4',
          expected_output: '9',
          actual_output: '8',
          stderr: '',
          exit_code: 0
        }
      ]
    } as JudgeResponse)
    const wrapper = mount(VariantPracticeView)
    await flushPromises()
    await nextTick()

    await wrapper.findAll('button').find((b) => b.text() === '提交')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('WA')
    expect(wrapper.text()).toContain('通过 0 / 1')
    expect(wrapper.text()).toContain('你的输出')
    expect(wrapper.text()).toContain('8')
    expect(wrapper.text()).toContain('期望输出')
    expect(wrapper.text()).toContain('9')
  })

  it('返回错题本', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('button').find((b) => b.text().includes('返回错题本'))!.trigger('click')
    expect(routerPush).toHaveBeenCalledWith({ name: 'mistakes' })
  })
})
