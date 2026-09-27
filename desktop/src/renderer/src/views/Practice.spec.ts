// @vitest-environment jsdom
/**
 * Practice 的视图测试。
 *
 * 这个视图 1200 行，引了 Monaco、调试、AI 面板一堆重组件。测试只钉**不依赖
 * 判题流实时事件**的、便宜又容易坏的不变式，子组件全部桩掉、api 全部 mock：
 *
 *   1. **自由运行模式**。`problemId` 为 null 时标题是「自由运行」、样例是页内
 *      自带的两组、且**不**去拉 `getProblem`（拉了就说明模式判断错了）。
 *   2. **换语言会确认**。`onLanguageChange` 在代码被改过时会弹 `confirm`；
 *      取消则语言不变，确认才套用新语言的起始模板——这是「切语言会覆盖未保存
 *      代码」这一危险动作的守门。
 *   3. **AI 代码是追加不是替换**（`insertAssistanceCode`），否则会吞掉学生代码。
 *   4. **运行/提交的禁用门**（canRun/canSubmit）随语言可用性变化。
 *   5. **快捷键** Ctrl+Enter 跑、Ctrl+Shift+Enter 交，且走的是对应封装函数。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import Practice from './Practice.vue'
import type { LanguageOption } from '../api/languages'
import type { ProblemDetail } from '../api/problems'

const cppOpt: LanguageOption = {
  id: 'cpp',
  display_name: 'C++',
  monaco_language: 'cpp',
  default_template: '#include <iostream>\nint main() {}\n',
  available: true,
  version: '11',
  detail: null
}
const pyOpt: LanguageOption = {
  id: 'python',
  display_name: 'Python',
  monaco_language: 'python',
  default_template: 'print()\n',
  available: true,
  version: '3',
  detail: null
}

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

/**
 * 确认框改成 `useDialog()` 了（宪法红线：`window.confirm` 禁用）。
 *
 * 这里把它桩成一个**立即结算**的 dialog：`warning()` 的 `onPositiveClick`
 * 是否被调用由每个用例通过 {@link dialogAnswer} 控制。不桩的话，真实
 * `useDialog()` 需要 `NDialogProvider` 宿主，App.vue 才有，测试里会抛。
 */
let dialogAnswer = false
/** 记录每次确认框的调用，供"弹过没有"这类断言。 */
const dialogCalls: Array<{ title: string; content: string }> = []

vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useDialog: () => ({
    warning: (opts: {
      title: string
      content: string
      onPositiveClick?: () => void
      onNegativeClick?: () => void
    }) => {
      dialogCalls.push({ title: opts.title, content: opts.content })
      // 同步结算：真实的 dialog 是异步的，但被测代码只 await 这个 promise，
      // 同步 resolve 不会改变它的行为，只让测试少一次 flush。
      if (dialogAnswer) opts.onPositiveClick?.()
      else opts.onNegativeClick?.()
    }
  })
}))

// 桩掉所有子组件——它们各自会拖 Monaco / ECharts / WebSocket，这里一个都不需要。
//
// 桩必须**声明它接收的 props**（`inheritAttrs: false` 也一样重要）。裸
// `<div />` 不声明任何 prop，于是父组件传的每个属性都会透传到这个 div 上；
// 而 `:prefix="code"` 的 `prefix` 是 DOM 的只读原生属性，`patchDOMProp`
// 写它会抛 `TypeError: Cannot set property prefix of ... which has only a
// getter`，每次渲染留一条 Vue 警告。这条警告是**测试替身自己造的**，与产品
// 代码无关，但会把真正的警告淹掉 —— 所以在这里关掉透传。
vi.mock('../components/AssistPanel.vue', () => ({
  default: { name: 'AssistPanel', inheritAttrs: false, template: '<div />' }
}))
vi.mock('../components/CodeEditor.vue', () => ({
  default: { name: 'CodeEditor', template: '<div />' }
}))
vi.mock('../components/DebugPanel.vue', () => ({
  default: { name: 'DebugPanel', template: '<div />' }
}))
vi.mock('../components/OutputPanel.vue', () => ({
  default: { name: 'OutputPanel', template: '<div />' }
}))
vi.mock('../components/ProblemStatement.vue', () => ({
  default: { name: 'ProblemStatement', template: '<div />' }
}))
vi.mock('../components/VerdictBadge.vue', () => ({
  default: { name: 'VerdictBadge', template: '<div />' }
}))

