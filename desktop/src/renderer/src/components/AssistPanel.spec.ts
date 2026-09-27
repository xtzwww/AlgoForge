// @vitest-environment jsdom
/**
 * 辅助面板的组件测试。
 *
 * 这个组件坏起来的样子特别安静：档位判断写反，多出来的按钮不报错、不空白，
 * 只是学生能在"只给提示"的作业上点出完整解法；或者反过来，五档的学生看不见
 * 「完整解法」，功能像没做。两种都不触发任何警告，只有人眼能发现 —— 所以
 * 用测试把这张表钉死。
 *
 * 四类断言
 * --------
 *   1. **按档位出现哪些入口**（下面那张表）。这是组件唯一真正的判断逻辑。
 *   2. **越档的回复按普通消息渲染**，不是红色报错。服务端对越档返回
 *      200 + `allowed=false` + `reason`，把它渲染成错误会把"教学设置"
 *      说成"系统故障"，学生就会去反馈一个不存在的 bug。
 *   3. **代码只经 `insert` / `replace` 事件出去**，面板从不自己改
 *      `currentCode`。面板拿不到编辑器是有意的：写进缓冲区要经过页面的
 *      替换/追加语义（`replace` 还可能要确认）。
 *   4. **学生能自己把档位往低调**，且点一下真的写进服务端 —— 服务端一直
 *      支持这件事，缺的只是入口。
 *
 * mock 的写法沿用 T34 的教训：`vi.mock` 的工厂会被提升到 import 之前，
 * 且**只导出被 mock 的函数会把模块里的常量一起吃掉** —— 子组件读常量时炸，
 * 报错指向组件而不是 mock。这里用 `importOriginal` 铺底。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import type { AssistancePolicy, AssistResponse } from '../api/assistance'

const api = vi.hoisted(() => ({
  listLevels: vi.fn(),
  complete: vi.fn(),
  chat: vi.fn(),
  insert: vi.fn(),
  solution: vi.fn(),
  getAssistanceUsage: vi.fn(),
  listModels: vi.fn(),
  setModel: vi.fn(),
  // 2026-09-26 新增：面板进来会问一次"这次作业到底几档"。
  getLevelState: vi.fn(),
  setLevelPreference: vi.fn()
}))

vi.mock('../api/assistance', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/assistance')>()),
  listLevels: api.listLevels,
  complete: api.complete,
  chat: api.chat,
  insert: api.insert,
  solution: api.solution,
  listModels: api.listModels,
  setModel: api.setModel,
  getLevelState: api.getLevelState,
  setLevelPreference: api.setLevelPreference
}))

/**
 * 档位刻度尺被真实渲染（面板现在把它嵌进来了）。
 *
 * 桩掉它会漏掉"学生能不能点"这一整类断言，而那正是本轮修的东西之一；
 * 真实渲染的代价只是它自己会读 `--af-*` 变量，jsdom 下取到空串也不报错。
 */
import AssistPanel from './AssistPanel.vue'

const mounted: VueWrapper[] = []

/**
 * 默认让模型列表请求失败：绝大多数用例不关心它。
 *
 * 不设默认的话，未 mock 的 `listModels()` 会走进真实的 axios，在 jsdom 里
 * 报 Network Error —— 每个用例都多一行无关的失败日志，真出问题时反而被
 * 淹没。关心模型选单的用例自己覆盖。
 */
api.listModels.mockRejectedValue(new Error('not under test'))

/**
 * 默认让档位查询**失败**：于是面板退回父组件传的 `policy`。
 *
 * 这是刻意的默认，而不是给一个假的成功值。理由有两层：
 *   1. 老用例全部是按"只有 `policy` 可用"写的，让默认走失败路径意味着
 *      它们继续测它们本来要测的东西（按档位渲染哪些入口），不被新功能
 *      搅动 —— 新功能有自己的用例。
 *   2. "取不到档位"本身是真实且必须优雅降级的分支：服务不可用、未登录、
 *      或这段代码将来被拿掉时，面板都得照常工作。让绝大多数用例跑在
 *      这条路径上，等于每一轮都在回归它。
 *
 * 关心"面板是否跟着服务端档位走"的用例自己覆盖成 resolve。
 */
api.getLevelState.mockRejectedValue(new Error('not under test'))

/**
 * 每个用例开工前重置默认桩。
 *
 * 放在 `beforeEach` 而不是模块顶层，是因为 `ALL_LEVELS` 定义在文件下半部分
 * —— 顶层调用会撞上 TDZ（`Cannot access 'L1' before initialization`），
 * 而"把常量提到文件最前面"会让档位表离它配套的说明和用例很远。
 */
beforeEach(() => {
  api.listModels.mockRejectedValue(new Error('not under test'))
  api.getLevelState.mockRejectedValue(new Error('not under test'))
  // 默认给一份真的五档表：面板用 `levels.length` 决定选择器渲不渲染，
  // "没给"会让一票用例静默失去被测量的对象。
  api.listLevels.mockResolvedValue(ALL_LEVELS)
})

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
  vi.clearAllMocks()
})

/** 一档：只有本机补全。 */
const L1: AssistancePolicy = {
  level: 1,
  label: '一档 · 无 AI 辅助',
  student_label: '本地补全',
  description: '只有本机的代码补全，不联网、不调用 AI。用于检验基本功。',
  completion_uses_model: false,
  allows_chat: false,
  chat_is_hint_only: true,
  allows_code_insertion: false,
  allows_full_solution: false
}

/** 二档：补全联网，但对话与代写都没开。 */
const L2: AssistancePolicy = {
  level: 2,
  label: '二档 · AI 补全',
  student_label: 'AI 补全',
  description: '补全由 AI 提供，可以把代码补全得更聪明；不能对话、不能整段代写。',
  completion_uses_model: true,
  allows_chat: false,
  chat_is_hint_only: true,
  allows_code_insertion: false,
  allows_full_solution: false
}

