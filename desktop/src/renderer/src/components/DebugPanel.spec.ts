// @vitest-environment jsdom
/**
 * 调试面板的组件测试。
 *
 * 这个组件**不拥有状态**：父组件把会话状态推进来，它只负责渲染
 * （见组件头注释）。所以可以完全脱离 HTTP 与 gdb 来测——传 session 就够了。
 *
 * 选的用例都是「面板坏得静悄悄」的那类：不报错、不空白，只是显示了一份
 * 读起来像事实、其实不是事实的东西。这类错误在这块面板上代价特别高，
 * 因为学生来这里就是为了相信屏幕上写的东西：
 *
 * - 把 gdb 打印的**参数值**当函数名渲染，会变成 `sum (n=5)()`；
 * - 把未初始化变量的**随机字节**当成值渲染，学生会以为循环从 583006768 开始；
 * - 程序已经结束却还显示「继续」，学生会按下去然后得到一个错误；
 * - 标准库栈帧不折叠，学生自己的 `main` 会被挤出可见区域。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import DebugPanel from './DebugPanel.vue'
import type { DebugFrame, DebugSession, DebugStop, DebugVariable } from '../api/debug'

const mounted: VueWrapper[] = []

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

function frame(over: Partial<DebugFrame> = {}): DebugFrame {
  return {
    level: 0,
    function: 'sum (n=5)',
    display_function: 'sum',
    file: 'main.cpp',
    line: 7,
    address: '0x4011a0',
    is_library: false,
    ...over
  }
}

function variable(over: Partial<DebugVariable> = {}): DebugVariable {
  return { name: 'i', value: '0', kind: 'local', uninitialised: false, ...over }
}

function stop(over: Partial<DebugStop> = {}): DebugStop {
  return {
    reason: 'breakpoint',
    file: 'main.cpp',
    line: 7,
    function: 'sum',
    breakpoint_number: 1,
    signal: null,
    signal_description: null,
    exit_code: null,
    variables: [],
    stack: [],
    source_line: null,
    line_visited_before: false,
    ...over
  }
}

function session(over: Partial<DebugSession> = {}): DebugSession {
  return {
    session_id: 'abc',
    breakpoints: [],
    built: true,
    finished: false,
    stop: null,
    ...over
  }
}

function mountPanel(props: {
  session?: DebugSession | null
  busy?: boolean
  errorMsg?: string
  notice?: string
  available?: boolean
  unavailableReason?: string
  hasCode?: boolean
}): VueWrapper {
  const w = mount(DebugPanel, {
    props: {
      session: props.session ?? null,
      busy: props.busy ?? false,
      errorMsg: props.errorMsg ?? '',
      notice: props.notice ?? '',
      available: props.available ?? true,
      unavailableReason: props.unavailableReason ?? '',
      hasCode: props.hasCode ?? true
    },
    attachTo: document.body
  })
  mounted.push(w)
  return w
}

/** Every button's label, in order. */
function buttonLabels(w: VueWrapper): string[] {
  return w.findAll('button').map((b) => b.text())
}

function buttonByText(w: VueWrapper, text: string) {
  return w.findAll('button').find((b) => b.text() === text)
}

describe('DebugPanel — 工具链不可用', () => {
  it('没有工具链时说明缺少什么，并且不给出按下去会失败的按钮', () => {
    const w = mountPanel({
      available: false,
      unavailableReason: '没有找到配套的调试工具链，调试功能需要以下两个文件：g++.exe、gdb.exe'
    })

    expect(w.text()).toContain('暂时无法使用断点调试')
    // 原因原文照出——「调试不可用」四个字学生拿去问老师也没用。
    expect(w.text()).toContain('gdb.exe')
    // 关键：一个按钮都不给。给一个必然失败的按钮比不给更糟。
    expect(w.findAll('button')).toHaveLength(0)
    // 并且要说清做题不受影响，否则学生会以为整个平台坏了。
    expect(w.text()).toContain('判题和 AI 分析不受影响')
  })
})