vi.mock('../api/assistance', () => ({ listLevels: vi.fn() }))
vi.mock('../api/debug', () => ({
  getAvailability: vi.fn(),
  createSession: vi.fn(),
  closeSession: vi.fn(),
  setBreakpoints: vi.fn(),
  step: vi.fn()
}))
vi.mock('../api/ws', () => ({
  runCases: vi.fn(),
  submitCases: vi.fn(),
  submitProblem: vi.fn()
}))
vi.mock('../api/problems', () => ({ getProblem: vi.fn(), getProblemTestCases: vi.fn(), listProblems: vi.fn() }))
vi.mock('../api/languages', () => ({ getLanguageOptions: vi.fn(), pickDefaultLanguage: vi.fn() }))
vi.mock('../api/assignments', () => ({ getMyAssignments: vi.fn() }))
vi.mock('../monaco-theme', () => ({ hasNativeHighlighting: vi.fn(() => true) }))
vi.mock('../edit-trace', () => ({ editTrace: { snapshot: vi.fn(() => ({})), recordRun: vi.fn() } }))
vi.mock('../composables/usePageCrumb', () => ({ publishPageCrumb: vi.fn() }))

const getLanguageOptions = vi.mocked(
  (await import('../api/languages')).getLanguageOptions
) as unknown as ReturnType<typeof vi.fn>
const pickDefaultLanguage = vi.mocked(
  (await import('../api/languages')).pickDefaultLanguage
) as unknown as ReturnType<typeof vi.fn>
const listLevels = vi.mocked(
  (await import('../api/assistance')).listLevels
) as unknown as ReturnType<typeof vi.fn>
const getAvailability = vi.mocked(
  (await import('../api/debug')).getAvailability
) as unknown as ReturnType<typeof vi.fn>
const getProblem = vi.mocked((await import('../api/problems')).getProblem) as unknown as ReturnType<
  typeof vi.fn
>
const getProblemTestCases = vi.mocked(
  (await import('../api/problems')).getProblemTestCases
) as unknown as ReturnType<typeof vi.fn>
const listProblems = vi.mocked(
  (await import('../api/problems')).listProblems
) as unknown as ReturnType<typeof vi.fn>
const getMyAssignments = vi.mocked(
  (await import('../api/assignments')).getMyAssignments
) as unknown as ReturnType<typeof vi.fn>
const runCases = vi.mocked((await import('../api/ws')).runCases) as unknown as ReturnType<
  typeof vi.fn
>
const submitCases = vi.mocked((await import('../api/ws')).submitCases) as unknown as ReturnType<
  typeof vi.fn
>

const freeRunCases = [
  { input: '3 5', expected_output: '8' },
  { input: '10 20', expected_output: '30' }
]

function seedFreeRun(): void {
  getLanguageOptions.mockResolvedValue([cppOpt, pyOpt])
  pickDefaultLanguage.mockReturnValue(cppOpt)
  listLevels.mockResolvedValue([])
  getAvailability.mockResolvedValue({ available: false, reason: 'no gdb' })
  runCases.mockResolvedValue({ type: 'judge_done', passed: 0, total: 0 })
  submitCases.mockResolvedValue({ type: 'judge_done', passed: 0, total: 0 })
}

async function mountPractice(props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const wrapper = mount(Practice, { props })
  mounted.push(wrapper)
  await flushPromises()
  await nextTick()
  return wrapper
}

