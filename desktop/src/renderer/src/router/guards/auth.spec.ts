/**
 * 认证守卫（2026-10-04）。
 *
 * 这是本次重构里**唯一**决定"能不能进这一页"的地方，所以它的行为就是
 * 全项目的鉴权行为。改造前同一件事有八个副本（六个面板 + `TeacherGate`
 * + `AppTopbar`），测八遍也不会有两遍测出同样的结论——现在改一处、
 * 测一处。
 *
 * 为什么不用真 router：本文件的目的是钉"**守卫函数对给定的 (route,
 * session) 给出什么答案**"，而真 router 会把整个路由表连同急加载的视图
 * （Monaco、ECharts）拖进来，还需要 jsdom 里跑完整的导航。这里只要一个
 * 有 `beforeEach` 的假 router 把守卫记下来，然后手工喂入 route 对象：
 * 每个用例因此是"输入 → 输出"的一句话，失败时不用先读懂一条导航链。
 *
 * **跑在 `node` 环境**（不写 `@vitest-environment jsdom`）：守卫不碰
 * DOM，`router/landing.ts` 也只 import 类型。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router, RouteLocationNormalized } from 'vue-router'
import { installAuthGuard } from './auth'
import { needsAuth, needsTeacher, usesAuthLayout } from '../meta'

/**
 * 会话替身。`vi.hoisted` 是因为 `vi.mock` 的工厂被提升到 import 之前，
 * 直接引用外层 `const` 会报 `Cannot access before initialization`。
 *
 * **值必须是 ref，不能是普通布尔。**守卫在每次导航时读 `.value`，普通
 * 属性虽然也能读，但用例之间共享同一个对象、赋值后无法体现"导航发生
 * 在赋值之后"这件事；更要紧的是与真模块的读法（`.value`）保持一致，
 * 否则替身通过、真代码崩。
 */
const session = vi.hoisted(() => ({
  ready: { value: false },
  loggedIn: { value: false },
  teacher: { value: false }
}))

vi.mock('../../api/session', () => ({
  sessionReady: {
    get value() {
      return session.ready.value
    }
  },
  isLoggedIn: {
    get value() {
      return session.loggedIn.value
    }
  },
  isTeacher: {
    get value() {
      return session.teacher.value
    }
  }
}))

/**
 * 入口固定成 `student`：入口有关的判断由 `guards/entry.ts` 与
 * `landing.spec.ts` 负责，本文件只测 auth 那一层。(`appMode` 在真构建里
 * 是加载时常量，所以只能整体 mock。)
 */
vi.mock('../../runtime/mode', () => ({ appMode: 'student' }))

/**
 * 一个只实现 `beforeEach` 的假 router。
 *
 * 完整的 `Router` 类型上有一百多个成员，写一个全部满足它的对象的代价
 * 远大于价值，所以过 `as unknown as Router`——这与项目里其它测试对
 * `useRouter()` 替身的处理方式一致（不用它就只能写 `any`，而
 * `no-explicit-any` 在 CI 里是 error）。
 */
function fakeRouter(): { router: Router; guard: (to: unknown) => unknown } {
  let captured: ((to: unknown) => unknown) | null = null
  const router = {
    beforeEach: (fn: (to: unknown) => unknown) => {
      captured = fn
    }
  } as unknown as Router
  installAuthGuard(router)
  if (captured === null) throw new Error('installAuthGuard 没有注册守卫')
  return { router, guard: captured }
}

/**
 * 一个只带守卫真正会读的那几个字段的 route。
 *
 * `to.meta` 是唯一有内容的字段——守卫不读 path、query、params，它读的是
 * 路由表上的**声明**。这也正是这次重构的意义：判断的依据是声明，不是
 * 当前在哪。
 */
function route(meta: Record<string, unknown>, name = 'somewhere'): RouteLocationNormalized {
  return { name, meta } as unknown as RouteLocationNormalized
}

/** 摆会话状态。不传的字段回 `false`。 */
function setSession(next: { ready?: boolean; loggedIn?: boolean; teacher?: boolean }): void {
  session.ready.value = next.ready ?? false
  session.loggedIn.value = next.loggedIn ?? false
  session.teacher.value = next.teacher ?? false
}

let guard: (to: unknown) => unknown

beforeEach(() => {
  guard = fakeRouter().guard
  setSession({})
})

describe('meta 判定（规则本身）', () => {
  it('requiresAuth 与 requiresTeacher 任一为真即需要登录', () => {
    expect(needsAuth({})).toBe(false)
    expect(needsAuth({ requiresAuth: true })).toBe(true)
    expect(needsAuth({ requiresTeacher: true })).toBe(true)
  })

  it('只有 requiresTeacher 才算"要教师角色"', () => {
    // 注意参数类型：`needsTeacher` 只接受 `requiresTeacher`，所以这里
    // 不能顺手塞一个 `requiresAuth` 来"证明它不算教师"。要表达"登录了
    // 但不要求角色"，用空 meta——`requiresAuth` 由上一个用例覆盖。
    expect(needsTeacher({})).toBe(false)
    expect(needsTeacher({ requiresTeacher: true })).toBe(true)
  })
})