/** 三档：加对话，但只给提示。 */
const L3: AssistancePolicy = {
  level: 3,
  label: '三档 · 只给提示',
  student_label: 'AI 提示',
  description: '可以问 AI 要思路，但它只讲方向、不给能直接粘贴的代码。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: true,
  allows_code_insertion: false,
  allows_full_solution: false
}

/** 四档：能写代码，但不能整解。 */
const L4: AssistancePolicy = {
  level: 4,
  label: '四档 · 部分代写',
  student_label: 'AI 部分代写',
  description: 'AI 可以帮你写出一部分代码，剩下的仍要自己想。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: false,
  allows_code_insertion: true,
  allows_full_solution: false
}

/** 五档：全开。 */
const L5: AssistancePolicy = {
  level: 5,
  label: '五档 · AI 代做',
  student_label: 'AI 代做',
  description: 'AI 可以直接给出完整解法。用于对照学习。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: false,
  allows_code_insertion: true,
  allows_full_solution: true
}

/** 五档全表，按由弱到强 —— 与 `app/assistance.py::_POLICIES` 同序。 */
const ALL_LEVELS: AssistancePolicy[] = [L1, L2, L3, L4, L5]

function reply(over: Partial<AssistResponse> = {}): AssistResponse {
  return {
    allowed: true,
    action: 'chat',
    level: 3,
    reason: '可以。',
    content: '先想想前缀和。',
    candidates: [],
    policy: L3,
    downgraded: false,
    ...over
  }
}

type PanelProps = {
  policy: AssistancePolicy | null
  assignmentId?: number | null
  problemId?: number | null
  language?: string
  prefix?: string
  currentCode?: string
  statement?: string
  busy?: boolean
}

function mountPanel(props: PanelProps): VueWrapper {
  const w = mount(AssistPanel, {
    props: {
      prefix: '',
      currentCode: '',
      statement: '求 a+b',
      ...props
    }
  })
  mounted.push(w)
  return w
}

/**
 * 面板上所有按钮的文字。
 *
 * `text()` 会把按钮里的装饰图标（发送按钮尾部的 `↵`）也算进去，于是
 * 断言 `toContain('生成')` 这种形式虽然能过，`toBe('生成')` 却会挂 ——
 * 而"按钮叫什么"是这批用例反复要钉的东西。所以**去掉 `aria-hidden`
 * 的子树**：那些节点本来就是给屏幕阅读器忽略的装饰，不该进可读名字。
 *
 * 用真正的元素而不是剥字符：剥字符会连带改掉档位按钮里的 `5 档`
 * （数字后跟的是普通空格，不是图标），把断言变成猜谜。
 */
function buttons(w: VueWrapper): string[] {
  return w.findAll('button').map((b) => readLabel(b.element))
}

function readLabel(el: Element): string {
  const clone = el.cloneNode(true) as Element
  clone.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove())
  return (clone.textContent ?? '').trim()
}

/**
 * 唯一的输入框在不在。
 *
 * **只有一个。** 上一版有两个（`#assist-question` 问 AI、`#assist-instruction`
 * 让 AI 写一段），学生看到的是"两个对话窗口"。现在合成一个
 * `#assist-instruction`，走对话还是代写由档位（以及 2026-09-27 起四/五档
 * 的句子意图路由）决定。
 *
 * 与 `hasChatBox` 是同一个判据，保留两个名字是因为这一组用例读起来仍然
 * 要区分"有没有输入框"与"能不能提问"：它们是同一个元素的两种说法，但
 * 断言的字面意思不同（一个是版式、一个是能力）。
 */
function hasComposeBox(w: VueWrapper): boolean {
  return w.find('#assist-instruction').exists()
}

/**
 * 现在能不能提问。
 *
 * 判据是**输入区在不在**（`#assist-instruction`），不是时间线在不在。
 *
 * 时间线在 2026-09-26 的重排里变成了**常驻**的：它是面板主体，一档没有
 * 对话能力时也留着（否则"只剩一个补全按钮"的面板会突然矮一截，而空着
 * 的对话区还能放"这一档只提供本机补全"的说明）。所以"能不能对话"只能
 * 看输入框，不能再拿时间线当代理 —— 那是旧布局的偶然特征。
 */
function hasChatBox(w: VueWrapper): boolean {
  return w.find('#assist-instruction').exists()
}

/**
 * 现在能不能代写。
 *
 * 判据是**标签文字**而不是输入框在不在 —— 输入框两者共用，分不出能力。
 *
 * 标签的位置跟着布局改过（2026-09-26）：它从输入框上方挪到了输入框**下方**
 * 的一行辅助文字里（`.assist-label-row`），和"只给提示"/"Ctrl+Enter 发送"
 * 排在一起。用 `.assist-label` 这个类名找，不绑容器 —— 绑容器的话下次
 * 再挪一次位置，这些用例会一起红，而它们想测的是能力不是版式。
 */
function hasInsertBox(w: VueWrapper): boolean {
  const label = w.find('.assist-label')
  // 一档/二档整个输入区都不存在，`.text()` 会抛 "empty DOMWrapper"，
  // 报错指向这一行而不是被测的档位渲染 —— 先判存在。
  return label.exists() && label.text().includes('让 AI 帮你写')
}

/** 当前面板顶栏写的档位号。 */
function levelText(w: VueWrapper): string {
  return w.find('.assist-level').exists() ? w.find('.assist-level').text() : ''
}

/**
 * 展开档位弹层。
 *
 * 强度选择器收在**弹层**里（2026-09-27 起是横向刻度轴 `AssistLevelAxis`）：
 * 面板只有 340px 宽，让刻度尺常驻会挤占对话区 —— 那是"挤成一团"最直接
 * 的来源。所以断言之前必须先点开「强度」按钮。
 *
 * 找不到按钮时**明确报出来**，而不是让 `find(...).trigger()` 抛一句
 * `Cannot read properties of undefined` —— 后者看着像测试写错了，其实
 * 是"这个档位下根本没有入口"。
 */
async function openLevelPop(w: VueWrapper): Promise<void> {
  const btn = w.find('.assist-switch')
  if (!btn.exists()) throw new Error('没有「强度」按钮：这一档不渲染档位入口')
  await btn.trigger('click')
  await flushPromises()
}

