import { describe, expect, it } from 'vitest'

import { looksLikeCodeRequest } from './intent'

describe('looksLikeCodeRequest', () => {
  it('把明确的代写指令判为代码请求', () => {
    expect(looksLikeCodeRequest('帮我写一个读入 n 的循环')).toBe(true)
    expect(looksLikeCodeRequest('帮我实现一个排序函数')).toBe(true)
    expect(looksLikeCodeRequest('生成一个数组')).toBe(true)
    expect(looksLikeCodeRequest('写代码遍历这个数组')).toBe(true)
    expect(looksLikeCodeRequest('定义一个函数')).toBe(true)
    expect(looksLikeCodeRequest('写一行读入的代码')).toBe(true)
  })

  it('把问思路的问题判为对话', () => {
    expect(looksLikeCodeRequest('这道题为什么用前缀和？')).toBe(false)
    expect(looksLikeCodeRequest('怎么写冒泡排序？')).toBe(false)
    expect(looksLikeCodeRequest('这道题怎么用循环？')).toBe(false)
    expect(looksLikeCodeRequest('这个哪里错了？')).toBe(false)
    expect(looksLikeCodeRequest('排序的时间复杂度是多少')).toBe(false)
  })

  it('空输入不是代码请求', () => {
    expect(looksLikeCodeRequest('')).toBe(false)
    expect(looksLikeCodeRequest('   ')).toBe(false)
  })
})
