/**
 * Application routes (PU-7).
 *
 * Replaces the `ref<Tab>` + `v-if` chain App.vue used to carry. Three
 * things forced the change:
 *
 *   1. The web target needs a URL — a browser refresh should come back to
 *      the page you were on, and `#/problems/12` should be shareable.
 *   2. The catalogue turned one page into several (list / problem /
 *      submissions), and a hand-written `v-if` chain does not survive that.
 *   3. The shell needs one place to answer "is the sidecar up?" before
 *      rendering a page — see `composables/useSidecarStatus.ts`.
 *
 * **Hash history, deliberately.** `createWebHashHistory` puts the route
 * after `#`, which is the only mode that works in both shells without
 * server cooperation: Electron loads the renderer from a `file://`-like
 * origin, and the web target is documented as a static bundle in
 * `dist-web/` (PU-5). History mode would need a rewrite rule on whatever
 * serves it, and would 404 on refresh in Electron.
 *
 * The routes are flat rather than nested. Nesting would mean every parent
 * needs its own `<RouterView>`, and the only parent that exists is the
 * shell in App.vue — which is not a route. Hierarchy for the breadcrumb
 * comes from `meta.parent` instead; see `components/AppTopbar.vue`.
 *
 * Views are imported eagerly. They are small next to Monaco (which
 * dominates the bundle), and lazy routes would add a chunk fetch plus a
 * loading flash on every navigation for no measurable gain. The one
 * exception is that the two problem routes share a component, which the
 * router caches per record on its own.
 */

import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { appMode, type AppMode } from '../runtime/mode'
// 增补 `RouteMeta`（含 requiresAuth / requiresTeacher / guestOnly）。
// 只 import 类型与两个判定函数——本文件不执行任何鉴权逻辑，见下面
// 「守卫由 main.ts 安装」那段说明。
import './meta'
import Practice from '../views/Practice.vue'
import ProblemPracticeView from '../views/ProblemPracticeView.vue'
import ProblemListView from '../views/ProblemListView.vue'
import MistakesView from '../views/MistakesView.vue'
import VariantPracticeView from '../views/VariantPracticeView.vue'
import ProfileView from '../views/ProfileView.vue'
import PlanView from '../views/PlanView.vue'
import TeacherOverviewView from '../views/teacher/TeacherOverviewView.vue'
import TeacherAnalysisView from '../views/teacher/TeacherAnalysisView.vue'
import TeacherWorkView from '../views/teacher/TeacherWorkView.vue'
import TeacherReviewView from '../views/teacher/TeacherReviewView.vue'
import TeacherRosterView from '../views/teacher/TeacherRosterView.vue'
import TeacherProblemsView from '../views/teacher/TeacherProblemsView.vue'
import AssignmentsView from '../views/AssignmentsView.vue'
import LoginView from '../views/auth/LoginView.vue'
import RegisterView from '../views/auth/RegisterView.vue'

/**
 * The top-level pages, in sidebar order.
 *
 * Exported because the sidebar renders its navigation from the route
 * table rather than a second hardcoded list: adding a page should mean
 * adding one route, not a route *and* a nav entry that can drift from it.
 */
