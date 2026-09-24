// @vitest-environment jsdom
/**
 * 辅助面板的组件测试。
 *
 * 这个组件坏起来的样子特别安静：档位判断写反，多出来的按钮不报错、不空白，
 * 只是学生能在"只给提示"的作业上点出完整解法；或者反过来，五档的学生看不见
 * 「完整解法」，功能像没做。两种都不触发任何警告，只有人眼能发现 —— 所以
 * 用测试把这张表钉死。
 *
 * 三类断言
 * --------
 *   1. **按档位出现哪些入口**（下面那张表）。这是组件唯一真正的判断逻辑。
 *   2. **越档的回复按普通消息渲染**，不是红色报错。服务端对越档返回
 *      200 + `allowed=false` + `reason`，把它渲染成错误会把"教学设置"
 *      说成"系统故障"，学生就会去反馈一个不存在的 bug。
 *   3. **代码只经 `insert` 事件出去**，面板从不自己改 `currentCode`。
 *      面板拿不到编辑器是有意的：写进缓冲区要经过页面的追加逻辑。
 *
 * mock 的写法沿用 T34 的教训：`vi.mock` 的工厂会被提升到 import 之前，
 * 且**只导出被 mock 的函数会把模块里的常量一起吃掉** —— 子组件读常量时炸，
 * 报错指向组件而不是 mock。这里用 `importOriginal` 铺底。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
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
  setModel: vi.fn()
}))

vi.mock('../api/assistance', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/assistance')>()),
  complete: api.complete,
  chat: api.chat,
  insert: api.insert,
  solution: api.solution,
  listModels: api.listModels,
  setModel: api.setModel
}))

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

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
  vi.clearAllMocks()
  api.listModels.mockRejectedValue(new Error('not under test'))
})

/** 一档：只有本机补全。 */
const L1: AssistancePolicy = {
  level: 1,
  label: '纯手写',
  student_label: '不给 AI',
  description: '只有本机的词表补全，不联网。',
  completion_uses_model: false,
  allows_chat: false,
  chat_is_hint_only: true,
  allows_code_insertion: false,
  allows_full_solution: false
}

/** 三档：加对话，但只给提示。 */
const L3: AssistancePolicy = {
  level: 3,
  label: '提示模式',
  student_label: '可以问思路',
  description: '可以问 AI，但它只讲思路，不给代码。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: true,
  allows_code_insertion: false,
  allows_full_solution: false
}

/** 四档：能写代码，但不能整解。 */
const L4: AssistancePolicy = {
  level: 4,
  label: '部分代写',
  student_label: '可以代写片段',
  description: '可以让 AI 写一段代码插进来，但不给整道题。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: false,
  allows_code_insertion: true,
  allows_full_solution: false
}

/** 五档：全开。 */
const L5: AssistancePolicy = {
  level: 5,
  label: 'AI 代做',
  student_label: '可以直接要答案',
  description: '可以让 AI 给出完整解法。',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: false,
  allows_code_insertion: true,
  allows_full_solution: true
}

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

/** 面板上所有按钮的文字。 */
function buttons(w: VueWrapper): string[] {
  return w.findAll('button').map((b) => b.text().trim())
}

/** 对话输入区在不在。 */
function hasChatBox(w: VueWrapper): boolean {
  return w.find('#assist-question').exists()
}

