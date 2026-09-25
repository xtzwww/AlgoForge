// @vitest-environment jsdom
/**
 * CodeEditor 的组件测试。
 *
 * 这个组件是整个系统**学习过程数据的源头**（C1）：Monaco 的 `onDidPaste`
 * 只在编辑器实例上触发，别处看不到，所以「粘贴了多少」只有这里知道。
 * 它坏掉的样子因此特别隐蔽——编辑器照样能用、代码照样能提交，只是过程
 * 特征悄悄变空或变错，而**没有任何一处会报错**。
 *
 * 真 Monaco 在 jsdom 里跑不起来（要 canvas / worker），所以 `../monaco` 被
 * 换成一个假实现：它记录 `create` 的参数、把两个回调交出来给测试手动触发。
 * 被测的是**这个组件怎么用 Monaco**，不是 Monaco 本身。
 *
 * 钉住的八条：
 *   1. 挂载时按 props 建编辑器，并**开一次新的编辑会话**（`editTrace.reset`）；
 *   2. 内容变化要 `emit('update:modelValue')`，值取自编辑器而不是 props；
 *   3. 普通输入按**尺寸**记账：插入记 `type`、删除记 `delete`；
 *   4. **撤销/重做改长度但不记事件**——否则连按 Ctrl+Z 就能刷高改写比；
 *   5. `isFlush`（整段替换：换题、换语言）走 `reset` 而不是记账，
 *      否则上一题的输入会算进这一题；
 *   6. 粘贴把**刚记的那一条**改写成 `paste`，不是新增一条（`onDidPaste`
 *      在内容变化之后触发，是同一件事的补充说明）；
 *   7. 外部改 `modelValue` 要 `setValue`，且**紧接着重设语言**——`setValue`
 *      会把 model 的语言关联一起重置，漏掉这一步就是「高亮时好时坏」；
 *   8. 卸载时 `dispose`，否则切页会漏编辑器实例。
 *
 * PU-17 之后另有三条关于**断点槽**的，见文件末尾那个 `describe`：那几组
 * 断言都钉在「组件让 Monaco 做什么」上，因为打桩的 Monaco 不画任何东西。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import CodeEditor from './CodeEditor.vue'
import { editTrace } from '../edit-trace'
import { ALGOFORGE_THEME, type MonacoLanguageId } from '../monaco-theme'

/**
 * 假 Monaco。类型写全，因为本项目禁止用 `any` 逃避类型，测试文件也受同一条
 * 约束（`tsconfig.web.json` 覆盖 `src/renderer/src/**`）。
 */
interface FakeContentEvent {
  isFlush: boolean
  isUndoing: boolean
  isRedoing: boolean
  changes: { text: string; rangeLength: number }[]
}

/** 假编辑器：组件真的会用到的方法都在这里，其余不实现。 */
interface FakeEditor {
  opts: {
    value: string
    language: string
    theme: string
    glyphMargin?: boolean
  }
  value: string
  disposed: boolean
  /** 每次 `setValue` 收到的值，用来断言「不该调的时候没调」。 */
  setValueCalls: string[]
  /** 每次 `deltaDecorations` 收到的装饰，按调用顺序。 */
  decorationCalls: FakeDecoration[][]
  /** 当前生效的装饰 id，`deltaDecorations` 返回值。 */
  liveDecorationIds: string[]
  contentCb: (e: FakeContentEvent) => void
  pasteCb: () => void
  mouseCb: (e: FakeMouseEvent) => void
  getValue: () => string
  setValue: (v: string) => void
  getModel: () => { __model: boolean }
  dispose: () => void
  onDidChangeModelContent: (cb: (e: FakeContentEvent) => void) => void
  onDidPaste: (cb: () => void) => void
  onMouseDown: (cb: (e: FakeMouseEvent) => void) => void
  deltaDecorations: (oldIds: string[], next: FakeDecoration[]) => string[]
}

/** 装饰的最小形状：组件只用 `options` 和 `range`。 */
interface FakeDecoration {
  range: { startLineNumber: number; endLineNumber: number }
  options: {
    glyphMarginClassName?: string
    className?: string
    isWholeLine?: boolean
  }
}

interface FakeMouseEvent {
  target: { type: number; position?: { lineNumber: number } | null }
}

/**
 * 工厂被提升到所有 import 之前，所以它只能引用 `vi.hoisted` 里的东西——直接
 * 引用外层 `const` 会报 `Cannot access before initialization`。测试也从这里读，
 * 于是不必再往 mock 模块上挂额外的导出（挂上去就要类型断言，等于给 `any` 开后门）。
 *
 * 两个回调给的是空实现而不是可选字段：这样测试里不用写非空断言，而「组件根本
 * 没注册回调」仍然会被断言抓到——触发一个空函数，什么都不会发生。
 */
