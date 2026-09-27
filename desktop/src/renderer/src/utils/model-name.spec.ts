import { describe, expect, it } from 'vitest'

import { shortModelName } from './model-name'

describe('shortModelName', () => {
  it('压缩 厂商-版本-后缀 的常见形态', () => {
    expect(shortModelName('agnes-3.0-flash')).toBe('agnes3.0f')
    expect(shortModelName('gpt-4o')).toBe('gpt4o')
  })

  it('保留没有版本段的模型名', () => {
    expect(shortModelName('deepseek-coder')).toBe('deepseek-coder')
    expect(shortModelName('qwen2.5-coder:7b')).toBe('qwen2.5-coder:7b')
    expect(shortModelName('qwen2.5:7b')).toBe('qwen2.5:7b')
  })

  it('空输入原样返回', () => {
    expect(shortModelName('')).toBe('')
    expect(shortModelName('   ')).toBe('')
  })
})