/** 代写输入区在不在。 */
function hasInsertBox(w: VueWrapper): boolean {
  return w.find('#assist-instruction').exists()
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
  })

  it('二档补全改口叫 AI 补全，但对话仍未开放', () => {
    const w = mountPanel({ policy: { ...L1, level: 2, completion_uses_model: true } })

    expect(buttons(w)).toContain('AI 补全')
    expect(buttons(w)).not.toContain('本机补全')
    expect(hasChatBox(w)).toBe(false)
  })

  it('三档开放对话，并标明只给提示', () => {
    const w = mountPanel({ policy: L3 })

    expect(hasChatBox(w)).toBe(true)
    expect(hasInsertBox(w)).toBe(false)
    expect(buttons(w)).not.toContain('完整解法')
    // 「只给提示」这个标签是学生理解三档的唯一线索，不能只靠说明文字。
    expect(w.text()).toContain('只给提示')
  })

  it('四档开放代写，但仍不给完整解法', () => {
    const w = mountPanel({ policy: L4 })

    expect(hasChatBox(w)).toBe(true)
    expect(hasInsertBox(w)).toBe(true)
    expect(buttons(w)).not.toContain('完整解法')
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

    await w.find('#assist-question').setValue('为什么用前缀和？')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(api.chat).toHaveBeenCalledWith(expect.objectContaining({ question: '为什么用前缀和？' }))
    expect(w.text()).toContain('想想前缀和。')
    // 提示型回复没有插入按钮 —— 三档的代码不该有捷径进缓冲区。
    expect(buttons(w)).not.toContain('插入到编辑器')
    expect(w.emitted('insert')).toBeUndefined()
  })

  it('三档的回复被降级时说出来，学生才知道不是模型抽风', async () => {
    api.chat.mockResolvedValue(reply({ content: '思路是先排序。', downgraded: true }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('怎么写')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('已自动降级')
  })

  it('问空了不请求', async () => {
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('   ')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(api.chat).not.toHaveBeenCalled()
  })
})

describe('AssistPanel 的越档处理', () => {
  it('被拒渲染成一句解释，不是错误', async () => {
    api.chat.mockResolvedValue(
      reply({ allowed: false, reason: '这次作业只用本机补全。', content: '' })
    )
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('给我答案')
    await w.find('.assist-chat button').trigger('click')
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

    await w.find('#assist-question').setValue('在吗')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(w.find('.assist-note-error').text()).toContain('Network Error')
  })

  it('失败后不清空输入框 —— 重试不该重打一遍', async () => {
    api.chat.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('这个问题很长')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect((w.find('#assist-question').element as HTMLTextAreaElement).value).toBe('这个问题很长')
  })

  it('成功后清空输入框', async () => {
    api.chat.mockResolvedValue(reply({}))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('问一句')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect((w.find('#assist-question').element as HTMLTextAreaElement).value).toBe('')
  })
})

describe('AssistPanel 的完整解法', () => {
  it('五档的解法带插入按钮，点了才发事件', async () => {
    api.solution.mockResolvedValue(
      reply({ action: 'full_solution', content: 'int main(){}', policy: L5, level: 5 })
    )
    const w = mountPanel({ policy: L5, statement: '求 a+b' })

    const solve = w.findAll('button').find((b) => b.text().trim() === '完整解法')
    await solve?.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('int main(){}')
    const insertBtn = w.findAll('button').find((b) => b.text().trim() === '插入到编辑器')
    expect(insertBtn).toBeDefined()

    expect(w.emitted('insert')).toBeUndefined()
    await insertBtn?.trigger('click')
    expect(w.emitted('insert')?.[0]).toEqual(['int main(){}'])
  })
})