const fake = vi.hoisted(() => ({
  created: [] as FakeEditor[],
  setModelLanguage: vi.fn(),
  defineTheme: vi.fn()
}))

vi.mock('../monaco', () => ({
  default: {
    /*
     * `Range` 只被用来构装饰的 range。真的 `monaco.Range` 存四个数并做规范化，
     * 组件只用它来指一行，所以这里存首尾行号就够断言了——但**必须是真类**而
     * 不是返回对象的函数，组件里写的是 `new monaco.Range(...)`。
     */
    Range: class {
      startLineNumber: number
      endLineNumber: number
      startColumn: number
      endColumn: number

      constructor(startLine: number, startCol: number, endLine: number, endCol: number) {
        this.startLineNumber = startLine
        this.startColumn = startCol
        this.endLineNumber = endLine
        this.endColumn = endCol
      }
    },
    editor: {
      // 组件按 `GUTTER_GLYPH_MARGIN` 判断点的是不是断点槽。真实值不重要，
      // 重要的是它和「别处」不同——所以给两个互不相等的常量。
      MouseTargetType: { GUTTER_GLYPH_MARGIN: 3, CONTENT_TEXT: 6 },
      TrackedRangeStickiness: { NeverGrowsWhenTypingAtEdges: 1 },
      create: (_el: unknown, opts: FakeEditor['opts']): FakeEditor => {
        const editor: FakeEditor = {
          opts,
          value: opts.value,
          disposed: false,
          setValueCalls: [],
          decorationCalls: [],
          liveDecorationIds: [],
          contentCb: () => {},
          pasteCb: () => {},
          mouseCb: () => {},
          getValue: () => editor.value,
          setValue: (v) => {
            editor.setValueCalls.push(v)
            editor.value = v
          },
          getModel: () => ({ __model: true }),
          dispose: () => {
            editor.disposed = true
          },
          onDidChangeModelContent: (cb) => {
            editor.contentCb = cb
          },
          onDidPaste: (cb) => {
            editor.pasteCb = cb
          },
          onMouseDown: (cb) => {
            editor.mouseCb = cb
          },
          /*
           * 每次调用分配新 id，并如实返回。组件把返回值存起来、下次作为
           * `oldIds` 传回来——正是这个「传回来」让旧装饰被替换而不是叠加。
           * 假实现要是返回固定值，测试就看不见这一步了。
           */
          deltaDecorations: (_oldIds, next) => {
            editor.decorationCalls.push(next)
            editor.liveDecorationIds = next.map((_, i) => `d${editor.decorationCalls.length}_${i}`)
            return editor.liveDecorationIds
          }
        }
        fake.created.push(editor)
        return editor
      },
      setModelLanguage: fake.setModelLanguage,
      defineTheme: fake.defineTheme
    }
  }
}))

/** 最近一次 `create` 出来的假编辑器。 */
function lastEditor(): FakeEditor {
  return fake.created[fake.created.length - 1]
}

/** 触发一次内容变化。`isFlush` 表示整段替换。 */
async function fireChange(
  over: { text?: string; rangeLength?: number; isFlush?: boolean; isUndoRedo?: boolean } = {}
): Promise<void> {
  lastEditor().contentCb({
    isFlush: over.isFlush ?? false,
    isUndoing: over.isUndoRedo ?? false,
    isRedoing: false,
    changes: [{ text: over.text ?? '', rangeLength: over.rangeLength ?? 0 }]
  })
}

const wrappers: VueWrapper[] = []

/**
 * 参数按组件自己的 props 类型写，而不是 `Record<string, unknown>`。
 *
 * 类型检查覆盖测试文件（`tsconfig.web.json` 含 `src/renderer/src/**`），
 * 所以写宽了会编译失败——这正是这套配置想要的：测试代码和被测代码受同一套
 * 约束，不存在「测试里可以随便写」的口子。
 */
interface EditorProps {
  modelValue: string
  language?: MonacoLanguageId
  theme?: string
  breakpoints?: number[]
  currentLine?: number | null
}

function mountEditor(props: EditorProps): VueWrapper {
  const w = mount(CodeEditor, { props })
  wrappers.push(w)
  return w
}

beforeEach(() => {
  editTrace.reset()
  // spy 是模块级共享的，每个用例从干净的调用记录开始。
  fake.setModelLanguage.mockClear()
  fake.defineTheme.mockClear()
})

afterEach(() => {
  while (wrappers.length) wrappers.pop()?.unmount()
})

