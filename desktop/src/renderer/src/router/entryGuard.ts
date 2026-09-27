/**
 * 入口即身份：全局导航守卫（2026-09-25 第三轮）。
 *
 * 一个会话的角色不属于当前入口时（教师账号在学生端网址、学生账号在
 * 教师端网址），把它送回登录页并在 `?entryErr=` 里点名不匹配类型，
 * `LoginView` 据此显示提示。
 *
 * 为什么独立成文件、由 `main.ts` 安装：
 *   - 登录/注册页已就地拦截同场景，这里兜底「没经过登录页就带进来的
 *     会话」——刷新、深链、旧构建残留的整页跳转；
 *   - `AppSidebar.spec` 这类组件测试 mock 了 `vue-router`（`createRouter`
 *     返回无 `beforeEach` 的普通对象），守卫若在 `router/index.ts` 模块
 *     顶层注册会让那些测试整文件挂掉。
 *
 * `main.ts` 另有一个会话 watch 做同样的事：页面**首次导航**发生在
 * `restoreSession()` 应答之前，守卫此时看到 `sessionReady` 为 false 会
 * 放行；watch 在会话恢复完成后补上同一跳转。两者配合，任何路径都到不
 * 了另一端——Electron（`both`）没有入口之分，天然豁免。
 */
import type { Router } from 'vue-router'
import { logout, sessionReady, sessionUser } from '../api/session'
import { entryMismatch } from './landing'
import { appMode } from '../runtime/mode'

export function installEntryGuard(router: Router): void {
  router.beforeEach(async (to) => {
    if (!sessionReady.value) return true
    const user = sessionUser.value
    if (!user) return true
    const mismatch = entryMismatch(appMode, user.role)
    if (!mismatch) return true
    // Already on the sign-in page: `LoginView` handles the message itself.
    if (to.name === 'login') return true
    await logout()
    return { name: 'login', query: { entryErr: mismatch } }
  })
}