describe('DebugPanel — 起始状态', () => {
  it('还没运行时只给「运行」，单步按钮全部禁用', () => {
    const w = mountPanel({ session: session({ stop: null }) })

    const run = buttonByText(w, '运行')
    expect(run).toBeDefined()
    expect(run?.attributes('disabled')).toBeUndefined()

    // 没有停住的位置就没有可移动的步——按下去服务端会回 400。
    for (const label of ['单步跳过', '单步进入', '跳出']) {
      expect(buttonByText(w, label)?.attributes('disabled')).toBeDefined()
    }
  })

  it('还没编译完时不给「运行」——那时还没有可执行文件', () => {
    const w = mountPanel({ session: session({ built: false }) })

    expect(buttonByText(w, '运行')?.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('正在编译')
  })

  it('没有代码时不显示控制条，提示先去写代码', () => {
    const w = mountPanel({ hasCode: false })

    expect(w.text()).toContain('还没有可以调试的代码')
    expect(w.findAll('button')).toHaveLength(0)
  })
})

describe('DebugPanel — 重启与继续的区别', () => {
  it('程序结束后按钮变成「重新运行」，因为服务端把 continue 当成重启', () => {
    const w = mountPanel({
      session: session({ finished: true, stop: stop({ reason: 'exited', exit_code: 0 }) })
    })

    // 显示「继续」而实际会重启，是一个只有按下去才发现的谎言。
    expect(buttonByText(w, '继续')).toBeUndefined()
    expect(buttonByText(w, '重新运行')).toBeDefined()
    expect(w.text()).toContain('程序已正常结束')
  })

  it('程序结束后单步按钮禁用——没有位置可以移动', () => {
    const w = mountPanel({
      session: session({ finished: true, stop: stop({ reason: 'exited', exit_code: 0 }) })
    })

    for (const label of ['单步跳过', '单步进入', '跳出']) {
      expect(buttonByText(w, label)?.attributes('disabled')).toBeDefined()
    }
  })

  it('停在断点上时显示「继续」', () => {
    const w = mountPanel({ session: session({ stop: stop() }) })

    expect(buttonByText(w, '继续')).toBeDefined()
    expect(buttonByText(w, '运行')).toBeUndefined()
  })
})

describe('DebugPanel — 变量', () => {
  it('未初始化的变量显示「尚未初始化」，绝不显示 gdb 读到的随机字节', () => {
    const w = mountPanel({
      session: session({
        stop: stop({ variables: [variable({ name: 'i', value: '583006768', uninitialised: true })] })
      })
    })

    expect(w.text()).toContain('尚未初始化')
    // 这一条是整块面板最重要的断言：把 583006768 显示出来，学生会以为
    // 循环从那里开始。实测同一个断点跑三次得到三个不同的数。
    expect(w.text()).not.toContain('583006768')
    // 还要解释为什么没有值，否则跟调试器坏了长得一样。
    expect(w.text()).toContain('还没有执行到')
  })

  it('正常变量照常显示值', () => {
    const w = mountPanel({
      session: session({ stop: stop({ variables: [variable({ name: 'n', value: '5' })] }) })
    })

    expect(w.text()).toContain('n')
    expect(w.text()).toContain('5')
    expect(w.text()).not.toContain('尚未初始化')
  })

  it('程序结束后说明变量为什么不可见，而不是渲染一张空表', () => {
    const w = mountPanel({
      session: session({ finished: true, stop: stop({ reason: 'exited', exit_code: 0 }) })
    })

    expect(w.text()).toContain('程序已结束，变量不再可见')
  })
})

describe('DebugPanel — 调用栈', () => {
  it('函数名用 display_function，不带 gdb 打印的参数值', () => {
    const w = mountPanel({
      session: session({ stop: stop({ stack: [frame({ function: 'sum (n=5)', display_function: 'sum' })] }) })
    })

    expect(w.text()).toContain('sum()')
    // `function` 是 `sum (n=5)`，面板自己再加一对括号就成了 `sum (n=5)()`。
    expect(w.text()).not.toContain('sum (n=5)()')
    expect(w.text()).not.toContain('n=5')
  })

  it('折叠标准库栈帧，否则学生自己的 main 会被挤出去', () => {
    const w = mountPanel({
      session: session({
        stop: stop({
          stack: [
            frame({ level: 0, display_function: 'sort', function: 'std::sort<int*> (first=0x0)', is_library: true }),
            frame({ level: 1, display_function: 'main', function: 'main ()', is_library: false })
          ]
        })
      })
    })

    const text = w.text()
    expect(text).toContain('main()')
    expect(text).not.toContain('sort()')
    // 折叠要说出来，否则学生以为调用栈就这么深。
    expect(text).toContain('1 层是标准库内部调用')
  })

  it('整条栈都是库代码时不做折叠——宁可显示库帧，也不要一张空列表', () => {
    const w = mountPanel({
      session: session({
        stop: stop({
          stack: [frame({ level: 0, display_function: '__memmove_avx', is_library: true })]
        })
      })
    })

    // 崩在标准库内部是完全正常的，此时空列表会让人以为栈读取失败。
    expect(w.text()).toContain('__memmove_avx()')
    expect(w.text()).not.toContain('已折叠')
  })
})

describe('DebugPanel — 停止原因的语气', () => {
  it('崩溃用错误语气，并且带上信号描述', () => {
    const w = mountPanel({
      session: session({
        finished: true,
        stop: stop({ reason: 'signal', signal: 'SIGSEGV', signal_description: 'Segmentation fault' })
      })
    })

    expect(w.text()).toContain('程序崩溃')
    expect(w.text()).toContain('Segmentation fault')
    expect(w.find('.debug-status').classes()).toContain('is-error')
  })

  it('正常结束用「完成」语气，不是错误语气', () => {
    const w = mountPanel({
      session: session({ finished: true, stop: stop({ reason: 'exited', exit_code: 0 }) })
    })

    // 达成目标是好事，用红色边框会把成功渲染成失败。
    expect(w.find('.debug-status').classes()).toContain('is-done')
    expect(w.find('.debug-status').classes()).not.toContain('is-error')
  })

  it('非零退出码被如实报出', () => {
    const w = mountPanel({
      session: session({ finished: true, stop: stop({ reason: 'exited', exit_code: 2 }) })
    })

    expect(w.text()).toContain('退出码 2')
  })
})

describe('DebugPanel — 消息', () => {
  it('断点保存的提示不能长得像报错', () => {
    const w = mountPanel({ session: session({ stop: stop() }), notice: '断点已保存，下次单步生效' })

    const msg = w.find('.debug-msg')
    expect(msg.text()).toContain('断点已保存')
    // 与错误共用一个样式，「已保存」会看起来像保存失败了。
    expect(msg.classes()).toContain('debug-msg-notice')
    expect(msg.classes()).not.toContain('debug-msg-error')
  })

  it('单步失败时保留面板——会话通常还能继续用', () => {
    const w = mountPanel({
      session: session({ stop: stop({ line: 12, variables: [variable()] }) }),
      errorMsg: '调试超时'
    })

    expect(w.text()).toContain('调试超时')
    // 位置和变量还在，丢掉它们等于把学生已经看到的信息抹掉。
    expect(w.text()).toContain('第 12 行')
    expect(w.text()).toContain('i')
  })

  it('执行中时明确说出来，避免看起来像卡住', () => {
    const w = mountPanel({ session: session({ stop: stop() }), busy: true })

    expect(w.text()).toContain('执行中')
    expect(buttonByText(w, '单步跳过')?.attributes('disabled')).toBeDefined()
  })
})

describe('DebugPanel — 事件', () => {
  it('按下单步按钮发出对应的动作', async () => {
    const w = mountPanel({ session: session({ stop: stop() }) })

    await buttonByText(w, '单步进入')?.trigger('click')

    expect(w.emitted('step')).toEqual([['step']])
  })

  it('程序第一次运行时发出 run，停在断点上之后发出 continue', async () => {
    const fresh = mountPanel({ session: session({ stop: null }) })
    await buttonByText(fresh, '运行')?.trigger('click')
    expect(fresh.emitted('step')).toEqual([['run']])

    const stopped = mountPanel({ session: session({ stop: stop() }) })
    await buttonByText(stopped, '继续')?.trigger('click')
    expect(stopped.emitted('step')).toEqual([['continue']])
  })

  it('结束调试发出 close', async () => {
    const w = mountPanel({ session: session({ stop: stop() }) })

    await buttonByText(w, '结束调试')?.trigger('click')

    expect(w.emitted('close')).toEqual([[]])
  })

  it('控制条上恰好有五个按钮', () => {
    const w = mountPanel({ session: session({ stop: stop() }) })

    expect(buttonLabels(w)).toEqual(['继续', '单步跳过', '单步进入', '跳出', '结束调试'])
  })
})
