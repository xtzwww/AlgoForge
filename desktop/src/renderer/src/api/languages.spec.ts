/**
 * `pickDefaultLanguage` 的兜底规则测试。
 *
 * 这个函数只有 4 行，但它守着「打开发现编辑器是空的、或者默认选了个
 * 本机编译不了的语言」这类开场故障——那类故障的可读性极差（用户只会
 * 看到一片 CE），所以规则值得写下来：
 *
 * - 优先 C++，与侧车的 `DEFAULT_LANGUAGE` 一致；
 * - **只有可用**的 C++ 才算，装了但工具链坏了不算；
 * - 都没有就退回第一个可用的，而不是返回 null 让编辑器空着。
 */

import { describe, expect, it } from 'vitest'
import type { LanguageOption } from './languages'
import { pickDefaultLanguage } from './languages'

function lang(id: string, available: boolean): LanguageOption {
  return {
    id,
    display_name: id.toUpperCase(),
    monaco_language: id,
    default_template: '',
    available,
    version: available ? '1.0' : null,
    detail: null
  }
}

describe('pickDefaultLanguage', () => {
  it('C++ 可用时选 C++', () => {
    const picked = pickDefaultLanguage([lang('python', true), lang('cpp', true)])
    expect(picked?.id).toBe('cpp')
  })

  it('C++ 不可用时不选它，即使它排在最前', () => {
    // 装了编译器但版本不对、`available: false`——选它只会得到一片 CE。
    const picked = pickDefaultLanguage([lang('cpp', false), lang('python', true)])
    expect(picked?.id).toBe('python')
  })

  it('没有任何语言可用时返回 null', () => {
    expect(pickDefaultLanguage([lang('cpp', false), lang('python', false)])).toBeNull()
  })

  it('空目录返回 null', () => {
    expect(pickDefaultLanguage([])).toBeNull()
  })

  it('多个可用语言时取第一个可用的', () => {
    const picked = pickDefaultLanguage([
      lang('cpp', false),
      lang('java', true),
      lang('python', true)
    ])
    expect(picked?.id).toBe('java')
  })
})
