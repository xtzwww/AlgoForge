// @vitest-environment jsdom
/**
 * EmptyState 的组件测试。
 *
 * 这个组件存在的理由是 PU-9 那句「三个页面没数据时不能渲染成空白，而且
 * **不能只渲染一句『暂无数据』**」——原来每个页面各写一句灰字，说了「没有」
 * 就完了，不告诉用户怎么出去。所以它坏掉的样子不是报错，而是**出口消失**：
 * 页面照样有字，只是走不掉。这一条肉眼最容易漏，因为它看起来「有内容」。
 *
 * 钉住的五条：
 *   1. 标题必渲染，解释只在给了的时候渲染；
 *   2. **没给 actionLabel 就不该有按钮**——有按钮但点了没用比没按钮更糟；
 *   3. 点了按钮 emit 的是 `navigate`，由**页面**决定去哪（组件不持有路由）；
 *   4. icon 只在给了的时候画，且是 `aria-hidden`（旁边的标题已经承载了语义，
 *      图标再报一遍会被读屏念成重复内容）；
 *   5. variant 落在根元素的 class 上——`first-run` 是邀请、`search` 是
 *      「你筛空了」，两种语气靠这个 class 区分，丢了就退回同一种语气。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import EmptyState from './EmptyState.vue'

describe('EmptyState', () => {
  it('标题一定渲染，解释只在给了的时候渲染', () => {
    const bare = mount(EmptyState, { props: { title: '错题本为空' } })
    expect(bare.find('.empty-title').text()).toBe('错题本为空')
    expect(bare.find('.empty-explanation').exists()).toBe(false)

    const explained = mount(EmptyState, {
      props: { title: '错题本为空', explanation: '只有判错且已分析的提交才会记进来' }
    })
    expect(explained.find('.empty-explanation').text()).toContain('只有判错且已分析的提交')
  })

  it('没给 actionLabel 就不渲染按钮——点了没用的按钮比没有按钮更糟', () => {
    const w = mount(EmptyState, { props: { title: '暂无数据' } })
    expect(w.find('button.empty-action').exists()).toBe(false)
  })

  it('给了 actionLabel 就渲染按钮，点击 emit navigate', async () => {
    const w = mount(EmptyState, { props: { title: '暂无数据', actionLabel: '去练习题' } })
    const btn = w.find('button.empty-action')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('去练习题')

    await btn.trigger('click')
    expect(w.emitted('navigate')).toHaveLength(1)
  })

  it('icon 只在给了的时候画，且对读屏隐藏', () => {
    const without = mount(EmptyState, { props: { title: '暂无数据' } })
    expect(without.find('svg.empty-icon').exists()).toBe(false)

    const withIcon = mount(EmptyState, { props: { title: '暂无数据', icon: 'chart' } })
    const svg = withIcon.find('svg.empty-icon')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('aria-hidden')).toBe('true')
    // 路径来自 ICONS 表；空 d 会让图标变成一块看不见的空白。
    expect(svg.find('path').attributes('d')).toBeTruthy()
  })

  it('variant 落在根元素 class 上，默认 first-run', () => {
    const firstRun = mount(EmptyState, { props: { title: '暂无数据' } })
    expect(firstRun.find('.empty-state').classes()).toContain('empty-first-run')

    const search = mount(EmptyState, { props: { title: '没有匹配', variant: 'search' } })
    expect(search.find('.empty-state').classes()).toContain('empty-search')
    expect(search.find('.empty-state').classes()).not.toContain('empty-first-run')
  })
})
