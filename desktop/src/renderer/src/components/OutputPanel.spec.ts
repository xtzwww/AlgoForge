// @vitest-environment jsdom
/**
 * 输出面板的组件测试。
 *
 * 这个组件**不拥有状态**：父组件把事件流推进来，它只负责推导显示什么
 * （见组件头注释）。所以它可以完全脱离 WebSocket 与 HTTP 来测——传 events
 * 和 result 就够了。这在此项目里是少数，值得利用。
 *
 * 选的四条都是「面板坏得静悄悄」的那类：不报错、不空白，只是该出现的不出现、
 * 或者出现了一份读起来像结论其实不是结论的字。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import OutputPanel from './OutputPanel.vue'
import type { JudgeEvent } from '../api/ws'
import type { JudgeResponse } from '../api/judge'

const mounted: VueWrapper[] = []

function mountPanel(props: {
  events?: JudgeEvent[]
  result?: JudgeResponse | null
  errorMsg?: string
  isRunning?: boolean
  isScratch?: boolean
}): VueWrapper {
  const w = mount(OutputPanel, {
    props: {
      events: props.events ?? [],
      result: props.result ?? null,
      errorMsg: props.errorMsg ?? '',
      isRunning: props.isRunning ?? false,
      isScratch: props.isScratch ?? false
    },
    attachTo: document.body
  })
  mounted.push(w)
  return w
}

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

function result(over: Partial<JudgeResponse> = {}): JudgeResponse {
  return {
    status: 'WA',
    compile_output: '',
    passed: 1,
    total: 2,
    problem_id: 7,
    test_cases: [],
    ...over
  }
}

/** 一次失败的分析：`ok: false`，三段全空。 */
const AGENT_FAILED: JudgeEvent = {
  type: 'agent_done',
  ok: false,
  error: '模型超时',
  error_type: null
}

const AGENT_OK: JudgeEvent = {
  type: 'agent_done',
  ok: true,
  error_type: 'misread_problem',
  error_type_description: '理解错题意',
  error_detail: '把输入当成了一行数。',
  hint: '先读 n，再读 n 个数。',
  suggested_fix: '先 cin >> n，再循环 n 次读。'
}

describe('OutputPanel', () => {
  it('分析失败时不渲染分层反馈——否则会出现一张「以上是全部反馈」的空卡', () => {
    const w = mountPanel({
      events: [{ type: 'agent_start', verdict: 'WA' }, AGENT_FAILED],
      result: result()
    })

    // 历史故障：这里曾是 `v-if` 缺失，于是学生看到一张空卡片，
    // 读起来像「分析完了，但什么都没说」。
    expect(w.find('.agent-diagnosis').exists()).toBe(false)
    expect(w.find('.agent-panel').text()).toContain('分析未完成')
    // 失败原因要说出来，否则「分析未完成」等于什么都没解释。
    expect(w.text()).toContain('模型超时')
  })

  it('分析成功时把三段交给分层反馈，并逐层释放', async () => {
    const w = mountPanel({
      events: [{ type: 'agent_start', verdict: 'WA' }, AGENT_OK],
      result: result()
    })

    const card = w.find('.agent-diagnosis')
    expect(card.exists()).toBe(true)
    // 初始一层不给。
    expect(card.findAll('.feedback-section').filter((s) => s.isVisible())).toHaveLength(0)

    await card.find('.btn-reveal').trigger('click')
    expect(card.text()).toContain('把输入当成了一行数。')
    // 中文错因在卡片里，标题栏不重复一遍（两处同一事实迟早会不一致）。
    expect(card.text()).toContain('理解错题意')
  })

  it('工具那一行说「返回了什么」，不是「调用了什么」', () => {
    const w = mountPanel({
      events: [
        { type: 'agent_start', verdict: 'WA' },
        {
          type: 'agent_tool',
          name: 'run_code',
          ok: true,
          result: { stdout: '21\n', exit_code: 0, timed_out: false }
        },
        AGENT_OK
      ],
      result: result()
    })

    const gist = w.find('.agent-tool-gist').text()
    // 「Agent 调用了 run_code」不是信息；「输出：21」才是。
    expect(gist).toContain('输出：21')
    expect(w.find('.agent-tool-name').text()).toBe('run_code')
    // 完整结果留在折叠区里，供真正想看的人展开。
    expect(w.find('.agent-tool-raw').text()).toContain('"stdout"')
  })

  it('工具失败时标失败，不装作成功', () => {
    const w = mountPanel({
      events: [
        { type: 'agent_start', verdict: 'WA' },
        { type: 'agent_tool', name: 'judge', ok: false, result: { ok: false, error: '编译错误' } },
        AGENT_OK
      ],
      result: result()
    })

    expect(w.find('.agent-tool-ok').classes()).toContain('agent-tool-bad')
    expect(w.find('.agent-tool-gist').text()).toContain('编译错误')
  })

  it('判题跑完但分析还在跑时，仍然说「分析中…」', () => {
    const w = mountPanel({
      events: [
        { type: 'case_done', index: 1, status: 'AC' },
        { type: 'judge_done', passed: 1, total: 2, analysing: true },
        { type: 'agent_start', verdict: 'WA' }
      ],
      result: result(),
      isRunning: true
    })

    // 病例跑完了、进度条满了，但整件事没结束。这一行要是消失，
    // 学生就会以为卡住了——这正是 P2-8 修掉的那条。
    expect(w.find('.agent-state').text()).toBe('分析中…')
  })

  it('试运行要标明未记录，别让学生以为交了', () => {
    const scratch = mountPanel({ result: result(), isScratch: true })
    expect(scratch.find('.scratch-note').text()).toContain('未记录')

    const submit = mountPanel({ result: result(), isScratch: false })
    expect(submit.find('.scratch-note').exists()).toBe(false)
  })

  it('没有 Agent 事件时整块不出现', () => {
    const w = mountPanel({
      events: [{ type: 'case_done', index: 0, status: 'AC' }],
      result: result()
    })

    expect(w.find('.agent-panel').exists()).toBe(false)
  })
})
