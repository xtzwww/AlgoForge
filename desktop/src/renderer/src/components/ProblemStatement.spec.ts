// @vitest-environment jsdom
/**
 * 题面的组件测试。
 *
 * 题面是「学生照着它写代码」的那块，所以它坏了的样子不是报错，而是学生
 * 读到一个**不对但不空**的题面：空输入写成空白、样例被当成全部用例。
 * 这两件事在 `Practice.vue` 里断言要先把编辑器与判题流搭起来，抽出来之后
 * 只需要给它一组 props。
 *
 * 钉住的四条：
 *   1. **空输入显示「(无)」** —— 空输入是合法用例（读到 EOF、或纯打印题），
 *      显示成空白会被读成「这道题没有输入」，照着写就 WA；
 *   2. 没有知识点标签时整行不渲染，不留一个空行占位；
 *   3. 题库模式说「样例」并说明**隐藏用例要提交才跑**，自由练习说「用例」——
 *      措辞错了学生会以为自己看见了全部用例；
 *   4. 时限那行只在真的有时限时出现，而不是渲染「时限 nulls」。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ProblemStatement from './ProblemStatement.vue'
import type { TestCaseIn } from '../api/judge'

type StatementProps = {
  description: string
  inputFormat: string
  outputFormat: string
  knowledgeTags: string[]
  cases: TestCaseIn[]
  isProblemMode: boolean
  timeLimit: number | null
  memoryLimit: number | null
}

const mounted: VueWrapper[] = []

function mountStatement(over: Partial<StatementProps> = {}): VueWrapper {
  const props: StatementProps = {
    description: '给定两个整数，输出它们的和。',
    inputFormat: '一行两个整数 a b',
    outputFormat: '一个整数',
    knowledgeTags: ['基础语法'],
    cases: [{ input: '3 5', expected_output: '8' }],
    isProblemMode: true,
    timeLimit: 1,
    memoryLimit: 256,
    ...over
  }
  const w = mount(ProblemStatement, { props, attachTo: document.body })
  mounted.push(w)
  return w
}

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
})

describe('ProblemStatement', () => {
  it('空输入显示「(无)」，而不是一片空白', () => {
    const w = mountStatement({ cases: [{ input: '', expected_output: 'hello' }] })
    expect(w.find('.sample-row code').text()).toBe('(无)')
  })

  it('有输入时原样显示，不被「(无)」顶掉', () => {
    const w = mountStatement({ cases: [{ input: '3 5', expected_output: '8' }] })
    const codes = w.findAll('.sample-row code').map((n) => n.text())
    expect(codes).toEqual(['3 5', '8'])
  })

  it('没有知识点标签时整行不渲染', () => {
    const w = mountStatement({ knowledgeTags: [] })
    expect(w.find('.tag-row').exists()).toBe(false)
  })

  it('题库模式说「样例」并说明隐藏用例要提交才跑', () => {
    const w = mountStatement({ isProblemMode: true })
    expect(w.text()).toContain('样例')
    expect(w.text()).toContain('隐藏用例')
  })

  it('自由练习模式说「用例」，且不说隐藏用例', () => {
    const w = mountStatement({ isProblemMode: false })
    expect(w.text()).toContain('用例')
    expect(w.text()).not.toContain('隐藏用例')
  })

  it('时限那行只在真的有时限时出现', () => {
    const withLimits = mountStatement({ timeLimit: 2, memoryLimit: 128 })
    expect(withLimits.text()).toContain('时限 2s · 内存 128 MB')

    const without = mountStatement({ timeLimit: null, memoryLimit: null })
    expect(without.text()).not.toContain('时限')
  })

  it('输入/输出格式段落只在非空时出现', () => {
    const w = mountStatement({ inputFormat: '', outputFormat: '' })
    expect(w.text()).not.toContain('输入格式')
    expect(w.text()).not.toContain('输出格式')
    // 描述本身还在——那才是题面的主体。
    expect(w.text()).toContain('给定两个整数')
  })
})
