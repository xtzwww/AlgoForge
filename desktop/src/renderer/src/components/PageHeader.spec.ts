// @vitest-environment jsdom
/**
 * PageHeader 的组件测试。
 *
 * 它是 PU-9 从三个页面里收拢出来的同一个页头（错题本 / 学情画像 / 复习计划）。
 * 收拢前那三份已经在细节上漂了——复习计划的页头下边距 12px、另外两个 16px，
 * 而且只有画像页把摘要行放在页头**外面**，于是三个页面的正文起点差三个高度。
 * 所以这个组件坏掉的样子是**三个页面又开始各长各的**，而不是某一处报错。
 *
 * 钉住的六条：
 *   1. `loading` 省略时**不渲染按钮**，不是渲染一个 disabled 的——静态页
 *      （没有可刷新的东西）不该出现一个永远点不动的控件；
 *   2. `loading` 的真假同时决定文案（刷新 / 加载中…）与 disabled；
 *   3. `refreshLabel` 一旦给了就压过默认文案（**包括加载中**）——页面有更
 *      具体的说法时不能被默认值盖掉；
 *   4. 点击 emit `refresh`，组件不自己发请求；
 *   5. 错误横幅带前缀，默认「请求失败」、可由页面换成更具体的主语；
 *   6. `count` 插槽落在 `<h1>` **里面**——它是标题的一部分，必须在空状态下
 *      也活着，所以不能挂到正文里去。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from './PageHeader.vue'

describe('PageHeader', () => {
  it('标题必渲染，副标题只在给了的时候渲染', () => {
    const bare = mount(PageHeader, { props: { title: '错题本' } })
    expect(bare.find('h1').text()).toBe('错题本')
    expect(bare.find('.page-subtitle').exists()).toBe(false)

    const withSub = mount(PageHeader, { props: { title: '错题本', subtitle: '共 12 条' } })
    expect(withSub.find('.page-subtitle').text()).toBe('共 12 条')
  })

  it('loading 省略时没有按钮——静态页不该出现一个永远点不动的控件', () => {
    const w = mount(PageHeader, { props: { title: '题库' } })
    expect(w.find('button.refresh-btn').exists()).toBe(false)
  })

  it('loading 的真假同时决定文案与 disabled', () => {
    const idle = mount(PageHeader, { props: { title: '题库', loading: false } })
    const idleBtn = idle.find('button.refresh-btn')
    expect(idleBtn.text()).toBe('刷新')
    expect(idleBtn.attributes('disabled')).toBeUndefined()

    const busy = mount(PageHeader, { props: { title: '题库', loading: true } })
    const busyBtn = busy.find('button.refresh-btn')
    expect(busyBtn.text()).toBe('加载中…')
    expect(busyBtn.attributes('disabled')).toBeDefined()
  })

  it('refreshLabel 压过默认文案，加载中也一样', () => {
    const w = mount(PageHeader, {
      props: { title: '题库', loading: true, refreshLabel: '重新载入' }
    })
    expect(w.find('button.refresh-btn').text()).toBe('重新载入')
  })

  it('点击 emit refresh，组件自己不发请求', async () => {
    const w = mount(PageHeader, { props: { title: '题库', loading: false } })
    await w.find('button.refresh-btn').trigger('click')
    expect(w.emitted('refresh')).toHaveLength(1)
  })

  it('错误横幅用默认前缀，可由页面换掉；没错误就不渲染', () => {
    const none = mount(PageHeader, { props: { title: '题库' } })
    expect(none.find('.page-error').exists()).toBe(false)

    const def = mount(PageHeader, { props: { title: '题库', error: '连接超时' } })
    expect(def.find('.page-error').text()).toBe('请求失败：连接超时')

    const custom = mount(PageHeader, {
      props: { title: '题库', error: '连接超时', errorPrefix: '无法加载题库' }
    })
    expect(custom.find('.page-error').text()).toBe('无法加载题库：连接超时')
  })

  it('count 插槽落在 h1 里，actions 插槽落在动作区', () => {
    const w = mount(PageHeader, {
      props: { title: '错题本', loading: false },
      slots: { count: '共 12 条', actions: '<button class="extra">导出</button>' }
    })
    // 在 h1 内部，才会在空状态下跟着标题一起活着。
    expect(w.find('h1').text()).toContain('共 12 条')
    expect(w.find('.page-actions .extra').exists()).toBe(true)
  })
})
