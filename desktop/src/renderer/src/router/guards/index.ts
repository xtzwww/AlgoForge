/**
 * 守卫的统一安装入口（2026-10-04）。
 *
 * `main.ts` 只 import 这一个函数。守卫**不在** `router/index.ts` 里注册，
 * 理由见该文件末尾：组件测试 mock 了 `vue-router`，`createRouter` 返回的
 * 对象没有 `beforeEach`，在那里注册会让 `AppSidebar.spec` 这类测试整
 * 文件挂掉。
 *
 * **安装顺序即执行顺序**，这个顺序是语义的一部分：
 *
 *   1. `entry` —— 先判"你是不是走错了端"。
 *   2. `auth`  —— 再判"这一页你能不能进"。
 *
 * 反过来会怎样：一个学生账号被带进教师端网址（刷新、深链）。`auth`
 * 先跑，看到 `/teacher` 要教师角色、当前是学生 → 送回"学生首页"，而
 * 教师端网址上并没有学生首页，`landingName('teacher', 'student')` 给的是
 * `teacher`——于是被送回同一页，死循环。`entry` 先跑则一步到位：这是
 * 走错了端，去登录页，并带上"该账号是学生账号，请到学生网址登录"。
 */
import type { Router } from 'vue-router'
import { installAuthGuard } from './auth'
import { installEntryGuard } from './entry'

export function installGuards(router: Router): void {
  installEntryGuard(router)
  installAuthGuard(router)
}