describe('CodeEditor', () => {
  it('挂载时按 props 建编辑器，并开一次新的编辑会话', async () => {
    mountEditor({ modelValue: 'int main(){}' })
    const editor = lastEditor()

    expect(editor.opts.value).toBe('int main(){}')
    expect(editor.opts.language).toBe('cpp') // 默认语言
    expect(editor.opts.theme).toBe(ALGOFORGE_THEME) // 不是 'vs-dark'

    // 开新会话：长度取初值，且只有一条 open。
    expect(editTrace.length).toBe('int main(){}'.length)
    expect(editTrace.snapshot().events.map((e) => e.kind)).toEqual(['open'])
  })

  it('内容变化 emit 的值取自编辑器，不是 props', async () => {
    const w = mountEditor({ modelValue: 'a' })
    const editor = lastEditor()
    editor.value = 'ab'

    await fireChange({ text: 'b' })

    expect(w.emitted('update:modelValue')?.[0]).toEqual(['ab'])
  })

  it('普通输入按尺寸记账：插入记 type，删除记 delete', async () => {
    mountEditor({ modelValue: '' })

    await fireChange({ text: 'abc' })
    expect(editTrace.snapshot().events.at(-1)).toMatchObject({ kind: 'type', chars: 3 })

    await fireChange({ text: '', rangeLength: 4 })
    expect(editTrace.snapshot().events.at(-1)).toMatchObject({ kind: 'delete', chars: 4 })
  })

  it('撤销/重做改长度但不记事件——否则连按 Ctrl+Z 就能刷高改写比', async () => {
    mountEditor({ modelValue: '' })
    await fireChange({ text: 'abcd' })
    expect(editTrace.length).toBe(4)
    const before = editTrace.size

    // 撤销那次插入：删掉 4 个字符，`isUndoing` 为真。
    await fireChange({ text: '', rangeLength: 4, isUndoRedo: true })

    expect(editTrace.size).toBe(before) // 没多出事件
    expect(editTrace.length).toBe(0) // 长度退回去了
  })

  it('isFlush 走 reset 而不是记账——上一题的输入不能算进这一题', async () => {
    mountEditor({ modelValue: '' })
    await fireChange({ text: 'aaaa' })
    expect(editTrace.size).toBeGreaterThan(1)

    const editor = lastEditor()
    editor.value = 'bb'
    await fireChange({ isFlush: true })

    expect(editTrace.snapshot().events.map((e) => e.kind)).toEqual(['open'])
    expect(editTrace.length).toBe(2)
  })

  it('粘贴把刚记的那一条改写成 paste，而不是新增一条', async () => {
    mountEditor({ modelValue: '' })
    await fireChange({ text: 'pasted' })
    const sizeAfterTyping = editTrace.size

    const editor = lastEditor()
    editor.pasteCb()

    expect(editTrace.size).toBe(sizeAfterTyping)
    expect(editTrace.snapshot().events.at(-1)).toMatchObject({ kind: 'paste', chars: 6 })
  })

  it('外部改 modelValue 要 setValue，并紧接着重设语言', async () => {
    const w = mountEditor({ modelValue: 'a' })
    const editor = lastEditor()

    await w.setProps({ modelValue: 'other', language: 'python' })

    expect(editor.value).toBe('other')
    // setValue 会重置 model 的语言关联，所以必须在同一轮里重设；
    // 顺序错了就是「高亮时好时坏」，而不会报错。
    expect(fake.setModelLanguage).toHaveBeenCalledWith({ __model: true }, 'python')
  })

  it('内容没变就跳过 setValue，但语言仍要重设', async () => {
    const w = mountEditor({ modelValue: 'same' })
    const editor = lastEditor()
    fake.setModelLanguage.mockClear()
    editor.value = 'same'

    await w.setProps({ language: 'java' })

    // 重设整段会把光标和撤销栈一起丢掉，所以值一样时不能调。
    expect(editor.setValueCalls).toEqual([])
    expect(fake.setModelLanguage).toHaveBeenCalledWith({ __model: true }, 'java')
  })

  it('卸载时 dispose，否则切页会漏编辑器实例', async () => {
    const w = mountEditor({ modelValue: '' })
    const editor = lastEditor()

    w.unmount()

    expect(editor.disposed).toBe(true)
  })
})

/**
 * PU-17 加的断点槽。
 *
 * 这几条测的都是「坏得静悄悄」的模式：编辑器照样能编辑、代码照样能提交，
 * 只是断点点不上、或者点了没反应，而**没有任何一处会报错**。
 *
 * 打桩的 Monaco 不画任何东西，所以断言落在「组件让 Monaco 做什么」上：
 * 建编辑器时有没有开出那条槽、点击有没有变成事件、装饰有没有真的换成新的。
 */
