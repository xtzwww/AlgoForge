// @vitest-environment jsdom
/**
 * `/problems/:id` 路由适配器（PU-7）的视图测试。
 *
 * 这个视图本身几乎不做事：它把 `problemId` 透传给 `Practice`，并在
 * `Practice` 发出 `exit` 时把路由 `replace` 到 free-run（`/practice`）。
 * 所以它值得测的只有这两件事，且用得着把 `Practice` 桩掉——
 * `Practice` 自己会拉 Monaco、发请求，那些是它的测试该覆盖的，不是这层
 * 适配器的。把桩挂上之后，这里只钉"接线对不对"，不重复测组件内部。
 *
 * 两条失败模式值得钉：
 * * **prop 没透传** —— `Practice` 拿不到 `problemId`，路由参数就白传了。
 * * **exit 走成 push** —— 原代码用 `replace` 而非 `push` 是刻意的：退回
 *   题目不该在 history 里留一条 `/problems/12`，否则后退按钮会重新进题。
 *   测 `replace` 被调用、且参数正是 `practice` 路由名，就把这个意图锁住。
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ProblemPracticeView from './ProblemPracticeView.vue'

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace })
}))

// 桩掉真正的 Practice，只留"接收 problemId、能 emit exit"的壳。
vi.mock('./Practice.vue', () => ({
  default: {
    name: 'Practice',
    props: ['problemId'],
    emits: ['exit'],
    template: '<div class="stub-practice" />'
  }
}))

describe('ProblemPracticeView', () => {
  it('把路由的 problemId 透传给 Practice', () => {
    replace.mockReset()
    const wrapper = mount(ProblemPracticeView, { props: { problemId: 12 } })

    const practice = wrapper.findComponent({ name: 'Practice' })
    expect(practice.exists()).toBe(true)
    expect(practice.props('problemId')).toBe(12)
  })

  it('Practice 发出 exit 时，路由 replace 到 free-run（不再 push 进 history）', async () => {
    replace.mockReset()
    const wrapper = mount(ProblemPracticeView, { props: { problemId: 12 } })

    await wrapper.findComponent({ name: 'Practice' }).vm.$emit('exit')

    expect(replace).toHaveBeenCalledTimes(1)
    expect(replace).toHaveBeenCalledWith({ name: 'practice' })
  })
})