/** 展开模型弹层。同理，模型列表长度 > 1 才有这个按钮。 */
async function openModelPop(w: VueWrapper): Promise<void> {
  const btn = w.find('.assist-switch-model')
  if (!btn.exists()) throw new Error('没有「模型」按钮：模型列表长度不足 2')
  await btn.trigger('click')
  await flushPromises()
}

describe('AssistPanel 按档位给入口', () => {
  it('一档只有本机补全，没有对话也没有代写', () => {
    const w = mountPanel({ policy: L1 })

    expect(buttons(w)).toContain('本机补全')
    // 一档的关键区别在这里：文案说"本机"，学生才知道没联网。
    expect(buttons(w)).not.toContain('AI 补全')
    expect(hasChatBox(w)).toBe(false)
    expect(hasInsertBox(w)).toBe(false)
    expect(buttons(w)).not.toContain('完整解法')
    // 一档连输入框都不给：它没有任何能接住那句话的能力。
    expect(hasComposeBox(w)).toBe(false)
  })

  it('二档补全改口叫 AI 补全，但对话仍未开放', () => {
    const w = mountPanel({ policy: L2 })

    expect(buttons(w)).toContain('AI 补全')
    expect(buttons(w)).not.toContain('本机补全')
    expect(hasChatBox(w)).toBe(false)
    expect(hasInsertBox(w)).toBe(false)
  })

  it('三档开放对话，并标明只给提示', () => {
    const w = mountPanel({ policy: L3 })

    expect(hasChatBox(w)).toBe(true)
    expect(hasInsertBox(w)).toBe(false)
    expect(buttons(w)).not.toContain('完整解法')
    // 「只给提示」这个标签是学生理解三档的唯一线索，不能只靠说明文字。
    expect(w.text()).toContain('只给提示')
    // 三档的按钮叫"提问"（能力是对话），不叫"生成"。
    expect(buttons(w)).toContain('提问')
  })

  it('四档开放代写，但仍不给完整解法', () => {
    const w = mountPanel({ policy: L4 })

    expect(hasChatBox(w)).toBe(true)
    expect(hasInsertBox(w)).toBe(true)
    expect(buttons(w)).not.toContain('完整解法')
    // 四档的按钮叫"发送"（代写 + 对话共用一个输入框，2026-09-27 合并）。
    expect(buttons(w)).toContain('发送')
    expect(buttons(w)).not.toContain('提问')
  })

  it('五档才有完整解法', () => {
    const w = mountPanel({ policy: L5 })

    expect(buttons(w)).toContain('完整解法')
  })

  it('规则还没取到时按钮禁用，但不显示一个假的档位号', () => {
    const w = mountPanel({ policy: null })

    expect(w.text()).toContain('档位读取中')
    // 排除折叠开关：它是面板的结构控件，任何时候都能点。
    const btns = w.findAll('button').filter((b) => !b.classes().includes('assist-toggle'))
    expect(btns.length).toBeGreaterThan(0)
    for (const b of btns) {
      expect(b.attributes('disabled')).toBeDefined()
    }
  })
})

describe('AssistPanel 的补全', () => {
  it('本机候选渲染成可点的候选词，且不自动写进编辑器', async () => {
    api.complete.mockResolvedValue(
      reply({ action: 'complete', content: '', candidates: ['for (int i = 0', 'while (cin >>'] })
    )
    const w = mountPanel({ policy: L1, prefix: 'int main() {\n  ' })

    await w.find('.assist-actions button').trigger('click')
    await flushPromises()

    const items = w.findAll('.assist-candidate')
    expect(items.map((i) => i.text())).toEqual(['for (int i = 0', 'while (cin >>'])
    // 「没自动写进去」是这里最重要的一条：面板发的是事件，页面决定落不落。
    expect(w.emitted('insert')).toBeUndefined()
  })

  it('点候选词才发出 insert 事件', async () => {
    api.complete.mockResolvedValue(reply({ action: 'complete', content: '', candidates: ['x'] }))
    const w = mountPanel({ policy: L1 })

    await w.find('.assist-actions button').trigger('click')
    await flushPromises()

    await w.find('.assist-candidate').trigger('click')
    expect(w.emitted('insert')?.[0]).toEqual(['x'])
  })

  it('请求带上 assignment_id —— 档位由服务端按它裁决', async () => {
    api.complete.mockResolvedValue(reply({ action: 'complete', content: '', candidates: [] }))
    const w = mountPanel({ policy: L1, assignmentId: 42, problemId: 7 })

    await w.find('.assist-actions button').trigger('click')
    await flushPromises()

    expect(api.complete).toHaveBeenCalledWith(
      expect.objectContaining({ assignment_id: 42, problem_id: 7 })
    )
  })

  it('没有作业时不传 assignment_id，让默认档生效', async () => {
    api.complete.mockResolvedValue(reply({ action: 'complete', content: '', candidates: [] }))
    const w = mountPanel({ policy: L1 })

    await w.find('.assist-actions button').trigger('click')
    await flushPromises()

    const sent = api.complete.mock.calls[0][0] as Record<string, unknown>
    expect('assignment_id' in sent).toBe(false)
  })
})