/**
 * 挂载过的组件，`afterEach` 统一卸载。
 *
 * **不是可选的清理。** 页面在 `window` 上挂了 Ctrl+Enter / Ctrl+Shift+Enter
 * 的 keydown 监听，不卸载就会一直留着 —— 于是"按一次运行只该调一次
 * runCases"这条断言会被前面所有用例残留的监听一起响应，报
 * `expected 1 times, but got 13 times`。数字随用例数增长而变，看起来像
 * 是快捷键逻辑坏了，实际是这个文件从来没有清理过挂载。
 */
const mounted: VueWrapper[] = []

beforeEach(() => {
  vi.clearAllMocks()
  seedFreeRun()
  // 默认"取消"：大部分用例不关心确认框，而"取消"是最安全的那条分支。
  dialogAnswer = false
  dialogCalls.length = 0
})

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

describe('Practice', () => {
  it('自由运行模式：标题是「自由运行」，且不去拉题目详情', async () => {
    const w = await mountPractice({ problemId: null })
    expect(w.find('.problem-title .title-text').text()).toBe('自由运行')
    expect(getProblem).not.toHaveBeenCalled()
  })

  it('自由运行模式用页内自带的两组样例', async () => {
    const w = await mountPractice({ problemId: null })
    const cases = (w.vm as unknown as { displayCases: typeof freeRunCases }).displayCases
    expect(cases).toHaveLength(2)
    expect(cases[0]).toEqual(freeRunCases[0])
  })

  it('isProblemMode 由 prop 决定：null 为 false', async () => {
    const w = await mountPractice({ problemId: null })
    expect((w.vm as unknown as { isProblemMode: boolean }).isProblemMode).toBe(false)
  })

  it('isProblemMode 由 prop 决定：给 id 为 true（并拉题目详情）', async () => {
    const detail: ProblemDetail = {
      id: 12,
      title: 'A+B',
      knowledge_tags: [],
      description: 'd',
      input_format: null,
      output_format: null,
      samples: [],
      time_limit: 1,
      memory_limit: 256,
      difficulty: 'easy',
      source: null,
      is_active: true,
      test_cases: []
    }
    getProblem.mockResolvedValue(detail)
    getProblemTestCases.mockResolvedValue({ problem_id: 12, total: 1, test_cases: [] })
    const w = await mountPractice({ problemId: 12 })
    expect((w.vm as unknown as { isProblemMode: boolean }).isProblemMode).toBe(true)
    expect(getProblem).toHaveBeenCalledWith(12)
  })

  it('换语言且代码已改动时弹确认；取消则语言不变', async () => {
    const w = await mountPractice({ problemId: null })
    // 把缓冲区改成「脏」状态。
    ;(w.vm as unknown as { code: string }).code = '// dirty edit'

    await (w.vm as unknown as { onLanguageChange: (e: unknown) => Promise<void> }).onLanguageChange(
      {
        target: { value: 'python' }
      }
    )

    // 弹的是 useDialog，不是 window.confirm（后者是宪法红线）。
    expect(dialogCalls).toHaveLength(1)
    expect(dialogCalls[0].title).toContain('Python')
    expect((w.vm as unknown as { languageId: string }).languageId).toBe('cpp')
  })

  it('换语言确认后套用新语言的起始模板', async () => {
    dialogAnswer = true
    const w = await mountPractice({ problemId: null })
    ;(w.vm as unknown as { code: string }).code = '// dirty edit'

    await (w.vm as unknown as { onLanguageChange: (e: unknown) => Promise<void> }).onLanguageChange(
      {
        target: { value: 'python' }
      }
    )

    expect((w.vm as unknown as { languageId: string }).languageId).toBe('python')
    expect((w.vm as unknown as { code: string }).code).toBe(pyOpt.default_template)
  })

  it('代码没动过时换语言不弹确认 —— 没东西可丢就别拦一道', async () => {
    const w = await mountPractice({ problemId: null })

    await (w.vm as unknown as { onLanguageChange: (e: unknown) => Promise<void> }).onLanguageChange(
      {
        target: { value: 'python' }
      }
    )

    expect(dialogCalls).toHaveLength(0)
    expect((w.vm as unknown as { languageId: string }).languageId).toBe('python')
  })

  it('AI 代码是追加不是替换，且不带尾部空白', async () => {
    const w = await mountPractice({ problemId: null })
    const before = (w.vm as unknown as { code: string }).code
    ;(w.vm as unknown as { insertAssistanceCode: (s: string) => void }).insertAssistanceCode(
      'int x = 1;'
    )
    const after = (w.vm as unknown as { code: string }).code
    expect(after.startsWith(before.replace(/\s+$/, ''))).toBe(true)
    expect(after).toContain('int x = 1;')
    expect(after.endsWith('\n')).toBe(true)
  })

  /**
   * 五档的整解会**替换**整个缓冲区（阶段 5）。
   *
   * 这组断言钉住的是"唯一一条会覆盖学生代码的路径"：它必须覆盖（不然两份
   * 程序接在一起编译不过），也必须**先问一次**（不然一次误点就没了半篇
   * 代码）。两条都成立才是对的 —— 只测覆盖会放过静默销毁，只测询问会
   * 放过"问完什么都没做"。
   */
  describe('五档整解填入编辑器', () => {
    it('缓冲区干净时直接替换，不弹确认', async () => {
      const w = await mountPractice({ problemId: null })
      // 未改动过的缓冲区：pristineCode === code。
      await (
        w.vm as unknown as { replaceAssistanceCode: (s: string) => Promise<void> }
      ).replaceAssistanceCode('#include <iostream>\nint main() { return 0; }')

      expect(dialogCalls).toHaveLength(0)
      const code = (w.vm as unknown as { code: string }).code
      expect(code).toContain('#include <iostream>')
      // 是**替换**：原来那份 a+b 模板不该还在。
      expect(code).not.toContain('cin >> a >> b')
    })

    it('缓冲区被改过时先弹确认；取消则代码原封不动', async () => {
      dialogAnswer = false
      const w = await mountPractice({ problemId: null })
      ;(w.vm as unknown as { code: string }).code = '// 学生自己写的半成品'
      // 让 pristineCode 与 code 不同（模拟"改过"）。
      ;(w.vm as unknown as { pristineCode: string }).pristineCode = '/* 起始模板 */'

      await (
        w.vm as unknown as { replaceAssistanceCode: (s: string) => Promise<void> }
      ).replaceAssistanceCode('int main() {}')

      expect(dialogCalls).toHaveLength(1)
      expect((w.vm as unknown as { code: string }).code).toBe('// 学生自己写的半成品')
    })

    it('确认后整段替换', async () => {
      dialogAnswer = true
      const w = await mountPractice({ problemId: null })
      ;(w.vm as unknown as { code: string }).code = '// 学生自己写的半成品'
      ;(w.vm as unknown as { pristineCode: string }).pristineCode = '/* 起始模板 */'

      await (
        w.vm as unknown as { replaceAssistanceCode: (s: string) => Promise<void> }
      ).replaceAssistanceCode('int main() { return 0; }')

      expect((w.vm as unknown as { code: string }).code).toBe('int main() { return 0; }\n')
    })

    it('替换后 pristineCode 跟着走 —— 否则下次切语言会为学生没做的改动报警', async () => {
      const w = await mountPractice({ problemId: null })
      await (
        w.vm as unknown as { replaceAssistanceCode: (s: string) => Promise<void> }
      ).replaceAssistanceCode('int main() { return 0; }')

      const code = (w.vm as unknown as { code: string }).code
      const pristine = (w.vm as unknown as { pristineCode: string }).pristineCode
      expect(pristine).toBe(code)
    })

    it('空片段不动缓冲区', async () => {
      const w = await mountPractice({ problemId: null })
      const before = (w.vm as unknown as { code: string }).code
      await (
        w.vm as unknown as { replaceAssistanceCode: (s: string) => Promise<void> }
      ).replaceAssistanceCode('   \n  ')
      expect((w.vm as unknown as { code: string }).code).toBe(before)
    })
  })

  it('运行/提交按钮随语言可用性变化：没有可用语言时禁用', async () => {
    const w = await mountPractice({ problemId: null })
    // 默认 cpp 可用，按钮应启用。
    expect(w.find('.btn-secondary').attributes('disabled')).toBeUndefined()
    expect(w.find('.btn-primary').attributes('disabled')).toBeUndefined()

    // 把语言 id 置空 → 没有选中语言 → canSubmit 为假 → 禁用。
    ;(w.vm as unknown as { languageId: string }).languageId = ''
    await nextTick()
    expect(w.find('.btn-secondary').attributes('disabled')).toBeDefined()
    expect(w.find('.btn-primary').attributes('disabled')).toBeDefined()
  })

  it('快捷键 Ctrl+Enter 触发运行（走 runCases）', async () => {
    await mountPractice({ problemId: null })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true }))
    await flushPromises()
    expect(runCases).toHaveBeenCalledTimes(1)
    // Ctrl+Shift+Enter 走提交。
    window.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, shiftKey: true })
    )
    await flushPromises()
    expect(submitCases).toHaveBeenCalledTimes(1)
  })

  // --- 下一题 + 完成进度（作业/题库导航） --------------------------------
  const assignmentRow = {
    id: 5,
    title: '栈与队列',
    description: '',
    due_at: null,
    overdue: false,
    assistance_level: 3,
    class_id: null,
    classroom_name: null,
    problem_ids: [10, 12, 13],
    problem_count: 3,
    solved: 1,
    solved_problem_ids: [10],
    submissions: 1,
    progress: 1 / 3
  }

  function seedProblemMode(problemId: number): void {
    getProblem.mockResolvedValue({ id: problemId, title: '题目' } as never)
    getProblemTestCases.mockResolvedValue([] as never)
  }

  it('作业模式：显示「第 2/3 题」+「已完成 1/3」，下一题指向后面第一道没做对的', async () => {
    getMyAssignments.mockResolvedValue([assignmentRow as never])
    seedProblemMode(12)
    const w = await mountPractice({ problemId: 12, assignmentId: 5 })

    expect(w.find('.nav-position').text()).toBe('第 2/3 题')
    expect(w.find('.nav-progress').text()).toContain('已完成 1/3')
    // 当前题(12)之后第一道没做对的是 13（10 已做对）。
    expect((w.vm as unknown as { nextProblemId: number | null }).nextProblemId).toBe(13)
    const btn = w.find('.toolbar-right button.btn-secondary.btn-sm')
    expect(btn.text()).toBe('下一题')
  })

  it('作业模式：全部做对后按钮禁用并显示「全部完成」', async () => {
    getMyAssignments.mockResolvedValue([
      { ...assignmentRow, solved: 3, solved_problem_ids: [10, 12, 13], progress: 1 } as never
    ])
    seedProblemMode(12)
    const w = await mountPractice({ problemId: 12, assignmentId: 5 })

    expect((w.vm as unknown as { nextProblemId: number | null }).nextProblemId).toBeNull()
    const btn = w.find('.toolbar-right button.btn-secondary.btn-sm')
    expect(btn.text()).toBe('全部完成')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('作业导航拉不到（未登录/接口失败）时：隐藏按钮，不显示误导性的「全部完成」', async () => {
    getMyAssignments.mockRejectedValue(new Error('401'))
    seedProblemMode(12)
    const w = await mountPractice({ problemId: 12, assignmentId: 5 })

    expect(w.find('.nav-progress').exists()).toBe(false)
    expect(w.find('.toolbar-right button.btn-secondary.btn-sm').exists()).toBe(false)
  })

  it('题库模式：下一题 = 目录顺序的下一道，不显示进度条', async () => {
    listProblems.mockResolvedValue({ items: [{ id: 7 }, { id: 8 }, { id: 12 }] } as never)
    seedProblemMode(8)
    const w = await mountPractice({ problemId: 8 })

    expect((w.vm as unknown as { nextProblemId: number | null }).nextProblemId).toBe(12)
    expect(w.find('.nav-progress').exists()).toBe(false)
    const btn = w.find('.toolbar-right button.btn-secondary.btn-sm')
    expect(btn.text()).toBe('下一题')
  })
})
