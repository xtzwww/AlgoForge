/**
 * 登录/注册成功后的落点（2026-09-25）。
 *
 * 为什么是独立文件：`LoginView` / `RegisterView` 只需要「落点」这一个
 * 判断，而 `router/index.ts` 会把急加载的视图（Monaco、ECharts）一起
 * 拖进来——在测试环境里那意味着整个编辑器依赖链。这里零运行时依赖：
 * `AppMode` 是纯类型，`import type` 在编译后会被擦除。
 *
 * 规则（与各端可见路由一致）：
 *   - `both`（Electron）：教师落「学情看板」，其他人落「我的作业」；
 *   - `teacher`（教师端入口）：没有学生页面，任何账号都落控制台——
 *     学生账号落上去由页面自己的 403 说明兜着；
 *   - `student`（学生端入口）：没有控制台，任何账号都落作业列表——
 *     教师账号落上去看到的是空列表，那是诚实的「这里没有你的东西」。
 *
 * 入口即身份（2026-09-25 第三轮）：学生端网址只登录学生账号、教师端网址
 * 只登录教师账号。`entryMismatch` 是这条边界——`both`（Electron）没有
 * 入口之分，不拦。登录/注册页与全局守卫共用它，所以「账号角色 × 当前
 * 网址」不一致时，提示文案只有这一份。
 */

import type { AppMode } from '../runtime/mode'

export type LoginIntent = 'student' | 'teacher'

/** 账号角色与当前入口不一致的类型；`null` = 匹配。 */
export type EntryMismatch = 'student-on-teacher' | 'teacher-on-student' | null

export function landingName(mode: AppMode, role: string): string {
  if (mode === 'teacher') return 'teacher'
  if (mode === 'student') return 'assignments'
  return role === 'teacher' ? 'teacher' : 'assignments'
}

/** 登录/注册页的默认意图：教师入口默认老师，其余默认学生。 */
export function defaultIntent(mode: AppMode): LoginIntent {
  return mode === 'teacher' ? 'teacher' : 'student'
}

/**
 * 该意图的首页在当前入口的路由名；当前入口没有对应路由时返回 `null`
 * （调用方应整页跳到 {@link entryUrlForIntent} 的另一端入口）。
 */
export function homeForIntent(intent: LoginIntent, mode: AppMode): string | null {
  if (intent === 'teacher') return mode === 'student' ? null : 'teacher'
  return mode === 'teacher' ? null : 'assignments'
}

/**
 * 账号角色与当前入口是否一致（2026-09-25 第三轮）。
 *
 * `both`（Electron 桌面壳）没有「当前入口」，一律放行；教师端网址只认
 * 教师账号、学生端网址只认学生账号。登录页、注册页、全局守卫都调它，
 * 保证互登另一端在任何路径上都被同一份判断拦住。
 */
export function entryMismatch(mode: AppMode, role: string): EntryMismatch {
  if (mode === 'both') return null
  if (mode === 'teacher') return role === 'teacher' ? null : 'student-on-teacher'
  return role === 'teacher' ? 'teacher-on-student' : null
}

/** 把 {@link EntryMismatch} 变成一句能照着做的提示（含另一端网址）。 */
export function mismatchMessage(mismatch: EntryMismatch): string {
  switch (mismatch) {
    case 'student-on-teacher':
      return '该账号是学生账号，请到学生网址（index.web.html）登录'
    case 'teacher-on-student':
      return '该账号是教师账号，请到教师网址（teacher.web.html）登录'
    default:
      return ''
  }
}

/**
 * 该意图的另一端入口 URL（同源静态服务，与当前页面同目录）。
 *
 * `page` 指定跳到那端的哪一页：默认首页，登录/注册页的「学生/老师」
 * 切换器用它把使用者直接送去另一端的登录页或注册页——入口即身份，
 * 跨端的登录动作不应该发生在本页。
 */
export function entryUrlForIntent(
  intent: LoginIntent,
  page: 'home' | 'login' | 'register' = 'home'
): string {
  const entry = intent === 'teacher' ? 'teacher.web.html' : 'index.web.html'
  const hash =
    page === 'login'
      ? '#/login'
      : page === 'register'
        ? '#/register'
        : intent === 'teacher'
          ? '#/teacher'
          : '#/assignments'
  return `${entry}${hash}`
}