describe('AssistPanel 换题清空', () => {
  it('换题目后上一题的回复不留在屏幕上', async () => {
    api.chat.mockResolvedValue(reply({ content: '上一题的答案' }))
    const w = mountPanel({ policy: L3, problemId: 1 })

    await w.find('#assist-question').setValue('问')
    await w.find('.assist-chat button').trigger('click')
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

    await w.find('#assist-question').setValue('第一问')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    await w.find('#assist-question').setValue('第二问')
    await w.find('.assist-chat button').trigger('click')
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

    await w.find('#assist-question').setValue('问题')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(w.findAll('.assist-turn.is-student')).toHaveLength(1)
    expect(w.findAll('.assist-turn.is-assistant')).toHaveLength(1)
  })

  it('提问后输入框立刻清空，问题已经进了时间线', async () => {
    api.chat.mockResolvedValue(reply({ content: '回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('问题')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect((w.find('#assist-question').element as HTMLTextAreaElement).value).toBe('')
    expect(w.text()).toContain('问题')
  })

  it('一档没有对话入口时时间线不渲染', () => {
    const w = mountPanel({ policy: L1 })
    expect(w.find('.assist-timeline').exists()).toBe(false)
  })

  it('降级的回复带一条说明 —— 学生要知道自己看的是被削过的内容', async () => {
    api.chat.mockResolvedValue(reply({ content: '只讲讲思路', downgraded: true }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('给我代码')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    expect(w.find('.assist-turn-flag').exists()).toBe(true)
    expect(w.text()).toContain('已自动降级')
  })

  it('对话的回答不进一次性回复区（那个区只放能插进编辑器的产物）', async () => {
    api.chat.mockResolvedValue(reply({ content: '一段回答' }))
    const w = mountPanel({ policy: L3 })

    await w.find('#assist-question').setValue('问')
    await w.find('.assist-chat button').trigger('click')
    await flushPromises()

    // `.assist-reply` 是补全/写一段/整解的容器，对话不该出现在里面。
    expect(w.find('.assist-reply').exists()).toBe(false)
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

  it('只有一个模型时不渲染选择器 —— 单选的下拉是噪声', async () => {
    api.listModels.mockResolvedValue({
      ...SNAP,
      models: [SNAP.models[0]]
    })
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    expect(w.find('.assist-model').exists()).toBe(false)
  })

  it('多个模型时渲染选择器，并显示当前选中的名字', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    expect(w.find('.assist-model').exists()).toBe(true)
    expect(w.find('.assist-model-name').text()).toBe('DeepSeek')
  })

  it('打开菜单能看到全部模型，本机的带「本机」标记', async () => {
    api.listModels.mockResolvedValue(SNAP)
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await w.find('.assist-model-trigger').trigger('click')

    const items = w.findAll('.assist-model-item')
    expect(items).toHaveLength(2)
    expect(w.text()).toContain('本机 Ollama')
    expect(w.text()).toContain('本机')
  })

  it('选一个模型会调 setModel，并把界面切过去', async () => {
    api.listModels.mockResolvedValue(SNAP)
    api.setModel.mockResolvedValue({ ...SNAP, active: 'ollama' })
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await w.find('.assist-model-trigger').trigger('click')
    // 第二项是 ollama。
    await w.findAll('.assist-model-item')[1].trigger('click')
    await flushPromises()

    expect(api.setModel).toHaveBeenCalledWith('ollama')
    expect(w.find('.assist-model-name').text()).toBe('本机 Ollama')
  })

  it('切换失败时提示一句，但选择器仍显示原来的模型', async () => {
    api.listModels.mockResolvedValue(SNAP)
    api.setModel.mockRejectedValue(new Error('该模型未配置'))
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    await w.find('.assist-model-trigger').trigger('click')
    await w.findAll('.assist-model-item')[1].trigger('click')
    await flushPromises()

    expect(w.find('.assist-note-error').text()).toContain('该模型未配置')
    // 没切成，就不能显示成切成了。
    expect(w.find('.assist-model-name').text()).toBe('DeepSeek')
  })

  it('模型列表拉不到时静默降级 —— 提问照常可用', async () => {
    api.listModels.mockRejectedValue(new Error('Network Error'))
    const w = mountPanel({ policy: L3 })
    await flushPromises()

    // 不渲染选择器，也不报错 —— 这是附属功能，它的失败不该看起来像
    // 整个 AI 不可用。
    expect(w.find('.assist-model').exists()).toBe(false)
    expect(w.find('.assist-note-error').exists()).toBe(false)
    expect(w.find('#assist-question').exists()).toBe(true)
  })
})
