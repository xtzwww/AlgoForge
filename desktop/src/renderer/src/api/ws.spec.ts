/**
 * 判题流 `streamJudge` 的生命周期测试。
 *
 * 这个文件存在的理由是 2026-09-18 的一个真实故障：客户端把 `judge_done`
 * 当成「流结束了」，立刻关闭 WebSocket。而侧车是先落库、先发 `judge_done`，
 * **再**跑智能体分析——分析的事件走同一条连接。于是「WA 之后永远看不到
 * 错因」被用户读成「分析没跑」，排查方向从第一天起就错了。
 *
 * 修法是引入 `judge_done.analysing`：有没有后续事件，只有服务端知道，
 * 客户端猜不出来（AC 和 WA 在这一刻长得一样）。本文件把这条时序钉死，
 * 因为它是那种「改动看起来无关、后果隔着一个页面才出现」的逻辑——
 * 任何人重排 `onmessage` 里的分支都会在这里被拦下。
 *
 * 为什么用假的 WebSocket 而不是起一个真服务：要测的是**关闭时机**，
 * 那是客户端单方面的决定，与服务端无关。真连接反而测不到「在收到
 * agent_done 之前不许关」这种断言——只能观察到结果，观察不到时刻。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { JudgeEvent, WsJudgeRequest } from './ws'
import { streamJudge } from './ws'

/**
 * 受控的 WebSocket 替身。
 *
 * 除了记录状态，它把「服务端推一帧」暴露成 `push()`，测试因此可以在
 * 两次推送之间断言 socket 的开关状态——这正是被测逻辑的全部内容。
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  readonly sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }

  /** 服务端推一条 JSON 事件。 */
  push(event: unknown): void {
    this.onmessage?.({ data: JSON.stringify(event) } as unknown as MessageEvent)
  }

  /** 推一帧**不是** JSON 的数据。 */
  pushRaw(data: unknown): void {
    this.onmessage?.({ data } as unknown as MessageEvent)
  }

  static get last(): FakeWebSocket {
    const last = FakeWebSocket.instances.at(-1)
    if (!last) throw new Error('还没有创建过 WebSocket')
    return last
  }
}

// `vi.mock` 的 factory 会被提升到 import 之前，所以可变状态必须先
// `vi.hoisted` 挂出去，否则 factory 里引用不到。
const hoisted = vi.hoisted(() => ({
  sidecarInfo: { url: 'http://127.0.0.1:8123', token: 'tok/en x' } as {
    url: string
    token: string
  } | null,
  userToken: ''
}))

// 两个模块依赖都被替掉，且只在本文件内生效：
// - `runtime` 要连真侧车才拿得到 sidecar 信息（构建期 alias 指向的实现
//   还会碰 Electron 桥 / fetch），测试只需它给出一个 URL。
// - `session` 顶层读 localStorage 并注册 axios 拦截器，与被测逻辑无关。
vi.mock('../runtime', () => ({
  runtime: { getSidecarInfo: async () => hoisted.sidecarInfo }
}))

vi.mock('./session', () => ({
  sessionToken: () => hoisted.userToken
}))

interface Harness {
  ws: FakeWebSocket
  events: JudgeEvent[]
  /** resolve 出来的终态事件，未 resolve 时为 null。 */
  settled: JudgeEvent | null
  /** reject 出来的错误，未 reject 时为 null。 */
  failed: Error | null
  promise: Promise<JudgeEvent | null>
}

async function start(req: WsJudgeRequest): Promise<Harness> {
  const events: JudgeEvent[] = []
  const harness: Harness = {
    ws: undefined as unknown as FakeWebSocket,
    events,
    settled: null,
    failed: null,
    promise: Promise.resolve(null)
  }

  harness.promise = streamJudge(req, (e) => events.push(e)).then(
    (v) => {
      harness.settled = v
      return v
    },
    (e: Error) => {
      harness.failed = e
      return null
    }
  )

  // `streamJudge` 是 async，`new WebSocket` 发生在第一个 await 之后，
  // 所以要先把微任务队列放干净。
  await new Promise((r) => setImmediate(r))
  harness.ws = FakeWebSocket.last
  harness.ws.onopen?.()
  return harness
}

const REQUEST: WsJudgeRequest = { code: 'int main(){}', problem_id: 7 }

