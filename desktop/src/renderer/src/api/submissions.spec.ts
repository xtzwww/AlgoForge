/**
 * `foldVerdicts` 的聚合规则测试。
 *
 * 题库页的「已通过 1/20」是前端拿一页历史自己折出来的（没有专门的
 * 聚合接口），所以这里是这道题唯一的真相来源。要钉住的是两条**反直觉**
 * 的规则，它们都是产品决定而不是实现细节，改掉任意一条页面就会开始
 * 说谎：
 *
 * - `passed` 是粘性的：AC 之后再 WA，仍然算通过。因为界面问的是
 *   「我有没有解出过这题」，不是「我最后一次做得好不好」。
 * - 自由运行（`problem_id === null`）不进表，也不占 attempts。
 */

import { describe, expect, it } from 'vitest'
import type { SubmissionSummary } from './submissions'
import { foldVerdicts } from './submissions'

function row(problemId: number | null, status: string): SubmissionSummary {
  return {
    id: 1,
    problem_id: problemId,
    status,
    language: 'cpp',
    exec_time: null,
    memory_used: null,
    created_at: null
  }
}

describe('foldVerdicts', () => {
  it('空历史折出空表', () => {
    expect(foldVerdicts([]).size).toBe(0)
  })

  it('同一题的多次提交累加 attempts', () => {
    const map = foldVerdicts([row(3, 'WA'), row(3, 'WA'), row(3, 'AC')])
    expect(map.get(3)).toEqual({ passed: true, attempts: 3 })
  })

  it('passed 是粘性的：AC 之后的 WA 不会把它抹掉', () => {
    const map = foldVerdicts([row(3, 'AC'), row(3, 'WA'), row(3, 'TLE')])
    expect(map.get(3)?.passed).toBe(true)
  })

  it('先 WA 后 AC 同样算通过（顺序无关）', () => {
    const map = foldVerdicts([row(3, 'WA'), row(3, 'AC')])
    expect(map.get(3)?.passed).toBe(true)
  })

  it('一次都没 AC 就是没通过', () => {
    const map = foldVerdicts([row(3, 'CE'), row(3, 'WA')])
    expect(map.get(3)).toEqual({ passed: false, attempts: 2 })
  })

  it('自由运行（problem_id 为 null）不进表', () => {
    const map = foldVerdicts([row(null, 'AC'), row(3, 'WA')])
    expect(map.has(3)).toBe(true)
    expect(map.size).toBe(1)
  })

  it('各题互不干扰', () => {
    const map = foldVerdicts([row(1, 'AC'), row(2, 'WA'), row(1, 'WA'), row(3, 'AC')])
    expect(map.get(1)).toEqual({ passed: true, attempts: 2 })
    expect(map.get(2)).toEqual({ passed: false, attempts: 1 })
    expect(map.get(3)).toEqual({ passed: true, attempts: 1 })
  })
})
