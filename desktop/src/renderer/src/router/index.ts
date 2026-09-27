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
import Practice from '../views/Practice.vue'
import ProblemPracticeView from '../views/ProblemPracticeView.vue'
import ProblemListView from '../views/ProblemListView.vue'
import MistakesView from '../views/MistakesView.vue'
import ProfileView from '../views/ProfileView.vue'
import PlanView from '../views/PlanView.vue'
import TeacherOverviewView from '../views/teacher/TeacherOverviewView.vue'
import TeacherAnalysisView from '../views/teacher/TeacherAnalysisView.vue'
import TeacherWorkView from '../views/teacher/TeacherWorkView.vue'
import TeacherReviewView from '../views/teacher/TeacherReviewView.vue'
import TeacherRosterView from '../views/teacher/TeacherRosterView.vue'
import TeacherProblemsView from '../views/teacher/TeacherProblemsView.vue'
import AssignmentsView from '../views/AssignmentsView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'

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
  }
}

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
    meta: { title: '练习', mode: 'student' }
  },
  {
    // Where a student lands after signing in (PU-13). Not role-gated:
    // a teacher opening it sees the same list, which is empty for them
    // because they have joined no class — an honest answer, and one
    // that needs no redirect to explain.
    path: '/assignments',
    name: 'assignments',
    component: AssignmentsView,
    meta: { title: '我的作业', mode: 'student' }
  },
  {
    path: '/problems',
    name: 'problems',
    component: ProblemListView,
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
    meta: { title: '错题本', mode: 'student' }
  },
  {
    path: '/profile',
    name: 'profile',
    component: ProfileView,
    meta: { title: '画像', mode: 'student' }
  },
  {
    path: '/plan',
    name: 'plan',
    component: PlanView,
    meta: { title: '复习', mode: 'student' }
  },
  {
    // Deliberately *not* role-gated in the route table. The console ships
    // in the same bundle the students use, so "the student's build has no
    // link to it" was never the boundary — the server's 403 is, and every
    // teacher route raises it (see `app/api/teacher.py`). The page itself
    // renders a signpost for everybody else, which is a convenience
    // rather than a gate.
    //
    // The web build's *side split* is a different thing from a role gate:
    // `mode: 'teacher'` removes this route from the student entry, so the
    // student address cannot even resolve it. Electron (`both`) and a
    // teacher account on the student entry still behave as above.
    //
    // **教学概览是教师入口的落点。** 它曾是那个 1600 行的 `/teacher`
    // 学情看板；2026-09-26 拆成五个面板后，名字留给这个只管"我现在要
    // 不要做点什么"的首屏（见 `TeacherOverviewView` 的文档）。
    path: '/teacher',
    name: 'teacher',
    component: TeacherOverviewView,
    meta: { title: '教学概览', mode: 'teacher', group: 'teacher-overview' }
  },
  {
    // 下节课讲什么 + 谁需要我。曾经的 `/teacher` 主体。
    // `?student=<id>` 是一次性意图（概览页点人名进来），打开即消费掉，
    // 理由见 `TeacherAnalysisView` 的文档。
    path: '/teacher/analysis',
    name: 'teacher-analysis',
    component: TeacherAnalysisView,
    meta: { title: '学情分析', mode: 'teacher', group: 'teacher-analysis' }
  },
  {
    // 作业 + AI 使用记录 + 成绩单。
    path: '/teacher/work',
    name: 'teacher-work',
    component: TeacherWorkView,
    meta: { title: '作业', mode: 'teacher', group: 'teacher-teaching' }
  },
  {
    // 存疑提交独立成屏（2026-09-26）：它是批阅型事务，要一条条处理完，
    // 中途会切走再回来——夹在概览的一串数字里，每次滚过去都看到它、
    // 每次都没处理完。
    path: '/teacher/review',
    name: 'teacher-review',
    component: TeacherReviewView,
    meta: { title: '存疑提交', mode: 'teacher', group: 'teacher-teaching' }
  },
  {
    // 名册 + 班级（2026-09-26 合并）。原先这是两条侧边栏项，但它们读写
    // 同一批数据，任何一次修改之后两边都要刷新——分在两地，"移出班级"
    // 之后名册上的班级标签就过期了，而老师不会知道要回去刷一下。
    path: '/teacher/roster',
    name: 'teacher-roster',
    component: TeacherRosterView,
    meta: { title: '名册与班级', mode: 'teacher', group: 'teacher-people' }
  },
  {
    // 题库管理（2026-09-27）。与作业同一分组：备课建题 → 布置作业是
    // 一条链，老师不会在"建题"和"布置"之间切换分组。
    path: '/teacher/problems',
    name: 'teacher-problems',
    component: TeacherProblemsView,
    meta: { title: '题库', mode: 'teacher', group: 'teacher-teaching' }
  },
  {
    // `hidden` keeps this out of the sidebar while leaving the crumb
    // working — see the note on `RouteMeta.hidden`.
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { title: '登录', hidden: true }
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
    path: '/register',
    name: 'register',
    component: RegisterView,
    meta: { title: '注册', hidden: true }
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
 * 入口即身份的全局守卫由 `main.ts` 通过 {@link installEntryGuard} 安装，
 * 不放在本文件：`AppSidebar.spec` 这类测试 mock 了 `vue-router`，
 * `createRouter` 返回的对象没有 `beforeEach`，守卫若在此注册会让
 * 组件测试整文件挂掉。逻辑见 `./entryGuard.ts`。
 */