describe('AssistPanel 的对话', () => {
  it('提问走对话接口，回复只进面板不进编辑器', async () => {
    api.chat.mockResolvedValue(reply({ content: '想想前缀和。' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('为什么用前缀和？')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledWith(expect.objectContaining({ question: '为什么用前缀和？' }))
    expect(w.text()).toContain('想想前缀和。')
    // 提示型回复没有插入按钮 —— 三档的代码不该有捷径进缓冲区。
    expect(buttons(w)).not.toContain('插入到编辑器末尾')
    expect(w.emitted('insert')).toBeUndefined()
  })

  it('三档的回复被降级时说出来，学生才知道不是模型抽风', async () => {
    api.chat.mockResolvedValue(reply({ content: '思路是先排序。', downgraded: true }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('怎么写')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('已自动降级')
  })

  it('问空了不请求', async () => {
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('   ')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).not.toHaveBeenCalled()
  })

  /**
   * 这一组钉住本轮修的核心：**三档与四档共用同一个输入框**。
   *
   * 坏起来的样子是"两个输入框又长回来了"，或者更糟：四档学生打了一句话，
   * 被当成提问发了 chat，于是拿到一段提示而不是代码 —— 学生以为 AI 坏了。
   */
  it('三档的那句话走 chat（只给提示）', async () => {
    api.chat.mockResolvedValue(reply({ content: '先想前缀和。' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('这道题怎么做')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.insert).not.toHaveBeenCalled()
  })

  it('四档的同一句话走 insert（写代码）', async () => {
    api.insert.mockResolvedValue(reply({ action: 'insert', content: 'for (int i = 0;;)' }))
    const w = mountPanel({ policy: L4 })

    await w.find('#assist-instruction').setValue('写一个读入 n 的循环')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.insert).toHaveBeenCalledTimes(1)
    expect(api.chat).not.toHaveBeenCalled()
    expect(api.insert).toHaveBeenCalledWith(
      expect.objectContaining({ instruction: '写一个读入 n 的循环' })
    )
  })

  it('四档代写的结果给「插入到编辑器末尾」，点了才发事件', async () => {
    api.insert.mockResolvedValue(reply({ action: 'insert', content: 'int x = 1;' }))
    const w = mountPanel({ policy: L4 })

    await w.find('#assist-instruction').setValue('写一行')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.emitted('insert')).toBeUndefined()
    const btn = w.findAll('button').find((b) => b.text().trim() === '插入到编辑器末尾')
    expect(btn).toBeDefined()
    await btn?.trigger('click')
    expect(w.emitted('insert')?.[0]).toEqual(['int x = 1;'])
  })

  it('四档代写的成果也进时间线 —— 学生要能看到自己刚要求了什么', async () => {
    api.insert.mockResolvedValue(reply({ action: 'insert', content: 'int x = 1;' }))
    const w = mountPanel({ policy: L4 })

    await w.find('#assist-instruction').setValue('写一行')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.findAll('.assist-turn.is-student')).toHaveLength(1)
    expect(w.text()).toContain('写一行')
  })
})

/**
 * 合并输入框后的意图路由（2026-09-27）。
 *
 * 四档与五档都是能力**并集**（对话 + 代写）。上一版在输入框上方放了一组
 * 「写代码 / 问问题」二选一按钮 —— 学生看到的是"两个窗口"、每次说话前
 * 都要先决定走哪边。现在按钮整个去掉，只有一个输入框，`submit` 按档位与
 * 句子意图自动路由（判据在 `utils/intent.ts`，只认生成动词、刻意偏保守）。
 *
 * 坏起来的样子：路由写反 —— "为什么用 long long"被送进 insert，学生收到
 * 一段代码；或"写一段循环"被送进 chat，学生收到一段提示。两者都不报错，
 * 只有对着接口看才能发现，所以这里把"句子 → 接口"钉死。
 */
describe('AssistPanel 合并输入框后的意图路由', () => {
  it('三个档位都没有「写代码 / 问问题」开关 —— 只留一个输入框', () => {
    for (const policy of [L3, L4, L5]) {
      const w = mountPanel({ policy })
      expect(w.find('.assist-mode').exists()).toBe(false)
    }
  })

  it('五档说"写一段…"走 insert（代写）', async () => {
    api.insert.mockResolvedValue(reply({ action: 'insert', content: 'int x = 1;' }))
    const w = mountPanel({ policy: L5 })

    await w.find('#assist-instruction').setValue('帮我写一个读入 n 的循环')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.insert).toHaveBeenCalledTimes(1)
    expect(api.chat).not.toHaveBeenCalled()
  })

  it('五档问一句思路就走 chat，不被当成要代码', async () => {
    api.chat.mockResolvedValue(reply({ content: '因为会溢出。', policy: L5, level: 5 }))
    const w = mountPanel({ policy: L5 })

    await w.find('#assist-instruction').setValue('为什么要开 long long')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.insert).not.toHaveBeenCalled()
    expect(w.text()).toContain('因为会溢出。')
  })

  it('四档的"帮我实现…"走 insert', async () => {
    api.insert.mockResolvedValue(reply({ action: 'insert', content: 'int x = 1;' }))
    const w = mountPanel({ policy: L4 })

    await w.find('#assist-instruction').setValue('帮我实现一个排序函数')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.insert).toHaveBeenCalledTimes(1)
    expect(api.chat).not.toHaveBeenCalled()
  })

  it('三档只有对话：哪怕说"写一个循环"也走 chat（服务端会降级成提示）', async () => {
    api.chat.mockResolvedValue(reply({ content: '想想循环怎么写。', policy: L3, level: 3 }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('写一个循环')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.insert).not.toHaveBeenCalled()
  })

  it('档位被调低到三档后，输入框照常、但只走对话那条路', async () => {
    api.getLevelState.mockResolvedValue({
      level: 3,
      policy: L3,
      ceiling: 5,
      preference: 3,
      has_assignment: true
    })
    const w = mountPanel({ policy: L5 })
    await flushPromises()

    // 服务端裁决成三档：没有代写能力了。输入框还在（还能问），但无论
    // 说什么都走 chat —— 把"写代码"的句子送进 insert 会被服务端拒。
    expect(w.find('.assist-mode').exists()).toBe(false)
    expect(w.find('#assist-instruction').exists()).toBe(true)
    expect(w.find('.assist-label').text()).toContain('问 AI')
    expect(readLabel(w.find('.assist-submit').element)).toBe('提问')

    await w.find('#assist-instruction').setValue('写一段循环')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.insert).not.toHaveBeenCalled()
  })
})

