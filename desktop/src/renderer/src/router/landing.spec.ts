// @vitest-environment jsdom
/**
 * 登录/注册落点纯函数（2026-09-25 第二轮；第三轮起加入口即身份）。
 *
 * `LoginView` / `RegisterView` 都走这一组函数决定「登录/注册成功去哪」，
 * 所以把组合在这里钉死：两端入口 × 两种意图的落点，谁都不能悄悄改一边。
 *
 * 规则回顾（`router/landing.ts`）：
 *   - 意图只决定落点，不决定账号角色（角色是服务端的答案）；
 *   - 目标端路由在当前入口的路由表里 → 直接跳路由；
 *   - 不在（学生入口选老师、教师入口选学生）→ 返回 null，调用方整页跳到
 *     `entryUrlForIntent` 的另一端入口，同源会话 token 让跳转后直接进入。
 *
 * 第三轮（入口即身份）新增：
 *   - `entryMismatch` 是「学生/教师互登另一端」的防线——登录页、注册页、
 *     路由守卫三处共用同一份判断；
 *   - `entryUrlForIntent` 多了 `page` 参数：登录/注册页的「学生/老师」
 *     切换器跨端时直接送去那端的登录页/注册页（指路，而不是登录后再跳）。
 */

import { describe, expect, it } from 'vitest'
import {
  defaultIntent,
  entryMismatch,
  entryUrlForIntent,
  homeForIntent,
  landingName,
  mismatchMessage
} from './landing'

describe('defaultIntent', () => {
  it('教师入口默认老师，学生入口与 Electron 默认学生', () => {
    expect(defaultIntent('teacher')).toBe('teacher')
    expect(defaultIntent('student')).toBe('student')
    expect(defaultIntent('both')).toBe('student')
  })
})

describe('homeForIntent —— 意图落点 × 入口可用性', () => {
  it('学生入口选学生 → 我的作业；选老师 → 无路由（需整页跳）', () => {
    expect(homeForIntent('student', 'student')).toBe('assignments')
    expect(homeForIntent('teacher', 'student')).toBeNull()
  })

  it('教师入口选老师 → 学情看板；选学生 → 无路由（需整页跳）', () => {
    expect(homeForIntent('teacher', 'teacher')).toBe('teacher')
    expect(homeForIntent('student', 'teacher')).toBeNull()
  })

  it('Electron 两端都有 → 两种意图都能直接落', () => {
    expect(homeForIntent('student', 'both')).toBe('assignments')
    expect(homeForIntent('teacher', 'both')).toBe('teacher')
  })
})

describe('entryUrlForIntent —— 跨端跳转地址', () => {
  it('老师 → 教师端控制台；学生 → 学生端作业页', () => {
    expect(entryUrlForIntent('teacher')).toBe('teacher.web.html#/teacher')
    expect(entryUrlForIntent('student')).toBe('index.web.html#/assignments')
  })

  it('page=login：登录页切换器跨端时送去那端的登录页', () => {
    expect(entryUrlForIntent('teacher', 'login')).toBe('teacher.web.html#/login')
    expect(entryUrlForIntent('student', 'login')).toBe('index.web.html#/login')
  })

  it('page=register：注册页切换器跨端时送去那端的注册页', () => {
    expect(entryUrlForIntent('teacher', 'register')).toBe('teacher.web.html#/register')
    expect(entryUrlForIntent('student', 'register')).toBe('index.web.html#/register')
  })
})

describe('entryMismatch —— 入口即身份（第三轮）', () => {
  it('Electron（both）没有入口之分，任何角色都放行', () => {
    expect(entryMismatch('both', 'student')).toBeNull()
    expect(entryMismatch('both', 'teacher')).toBeNull()
  })

  it('教师端入口只收教师账号', () => {
    expect(entryMismatch('teacher', 'teacher')).toBeNull()
    expect(entryMismatch('teacher', 'student')).toBe('student-on-teacher')
  })

  it('学生端入口只收学生账号', () => {
    expect(entryMismatch('student', 'student')).toBeNull()
    expect(entryMismatch('student', 'teacher')).toBe('teacher-on-student')
  })
})

describe('mismatchMessage —— 每种不匹配都有能照着做的提示', () => {
  it('学生账号误入教师端：告诉对方这是学生账号，去学生网址', () => {
    expect(mismatchMessage('student-on-teacher')).toContain('学生账号')
    expect(mismatchMessage('student-on-teacher')).toContain('index.web.html')
  })

  it('教师账号误入学生端：告诉对方这是教师账号，去教师网址', () => {
    expect(mismatchMessage('teacher-on-student')).toContain('教师账号')
    expect(mismatchMessage('teacher-on-student')).toContain('teacher.web.html')
  })

  it('匹配（null）没有提示', () => {
    expect(mismatchMessage(null)).toBe('')
  })
})

describe('landingName —— 已登录/按角色的兜底落点（现状不回归）', () => {
  it('教师端入口任何角色都落控制台', () => {
    expect(landingName('teacher', 'teacher')).toBe('teacher')
    expect(landingName('teacher', 'student')).toBe('teacher')
  })

  it('学生端入口任何角色都落作业页', () => {
    expect(landingName('student', 'student')).toBe('assignments')
    expect(landingName('student', 'teacher')).toBe('assignments')
  })

  it('Electron 按角色分流', () => {
    expect(landingName('both', 'teacher')).toBe('teacher')
    expect(landingName('both', 'student')).toBe('assignments')
  })
})
