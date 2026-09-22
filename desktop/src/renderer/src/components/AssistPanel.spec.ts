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
  getAssistanceUsage: vi.fn()
}))

vi.mock('../api/assistance', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/assistance')>()),
  complete: api.complete,
  chat: api.chat,
  insert: api.insert,
  solution: api.solution
}))

import AssistPanel from './AssistPanel.vue'

const mounted: VueWrapper[] = []

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
  vi.clearAllMocks()
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
    // 被拒用的是和成功回复一样的版式 —— 没有 .assist-note-error。
    expect(w.find('.assist-reply-reason').exists()).toBe(true)
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