describe('AssistPanel 的越档处理', () => {
  it('被拒渲染成一句解释，不是错误', async () => {
    api.chat.mockResolvedValue(
      reply({ allowed: false, reason: '这次作业只用本机补全。', content: '' })
    )
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('给我答案')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('这次作业只用本机补全。')
    // PU-13：对话的回答进时间线，被拒也一样 —— 它是一句话，不是错误。
    // 仍然没有 .assist-note-error（那是真故障的版式）。
    expect(w.find('.assist-turn.is-refused').exists()).toBe(true)
    expect(w.find('.assist-note-error').exists()).toBe(false)
  })

  it('真故障（网络）才用错误样式', async () => {
    api.chat.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('在吗')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.find('.assist-note-error').text()).toContain('Network Error')
  })

  it('失败后不清空输入框 —— 重试不该重打一遍', async () => {
    api.chat.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('这个问题很长')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect((w.find('#assist-instruction').element as HTMLTextAreaElement).value).toBe(
      '这个问题很长'
    )
  })

  it('成功后清空输入框', async () => {
    api.chat.mockResolvedValue(reply({}))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('问一句')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect((w.find('#assist-instruction').element as HTMLTextAreaElement).value).toBe('')
  })
})

describe('AssistPanel 的完整解法', () => {
  /**
   * 五档的整解：生成完**直接替换编辑器**（本轮改动）。
   *
   * 上一版只把带围栏的原文塞进 `<pre>`，要学生自己点「插入到编辑器」——
   * 而粘进去的 ```` ```cpp ```` 会让编译报错，看起来像"AI 给的代码跑不了"。
   * 现在剥围栏并直接发 `replace`。
   */
  const SOLUTION = '思路：前缀和。\n\n```cpp\n#include <iostream>\nint main() { return 0; }\n```'

  it('剥掉围栏后发 replace 事件，内容是可编译的代码', async () => {
    api.solution.mockResolvedValue(
      reply({ action: 'full_solution', content: SOLUTION, policy: L5, level: 5 })
    )
    const w = mountPanel({ policy: L5, statement: '求 a+b' })

    const solve = w.findAll('button').find((b) => b.text().trim() === '完整解法')
    await solve?.trigger('click')
    await flushPromises()

    const replaced = w.emitted('replace')
    expect(replaced).toBeDefined()
    expect(replaced?.[0][0]).toBe('#include <iostream>\nint main() { return 0; }')
    // 围栏不能进编辑器 —— 它是 Markdown，不是 C++。
    expect(replaced?.[0][0]).not.toContain('```')
    // 也不该走追加那条路：整解的意义就是"这份代码就是答案"。
    expect(w.emitted('insert')).toBeUndefined()
  })

  it('预览区显示的是剥过围栏的代码，而不是原文', async () => {
    api.solution.mockResolvedValue(
      reply({ action: 'full_solution', content: SOLUTION, policy: L5, level: 5 })
    )
    const w = mountPanel({ policy: L5, statement: '求 a+b' })

    const solve = w.findAll('button').find((b) => b.text().trim() === '完整解法')
    await solve?.trigger('click')
    await flushPromises()

    expect(w.find('.assist-code').text()).not.toContain('```')
    expect(w.find('.assist-code').text()).toContain('#include <iostream>')
  })

  it('模型只答了一句话、没有代码时不动编辑器', async () => {
    api.solution.mockResolvedValue(
      reply({ action: 'full_solution', content: '这道题需要先排序。', policy: L5, level: 5 })
    )
    const w = mountPanel({ policy: L5, statement: '求 a+b' })

    const solve = w.findAll('button').find((b) => b.text().trim() === '完整解法')
    await solve?.trigger('click')
    await flushPromises()

    // 宁可什么都不做，也不要拿一句中文去覆盖学生的代码。
    expect(w.emitted('replace')).toBeUndefined()
    expect(w.text()).toContain('没有可识别的代码')
  })

  it('被拒时不动编辑器', async () => {
    api.solution.mockResolvedValue(
      reply({ action: 'full_solution', allowed: false, content: '', reason: '只有五档给整解。' })
    )
    const w = mountPanel({ policy: L5, statement: '求 a+b' })

    const solve = w.findAll('button').find((b) => b.text().trim() === '完整解法')
    await solve?.trigger('click')
    await flushPromises()

    expect(w.emitted('replace')).toBeUndefined()
  })
})

describe('AssistPanel 换题清空', () => {
  it('换题目后上一题的回复不留在屏幕上', async () => {
    api.chat.mockResolvedValue(reply({ content: '上一题的答案' }))
    const w = mountPanel({ policy: L3, problemId: 1 })

    await w.find('#assist-instruction').setValue('问')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('上一题的答案')

    await w.setProps({ problemId: 2 })
    await flushPromises()
    expect(w.text()).not.toContain('上一题的答案')
  })
})

describe('AssistPanel 的收起', () => {
  it('收起后按钮全部消失，只剩展开控件', async () => {
    const w = mountPanel({ policy: L5 })
    expect(buttons(w)).toContain('完整解法')

    await w.find('.assist-toggle').trigger('click')
    expect(buttons(w)).not.toContain('完整解法')
    expect(buttons(w)).not.toContain('AI 补全')
  })
})

// --- 对话时间线（PU-13）-----------------------------------------------

/**
 * 这一组钉住的是"多轮"这个新能力。它坏起来的样子：面板看着有输入框、
 * 也能提问，但每问一次就把上一次的回答擦掉 —— 学生以为自己在连续提问，
 * 实际上前面全没了，于是反复问同一个问题。
 */
