// @vitest-environment jsdom
/**
 * AppTopbar 的组件测试。
 *
 * 它管三件事：面包屑、抽屉开关、环境徽标。三者坏掉的样子都不响：
 *   - **面包屑**少一级，用户看到的是一条没有起点的路径；多一级则会出现
 *     一个不是页面的段（所以链条是按 `meta.parent` 走，不是切路径）。
 *   - **环境徽标**是这个壳唯一说出「你这里做不到什么」的地方。它一旦少报
 *     一条，用户就会去找一个根本不存在的能力；反过来，桌面版被报成有
 *     限制，用户会白白绕路。
 *   - **抽屉开关**的 `aria-expanded` 掉了，读屏用户就不知道菜单是开是关。
 *
 * `vue-router` 与 `runtime` 被换成替身：这里要测的是「给这条路和这个壳，
 * 顶栏画什么」，不是路由库或运行时探测本身。`usePageCrumb` **不换**——它是
 * 本仓库自己的模块，而且「页面发布的标签压过 `meta.title`」正是要靠真模块
 * 才能验的那条链路，用 `publishPageCrumb` 推一个值进去即可。
 *
 * 钉住的九条：
 *   1. 无父路由时只有一条面包屑，且是**当前页**（`aria-current`，不是链接）；
 *   2. 有父路由时按 `meta.parent` 走出一级，父级是链接、自己仍是当前页；
 *   3. 页面发布的 crumb 压过 `meta.title`（`/problems/:id` 靠这条才有题名）；
 *   4. 父路由没写 title 时退回用**路由名**，不能变成空字符串；
 *   5. 网页版才画品牌——Electron 的窗口标题已经写了；
 *   6. 徽标 tooltip 按能力拼：缺能力逐条列，全都有就说「功能完整」；
 *   7. 抽屉按钮 emit `toggle-drawer`，`aria-expanded` 跟随 props；
 *   8. **未登录且会话已就绪时给出「登录」入口**——这是登录页唯一的入口，
 *      少了它那个页面就没人到得了；
 *   9. 已登录时画姓名与角色，登出后跳登录页。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'
import AppTopbar from './AppTopbar.vue'
import { publishPageCrumb } from '../composables/usePageCrumb'
import type { Runtime } from '../runtime'

/** 一条路由替身：组件只读 `meta.title` 与 `meta.parent`。 */
interface RouteLike {
  meta: { title?: string; parent?: string }
}

/**
 * 可变的替身状态。放在 `vi.hoisted` 里，因为 `vi.mock` 的工厂会被提升到所有
 * import 之前，直接引用外层 `const` 会报 `Cannot access before initialization`。
 * 里面全是普通值（不是 ref），所以不需要 import 任何东西——`vi.hoisted`
 * 的时机早于 import，那里拿不到 `ref`。
 */
const env = vi.hoisted(() => ({
  route: { meta: {} } as { meta: { title?: string; parent?: string } },
  parents: {} as Record<string, { meta: { title?: string; parent?: string } }>,
  kind: 'web' as 'web' | 'electron',
  displayName: '网页版',
  windowReveal: false,
  nativeFileAccess: false,
  /** `router.replace` 的去向，登出后要跳登录页。 */
  replaced: [] as unknown[]
}))

/**
 * 会话替身。
 *
 * 同样放在 `vi.hoisted` 里：`vi.mock` 的工厂被提升到 import 之前，直接引用
 * 外层 `const` 会报 `Cannot access before initialization`。
 *
 * `sessionReady` 必须可控，因为**它和 `isLoggedIn` 不是一回事**：启动时那次
 * 「这个 token 还算不算数」的往返没回来之前，两者都是 false，而那时候画
 * 「登录」会让已经登录的人以为会话掉了。这条区分是这段代码里唯一容易写错的
 * 地方，所以要能单独摆出那个状态。
 *
 * **`state` 里的字段必须是 ref，不能是普通布尔。** 第一版写成了普通属性，
 * 于是 `computed(() => session.ready)` 没有任何响应式依赖：它在**第一个**
 * 挂载的用例里读到 `false` 就永久缓存，后面用例再赋值也不重算。症状极具
 * 误导性——单独跑那个用例是绿的，整个文件一起跑就红。
 *
 * `vi.hoisted` 里拿不到 `ref`（它的执行时机早于 import），所以这里只放一个
 * 容器，由 `vi.mock` 的工厂把真的 ref 填进去。`logoutCalls` 是普通计数，
 * 没有响应式需求。
 */