describe('streamJudge', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    hoisted.sidecarInfo = { url: 'http://127.0.0.1:8123', token: 'tok/en x' }
    hoisted.userToken = ''
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('把 http 基址换成 ws，token 走 query param', async () => {
    const { ws } = await start(REQUEST)
    // WebSocket 握不上自定义头，所以 token 只能挂在 URL 上，且必须转义。
    expect(ws.url).toBe('ws://127.0.0.1:8123/ws/run-log?token=tok%2Fen%20x')
  })

  it('连接建立后才发请求体', async () => {
    const { ws } = await start(REQUEST)
    expect(ws.sent).toHaveLength(1)
    expect(JSON.parse(ws.sent[0])).toMatchObject({ code: 'int main(){}', problem_id: 7 })
  })

  it('未登录时不带 user_token', async () => {
    const { ws } = await start(REQUEST)
    expect(JSON.parse(ws.sent[0])).not.toHaveProperty('user_token')
  })

  it('已登录时把 user_token 拼进请求体', async () => {
    hoisted.userToken = 'user-token-hex'
    const { ws } = await start(REQUEST)
    expect(JSON.parse(ws.sent[0])).toMatchObject({ user_token: 'user-token-hex' })
  })

  it('侧车信息拿不到时直接抛错，一条连接都不建', async () => {
    hoisted.sidecarInfo = null
    await expect(streamJudge(REQUEST, () => {})).rejects.toThrow(/sidecar/i)
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('judge_done 且没有后续分析：立刻关闭并 resolve', async () => {
    const h = await start(REQUEST)
    h.ws.push({ type: 'judge_done', passed: 5, total: 5, analysing: false })

    expect(h.ws.closed).toBe(true)
    // resolve 发生在微任务里：socket 关了不等于 promise 已经落地。
    await h.promise
    expect(h.settled).toMatchObject({ type: 'judge_done', passed: 5 })
  })

  it('judge_done 不带 analysing 字段时按无分析处理', async () => {
    // 老服务端不发这个字段。宁可当成「没有后续」——那样只是看不到分析，
    // 而反过来（当成「有后续」）会让客户端永远挂着不 resolve。
    const h = await start(REQUEST)
    h.ws.push({ type: 'judge_done', passed: 5, total: 5 })

    expect(h.ws.closed).toBe(true)
    await h.promise
    expect(h.settled).not.toBeNull()
  })

  it('judge_done 带 analysing=true：先不关，等 agent_done', async () => {
    const h = await start(REQUEST)
    h.ws.push({ type: 'judge_done', passed: 0, total: 5, analysing: true })

    // 整个 bug 就在这里：以前这一行是 `expect(closed).toBe(true)`。
    expect(h.ws.closed).toBe(false)
    expect(h.settled).toBeNull()

    h.ws.push({ type: 'agent_start', verdict: 'WA' })
    expect(h.ws.closed).toBe(false)

    h.ws.push({ type: 'agent_done', error_type: 'misread_problem', suggestion: '【错在哪】…' })

    expect(h.ws.closed).toBe(true)
    await h.promise
    // resolve 出来的是**判定**而不是分析：调用方要的是终态事件，
    // 分析内容通过 onEvent 已经陆续给过了。
    expect(h.settled).toMatchObject({ type: 'judge_done', passed: 0 })
  })

  it('分析期间的每一条事件都转发给 onEvent', async () => {
    const h = await start(REQUEST)
    h.ws.push({ type: 'compile_done', success: true })
    h.ws.push({ type: 'judge_done', passed: 0, total: 5, analysing: true })
    h.ws.push({ type: 'agent_start', verdict: 'WA' })
    h.ws.push({ type: 'agent_tool', name: 'run_code', ok: true })
    h.ws.push({ type: 'agent_done', error_type: 'misread_problem' })
    await h.promise

    expect(h.events.map((e) => e.type)).toEqual([
      'compile_done',
      'judge_done',
      'agent_start',
      'agent_tool',
      'agent_done'
    ])
  })

  it('judge_error 同样终结流', async () => {
    const h = await start(REQUEST)
    h.ws.push({ type: 'judge_error', error: 'compile failed' })

    expect(h.ws.closed).toBe(true)
    await h.promise
    expect(h.settled).toMatchObject({ type: 'judge_error' })
  })

  it('agent_done 先于判定到达时不关闭（防御性）', async () => {
    // 正常时序下不会发生；但真发生了也不能把 socket 关掉——那会让
    // 后面的 judge_done 落在一个已关闭的连接上，promise 永远不 resolve。
    const h = await start(REQUEST)
    h.ws.push({ type: 'agent_done', error_type: 'x' })

    expect(h.ws.closed).toBe(false)
    expect(h.settled).toBeNull()
  })

  it('判定到达前连接被关闭：reject', async () => {
    const h = await start(REQUEST)
    h.ws.push({ type: 'case_done', index: 0 })
    h.ws.close()

    await h.promise
    expect(h.failed?.message).toMatch(/closed before judge_done/i)
  })

  it('连接失败且没有判定：reject', async () => {
    const h = await start(REQUEST)
    h.ws.onerror?.()

    await h.promise
    expect(h.failed?.message).toMatch(/connection failed/i)
  })

  it('畸形 JSON 被忽略，不影响后续事件', async () => {
    const h = await start(REQUEST)
    h.ws.pushRaw('not json at all')
    h.ws.pushRaw(undefined)

    expect(h.events).toHaveLength(0)
    expect(h.ws.closed).toBe(false)

    h.ws.push({ type: 'judge_done', passed: 5, total: 5, analysing: false })
    expect(h.ws.closed).toBe(true)
    await h.promise
    expect(h.settled).not.toBeNull()
  })
})
