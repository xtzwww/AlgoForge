// @vitest-environment jsdom
/**
 * 变式题的组件测试。
 *
 * 这里唯一真正要紧的一条是**默认折叠**。`difference` 字段写的是「与原题的区别」，
 * 而那句话在多数情况下直接就是错因本身（「把 n 从第二行挪到第一行」）——摊开着渲染，
 * 等于把诊断以「额外练习」的名义塞给学生，而且是在他还没改完原题的屏幕上。
 * 这类错误不会报错，只会让 feedback 越给越快。
 *
 * 另外钉住两条与其它反馈组件一致的契约：空列表整个不渲染、新的一组要重新折叠。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import VariantProblem from './VariantProblem.vue'
import type { Variant } from '../api/mistake_book'

const routerPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush })
}))

/**
 * `attachTo: document.body` 不是可选项。
 *
 * 展开区用 `v-show`，断言必须看**可见性**而不是元素存不存在——内容一直在 DOM 里，
 * 只是 `display:none`。`isVisible()` 读 computed style，jsdom 对游离节点算不出值。
 */
type VariantProps = {
  variants: Variant[]
  entryId?: number
}

const mounted: VueWrapper[] = []

function mountVariants(props: VariantProps): VueWrapper {
  const w = mount(VariantProblem, { props, attachTo: document.body })
  mounted.push(w)
  return w
}

beforeEach(() => {
  routerPush.mockReset()
})

afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.unmount()
})

const VARIANT: Variant = {
  statement: '输入第一行是 n，第二行是 n 个整数，求最大值。',
  sample_input: '3\n1 9 4',
  sample_output: '9',
  knowledge_points: ['循环边界', '数组下标'],
  difference: '把 n 挪到了第一行，不再需要自己数。'
}

async function toggle(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('.btn-variant').trigger('click')
}

describe('VariantProblem', () => {
  it('空列表整个不渲染，不留一张空卡片', () => {
    const w = mountVariants({ variants: [] })

    expect(w.find('.variants').exists()).toBe(false)
  })

  it('默认折叠：题干和差异都看不到，只给一个入口', () => {
    const w = mountVariants({ variants: [VARIANT] })

    expect(w.find('.variants-body').isVisible()).toBe(false)
    expect(w.find('.btn-variant').text()).toBe('看看类似的题')
    // 折叠状态要说明它在等什么，而不是留一个猜不出内容的收起块。
    expect(w.text()).toContain('先把这道题改对')
  })

  it('展开后题干、样例、知识点、差异都呈现', async () => {
    const w = mountVariants({ variants: [VARIANT] })

    await toggle(w)

    expect(w.find('.variants-body').isVisible()).toBe(true)
    expect(w.text()).toContain('第二行是 n 个整数')
    // 样例是空白敏感的，必须原样给出（含换行）。
    expect(w.find('.variant-samples').text()).toContain('3')
    expect(w.find('.variant-samples').text()).toContain('9')
    expect(w.findAll('.variant-point').map((p) => p.text())).toEqual(['循环边界', '数组下标'])
    expect(w.text()).toContain('把 n 挪到了第一行')
  })

  it('可以再收起', async () => {
    const w = mountVariants({ variants: [VARIANT] })

    await toggle(w)
    expect(w.find('.variants-body').isVisible()).toBe(true)

    await toggle(w)
    expect(w.find('.variants-body').isVisible()).toBe(false)
    expect(w.find('.btn-variant').text()).toBe('看看类似的题')
  })

  it('新的一组变式题到来时重新折叠，不会摊着上一题的差异', async () => {
    const w = mountVariants({ variants: [VARIANT] })

    await toggle(w)
    expect(w.find('.variants-body').isVisible()).toBe(true)

    await w.setProps({
      variants: [{ ...VARIANT, statement: '这次是求最小值。', difference: '把最大值改成最小值。' }]
    })
    expect(w.find('.variants-body').isVisible()).toBe(false)
  })

  it('多道变式题各渲染一张，共用同一个入口', async () => {
    const w = mountVariants({
      variants: [VARIANT, { ...VARIANT, statement: '第二道：求最小值。' }]
    })

    await toggle(w)

    expect(w.findAll('.variant-card')).toHaveLength(2)
    expect(w.findAll('.btn-variant')).toHaveLength(1)
    expect(w.text()).toContain('第二道：求最小值。')
    expect(w.find('.variants-count').text()).toContain('2')
  })

  it('没有 difference 时不渲染那一行，不出现空标签', async () => {
    const w = mountVariants({ variants: [{ ...VARIANT, difference: '' }] })

    await toggle(w)
    expect(w.find('.variant-diff').exists()).toBe(false)
  })

  it('知识点为空时不渲染空的一排 chip', async () => {
    const w = mountVariants({ variants: [{ ...VARIANT, knowledge_points: [] }] })

    await toggle(w)
    expect(w.find('.variant-points').exists()).toBe(false)
  })

  it('有 entryId 时每张卡片给「用编辑器做这道题」的门，点击去练习页', async () => {
    const w = mountVariants({
      variants: [VARIANT, { ...VARIANT, statement: '第二道：求最小值。' }],
      entryId: 7
    })

    await toggle(w)

    const doors = w.findAll('.btn-practise')
    expect(doors).toHaveLength(2)

    await doors[1].trigger('click')
    expect(routerPush).toHaveBeenCalledWith({
      name: 'variant-practice',
      query: { entry: 7, index: 1 }
    })
  })

  it('没有 entryId（如判题面板的展示）不给做题门', async () => {
    const w = mountVariants({ variants: [VARIANT] })

    await toggle(w)
    expect(w.find('.btn-practise').exists()).toBe(false)
  })
})