export const routes: RouteRecordRaw[] = [
  // No separate "/" page: the practice editor is what the app opens on,
  // and free-run mode is the state with no problem bound to it. The
  // landing follows the current side: a teacher entry opens the console.
  { path: '/', redirect: () => ({ name: appMode === 'teacher' ? 'teacher' : 'practice' }) },

  {
    path: '/practice',
    name: 'practice',
    component: Practice,
    // 公开：游客可以打开编辑器自由跑代码（判题不等登录）。
    meta: { title: '练习', mode: 'student' }
  },
  {
    // Where a student lands after signing in (PU-13).
    //
    // **2026-10-04 起 requiresAuth: true。** 它原先不拦，靠页面里的
    // `v-if="!signedIn"` 画一张"还没有登录"的卡片。那条路是错的：这一页
    // 的**全部内容**都是"我的"——我的班级、我的作业、我的进度——未登录
    // 时没有任何可渲染的东西，画一张卡片只是把一个路由问题展示成一个
    // 页面状态。现在由守卫在导航阶段拦下，页面本身不再有登录分叉。
    //
    // 仍然**不**要求教师角色：教师打开它看到的是空列表，那是诚实的
    // 「你没有加入任何班级」，不需要一次重定向来解释。
    path: '/assignments',
    name: 'assignments',
    component: AssignmentsView,
    meta: { title: '我的作业', mode: 'student', requiresAuth: true }
  },
  {
    path: '/problems',
    name: 'problems',
    component: ProblemListView,
    // 公开：题库是刷题入口，游客可以先看题再决定要不要注册。
    meta: { title: '题库', mode: 'student' }
  },
  {
    // `(\\d+)` keeps this from swallowing anything else under /problems
    // and, more usefully, guarantees the id is numeric before it reaches
    // the API client — a non-numeric id would otherwise be sent as a path
    // segment and come back as a 422 the UI has to explain.
    //
    // `?assignment=<id>` is how a student's practice knows which homework
    // it belongs to (阶段 5). It is a **query** rather than a path segment
    // because the problem is the resource and the assignment is a lens on
    // it: the same problem can be opened from a lesson, a homework, or
    // the catalogue, and those should share one URL up to the query.
    // Absent = free practice, which legitimately has no level to read.
    //
    // 公开：与 `/problems` 一致。带 `?assignment=` 时交作业的人必须已
    // 登录，但那由接口自己回答（401），不该在路由层把它变成"整页不可见"。
    path: '/problems/:id(\\d+)',
    name: 'problem',
    component: ProblemPracticeView,
    props: (route) => ({
      problemId: Number(route.params.id),
      // Coerced here rather than in the component, so the page can hold a
      // plain `number | null` and never has to parse a route string.
      // A non-numeric value becomes null (= free practice) instead of
      // NaN, which would be sent to the API as a garbage id.
      assignmentId: Number(route.query.assignment) || null
    }),
    meta: { title: '题目', parent: 'problems', mode: 'student' }
  },
  {
    path: '/mistakes',
    name: 'mistakes',
    component: MistakesView,
    // 错题本全是"我的"，未登录时没有可渲染的内容。
    meta: { title: '错题本', mode: 'student', requiresAuth: true }
  },
  {
    // #5 变式题闭环：错题本里 S7 的变式题没有题目行、没有隐藏用例，
    // 判题用它的样例走 inline 自测。entry / index 指向错题本里的哪条
    // 变式题——它们必须在 mistakes 路由可达的上下文里才有意义。
    path: '/practice/variant',
    name: 'variant-practice',
    component: VariantPracticeView,
    // 入口只从错题本来，跟着 mistakes 一起要登录。
    meta: { title: '变式题练习', parent: 'mistakes', mode: 'student', requiresAuth: true }
  },
  {
    path: '/profile',
    name: 'profile',
    component: ProfileView,
    meta: { title: '画像', mode: 'student', requiresAuth: true }
  },
  {
    path: '/plan',
    name: 'plan',
    component: PlanView,
    meta: { title: '复习', mode: 'student', requiresAuth: true }
  },
  {
    // **2026-10-04 起 requiresTeacher: true。** 这一页原先刻意不做角色门
    // ——理由是"服务端的 403 才是边界"。那个理由本身没错，但结论下错了：
    // 前端不做门**不等于**前端要开着，它意味着"每个页面各自处理被拒的
    // 情况"。结果是六个面板各写一遍 `if (!isTeacher.value) return`，加上
    // `TeacherGate` 和 `AppTopbar`，同一个判断有八个副本。
    //
    // 现在门收在路由表上：能渲染这一页 = 你是教师。服务端的 403 仍然
    // 兜底（它才是安全边界），而 `TeacherGate` 保留为**展示层**——它渲染
    // "角色不符"那屏，是服务端拒绝的用户可读版本。
    //
    // **教学概览是教师入口的落点。** 它曾是那个 1600 行的 `/teacher`
    // 学情看板；2026-09-26 拆成五个面板后，名字留给这个只管"我现在要
    // 不要做点什么"的首屏（见 `TeacherOverviewView` 的文档）。
    path: '/teacher',
    name: 'teacher',
    component: TeacherOverviewView,
    meta: {
      title: '教学概览',
      mode: 'teacher',
      group: 'teacher-overview',
      requiresTeacher: true
    }
  },
  {
    // 下节课讲什么 + 谁需要我。曾经的 `/teacher` 主体。
    // `?student=<id>` 是一次性意图（概览页点人名进来），打开即消费掉，
    // 理由见 `TeacherAnalysisView` 的文档。
    path: '/teacher/analysis',
    name: 'teacher-analysis',
    component: TeacherAnalysisView,
    meta: {
      title: '学情分析',
      mode: 'teacher',
      group: 'teacher-analysis',
      requiresTeacher: true
    }
  },
  {
    // 作业 + AI 使用记录 + 成绩单。
    path: '/teacher/work',
    name: 'teacher-work',
    component: TeacherWorkView,
    meta: {
      title: '作业',
      mode: 'teacher',
      group: 'teacher-teaching',
      requiresTeacher: true
    }
  },
  {
    // 存疑提交独立成屏（2026-09-26）：它是批阅型事务，要一条条处理完，
    // 中途会切走再回来——夹在概览的一串数字里，每次滚过去都看到它、
    // 每次都没处理完。
    path: '/teacher/review',
    name: 'teacher-review',
    component: TeacherReviewView,
    meta: {
      title: '存疑提交',
      mode: 'teacher',
      group: 'teacher-teaching',
      requiresTeacher: true
    }
  },
  {
    // 名册 + 班级（2026-09-26 合并）。原先这是两条侧边栏项，但它们读写
    // 同一批数据，任何一次修改之后两边都要刷新——分在两地，"移出班级"
    // 之后名册上的班级标签就过期了，而老师不会知道要回去刷一下。
    path: '/teacher/roster',
    name: 'teacher-roster',
    component: TeacherRosterView,
    meta: {
      title: '名册与班级',
      mode: 'teacher',
      group: 'teacher-people',
      requiresTeacher: true
    }
  },
  {
    // 题库管理（2026-09-27）。与作业同一分组：备课建题 → 布置作业是
    // 一条链，老师不会在"建题"和"布置"之间切换分组。
    path: '/teacher/problems',
    name: 'teacher-problems',
    component: TeacherProblemsView,
    meta: {
      title: '题库',
      mode: 'teacher',
      group: 'teacher-teaching',
      requiresTeacher: true
    }
  },
  {
    // `hidden` keeps this out of the sidebar while leaving the crumb
    // working — see the note on `RouteMeta.hidden`.
    //
    // `guestOnly`（2026-10-04）是防御而不是装饰：认证守卫"统一要求登录"
    // 的写法会把这一页自己也拦下，于是它重定向到自己，浏览器报"重定向
    // 次数过多"。见 `RouteMeta.guestOnly`。
    //
    // 注意它**没有** `mode`：登录页两端共享，学生在学生网址、老师在教师
    // 网址，各自打开同一份组件。
    //
    // `layout: 'auth'`（2026-10-04）：全屏独立页，不渲染侧边栏/顶栏。
    // 见 `RouteMeta.layout` —— 这是"视觉上独立"的那一半，`guestOnly`
    // 管的是"能不能进"，两者都要。
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { title: '登录', hidden: true, guestOnly: true, layout: 'auth' }
  },
  {
    // A page of its own, not a tab or a modal on `/login` (2026-09-25).
    // The two flows share a shape and almost nothing else — see the
    // docstring in `RegisterView.vue` for why one card with two modes
    // was the wrong trade.
    //
    // `hidden` for the same reason as `/login`: a permanent 「注册」 entry
    // in the nav is an invitation to create a second account while
    // already signed in as the first.
    //
    // `layout: 'auth'` 同 `/login`：全屏独立页。
    path: '/register',
    name: 'register',
    component: RegisterView,
    meta: { title: '注册', hidden: true, guestOnly: true, layout: 'auth' }
  },

  // A hand-typed or stale URL should land on the editor, not on a blank
  // page with a console warning. The landing follows the current side,
  // like `/`.
  {
    path: '/:pathMatch(.*)*',
    redirect: () => ({ name: appMode === 'teacher' ? 'teacher' : 'practice' })
  }
]