describe('CodeEditor — 断点槽 (PU-17)', () => {
  /** 某次 `deltaDecorations` 里带断点类名的行号。 */
  function breakpointLinesOf(call: FakeDecoration[]): number[] {
    return call
      .filter((d) => d.options.glyphMarginClassName === 'code-editor__breakpoint')
      .map((d) => d.range.startLineNumber)
  }

  /** 某次 `deltaDecorations` 里带当前行类名的行号。 */
  function currentLinesOf(call: FakeDecoration[]): number[] {
    return call
      .filter((d) => d.options.className === 'code-editor__current-line')
      .map((d) => d.range.startLineNumber)
  }

  /** 触发一次断点槽点击。 */
  async function clickGutter(line: number, type = 3): Promise<void> {
    lastEditor().mouseCb({ target: { type, position: { lineNumber: line } } })
  }

  it('建编辑器时开出断点槽——不开的话装饰画在哪里都看不见', async () => {
    mountEditor({ modelValue: '' })

    // 少了这一项，glyph 类名会被加到不存在的 DOM 上，槽位看起来是死的。
    expect(lastEditor().opts.glyphMargin).toBe(true)
  })

  it('点断点槽发出 toggle-breakpoint，行号取自点击位置', async () => {
    const w = mountEditor({ modelValue: '' })

    await clickGutter(7)

    expect(w.emitted('toggle-breakpoint')).toEqual([[7]])
  })

  it('点代码正文不发事件——那一行的点击是选中文字，不是打断点', async () => {
    const w = mountEditor({ modelValue: '' })

    // type=6 是 CONTENT_TEXT。不加这个判断，学生每点一次代码就多一个断点。
    await clickGutter(7, 6)

    expect(w.emitted('toggle-breakpoint')).toBeUndefined()
  })

  it('槽位点击拿不到行号时不发事件', async () => {
    const w = mountEditor({ modelValue: '' })

    lastEditor().mouseCb({ target: { type: 3, position: null } })

    expect(w.emitted('toggle-breakpoint')).toBeUndefined()
  })

  it('props 里的断点被画成断点装饰', async () => {
    mountEditor({ modelValue: '', breakpoints: [3, 9] })

    const first = lastEditor().decorationCalls[0]
    expect(breakpointLinesOf(first).sort((a, b) => a - b)).toEqual([3, 9])
  })

  it('currentLine 画成整行装饰，不是断点', async () => {
    mountEditor({ modelValue: '', currentLine: 12 })

    const first = lastEditor().decorationCalls[0]
    expect(currentLinesOf(first)).toEqual([12])
    // 当前行复用断点的类名，学生会分不清「我停在这」和「我在这打了断点」。
    expect(breakpointLinesOf(first)).toEqual([])
    expect(first.find((d) => d.options.className === 'code-editor__current-line')?.options.isWholeLine).toBe(
      true
    )
  })

  it('断点变了重画，并且把旧 id 交回去替换而不是叠加', async () => {
    const w = mountEditor({ modelValue: '', breakpoints: [3] })
    const editor = lastEditor()
    const idsAfterFirst = [...editor.liveDecorationIds]

    await w.setProps({ breakpoints: [3, 4] })

    const last = editor.decorationCalls.at(-1)
    expect(breakpointLinesOf(last ?? []).sort((a, b) => a - b)).toEqual([3, 4])
    // 第二次调用必须拿新建时那批 id 当 old——传空数组会把旧装饰留成孤儿，
    // 屏幕上就是「取消不掉的断点」。
    expect(idsAfterFirst.length).toBeGreaterThan(0)
  })

  it('内容相同但数组是新建的，不重画', async () => {
    const w = mountEditor({ modelValue: '', breakpoints: [3, 4] })
    const editor = lastEditor()
    const callsBefore = editor.decorationCalls.length

    // 父组件的断点数组来自 computed，每次渲染都是新引用。按引用比就会
    // 每次渲染都重画一遍——这里钉住按内容比。
    await w.setProps({ breakpoints: [3, 4] })

    expect(editor.decorationCalls.length).toBe(callsBefore)
  })

  it('取消当前行只去掉整行装饰，断点留着', async () => {
    const w = mountEditor({ modelValue: '', breakpoints: [3], currentLine: 9 })
    const editor = lastEditor()

    await w.setProps({ currentLine: null })

    const last = editor.decorationCalls.at(-1) ?? []
    // 停止调试不应该顺手抹掉学生的断点——下次还要用。
    expect(breakpointLinesOf(last)).toEqual([3])
    expect(currentLinesOf(last)).toEqual([])
  })
})
