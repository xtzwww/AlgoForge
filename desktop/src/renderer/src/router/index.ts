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
import Practice from '../views/Practice.vue'
import ProblemPracticeView from '../views/ProblemPracticeView.vue'
import ProblemListView from '../views/ProblemListView.vue'
import MistakesView from '../views/MistakesView.vue'
import ProfileView from '../views/ProfileView.vue'
import PlanView from '../views/PlanView.vue'
import TeacherView from '../views/TeacherView.vue'
import TeacherClassesView from '../views/TeacherClassesView.vue'
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
  // and free-run mode is the state with no problem bound to it.
  { path: '/', redirect: { name: 'practice' } },

  {
    path: '/practice',
    name: 'practice',
    component: Practice,
    meta: { title: '练习' }
  },
  {
    // Where a student lands after signing in (PU-13). Not role-gated:
    // a teacher opening it sees the same list, which is empty for them
    // because they have joined no class — an honest answer, and one
    // that needs no redirect to explain.
    path: '/assignments',
    name: 'assignments',
    component: AssignmentsView,
    meta: { title: '我的作业' }
  },
  {
    path: '/problems',
    name: 'problems',
    component: ProblemListView,
    meta: { title: '题库' }
  },
  {
    // `(\\d+)` keeps this from swallowing anything else under /problems
    // and, more usefully, guarantees the id is numeric before it reaches
    // the API client — a non-numeric id would otherwise be sent as a path
    // segment and come back as a 422 the UI has to explain.
    path: '/problems/:id(\\d+)',
    name: 'problem',
    component: ProblemPracticeView,
    props: (route) => ({ problemId: Number(route.params.id) }),
    meta: { title: '题目', parent: 'problems' }
  },
  {
    path: '/mistakes',
    name: 'mistakes',
    component: MistakesView,
    meta: { title: '错题本' }
  },
  {
    path: '/profile',
    name: 'profile',
    component: ProfileView,
    meta: { title: '画像' }
  },
  {
    path: '/plan',
    name: 'plan',
    component: PlanView,
    meta: { title: '复习' }
  },
  {
    // Deliberately *not* role-gated in the route table. The console ships
    // in the same bundle the students use, so "the student's build has no
    // link to it" was never the boundary — the server's 403 is, and every
    // teacher route raises it (see `app/api/teacher.py`). The page itself
    // renders a login form for everybody else, which is a convenience
    // rather than a gate.
    path: '/teacher',
    name: 'teacher',
    component: TeacherView,
    meta: { title: '学情看板' }
  },
  {
    // A top-level page rather than a child of `/teacher`, because a
    // teacher arrives here to set up a class *before* there is any
    // learning data to look at — it is not a drill-down from the
    // console, it is the step that makes the console meaningful.
    // Not role-gated here for the same reason `/teacher` is not: the
    // server refuses the calls, and the page explains itself.
    path: '/teacher/classes',
    name: 'teacher-classes',
    component: TeacherClassesView,
    meta: { title: '班级管理' }
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
  // page with a console warning.
  { path: '/:pathMatch(.*)*', redirect: { name: 'practice' } }
]

/** A top-level page that gets a sidebar entry. */
export interface NavRoute {
  /** Route name, used as the `RouterLink` target and the icon key. */
  name: string
  /** Crumb / sidebar label, from the route's `meta.title`. */
  title: string
}

/**
 * The sidebar's navigation, derived from the route table.
 *
 * "Top-level page" is expressed as *has a title, no parent, and not
 * `hidden`* rather than as a second hardcoded list, so adding a page
 * means adding one route — not a route plus a nav entry that can drift
 * from it. The redirect and the catch-all carry no `meta.title`,
 * `/problems/:id` has a parent, and `/login` is `hidden`, which is
 * exactly the three distinctions wanted.
 */
export const navRoutes: NavRoute[] = routes
  .filter(
    (r) =>
      typeof r.name === 'string' &&
      r.meta?.title && r.meta.parent === undefined && !r.meta.hidden
  )
  .map((r) => ({ name: r.name as string, title: r.meta!.title }))

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
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