describe('AssistPanel 的对话时间线', () => {
  it('多轮问答按顺序累积，不互相覆盖', async () => {
    api.chat
      .mockResolvedValueOnce(reply({ content: '第一次回答' }))
      .mockResolvedValueOnce(reply({ content: '第二次回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('第一问')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    await w.find('#assist-instruction').setValue('第二问')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    // 四段全在：两问两答。第一条回答**不能**被第二条顶掉。
    const text = w.text()
    expect(text).toContain('第一问')
    expect(text).toContain('第一次回答')
    expect(text).toContain('第二问')
    expect(text).toContain('第二次回答')
    expect(w.findAll('.assist-turn')).toHaveLength(4)
  })

  it('学生与 AI 的消息分属不同的 class，供对齐区分', async () => {
    api.chat.mockResolvedValue(reply({ content: '回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('问题')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.findAll('.assist-turn.is-student')).toHaveLength(1)
    expect(w.findAll('.assist-turn.is-assistant')).toHaveLength(1)
  })

  it('提问后输入框立刻清空，问题已经进了时间线', async () => {
    api.chat.mockResolvedValue(reply({ content: '回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('问题')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect((w.find('#assist-instruction').element as HTMLTextAreaElement).value).toBe('')
    expect(w.text()).toContain('问题')
  })

  /**
   * 一档没有对话，但**对话区本身还在**。
   *
   * 它是面板主体，撤掉会让面板在低档位突然矮一截；而空着的对话区正好用来
   * 说"这一档只提供本机补全"。所以"没有对话"的判据是**输入框不在**，
   * 不是时间线不在 —— 后者是旧布局的偶然特征（那时时间线整块挂在
   * `allows_chat` 下面）。
   */
  it('一档没有对话入口：输入框不在，但对话区仍留着放说明', () => {
    const w = mountPanel({ policy: L1 })
    expect(hasComposeBox(w)).toBe(false)
    expect(w.find('.assist-timeline').exists()).toBe(true)
  })

  it('降级的回复带一条说明 —— 学生要知道自己看的是被削过的内容', async () => {
    api.chat.mockResolvedValue(reply({ content: '只讲讲思路', downgraded: true }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('给我代码')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.find('.assist-turn-flag').exists()).toBe(true)
    expect(w.text()).toContain('已自动降级')
  })

  it('对话的回答不进一次性回复区（那个区只放能插进编辑器的产物）', async () => {
    api.chat.mockResolvedValue(reply({ content: '一段回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-instruction').setValue('问')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    // `.assist-reply` 是补全/写一段的容器，对话不该出现在里面。
    expect(w.find('.assist-reply').exists()).toBe(false)
  })

  /**
   * 五档的对话里顺带出现的完整代码给一个填入入口。
   *
   * **必须用五档。** 四档的输入框走的是 `insert`（它有代写能力），回复
   * 落在产出区，根本进不到这条路径；而且四档的定义是"帮你写一部分"，
   * 给它一个一键替换全份代码的按钮等于把四档升成五档。这条判据写在
   * `recordAssistantTurn` 里，这里用五档把它钉住。
   */
  it('五档的对话里带完整代码时给「替换编辑器」入口，点了才发 replace', async () => {
    api.chat.mockResolvedValue(
      reply({
        content: '```cpp\n#include <iostream>\nint main() { return 0; }\n```',
        policy: L5,
        level: 5
      })
    )
    const w = mountPanel({ policy: L5 })

    // 合并后的输入框按意图路由："怎么优化"不带生成动词，走对话 ——
    // 对话里顺带出现的完整代码才轮到「替换编辑器」入口。
    await w.find('#assist-instruction').setValue('这个还能怎么优化')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(w.emitted('replace')).toBeUndefined()
    const btn = w.find('.assist-turn-action')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(w.emitted('replace')?.[0][0]).toBe('#include <iostream>\nint main() { return 0; }')
  })

  /**
   * 四档的代码只能"追加"，不能"替换"。
   *
   * 这条是上面那条的对照组：同一个回复内容，四档就不该出现那个按钮。
   */
  it('四档的代码不给「替换编辑器」入口 —— 那是五档的能力', async () => {
    api.insert.mockResolvedValue(
      reply({ action: 'insert', content: '```cpp\n#include <iostream>\nint main() {}\n```' })
    )
    const w = mountPanel({ policy: L4 })

    await w.find('#assist-instruction').setValue('写个完整程序')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.find('.assist-turn-action').exists()).toBe(false)
    // 但追加那条路是通的。
    const ins = w.findAll('button').find((b) => b.text().trim() === '插入到编辑器末尾')
    expect(ins).toBeDefined()
  })

  it('纯文字回复不给填入入口 —— 没代码可填', async () => {
    api.chat.mockResolvedValue(reply({ content: '先想前缀和。' }))
    const w = mountPanel({ policy: L5 })

    await w.find('#assist-instruction').setValue('怎么做')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    expect(w.find('.assist-turn-action').exists()).toBe(false)
  })
})

// --- 模型选择（PU-13）-------------------------------------------------

/**
 * 模型选单坏起来的样子：下拉渲染了、点得动，但选了之后提问仍走旧模型
 * —— 或者更糟，切换请求发出去、界面显示新模型、实际没生效。两种都
 * 只有对比服务端日志才能发现，所以这里把"点选项 → 调 setModel"钉死。
 */
describe('AssistPanel 的模型选择', () => {
  const SNAP = {
    active: 'deepseek',
    default: 'deepseek',
    models: [
      {
        id: 'deepseek',
        label: 'DeepSeek',
        model: 'deepseek-coder',
        base_url: 'https://api.deepseek.com',
        local: false
      },
      {
        id: 'ollama',
        label: '本机 Ollama',
        model: 'qwen2.5-coder:7b',
        base_url: 'http://localhost:11434/v1',
        local: true
      }
    ]
  }

  it('只有一个模型时也渲染「模型」按钮 —— 它是配置自定义 API 的入口', async () => {
    api.listModels.mockResolvedValue({
      ...SNAP,
      models: [SNAP.models[0]]
    })
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    // 2026-09-27 起「模型」按钮不再按"多于一个模型"隐藏：它是打开
    // 「配置自定义 API」窗口的唯一入口，只有一个模型时也要进得去。
    expect(w.find('.assist-switch-model').exists()).toBe(true)
  })

  it('多个模型时渲染选择器，并显示当前选中模型的短名', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    expect(w.find('.assist-switch-model').exists()).toBe(true)
    // 按钮上直接写着当前模型名，不用展开就能看到。短名规则见
    // `utils/model-name.ts`：deepseek-coder 没有版本段，原样保留。
    expect(w.find('.assist-switch-model .assist-switch-value').text()).toBe('deepseek-coder')
  })

  it('打开菜单能看到全部模型，本机的带「本机」标记，末尾有配置入口', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await openModelPop(w)

    // 两个模型 + 「配置自定义 API 端点…」入口，共三项。
    const items = w.findAll('.assist-model-item')
    expect(items).toHaveLength(3)
    expect(w.text()).toContain('本机 Ollama')
    expect(w.text()).toContain('本机')
    expect(w.text()).toContain('配置自定义 API 端点')
  })
  it('弹层开着时，点里面的选项不会被「点外部关闭」抢先吞掉（2026-09-27 回归）', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await openModelPop(w)

    // 真实浏览器里点击是 pointerdown → click 两步。外部关闭监听挂在
    // document 的 pointerdown 上：若它把弹层先关掉，选项的 click 就落在
    // 已卸载的元素上、永远不触发 —— 之前判据用了不存在的 `.assist-composer`
    // （一/二档时它根本不渲染），弹层一开就被关，选项点了没反应。
    const items = w.findAll('.assist-model-item')
    items[1]!.element.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }))
    await flushPromises()

    // pointerdown 不该把弹层关掉（点在弹层内）。
    expect(w.find('.assist-pop-model').exists()).toBe(true)

    await items[1]!.trigger('click')
    await flushPromises()

    // 选中生效：开关上显示新模型的短名。
    expect(w.find('.assist-switch-model .assist-switch-value').text()).toBe('qwen2.5-coder:7b')
  })

  it('选一个模型只改本地状态，不改服务端的进程默认值', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await openModelPop(w)
    // 第二项是 ollama。
    await w.findAll('.assist-model-item')[1].trigger('click')
    await flushPromises()

    // 界面切过去了（按钮显示短名，qwen2.5-coder:7b 无版本段、原样保留）。
    expect(w.find('.assist-switch-model .assist-switch-value').text()).toBe('qwen2.5-coder:7b')
    // **但没有调 setModel**：那个接口改的是所有人的默认值，需要教师身份。
    // 学生的选择通过请求体里的 `provider` 逐请求生效，只影响自己 ——
    // 见 api/assistance.ts 里 setModel 与 AssistRequest.provider 的注释。
    // 这条断言是防止有人"顺手"把它接回全局开关。
    expect(api.setModel).not.toHaveBeenCalled()
  })

  it('切换模型后再提问，请求里带上选中的 provider', async () => {
    api.listModels.mockResolvedValue(SNAP)
    api.chat.mockResolvedValue(reply({ content: '好的。' }))
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await openModelPop(w)
    await w.findAll('.assist-model-item')[1].trigger('click')
    await flushPromises()

    await w.find('#assist-instruction').setValue('问一句')
    await w.find('.assist-submit').trigger('click')
    await flushPromises()

    // 选中的模型随请求出去 —— 这是它真正生效的方式。
    expect(api.chat).toHaveBeenCalledWith(expect.objectContaining({ provider: 'ollama' }))
  })

  it('模型列表拉不到时静默降级 —— 提问照常可用', async () => {
    api.listModels.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    // 不渲染选择器，也不报错 —— 这是附属功能，它的失败不该看起来像
    // 整个 AI 不可用。
    expect(w.find('.assist-switch-model').exists()).toBe(false)
    expect(w.find('.assist-note-error').exists()).toBe(false)
    expect(w.find('#assist-instruction').exists()).toBe(true)
  })
})

describe('AssistPanel 跟随服务端的真实档位', () => {
  /**
   * 这一组测的是 2026-09-26 修掉的那个 bug：老师把作业设成五档，学生的
   * 面板却仍然按默认档（三档）渲染，看起来像设置没生效。
   *
   * 关键点：面板渲染的档位必须与**服务端裁决时用的那一个**是同一个来源。
   * 所以这里的断言不是显示了什么按钮，而是显示的东西跟着服务端走。
   */

  /**
   * 造一份"服务端返回的档位状态"。
   *
   * 各能力位必须按**档号**推出来，不能从 `L3` 摊开再改一两个字段 ——
   * 那样 `allows_chat` 会一直是 `true`，一档的用例于是在测一个一档不该
   * 有的东西，还"通过"得莫名其妙。这里照 `app/assistance.py` 的规则重写
   * 一遍，保证档号与能力位自洽。
   */
  const stateAt = (level: number, ceiling: number, preference: number | null = null) => {
    const policy = {
      ...L3,
      level,
      student_label: `${level} 档`,
      allows_chat: level >= 3,
      chat_is_hint_only: level < 4,
      allows_code_insertion: level >= 4,
      allows_full_solution: level >= 5
    }
    return { level, policy, ceiling, preference, has_assignment: true }
  }

  it('服务端说五档，面板就按五档渲染出「完整解法」', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    // 五档才有的入口出现了 —— 而传进来的 policy 还是三档。
    expect(buttons(w)).toContain('完整解法')
    expect(levelText(w)).toContain('5 档')
  })

  it('服务端说一档，面板就不给对话入口（哪怕传进来的 policy 是三档）', async () => {
    api.getLevelState.mockResolvedValue(stateAt(1, 1))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    expect(hasComposeBox(w)).toBe(false)
    expect(levelText(w)).toContain('1 档')
  })

  it('档位查询失败时退回传进来的 policy，面板照常可用', async () => {
    api.getLevelState.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L4, assignmentId: 1 })
    await flushPromises()

    // 四档的入口按 props.policy 渲染出来了。
    expect(hasInsertBox(w)).toBe(true)
    // 而且不弹错误 —— 这是附属查询，它的失败不该看起来像 AI 坏了。
    expect(w.find('.assist-note-error').exists()).toBe(false)
    // 取不到档位状态时**不给档位入口**：一个不知道上限的滚轮会让学生
    // 以为自己能随便调，点下去却被服务端夹回来。
    expect(w.find('.assist-switch').exists()).toBe(false)
  })
})

/**
 * 学生自己调辅助强度（本轮新增）。
 *
 * 这一组钉住的是"服务端支持但界面没有入口"那个缺口：上一版面板只有一句
 * 「5 档 · AI 代做」，学生除了接受它没有任何操作空间，而
 * `PUT /assistance/level` 一直是可以调的。
 *
 * 坏起来的样子：刻度尺渲染了、点得动，但点了不发请求（或发错值）——
 * 学生以为自己调低了，实际服务端照旧按五档放行。这只有对着服务端看才
 * 发现得了，所以把"点刻度 → 发什么请求"钉死。
 */
describe('AssistPanel 让学生自己调辅助强度', () => {
  /**
   * 同上一组的 `stateAt`：能力位必须按**档号**推出来。
   *
   * 从 `L3` 摊开再只改 `level` 会让 `allows_chat` 永远是 `true` ——
   * 「调到二档后输入区消失」这条用例于是测不出任何东西。
   */
  const stateAt = (level: number, ceiling: number, preference: number | null = null) => ({
    level,
    policy: {
      ...L3,
      level,
      student_label: `${level} 档`,
      allows_chat: level >= 3,
      chat_is_hint_only: level < 4,
      allows_code_insertion: level >= 4,
      allows_full_solution: level >= 5
    },
    ceiling,
    preference,
    has_assignment: true
  })

  it('拿到档位状态后，工具条上出现「强度」按钮；展开才渲染刻度轴', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    // 按钮常驻：不展开也知道当前是几档。
    expect(w.find('.assist-switch').exists()).toBe(true)
    expect(w.find('.assist-switch-value').text()).toBe('5 档')
    // 刻度轴本身收在弹层里。
    expect(w.find('.axis-track').exists()).toBe(false)

    await openLevelPop(w)

    expect(w.find('.axis-track').exists()).toBe(true)
    // 五档，轴上 5 个刻度；「跟随老师走」是轴外的一个芯片。
    expect(w.findAll('.axis-btn')).toHaveLength(5)
    expect(w.find('.axis-default').exists()).toBe(true)
  })

  it('老师上限之外的档位对学生禁用', async () => {
    api.getLevelState.mockResolvedValue(stateAt(2, 2))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    await openLevelPop(w)

    const steps = w.findAll('.axis-btn')
    expect(steps).toHaveLength(5)
    // 第 3、4、5 档（index 2..4）被上限卡住。
    expect(steps[2].attributes('disabled')).toBeDefined()
    expect(steps[4].attributes('disabled')).toBeDefined()
    // 前两档可选。
    expect(steps[0].attributes('disabled')).toBeUndefined()
    expect(steps[1].attributes('disabled')).toBeUndefined()
  })

  it('点某一档就把这一档写进服务端', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    api.setLevelPreference.mockResolvedValue(stateAt(2, 5, 2))
    const w = mountPanel({ policy: L3, assignmentId: 7 })
    await flushPromises()

    await openLevelPop(w)
    // 第二个刻度 = 二档。
    await w.findAll('.axis-btn')[1].trigger('click')
    await flushPromises()

    expect(api.setLevelPreference).toHaveBeenCalledWith(2, 7)
  })

  it('调低之后顶栏跟着变成新档位', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    api.setLevelPreference.mockResolvedValue(stateAt(2, 5, 2))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()
    expect(levelText(w)).toContain('5 档')

    await openLevelPop(w)
    await w.findAll('.axis-btn')[1].trigger('click')
    await flushPromises()

    expect(levelText(w)).toContain('2 档')
    // 面板的能力也跟着收窄：二档没有对话，输入区该消失。
    expect(hasComposeBox(w)).toBe(false)
  })

  /**
   * 确认信息**不能挂在工具条里**。
   *
   * 工具条整块挂在"有对话或代写能力"下面，而"往低调"正是最需要看到确认的
   * 时刻 —— 二档没有工具条。挂进去的话这条消息会随工具条一起消失，学生
   * 看到的是点了、只有顶栏变了，像是没保存成功。
   */
  it('调完之后说一句，且这句话在二档（没有输入区）也看得见', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    api.setLevelPreference.mockResolvedValue(stateAt(2, 5, 2))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    await openLevelPop(w)
    await w.findAll('.axis-btn')[1].trigger('click')
    await flushPromises()

    expect(hasChatBox(w)).toBe(false)
    expect(w.find('.assist-note-ok').exists()).toBe(true)
    expect(w.text()).toContain('已把这次作业的辅助强度调成 2 档')
  })

  it('「改回默认」传 null，不是老师那个数字 —— 老师之后改档位时才能跟着走', async () => {
    api.getLevelState.mockResolvedValue(stateAt(2, 5, 2))
    api.setLevelPreference.mockResolvedValue(stateAt(5, 5))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    await openLevelPop(w)
    // 「跟随老师走」是轴上方的一个芯片，不在 5 个刻度里。
    const reset = w.find('.axis-default')
    expect(reset.text()).toContain('跟随老师走')
    await reset.trigger('click')
    await flushPromises()

    expect(api.setLevelPreference).toHaveBeenCalledWith(null, 1)
  })

  it('保存失败时不假装成功 —— 顶栏保持原档位，并说明原因', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    api.setLevelPreference.mockRejectedValue(new Error('需要登录才能保存辅助强度偏好'))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    await openLevelPop(w)
    await w.findAll('.axis-btn')[1].trigger('click')
    await flushPromises()

    expect(w.find('.assist-note-error').text()).toContain('需要登录')
    // 档位没有被乐观地改掉。
    expect(levelText(w)).toContain('5 档')
  })

  it('档位表拉不到时不渲染档位入口 —— 没有刻度的选择器比没有更糟', async () => {
    api.getLevelState.mockResolvedValue(stateAt(5, 5))
    api.listLevels.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3, assignmentId: 1 })
    await flushPromises()

    // 按钮整个不出现：一个点开是空刻度的入口，比没有入口更让人困惑。
    expect(w.find('.assist-switch').exists()).toBe(false)
    expect(w.find('.axis-track').exists()).toBe(false)
  })
})
