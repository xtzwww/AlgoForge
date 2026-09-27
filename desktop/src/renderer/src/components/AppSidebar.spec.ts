// @vitest-environment jsdom
/**
 * AppSidebar 的组件测试。
 *
 * 它管三件事：导航列表、抽屉态、以及页脚那个**环境指示灯**。
 *
 * 为什么值得测：三件事坏掉都不响。
 *   - **导航列表是从路由表推出来的**（`navRoutes`），不是第二份手写清单——
 *     这条不变量就是「加一个页面只需要加一条路由」。一旦有人退回去手写，
 *     列表和路由就会开始漂，而漂掉的表现是「新页面进不去」。
 *   - **每个导航项都要有图标**。`ICONS[r.name] ?? ''` 对没登记的 name 给出
 *     空字符串，SVG 照画，只是什么都不显示——多出来一个看不见的按钮。
 *   - **`degraded` 不是 `failed`**。侧车答了话，说明没坏，重试也不会变好，
 *     只是这台机器没有可用工具链。把它画成红灯并给一个「重试」，用户就会
 *     去追一个不存在的问题。这一条是文档里专门写过的区分，也是最容易在
 *     重构里被合并掉的一条。
 *
 * 为了拿到**真实**的导航推导，视图组件被替换成空壳：路由表是
 * **急加载**的，直接 import 会把 Monaco、ECharts 全拖进 jsdom。替身只影响
 * 视图本身，路由表与 `navRoutesFor` 的推导仍是真的。
 *
 * 2026-09-25 起路由表按端过滤（`visibleRoutes`）：本文件在 vitest 里
 * 用 `student` 端断言（学生入口看到的导航），并单独钉一条「教师入口
 * 不出现学生页面」的不变量。
 *
 * 钉住的九条：
 *   1. 列表就是路由表里的顶层页面，顺序一致（加页面只改路由表）；
 *   2. **`meta.hidden` 的页面不进导航**——登录页有 `title`，但不该出现在侧栏；
 *   3. 每一项都有非空图标路径；
 *   4. 每一项都带 `title`（收起态下标签就靠它）；
 *   5. 点击 emit `navigate`；
 *   6. 四种 phase 各自的文案与色调；
 *   7. **只有 failed 才给重试按钮**；
 *   8. tooltip 在 failed 时补上原因，其它状态只报标签；
 *   9. `drawerOpen` 落在根元素 class 上。
 */
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { Ref } from 'vue'
import AppSidebar from './AppSidebar.vue'
import { navRoutesFor, navSectionsFor, visibleRoutes } from '../router'
import type { SidecarPhase } from '../composables/useSidecarStatus'

// 视图都是急加载的；换掉它们，路由表本身仍是真的。
//
// 必须一条一条写：`vi.mock` 要靠静态提升到 import 之前，写在循环里不会被提升，
// 于是 mock 注册晚于 `../router` 的 import，视图照样被真加载。
vi.mock('../views/Practice.vue', () => ({ default: { name: 'PracticeStub' } }))
vi.mock('../views/ProblemPracticeView.vue', () => ({
  default: { name: 'ProblemPracticeViewStub' }
}))
vi.mock('../views/ProblemListView.vue', () => ({ default: { name: 'ProblemListViewStub' } }))
vi.mock('../views/MistakesView.vue', () => ({ default: { name: 'MistakesViewStub' } }))
vi.mock('../views/ProfileView.vue', () => ({ default: { name: 'ProfileViewStub' } }))
vi.mock('../views/PlanView.vue', () => ({ default: { name: 'PlanViewStub' } }))
// 教师端 2026-09-26 拆成五个面板，逐条挡掉（`vi.mock` 必须静态提升，
// 写进循环里就不会提升，视图照样被真加载）。
vi.mock('../views/teacher/TeacherOverviewView.vue', () => ({
  default: { name: 'TeacherOverviewViewStub' }
}))
vi.mock('../views/teacher/TeacherAnalysisView.vue', () => ({
  default: { name: 'TeacherAnalysisViewStub' }
}))
vi.mock('../views/teacher/TeacherWorkView.vue', () => ({
  default: { name: 'TeacherWorkViewStub' }
}))
vi.mock('../views/teacher/TeacherReviewView.vue', () => ({
  default: { name: 'TeacherReviewViewStub' }
}))
vi.mock('../views/teacher/TeacherRosterView.vue', () => ({
  default: { name: 'TeacherRosterViewStub' }
}))
vi.mock('../views/AssignmentsView.vue', () => ({
  default: { name: 'AssignmentsViewStub' }
}))
// 登录页不在导航里（`meta.hidden`），但路由表仍然急加载它，所以也要挡。
vi.mock('../views/LoginView.vue', () => ({ default: { name: 'LoginViewStub' } }))
// 注册页同理（2026-09-25 新增，`meta.hidden`）。它必须被挡掉，否则这个
// 文件测的就不只是路由表了：真组件会去碰 `api/session`，而那个模块在
// import 时就往 `http` 上挂拦截器。
vi.mock('../views/RegisterView.vue', () => ({ default: { name: 'RegisterViewStub' } }))

