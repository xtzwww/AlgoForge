// @vitest-environment jsdom
/**
 * 分层释放反馈的组件测试。
 *
 * 这是全项目**最该有组件测试**的一块，理由不是它复杂，而是它坏了别人看不出来：
 * `LayeredFeedback` 管的是「一次给几层」，而它一旦多给一层，页面上不会报错、
 * 不会空白、控制台也不会有警告——只是学生多看到了本该自己想出来的答案。
 * 这种失败只有真的渲染一遍才抓得到，api 层的单测碰不到它。
 *
 * 覆盖的是那份设计意图里写死的三条：
 *   1. 初始一层都不显示（gate 关着）；
 *   2. 空段被跳过，不会出现「打开一扇门，里面是空的」；
 *   3. **新诊断自动收起** —— 否则第二次提交会直接把上一题的解答摊在学生面前，
 *      而 gate 存在的意义正是阻止这件事。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import LayeredFeedback from './LayeredFeedback.vue'
import { FEEDBACK_REVEAL_LABELS } from '../api/mistake_book'

/**
 * `attachTo: document.body` 不是可选项。
 *
 * 组件用 `v-show` 控制分层的显示，所以断言必须看**可见性**而不是元素存不存在
 * ——三段一直都在 DOM 里，只是 `display:none`。而 `isVisible()` 读的是 computed
 * style，jsdom 对**游离节点**（没挂进 document 的）算不出值，于是已释放的段落
 * 也会被判成不可见：不挂上去，测试会「状态明明对了却失败」，且看不出错在哪。
 */
/** 与组件 props 一一对应；写全而不是用 `Record<string, unknown>`，否则
 *  `tsconfig.web.json` 的类型检查（它顺带覆盖 spec）会直接报错。 */
type FeedbackProps = {
  errorDetail: string
  hint: string
  suggestedFix: string
  errorTypeDescription?: string
  errorType?: string | null
}

const mounted: VueWrapper[] = []

function mountFeedback(props: FeedbackProps): VueWrapper {
  const w = mount(LayeredFeedback, { props, attachTo: document.body })
  mounted.push(w)
  return w
}

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

const DIAGNOSIS = {
  errorDetail: '把输入当成了一行数，实际上第二行才是 n。',
  hint: '先读 n，再读 n 个数；不要一次性读完。',
  suggestedFix: '先 cin >> n，然后循环 n 次读。'
}

/** 当前**可见**的那几段（按顺序）。 */
function visibleSections(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('.feedback-section')
    .filter((s) => s.isVisible())
    .map((s) => s.find('.feedback-title').text())
}

/** 释放按钮，或已全部释放时的收起按钮。 */
function actionButtons(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.feedback-actions button').map((b) => b.text().trim())
}

async function press(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('.btn-reveal').trigger('click')
}

describe('LayeredFeedback', () => {
  it('初始一层都不显示，只给一个入口按钮', () => {
    const w = mountFeedback(DIAGNOSIS)

    expect(visibleSections(w)).toEqual([])
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.error_detail])
  })

  it('每点一次只多给一层', async () => {
    const w = mountFeedback(DIAGNOSIS)

    await press(w)
    expect(visibleSections(w)).toEqual(['错在哪'])
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.hint])

    await press(w)
    expect(visibleSections(w)).toEqual(['错在哪', '提示'])
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.suggested_fix])

    await press(w)
    expect(visibleSections(w)).toEqual(['错在哪', '提示', '怎么改'])
  })

  it('全部释放后给收起入口，可以回到只看错因', async () => {
    const w = mountFeedback(DIAGNOSIS)

    await press(w)
    await press(w)
    await press(w)
    expect(w.text()).toContain('以上是这道题的全部反馈')

    await w.find('.btn-again').trigger('click')
    expect(visibleSections(w)).toEqual(['错在哪'])
    // 收起后释放按钮要重新出现，否则就卡在「只看错因」出不来了。
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.hint])
  })

  it('模型没给提示时跳过空的那一层，两次就到底', async () => {
    const w = mountFeedback({ ...DIAGNOSIS, hint: '' })

    // 「提示」这一段根本不渲染，而不是渲染一个空段落。
    expect(w.findAll('.feedback-section')).toHaveLength(2)

    await press(w)
    expect(visibleSections(w)).toEqual(['错在哪'])
    // 按钮直接指向「怎么改」，不会先给一次空的提示。
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.suggested_fix])

    await press(w)
    expect(w.text()).toContain('以上是这道题的全部反馈')
  })

  it('新诊断到来时自动收起，不会留着上一题的解答', async () => {
    const w = mountFeedback(DIAGNOSIS)

    await press(w)
    await press(w)
    await press(w)
    expect(visibleSections(w)).toHaveLength(3)

    // 这条测试存在的理由：不收起的话，学生第二次提交第一眼看到的就是上一题
    // 的「怎么改」。gate 就是为了拦这个。
    await w.setProps({ errorDetail: '这次是下标从 1 开始，数组却从 0 开始。' })
    expect(visibleSections(w)).toEqual([])
    expect(actionButtons(w)).toEqual([FEEDBACK_REVEAL_LABELS.error_detail])
  })

  it('只有一段时不给「重新自己想」——没有可收起的东西', async () => {
    const w = mountFeedback({ errorDetail: '输出格式不对。', hint: '', suggestedFix: '' })

    await press(w)
    expect(w.text()).toContain('以上是这道题的全部反馈')
    expect(w.find('.btn-again').exists()).toBe(false)
  })

  it('错因的中文描述与原始标签都呈现，且中文在前', () => {
    const w = mountFeedback({
      ...DIAGNOSIS,
      errorTypeDescription: '理解错题意',
      errorType: 'misread_problem'
    })

    const head = w.find('.analysis-head').text()
    expect(head.indexOf('理解错题意')).toBeLessThan(head.indexOf('misread_problem'))
    // 原始标签降级为 tooltip，不是正文。
    expect(w.find('.analysis-type').attributes('title')).toBe('错因分类：misread_problem')
  })
})