const session = vi.hoisted(() => ({
  state: null as null | {
    ready: Ref<boolean>
    loggedIn: Ref<boolean>
    teacher: Ref<boolean>
    displayName: Ref<string>
  },
  logoutCalls: 0
}))

vi.mock('../api/session', async () => {
  const { computed, ref } = await import('vue')
  const ready = ref(false)
  const loggedIn = ref(false)
  const teacher = ref(false)
  const displayName = ref('张三')
  session.state = { ready, loggedIn, teacher, displayName }

  return {
    sessionReady: computed(() => ready.value),
    isLoggedIn: computed(() => loggedIn.value),
    isTeacher: computed(() => teacher.value),
    sessionUser: computed(() =>
      loggedIn.value
        ? { id: 1, username: 'zhangsan', display_name: displayName.value, role: 'student' }
        : null
    ),
    logout: async () => {
      session.logoutCalls += 1
    }
  }
})

/** 摆会话状态；不传的字段回默认值。 */
function setSession(next: {
  ready?: boolean
  loggedIn?: boolean
  teacher?: boolean
  displayName?: string
}): void {
  const s = session.state
  if (s === null) throw new Error('session mock 还没被加载——先 import 一个用它的人')
  s.ready.value = next.ready ?? false
  s.loggedIn.value = next.loggedIn ?? false
  s.teacher.value = next.teacher ?? false
  s.displayName.value = next.displayName ?? '张三'
}

vi.mock('vue-router', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    RouterLink: defineComponent({
      name: 'RouterLink',
      props: { to: { type: [String, Object], required: false } },
      setup:
        (_props, { slots }) =>
        () =>
          h('a', { class: 'router-link' }, slots.default?.())
    }),
    // 只实现组件真的会用到的那一小块，所以这两处必须过 `as unknown as`：
    // 不用它就只能写 `any`，而 `no-explicit-any` 在 CI 里是 error。
    useRoute: () => env.route as unknown as RouteLocationNormalizedLoaded,
    useRouter: () =>
      ({
        resolve: (loc: { name: string }) => env.parents[loc.name] ?? { meta: {} },
        replace: async (to: unknown) => {
          env.replaced.push(to)
        }
      }) as unknown as Router
  }
})

vi.mock('../runtime/mode', () => ({
  // 本文件测的是「环境能力」而不是端名；`appMode` 在真实构建里是加载时
  // 常量（runtime/mode.ts），mock 成 both 让端名徽标不参与这里的断言，
  // 端名本身的逻辑由 AppSidebar.spec.ts 的可见路由用例覆盖。
  appMode: 'both'
}))

vi.mock('../runtime', () => ({
  // getter 而不是固定值：用例改了 `env` 之后，重新挂载就能读到新值。
  runtime: {
    get kind() {
      return env.kind
    },
    get displayName() {
      return env.displayName
    },
    get capabilities() {
      return { windowReveal: env.windowReveal, nativeFileAccess: env.nativeFileAccess }
    },
    getSidecarInfo: () => Promise.resolve(null),
    onReveal: () => {},
    onSidecarInfoChanged: () => {}
  } satisfies Runtime
}))

/** 每个用例先复位替身，免得互相污染。 */
function resetEnv(): void {
  env.route = { meta: {} }
  env.parents = {}
  env.kind = 'web'
  env.displayName = '网页版'
  env.windowReveal = false
  env.nativeFileAccess = false
  env.replaced = []
  setSession({})
  session.logoutCalls = 0
  publishPageCrumb(null)
}

/** 设一条路由，并可给父级路由（按 `meta.parent` 的名字查）。 */
function setRoute(route: RouteLike, parents: Record<string, RouteLike> = {}): void {
  env.route = route
  env.parents = parents
}

function mountTopbar(props: { drawerOpen?: boolean } = {}): VueWrapper {
  return mount(AppTopbar, { props })
}

/** 面包屑上所有可见的标签（不含分隔符）。 */
function crumbLabels(w: VueWrapper): string[] {
  return w.findAll('.router-link, [aria-current="page"]').map((n) => n.text())
}

/** 环境徽标的 tooltip。
 *
 * 用 `span.cursor-help` 精确命中环境徽标：顶栏左上角现在多了主题切换
 * 控件（ThemeSwitcher），它的触发按钮也带 `title`，`[title]` 会先命中
 * 它而不是徽标。徽标是带 `cursor-help` 的 span，可与其他带 title 的元素
 * 区分开。
 */
function badgeTitle(w: VueWrapper): string | undefined {
  return w.find('span.cursor-help').attributes('title')
}

afterEach(() => {
  resetEnv()
})

