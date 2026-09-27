/**
 * 端模式 —— 当前构建 / 入口属于学生端还是教师端（2026-09-25）。
 *
 * 为什么存在
 * ----------
 * 网页版原先把学生功能与教师功能放在同一个 bundle、同一份路由表里，
 * 侧边栏导航又从路由表直接派生 —— 于是学生登录后也能看到「学情看板」
 * 「班级管理」这些教师入口。产品要求把两个界面分开：学生打开学生网址、
 * 教师打开教师网址，各进各的。
 *
 * 判定方式
 * --------
 * web 构建有两个入口 HTML（`index.web.html` 学生端 / `teacher.web.html`
 * 教师端），共享同一个 `src/main.ts`。运行时按地址栏的文件名区分：
 *
 *   - `/teacher.web.html`   → teacher（教师端入口）
 *   - 其它（index.web.html、dev server 根路径）→ student
 *
 * Electron 构建只有一个入口（`index.html`），没有这两个文件，因此
 * 保持现状 `both`：桌面端同时承载两端，不做入口拆分。
 *
 * 消费方
 * ------
 * `router/index.ts` 用它过滤路由表（`visibleRoutes`），`AppSidebar.vue`
 * 的导航、`AppTopbar.vue` 的端名徽标、登录/注册后的落点
 * （`landingName`）都以它为准。
 */

import { runtime } from './index'

/** 端模式。`both` 仅 Electron 使用，web 构建只会是 student / teacher。 */
export type AppMode = 'student' | 'teacher' | 'both'

/** 教师端入口的文件名，与 `desktop/src/renderer/teacher.web.html` 一致。 */
const TEACHER_ENTRY = 'teacher.web.html'

function detect(): AppMode {
  if (runtime.kind === 'web') {
    const basename = location.pathname.split('/').pop() ?? ''
    return basename === TEACHER_ENTRY ? 'teacher' : 'student'
  }
  // Electron：桌面端同时承载两端（现状），不做入口拆分。
  return 'both'
}

/** 当前端模式。web 构建在加载时即确定，之后不变化。 */
export const appMode: AppMode = detect()