/** A top-level page that gets a sidebar entry. */
export interface NavRoute {
  /** Route name, used as the `RouterLink` target and the icon key. */
  name: string
  /** Crumb / sidebar label, from the route's `meta.title`. */
  title: string
  /** Sidebar section key, from the route's `meta.group`. */
  group: string
}

/**
 * The sidebar's sections, in order (2026-09-26).
 *
 * 分组按**使用场景**，不是按功能类型。这条区分是这次重构的核心：老师
 * 要找的不是"图表类页面"，是"下节课讲什么"。四组对应的四个时刻：
 *
 *   teacher-overview  备课时先看这一屏：整体怎么样、我现在要不要动手
 *   teacher-analysis  备课时对着数据想教学决策：讲什么、谁需要我
 *   teacher-teaching  上课前要交付的东西：布置作业、复核存疑提交
 *   teacher-people    学期初搭环境，以及随时要查的名册
 *
 * `student` 端的路由不带 `group`，它们的导航仍是一个平铺列表。
 */
export const NAV_GROUPS: { key: string; label: string }[] = [
  { key: 'teacher-overview', label: '概览' },
  { key: 'teacher-analysis', label: '学情' },
  { key: 'teacher-teaching', label: '教学' },
  { key: 'teacher-people', label: '人员' }
]

