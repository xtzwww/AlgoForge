/**
 * 路由元数据的类型扩展与判定函数（2026-10-04）。
 *
 * 这个文件是「谁能看这一页」的**唯一规则来源**。守卫（`guards/`）只
 * 执行它，业务页面完全不参与判断。
 *
 * 为什么独立成文件
 * ----------------
 * 两条理由，都不是"文件多了好看"：
 *
 *   1. **测试要能读规则而不建 router。** `guards/auth.spec.ts` 与
 *      `AppSidebar.spec` 都只需要知道"这条路由要不要登录"，而
 *      `router/index.ts` 会把急加载的视图（Monaco、ECharts）一起拖进来。
 *      本文件零运行时依赖，只 import 类型。
 *
 *   2. **`declare module 'vue-router'` 必须只写一次。** 它是对第三方模块
 *      的增补声明；写在两个文件里会静默合并，但读者会以为只有一处。
 *
 * 为什么字段默认是"不拦"
 * ----------------------
 * `requiresAuth` 缺省为 `false`。这是刻意的：现状是"游客可以刷题"，
 * 默认不拦 = 现有行为逐字不变，加标记才改变行为。**改造因此零回归**，
 * 而每次新增受保护页面都必须在路由表上做一个显式决定——漏写的后果是
 * "这一页没被拦"（可见、可测），而不是"某个页面静默地多了一道限制"。
 */

import type { AppMode } from '../runtime/mode'

declare module 'vue-router' {
  interface RouteMeta {
    /** Crumb text for this route, and the sidebar's label source. */
    title: string
    /**
     * Name of the route one level up, for the breadcrumb. Absent on the
     * top-level pages, which are their own root.
     */
    parent?: string
    /**
     * A page you reach from somewhere else rather than from the sidebar
     * (PU-13). Sign-in is the case this exists for: it needs a
     * `meta.title` so the breadcrumb can name it, but a permanent
     * 「登录」 entry in the nav is an invitation to sign in while already
     * signed in.
     */
    hidden?: boolean
    /**
     * Which side this page belongs to (2026-09-25). `student` / `teacher`
     * routes are filtered out of the other side's bundle view — the web
     * build has a separate entry per side, so a student's address should
     * not even resolve a teacher route. Absent means "both sides" (the
     * sign-in pages and the redirects). Electron runs in `both` mode and
     * ignores the distinction.
     *
     * **和 `requiresTeacher` 不能互相替代**：`mode` 在**构建期**把路由从
     * 对端的包里去掉了（学生网址上根本不存在这条路由），`requiresTeacher`
     * 在**运行期**拦"学生账号访问教师网址"。两者的作用对象不同——前者
     * 拦地址，后者拦账号。见 `requiresTeacher` 下的说明。
     */
    mode?: AppMode
    /**
     * Sidebar section (2026-09-26).
     *
     * 教师端原先只有两个导航项（学情看板 / 班级管理），而学情看板那个
     * 页面里平铺着十块功能。拆成五个面板之后，导航从两项变成五项，
     * 一个平铺的列表就不再能表达"这几项是一类"。
     *
     * 分组按**使用场景**分（备课 / 上课前 / 交接），不是按功能类型：
     * 老师要找的不是"图表类页面"，是"下节课讲什么"。四个组见
     * `NAV_GROUPS`。
     *
     * 承载在 `meta` 上而不是另写一份导航清单，理由和 `title` 一样：
     * 加一个页面应该只意味着加一条路由，而不是加一条路由**外加**一条
     * 会和它漂开的导航记录（见 `navRoutesFor`）。
     *
     * 学生端的路由不带它——它们的导航仍是一个平铺列表。
     */
    group?: string

    /**
     * 这一页需要登录才能访问（2026-10-04）。
     *
     * 缺省 `false` —— **不写就是不拦**。见文件头的说明。
     *
     * 注意这**不是安全边界**：任何人打开开发者工具都能改内存里的标志位。
     * 真正的边界是服务端的 403（`app/api/teacher.py`）。这个字段的作用
     * 是"不让用户看到注定要失败的空页面"，以及不让一个已经登录的人
     * 误以为"登录坏了"。
     */
    requiresAuth?: boolean