describe('AppTopbar', () => {
  it('无父路由时只有一条面包屑，且是当前页而不是链接', () => {
    setRoute({ meta: { title: '题库' } })

    const w = mountTopbar()
    expect(crumbLabels(w)).toEqual(['题库'])
    expect(w.findAll('.router-link')).toHaveLength(0)
    expect(w.find('[aria-current="page"]').text()).toBe('题库')
  })

  it('有父路由时按 meta.parent 走出一级，父级是链接', () => {
    setRoute(
      { meta: { title: '题目详情', parent: 'problems' } },
      {
        problems: { meta: { title: '题库' } }
      }
    )

    const w = mountTopbar()
    expect(crumbLabels(w)).toEqual(['题库', '题目详情'])
    expect(w.find('.router-link').text()).toBe('题库')
    expect(w.find('[aria-current="page"]').text()).toBe('题目详情')
    // 两级之间要有一个分隔符，否则读起来是两个并排的词。
    expect(w.find('nav').text()).toContain('/')
  })

  it('页面发布的 crumb 压过 meta.title', () => {
    setRoute({ meta: { title: '题目详情' } })
    publishPageCrumb('A + B Problem')

    expect(crumbLabels(mountTopbar())).toEqual(['A + B Problem'])
  })

  it('父路由没写 title 时退回路由名，不能变成空字符串', () => {
    setRoute({ meta: { title: '题目详情', parent: 'problems' } }, { problems: { meta: {} } })

    expect(crumbLabels(mountTopbar())).toEqual(['problems', '题目详情'])
  })

  it('网页版才画品牌——Electron 的窗口标题已经写了', () => {
    expect(mountTopbar().text()).toContain('AlgoForge')

    env.kind = 'electron'
    env.displayName = '桌面版'
    const desktop = mountTopbar()
    expect(desktop.text()).not.toContain('AlgoForge')
    // 但环境徽标两个壳都有。
    expect(desktop.text()).toContain('桌面版')
  })

  it('徽标 tooltip 按能力拼：缺能力逐条列，全都有就说功能完整', () => {
    const webTitle = badgeTitle(mountTopbar())
    expect(webTitle).toContain('网页版')
    expect(webTitle).toContain('以下功能不可用')
    expect(webTitle).toContain('无法直接读写本机文件')
    expect(webTitle).toContain('没有启动遮罩')

    env.kind = 'electron'
    env.displayName = '桌面版'
    env.windowReveal = true
    env.nativeFileAccess = true
    expect(badgeTitle(mountTopbar())).toBe('桌面版：功能完整')
  })

  it('未登录且会话就绪时给出「登录」入口', () => {
    // 登录页不在导航里（`meta.hidden`），所以这个链接是它**唯一**的入口。
    // 少了它，页面还在，只是没人到得了——而且不报错。
    setSession({ ready: true, loggedIn: false })

    const entry = mountTopbar()
      .findAll('.router-link')
      .find((n) => n.text() === '登录')
    expect(entry).toBeDefined()
  })

  it('会话还没就绪时不画「登录」——那会被读成会话掉了', () => {
    // 启动时那次「token 还算不算数」的往返没回来之前，`isLoggedIn` 也是
    // false。这时候画「登录」，已登录的人会先看到一个登录按钮再看到自己
    // 的名字，像是被登出了一次。
    setSession({ ready: false, loggedIn: false })

    expect(mountTopbar().findAll('.router-link').map((n) => n.text())).not.toContain('登录')
  })

  it('已登录时画姓名与角色，登出后跳登录页', async () => {
    setSession({ ready: true, loggedIn: true, teacher: true, displayName: '李老师' })

    const w = mountTopbar()
    expect(w.text()).toContain('李老师')
    expect(w.text()).toContain('教师')
    // 已登录就不该再有「登录」入口。
    expect(w.findAll('.router-link').map((n) => n.text())).not.toContain('登录')

    const btn = w.findAll('button').find((n) => n.text() === '登出')
    expect(btn).toBeDefined()
    await btn!.trigger('click')

    expect(session.logoutCalls).toBe(1)
    expect(env.replaced).toEqual([{ name: 'login' }])
  })

  it('抽屉按钮 emit toggle-drawer，aria-expanded 跟随 props', async () => {
    const closed = mountTopbar({ drawerOpen: false })
    const btn = closed.find('button[aria-label="切换导航菜单"]')
    expect(btn.attributes('aria-expanded')).toBe('false')

    await btn.trigger('click')
    expect(closed.emitted('toggle-drawer')).toHaveLength(1)

    const open = mountTopbar({ drawerOpen: true })
    expect(open.find('button[aria-label="切换导航菜单"]').attributes('aria-expanded')).toBe('true')
  })
})
