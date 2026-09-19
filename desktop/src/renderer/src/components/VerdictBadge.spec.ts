// @vitest-environment jsdom
/**
 * 判定徽章的组件测试。
 *
 * 这个组件的存在理由写在它自己的头注释里：判题状态到颜色的映射曾经有四份拷贝，
 * 漏一个分支就是「某一个判定的颜色在某一个界面上是错的」——**不会报错，只是错**。
 * 所以这里要钉住的是那份映射本身，以及它的降级行为。
 *
 * 降级那条最重要：状态是从 WebSocket 来的**字符串**，后端将来加一个判定（或打
 * 错一个字）时，徽章必须渲染成中性灰并**原样显示那个字符串**，而不是空白、也不
 * 是崩掉。空白意味着「这次判题没有结果」，比错颜色更糟。
 */
import { describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import VerdictBadge from './VerdictBadge.vue'

function badge(props: {
  status: string
  withLabel?: boolean
  size?: 'sm' | 'md' | 'lg'
}): VueWrapper {
  return mount(VerdictBadge, { props, attachTo: document.body })
}

describe('VerdictBadge', () => {
  it('六个判定各有自己的中文名与颜色变量', () => {
    const expected: [string, string, string][] = [
      ['AC', '通过', '--af-verdict-ac'],
      ['WA', '答案错误', '--af-verdict-wa'],
      ['TLE', '运行超时', '--af-verdict-tle'],
      ['MLE', '内存超限', '--af-verdict-mle'],
      ['CE', '编译错误', '--af-verdict-ce'],
      ['RE', '运行错误', '--af-verdict-re']
    ]

    for (const [code, label, cssVar] of expected) {
      const w = badge({ status: code, withLabel: true })
      // 颜色走 CSS 变量而不是硬编码色值：换主题改 tokens.css 就够了。
      expect((w.element as HTMLElement).style.color).toContain(cssVar)
      expect(w.find('.verdict-label').text()).toBe(label)
      expect(w.find('.verdict-code').text()).toBe(code)
      w.unmount()
    }
  })

  it('不认识的状态降级成中性灰，但字符串本身不能丢', () => {
    const w = badge({ status: 'XYZ', withLabel: true })

    expect((w.element as HTMLElement).style.color).toContain('--af-verdict-unknown')
    expect(w.find('.verdict-label').text()).toBe('未知结果')
    // 「吞掉未知值」是最坏的结果：出了新判定却看不出是哪个。
    expect(w.find('.verdict-code').text()).toBe('XYZ')
    expect(w.attributes('title')).toBe('XYZ · 未知结果')
  })

  it('不带 label 时只显示状态码', () => {
    const w = badge({ status: 'AC' })

    expect(w.find('.verdict-code').text()).toBe('AC')
    expect(w.find('.verdict-label').exists()).toBe(false)
  })

  it('三种尺寸各自落到自己的类上', () => {
    expect(badge({ status: 'AC', size: 'sm' }).classes()).toContain('verdict-sm')
    expect(badge({ status: 'AC', size: 'lg' }).classes()).toContain('verdict-lg')
    expect(badge({ status: 'AC' }).classes()).toContain('verdict-md')
  })
})