describe('layout 判定（外壳选择）', () => {
  it('只有显式 layout:"auth" 才是全屏认证页', () => {
    // 缺省必须是 `false`：App.vue 用它决定**要不要渲染侧边栏**，全项目
    // 只有登录/注册两条路由该走全屏，所以"没写"必须等于"保留常规外壳"。
    // 反过来的话，任何一条忘了写 layout 的路由都会突然失去导航。
    expect(usesAuthLayout({})).toBe(false)
    expect(usesAuthLayout({ layout: 'app' })).toBe(false)
    expect(usesAuthLayout({ layout: 'auth' })).toBe(true)
  })

  it('layout 与 guestOnly 是两个独立的问题', () => {
    // 钉住这个区分：`guestOnly` 管"能不能进"，`layout` 管"进去以后长
    // 什么样"。合成一个字段会让"登录页可以全屏"和"可以匿名访问"绑死，
    // 而它们是两个决定——合了就再也分不开了。
    //
    // 两个判定函数各自只接受自己那个字段，所以这里分开调：一个"只有
    // guestOnly"的 meta 对两者都不产生效果。
    expect(usesAuthLayout({ layout: undefined })).toBe(false)
    expect(needsAuth({ requiresAuth: undefined })).toBe(false)
  })
})

describe('铁律 1：会话未就绪一律放行', () => {
  it('sessionReady 为 false 时，受保护页面也放行', () => {
    // 这是整套逻辑里最重要的一条回归。`restoreSession()` 的往返没回来
    // 之前，`isLoggedIn` 对**所有人**都是 false——包括正被恢复的教师。
    // 在这里判"未登录 → 去登录页"，表现就是**每次刷新都被弹走**，而刷新
    // 恰是恢复会话的正常路径，于是永远登不进来。
    //
    // 反过来（未登录的人看到一帧受保护页面）只是页面显示加载态——它拿
    // 不到数据。两种误判的代价差着数量级，所以选放行。
    setSession({ ready: false, loggedIn: false, teacher: false })
    expect(guard(route({ requiresTeacher: true }))).toBe(true)
    expect(guard(route({ requiresAuth: true }))).toBe(true)
  })
})

describe('铁律 2：认证页永不参与判定', () => {
  it('guestOnly 的页面在未登录时也放行（否则会重定向到自己）', () => {
    setSession({ ready: true, loggedIn: false })
    expect(guard(route({ guestOnly: true }, 'login'))).toBe(true)
    expect(guard(route({ guestOnly: true }, 'register'))).toBe(true)
  })

  it('guestOnly 的页面即使被错误地标了 requiresAuth 也放行', () => {
    // 防御性：万一有人给登录页补了 `requiresAuth: true`（看起来"更安全"），
    // `guestOnly` 优先级更高，仍然是可达的。没有这一条，那一个字段就会
    // 把登录页变成不可达——用户永远进不去，且没有任何报错。
    setSession({ ready: true, loggedIn: false })
    expect(guard(route({ guestOnly: true, requiresAuth: true }, 'login'))).toBe(true)
  })
})

describe('铁律 3：只拦声明过的页', () => {
  it('没写 requiresAuth / requiresTeacher 的页面，未登录也放行', () => {
    // 练习、题库、题目详情都是公开的：游客可以先看题再决定要不要注册。
    // "不拦"是**默认**而不是"忘了写"，这一点由这个用例钉住。
    setSession({ ready: true, loggedIn: false })
    expect(guard(route({}))).toBe(true)
    expect(guard(route({ title: '练习', mode: 'student' }))).toBe(true)
  })
})

describe('受保护页面 × 会话状态', () => {
  it('游客访问 requiresAuth 页 → 去登录页', () => {
    setSession({ ready: true, loggedIn: false })
    expect(guard(route({ requiresAuth: true }, 'assignments'))).toEqual({ name: 'login' })
  })

  it('游客访问 requiresTeacher 页 → 去登录页', () => {
    setSession({ ready: true, loggedIn: false })
    expect(guard(route({ requiresTeacher: true }, 'teacher'))).toEqual({ name: 'login' })
  })

  it('学生登录后访问 requiresAuth 页 → 放行', () => {
    setSession({ ready: true, loggedIn: true, teacher: false })
    expect(guard(route({ requiresAuth: true }, 'assignments'))).toBe(true)
  })

  it('学生登录后访问教师页 → 回自己的首页，**不是**登录页', () => {
    // 本文件里语义最微妙的一条。人已经登录了，把他弹去登录页会让他以为
    // "我没登录 / 登录坏了"，然后反复重登——而那不会有任何帮助。回首页
    // 是一句诚实的话，且立刻给出一个他能用的地方。
    //
    // 落点名来自 `landingName(appMode='student', 'student')` = assignments。
    setSession({ ready: true, loggedIn: true, teacher: false })
    expect(guard(route({ requiresTeacher: true }, 'teacher'))).toEqual({ name: 'assignments' })
  })

  it('教师登录后访问教师页 → 放行', () => {
    setSession({ ready: true, loggedIn: true, teacher: true })
    expect(guard(route({ requiresTeacher: true }, 'teacher'))).toBe(true)
  })

  it('教师登录后访问学生页（requiresTeacher 未标）→ 放行', () => {
    // 教师打开"我的作业"看到空列表，那是诚实的「你没有加入任何班级」，
    // 不需要一次重定向来解释。所以教师页要角色，学生页不要。
    setSession({ ready: true, loggedIn: true, teacher: true })
    expect(guard(route({ requiresAuth: true }, 'assignments'))).toBe(true)
  })
})

describe('不记原目标（2026-10-04 的决定）', () => {
  it('未登录被拦时回一个裸的登录页，不带 redirect 查询', () => {
    // 登录成功后固定去首页。若将来要改成"回到刚才那一页"，会在这里带
    // `?redirect=`，由 LoginView 消费——注意那会引入"开放重定向"风险，
    // 只接受站内路径。这条用例就是那次改动的哨兵：它一变红，说明有人
    // 开始带原目标了。
    setSession({ ready: true, loggedIn: false })
    const answer = guard(route({ requiresAuth: true }, 'mistakes')) as {
      name: string
      query?: unknown
    }
    expect(answer.name).toBe('login')
    expect(answer.query).toBeUndefined()
  })
})
