/**
 * 跨入口整页跳转（2026-09-25 第三轮）。
 *
 * 学生端 / 教师端是两个独立的静态入口 HTML，切换「端」只能整页跳转
 * （同源，会话 token 共享）。所有跨端跳转统一走这里而不是各页直接写
 * `window.location.href`/`assign`：
 *
 *   - 跳转目标 URL 的构成集中在 `router/landing.ts` 的
 *     `entryUrlForIntent`（含登录页 / 注册页的 page 参数）；
 *   - 组件测试可以 mock 本模块拿到「跳去了哪里」，jsdom 的
 *     `location.assign` 不可重定义，直接调用会在测试里抛导航错误。
 */
export function goTo(url: string): void {
  window.location.assign(url)
}