    /**
     * 这一页需要教师角色（2026-10-04）。**隐含 `requiresAuth: true`**，
     * 所以教师页不要两个都写，写这一个即可。
     *
     * 与 `mode: 'teacher'` 的分工：
     *
     *   | | `mode: 'teacher'` | `requiresTeacher: true` |
     *   |---|---|---|
     *   | 作用时机 | 构建期（web 拆包） | 运行期（导航守卫） |
     *   | 拦的是谁 | 学生网址上不存在这条路由 | 学生**账号**访问教师**网址** |
     *   | Electron（`both`） | 不分端，不生效 | 生效 |
     *
     * 两者互补，都要有。学生账号在教师网页址上打开 `/teacher` 时，
     * 路由存在（`mode` 管不着），要靠这个字段把它送回自己的首页。
     */
    requiresTeacher?: boolean

    /**
     * 登录/注册页专用（2026-10-04）。这类页面**始终可达**，且永不参与
     * 任何登录/角色判定。
     *
     * 为什么必须显式标出来：如果认证守卫统一要求"登录才能进"，登录页
     * 自己也会被拦，于是它重定向到自己——死循环，浏览器给出的表现是
     * "这个页面无法正常工作 / 重定向次数过多"，与真正的鉴权 bug 长得
     * 一模一样。这个标记就是它的解药。
     */
    guestOnly?: boolean

    /**
     * 这一页用哪套外壳渲染（2026-10-04）。
     *
     * **`'app'`（缺省）** —— 侧边栏 + 顶栏 + 引擎状态条的常规壳。
     * **`'auth'`** —— 全屏独立页：只有居中卡片，**不渲染任何 App 外壳**。
     *
     * 为什么要有这个字段
     * ------------------
     * 登录/注册页原先走的是常规壳，于是登录屏上能看到侧边栏、顶栏
     * 「智能学情反馈」品牌行、底部的「分析引擎就绪」状态条，而且能直接
     * 点侧边栏跳去题库/练习（那些页面是公开的）。使用者对它的描述是
     * "没独立" —— 它不是文件或路由没分开，是**视觉上还在 App 里面**。
     *
     * 与 `guestOnly` 分工不同，两者都要写：`guestOnly` 管**能不能进**，
     * `layout` 管**进去以后长什么样**。一个页面可以只有其一（比如某天
     * 出现"登录后可见但仍要全屏"的引导页，就只写 `layout`）。
     */
    layout?: 'app' | 'auth'
  }
}

/**
 * 这一页需要在登录态下才能渲染，且（可选地）要有教师角色？
 *
 * 一个函数而不是在两个地方各写一遍"或"表达式：守卫要用它决定拦不拦，
 * 测试要用它断言。两份表达式迟早会分叉（比如有人加了 `requiresAdmin`
 * 却只改了一处）。
 */
export function needsAuth(meta: { requiresAuth?: boolean; requiresTeacher?: boolean }): boolean {
  return Boolean(meta.requiresAuth || meta.requiresTeacher)
}

/**
 * 这一页要求教师**角色**（而不是仅仅"登录"）？
 *
 * 单独抽出来是因为守卫在这一点上要做一个**不同的动作**：未登录去登录页，
 * 登录了但角色不对则回自己的首页。见 `guards/auth.ts`。
 */
export function needsTeacher(meta: { requiresTeacher?: boolean }): boolean {
  return Boolean(meta.requiresTeacher)
}

/**
 * 这一页要不要换成全屏认证外壳（2026-10-04）？
 *
 * `App.vue` 用它决定渲染哪套结构。缺省（`'app'`）返回 `false` = 保留
 * 侧边栏那套，现有页面逐字不变。
 *
 * 独立成函数而不是在 `App.vue` 里直接比 `meta.layout === 'auth'`：
 * 判定散在模板里就没人能对它写单测，而这条判定决定"外壳显不显示"，
 * 错了的表现是整个 App 的框架消失或不该出现时出现。
 */
export function usesAuthLayout(meta: { layout?: 'app' | 'auth' }): boolean {
  return meta.layout === 'auth'
}