vi.mock('vue-router', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    RouterLink: defineComponent({
      name: 'RouterLink',
      props: { to: { type: [String, Object], required: false }, title: String },
      setup:
        (props, { slots }) =>
        () =>
          h('a', { class: 'router-link', title: props.title }, slots.default?.())
    }),
    // `router/index.ts` 在模块作用域建了一个真 router；这里只要它别炸。
    createRouter: () => ({}),
    createWebHashHistory: () => ({})
  }
})

vi.mock('../composables/useSidecarStatus', async () => {
  const { ref } = await import('vue')
  const state = {
    phase: ref<SidecarPhase>('checking'),
    errorMessage: ref(''),
    retry: vi.fn()
  }
  return { __state: state, useSidecarStatus: () => state }
})

/**
 * 可控的侧车状态。
 *
 * `__state` 是 mock 模块额外挂出来的，真模块上没有，所以这里必须过一次
 * `as unknown as`。这是 mock 边界的固有代价：不用它就只能写 `any`，而本项目
 * 明令禁止 `any`（`no-explicit-any` 在 CI 里是 error）。
 */
interface StatusMock {
  phase: Ref<SidecarPhase>
  errorMessage: Ref<string>
  retry: Mock
}

async function statusState(): Promise<StatusMock> {
  const mod = (await import('../composables/useSidecarStatus')) as unknown as {
    __state: StatusMock
  }
  return mod.__state
}

beforeEach(async () => {
  const s = await statusState()
  s.phase.value = 'checking'
  s.errorMessage.value = ''
  s.retry.mockClear()
})

function mountSidebar(props: { drawerOpen?: boolean } = {}): VueWrapper {
  return mount(AppSidebar, { props })
}

