// @vitest-environment jsdom
/**
 * 提示阶梯的组件测试。
 *
 * 与 `LayeredFeedback.spec.ts` 同源的担心：坏了看不出来。阶梯多放一级，页面不
 * 报错、不空白、控制台无警告——只是学生多拿到了本该自己想出来的东西。
 *
 * 除了「一次多给一级」这条通用契约，这里额外钉住三件**只有阶梯才有**的事：
 *   1. 它和 `LayeredFeedback` 不是同一个东西（rung 不会变成「怎么改」）；
 *   2. **按模型给的 `level` 排序与显示**，而不是数组下标——下标会把跳级
 *      假装成连续；
 *   3. 完全空的阶梯**整个不渲染**，而不是渲染一张只有标题的空卡片。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import HintLadder from './HintLadder.vue'
import type { HintLadder as Ladder } from '../api/mistake_book'

/**
 * `attachTo: document.body` 不是可选项。
 *
 * rung 用 `v-show` 控制，断言必须看**可见性**而不是元素存不存在——所有 rung
 * 一直在 DOM 里，只是 `display:none`。而 `isVisible()` 读 computed style，
 * jsdom 对游离节点算不出值，于是已释放的 rung 也会被判成不可见。
 */
type LadderProps = {
  ladder: Ladder
}

const mounted: VueWrapper[] = []

function mountLadder(props: LadderProps): VueWrapper {
  const w = mount(HintLadder, { props, attachTo: document.body })
  mounted.push(w)
  return w
}

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

const LADDER: Ladder = {
  levels: [
    { level: 1, text: '先看看循环是从几开始的。' },
    { level: 2, text: '数组下标从 0 开始，你的 i 从 1 开始。' },
    { level: 3, text: '把 for (int i = 1; i <= n; i++) 改成 i = 0; i < n。' }
  ],
  self_check: ['你能说出这个循环一共跑几次吗？', 'n 个数里最后一个的下标是几？']
}

/** 当前**可见**的那几级（按顺序）。 */
function visibleRungs(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('.ladder-rung')
    .filter((r) => r.isVisible())
    .map((r) => r.find('.ladder-level').text())
}

function actionButtons(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.ladder-actions button').map((b) => b.text().trim())
}

async function press(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('.btn-ladder').trigger('click')
}

describe('HintLadder', () => {
  it('初始一级都不显示，只给一个入口按钮', () => {
    const w = mountLadder({ ladder: LADDER })

    expect(visibleRungs(w)).toEqual([])
    expect(actionButtons(w)).toEqual(['需要提示'])
  })

  it('每点一次只多给一级', async () => {
    const w = mountLadder({ ladder: LADDER })

    await press(w)
    expect(visibleRungs(w)).toEqual(['第 1 级'])
    expect(actionButtons(w)).toEqual(['还是不会，再明确一点'])

    await press(w)
    expect(visibleRungs(w)).toEqual(['第 1 级', '第 2 级'])
    expect(actionButtons(w)).toEqual(['还是不会，再具体一点'])

    await press(w)
    expect(visibleRungs(w)).toEqual(['第 1 级', '第 2 级', '第 3 级'])
  })

  it('给到底也只是提示，不给「怎么改」——那是 LayeredFeedback 的事', async () => {
    const w = mountLadder({ ladder: LADDER })

    await press(w)
    await press(w)
    await press(w)

    const text = w.text()
    expect(text).toContain('提示已经给到这里了')
    expect(text).not.toContain('怎么改')
    // 最后一级是「把 for 改成……」这种具体改法，但仍然标着「第 3 级」，
    // 不会被抬成诊断里的最终答案。
    expect(text).toContain('第 3 级')
  })

  it('全部释放后可收起，回到一级都不看', async () => {
    const w = mountLadder({ ladder: LADDER })

    await press(w)
    await press(w)
    await press(w)
    expect(visibleRungs(w)).toHaveLength(3)

    await w.find('.btn-again').trigger('click')
    expect(visibleRungs(w)).toEqual([])
    expect(actionButtons(w)).toEqual(['需要提示'])
  })

  it('新的阶梯到来时自动收起，不会留着上一题的第三级', async () => {
    const w = mountLadder({ ladder: LADDER })

    await press(w)
    await press(w)
    await press(w)
    expect(visibleRungs(w)).toHaveLength(3)

    // 这条测试存在的理由和 LayeredFeedback 那条一样：不收起，学生第二次提交
    // 第一眼看到的就是上一题的近答案。
    await w.setProps({
      ladder: { levels: [{ level: 1, text: '这次看看输出格式。' }], self_check: [] }
    })
    expect(visibleRungs(w)).toEqual([])
    expect(actionButtons(w)).toEqual(['需要提示'])
  })

  it('按模型给的 level 排序与显示，不假装下标是连续的', async () => {
    // 故意乱序 + 跳级（1 和 4，没有 2/3）。
    const w = mountLadder({
      ladder: {
        levels: [
          { level: 4, text: '最具体的一级。' },
          { level: 1, text: '最模糊的一级。' }
        ],
        self_check: []
      }
    })

    await press(w)
    expect(visibleRungs(w)).toEqual(['第 1 级'])

    await press(w)
    // 显示的是模型声明的 4，不是排序后的下标 2。
    expect(visibleRungs(w)).toEqual(['第 1 级', '第 4 级'])
    expect(w.text()).not.toContain('第 2 级')
  })

  it('自检问题在放出一级后才出现，不是一开始就摊开', async () => {
    const w = mountLadder({ ladder: LADDER })

    expect(w.find('.ladder-check').exists()).toBe(false)

    await press(w)
    expect(w.find('.ladder-check').isVisible()).toBe(true)
    expect(w.findAll('.ladder-check-list li')).toHaveLength(2)
  })

  it('阶梯为空时整个不渲染，不留一张空卡片', () => {
    const w = mountLadder({ ladder: { levels: [], self_check: [] } })

    expect(w.find('.ladder').exists()).toBe(false)
    expect(w.findAll('.ladder-rung')).toHaveLength(0)
  })

  it('只有自检问题、没有 rung 时也渲染，且不给释放按钮', () => {
    const w = mountLadder({
      ladder: { levels: [], self_check: ['这道题的边界是什么？'] }
    })

    expect(w.find('.ladder').exists()).toBe(true)
    expect(w.findAll('.ladder-rung')).toHaveLength(0)
    // 没有 rung 可放，就不该有一个点了没反应的按钮。
    expect(w.find('.btn-ladder').exists()).toBe(false)
  })
})
