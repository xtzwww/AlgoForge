// @vitest-environment jsdom
/**
 * 「需要关注」预警列表的组件测试。
 *
 * 这个列表坏了的样子是**该出现的没出现**：不是报错、不是空白，而是教师今天
 * 没看见那个连续 5 次没过的人。它的父视图 `TeacherView.vue` 有 1600 行，
 * 在那里断言等于要先把整个教师端的数据流搭起来——所以才把它抽成不持有状态、
 * 不发请求的组件：给它一个数组，看它画什么。
 *
 * 钉住的四条：
 *   1. 一条都没有时**整段不渲染**——渲染出来就是「你该关注 0 个人」；
 *   2. 每条都要带上触发依据，理由那行才是这个组件存在的意义；
 *   3. 点名字 emit 的是**那条 alert 本身**（不是索引、不是 user_id）；
 *   4. 依据缺失时**名字不能跟着消失**——降级要降在依据上，不是整行上。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AttentionAlerts from './AttentionAlerts.vue'
import type { AlertOut } from '../api/teacher'

const mounted: VueWrapper[] = []

/** `attachTo` 不可省：见 `LayeredFeedback.spec.ts` 里的说明（游离节点算不出可见性）。 */
function mountAlerts(alerts: AlertOut[]): VueWrapper {
  const w = mount(AttentionAlerts, { props: { alerts }, attachTo: document.body })
  mounted.push(w)
  return w
}

function alertOf(over: Partial<AlertOut> = {}): AlertOut {
  return {
    user_id: 7,
    display_name: '张三',
    kind: 'repeating',
    reason: '同一错因在 3 道题里反复出现',
    ...over
  }
}

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
})

describe('AttentionAlerts', () => {
  it('一条都没有时不渲染——「你该关注 0 个人」是噪音', () => {
    const w = mountAlerts([])
    expect(w.find('ul.alert-list').exists()).toBe(false)
    expect(w.text()).toBe('')
  })

  it('每条都带上触发它的依据', () => {
    const w = mountAlerts([alertOf()])
    expect(w.find('.alert-who').text()).toBe('张三')
    expect(w.find('.alert-reason').text()).toContain('同一错因在 3 道题里反复出现')
  })

  it('多条按传入顺序都渲染出来', () => {
    const w = mountAlerts([
      alertOf({ user_id: 1, display_name: '李四' }),
      alertOf({ user_id: 2, display_name: '王五', reason: '超过 7 天没有提交' })
    ])
    const names = w.findAll('.alert-who').map((n) => n.text())
    expect(names).toEqual(['李四', '王五'])
    const reasons = w.findAll('.alert-reason').map((n) => n.text())
    expect(reasons[1]).toContain('超过 7 天没有提交')
  })

  it('点名字 emit 的是那条 alert 本身，不是索引', async () => {
    const target = alertOf({ user_id: 42, display_name: '赵六' })
    const w = mountAlerts([alertOf({ user_id: 1 }), target])
    await w.findAll('.alert-who')[1].trigger('click')
    const emitted = w.emitted('select')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0]?.[0]).toEqual(target)
  })

  it('依据为空时名字仍在——降级降在依据上，不是整行上', () => {
    const w = mountAlerts([alertOf({ reason: '' })])
    expect(w.find('.alert-who').exists()).toBe(true)
    expect(w.find('.alert-who').text()).toBe('张三')
  })
})