/** 未分组的页面（学生端）落进哪个 section。空串表示"不显示组标题"。 */
const UNGROUPED = ''

/**
 * The routes a given side actually resolves (2026-09-25).
 *
 * `both` (Electron) sees the whole table. A web entry sees its own side
 * plus the shared pages — the sign-in pages and the redirects carry no
 * `meta.mode`, so they survive the filter. The redirect targets are
 * functions that read `appMode` at navigation time, so they keep working
 * whatever the entry.
 */
export function visibleRoutes(mode: AppMode): RouteRecordRaw[] {
  return routes.filter((r) => mode === 'both' || r.meta?.mode === undefined || r.meta.mode === mode)
}

/**
 * The sidebar's navigation, derived from the *visible* route table.
 *
 * "Top-level page" is expressed as *has a title, no parent, and not
 * `hidden`* rather than as a second hardcoded list, so adding a page
 * means adding one route — not a route plus a nav entry that can drift
 * from it. The redirect and the catch-all carry no `meta.title`,
 * `/problems/:id` has a parent, and `/login` is `hidden`, which is
 * exactly the three distinctions wanted. The side filter (see
 * `visibleRoutes`) is what keeps a student's sidebar free of teacher
 * entries in the split web entries.
 */
export function navRoutesFor(mode: AppMode): NavRoute[] {
  return visibleRoutes(mode)
    .filter(
      (r) =>
        typeof r.name === 'string' && r.meta?.title && r.meta.parent === undefined && !r.meta.hidden
    )
    .map((r) => ({
      name: r.name as string,
      title: r.meta!.title,
      group: r.meta!.group ?? UNGROUPED
    }))
}

/** One rendered section of the sidebar. */
export interface NavSection {
  /** Group key, or `''` for an ungrouped list. */
  key: string
  /** Heading text. Empty means "render no heading" (the student side). */
  label: string
  items: NavRoute[]
}

/**
 * The sidebar's sections, in {@link NAV_GROUPS} order.
 *
 * **没有 `group` 的页面落进一个无标题的 section**，这是学生端需要的行为：
 * 它的六个页面对等的，加标题只会给它们一个并不存在的层次。所以"没分组"
 * 不等于"漏了分组"，它是一个合法形态；`AppSidebar.spec` 钉住了这一点。
 *
 * 顺序取自 `NAV_GROUPS`，组内顺序取自路由表——**不在这里重排**，理由和
 * 页面里那些表格一样：第二次排序是对"什么重要"的第二个意见。
 *
 * 没有匹配路由的组会被丢掉而不是渲染成空标题：一个空 section 是噪音，
 * 而且它在 Electron（`both`）和 web 单端下会表现不一致。
 */
export function navSectionsFor(mode: AppMode): NavSection[] {
  const items = navRoutesFor(mode)
  return NAV_GROUPS.map((g) => ({
    key: g.key,
    label: g.label,
    items: items.filter((i) => i.group === g.key)
  }))
    .filter((s) => s.items.length > 0)
    .concat(
      items.some((i) => i.group === UNGROUPED)
        ? [{ key: UNGROUPED, label: '', items: items.filter((i) => i.group === UNGROUPED) }]
        : []
    )
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: visibleRoutes(appMode),
  /**
   * Reset the window's scroll position on navigation.
   *
   * Mostly a no-op today: the shell is `overflow: hidden` and each page
   * owns its own scroll container, so there is usually nothing to reset.
   * It is kept because the failure it prevents is silent — Vue Router's
   * default is to restore the previous position, so if a page ever does
   * grow the document, a half-scrolled new page reads as a rendering bug
   * rather than as a remembered offset.
   *
   * Note this does *not* reset a page's internal scroll container: going
   * from `/problems/12` to `/problems/13` reuses the same component
   * instance, so the statement panel keeps its scroll position. Fixing
   * that belongs to the page that owns the container.
   */
  scrollBehavior: () => ({ top: 0 })
})

/**
 * 全局守卫由 `main.ts` 通过 {@link installGuards} 安装，不放在本文件：
 * `AppSidebar.spec` 这类测试 mock 了 `vue-router`，`createRouter` 返回的
 * 对象没有 `beforeEach`，守卫若在此注册会让组件测试整文件挂掉。
 *
 * 两个守卫的代码与安装顺序在 `./guards/`：`guards/entry.ts`（入口即身份）
 * 先跑，`guards/auth.ts`（这一页你能不能进）后跑。顺序写在那里。
 */