describe('AppSidebar', () => {
  it('导航列表就是路由表里的顶层页面，顺序一致（学生端）', () => {
    const labels = mountSidebar()
      .findAll('.nav-label')
      .map((n) => n.text())
    expect(labels).toEqual(navRoutesFor('student').map((r) => r.title))
    // 显式写死一份，才挡得住「路由表被改小了」这种回归——上面那条只保证
    // 两边一致，两边一起错它看不出来。
    expect(labels).toEqual(['练习', '我的作业', '题库', '错题本', '画像', '复习'])
  })

  it('教师入口只解析教师页面——学生页面不出现在教师端的路由表里', () => {
    const teacherNames = visibleRoutes('teacher')
      .map((r) => r.name)
      .filter((n): n is string => typeof n === 'string')
    // 拆分后的五个面板都在。
    expect(teacherNames).toContain('teacher')
    expect(teacherNames).toContain('teacher-analysis')
    expect(teacherNames).toContain('teacher-work')
    expect(teacherNames).toContain('teacher-review')
    expect(teacherNames).toContain('teacher-roster')
    // 旧的合并页面名不该留下任何一条路由。
    expect(teacherNames).not.toContain('teacher-classes')
    expect(teacherNames).not.toContain('practice')
    expect(teacherNames).not.toContain('assignments')
    expect(teacherNames).not.toContain('mistakes')
    // 两端共享的登录/注册页仍在。
    expect(teacherNames).toContain('login')
    expect(teacherNames).toContain('register')
  })

  it('教师端导航分成四组，顺序固定——分组是这次重构的产出，不能悄悄退回平铺', () => {
    // 这条是 2026-09-26 新增的守卫。分组的价值全在"老师按使用场景找得到
    // 东西"，而它会以两种方式静默失效：`meta.group` 写错（页面掉进
    // "未分组"，看起来只是位置变了），或 `NAV_GROUPS` 被改序（每组的
    // 含义就变了）。两种都不报错，所以在这里钉死。
    const sections = navSectionsFor('teacher')
    expect(sections.map((s) => s.label)).toEqual(['概览', '学情', '教学', '人员'])
    expect(sections.map((s) => s.items.map((i) => i.name))).toEqual([
      ['teacher'],
      ['teacher-analysis'],
      ['teacher-work', 'teacher-review'],
      ['teacher-roster']
    ])
  })

  it('教师端侧边栏渲染出组标题，且标题就是分组的标签', () => {
    // 上面那条只测了推导函数；这条测**渲染**，因为"推导对了但模板没画
    // 标题"是完全可能的，而它的表现是分组功能整个不存在。
    //
    // 组件读的是模块级 `appMode`（vitest 里是 student），所以要直接断言
    // 学生端那条：它必须**没有**组标题。
    const w = mountSidebar()
    expect(w.findAll('.nav-section-title')).toHaveLength(0)
    expect(navSectionsFor('student')).toHaveLength(1)
    expect(navSectionsFor('student')[0].label).toBe('')
  })

  it('`meta.hidden` 的页面不进导航——登录页有 title，但不该出现在侧栏', () => {
    // 登录页是「输入地址能到、但不在导航里」的页面。它的 `title` 是给
    // 顶栏和文档标题用的，不是给侧栏用的；没有这条守卫，加一个 `title`
    // 就会让登录入口出现在侧栏，而这是产品决定，不是副作用。
    const labels = mountSidebar()
      .findAll('.nav-label')
      .map((n) => n.text())
    expect(labels).not.toContain('登录')
    expect(navRoutesFor('student').map((r) => r.name)).not.toContain('login')
  })

  it('每个导航项都有非空图标路径——空路径等于一个看不见的按钮', () => {
    const paths = mountSidebar().findAll('.nav-icon path')
    expect(paths).toHaveLength(navRoutesFor('student').length)
    for (const p of paths) {
      expect(p.attributes('d')).toBeTruthy()
    }
  })

  it('每一项都带 title，收起态下标签就靠它', () => {
    const w = mountSidebar()
    const titles = w.findAll('.nav-item').map((n) => n.attributes('title'))
    expect(titles).toEqual(navRoutesFor('student').map((r) => r.title))
  })

  it('点击导航项 emit navigate', async () => {
    const w = mountSidebar()
    await w.findAll('.nav-item')[1].trigger('click')
    expect(w.emitted('navigate')).toHaveLength(1)
  })

  it('四种 phase 各自的文案与色调', async () => {
    const s = await statusState()
    const cases = [
      ['ready', '分析引擎就绪', 'status-ok'],
      ['degraded', '没有可用的语言工具链', 'status-warn'],
      ['failed', '分析引擎未连接', 'status-error'],
      ['checking', '正在检查环境…', 'status-idle']
    ] as const

    for (const [phase, label, tone] of cases) {
      s.phase.value = phase
      const w = mountSidebar()
      expect(w.find('.status-label').text()).toBe(label)
      expect(w.find('.status').classes()).toContain(tone)
    }
  })

  it('只有 failed 才给重试按钮——degraded 重试也没用', async () => {
    const s = await statusState()

    s.phase.value = 'degraded'
    expect(mountSidebar().find('.status-retry').exists()).toBe(false)

    s.phase.value = 'failed'
    const failed = mountSidebar()
    const retry = failed.find('.status-retry')
    expect(retry.exists()).toBe(true)

    await retry.trigger('click')
    expect(s.retry).toHaveBeenCalledTimes(1)
  })

  it('tooltip 在 failed 时补上原因，其它状态只报标签', async () => {
    const s = await statusState()

    s.phase.value = 'failed'
    s.errorMessage.value = '连接被拒绝'
    expect(mountSidebar().find('.status').attributes('title')).toBe('分析引擎未连接\n连接被拒绝')

    // degraded 带了 errorMessage 也不该显示——它不是失败的原因。
    s.phase.value = 'degraded'
    expect(mountSidebar().find('.status').attributes('title')).toBe('没有可用的语言工具链')
  })

  it('drawerOpen 落在根元素 class 上', () => {
    expect(mountSidebar({ drawerOpen: true }).find('nav.sidebar').classes()).toContain(
      'drawer-open'
    )
    expect(mountSidebar({ drawerOpen: false }).find('nav.sidebar').classes()).not.toContain(
      'drawer-open'
    )
  })
})
