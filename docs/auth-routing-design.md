# 登录/注册抽离 + 鉴权拦截设计

> 2026-10-04。目标：把登录/注册从业务页面里抽离成独立路由模块，
> 认证完成后跳转到对应功能页，并让业务页面几乎不再出现鉴权代码。

---

## 一、现状诊断

### 已经做对的部分（不要动）

| 能力 | 位置 | 评价 |
|---|---|---|
| 会话单一真相源 | `api/session.ts` | 好。`sessionReady` 区分「未知」与「未登录」，避免了刷新即登出的经典 bug |
| 登录/注册是独立页面 | `views/LoginView.vue` / `RegisterView.vue` | 已是独立路由（`/login`、`/register`），`meta.hidden` 不进侧边栏 |
| 入口即身份守卫 | `router/entryGuard.ts` | 好。教师账号在教师网址、学生账号在学生网址，跨端自动踢回登录页 |
| 落点决策集中 | `router/landing.ts` | 好。`landingName` / `entryMismatch` 是全项目唯一判断处 |

**结论：你要的"抽离"在路由层面已经完成了 80%。** 真正的缺口不是"登录页没独立"，而是下面这条。

### 真正的缺口

**路由表里没有任何「这一页需要登录」的声明。**

`router/index.ts` 的 `meta` 只有 `title` / `parent` / `hidden` / `mode` / `group`，
**没有 `requiresAuth`**。于是"未登录会怎样"这件事，散落在 8 个地方各写一遍：

```ts
// 6 个教师面板里各有一份（TeacherOverview / Analysis / Work / Review / Roster / Problems）
if (!isTeacher.value) return

// TeacherGate.vue 里又做一遍
<div v-else-if="!isLoggedIn" class="gate">
<div v-else-if="!isTeacher" class="gate">

// AppTopbar.vue 里第四遍
const signedIn = computed(() => isLoggedIn.value)
```

这就是耦合的来源：**同一件"谁能看"的事，有 8 个副本，且没有任何两处能保证一致。**
加一个受保护页面 = 记得在新页面里再抄一遍判断，漏抄不会报错。

---

## 二、目标结构

```
desktop/src/renderer/src/
├── router/
│   ├── index.ts          # 只做「路由表 + 创建 router」，不含守卫
│   ├── guards/
│   │   ├── index.ts      # installGuards(router) —— 统一安装入口
│   │   ├── auth.ts       # 认证守卫：未登录 → 登录页
│   │   └── entry.ts      # 入口守卫：跨端 → 踢回（现有 entryGuard 迁入）
│   ├── meta.ts           # RouteMeta 类型扩展 + 判定函数（唯一的规则来源）
│   └── landing.ts        # 保持不动
├── views/
│   └── auth/             # 认证域，与业务页面物理隔离
│       ├── LoginView.vue
│       ├── RegisterView.vue
│       └── AuthLayout.vue   # 两个页面的共用外壳（品牌区 + 卡片）
└── api/session.ts        # 保持不动（真相源）
```

**核心原则：`meta.ts` 是唯一写规则的地方，`guards/` 是唯一执行的地方，业务页面零判断。**

---

## 三、路由配置

### 3.1 RouteMeta 扩展

```ts
// router/meta.ts
declare module 'vue-router' {
  interface RouteMeta {
    title: string
    parent?: string
    hidden?: boolean
    mode?: AppMode
    group?: string

    /** 这一页需要登录才能访问。默认 false —— 不写就是不拦。 */
    requiresAuth?: boolean

    /** 这一页需要教师角色。隐含 requiresAuth: true。 */
    requiresTeacher?: boolean
  }
}
```

**`requiresAuth` 默认 false**，这是刻意的选择：现状是"游客能刷题"，
默认不拦 = 现有行为不变，加标记才改变行为 → **改造零回归**。

### 3.2 路由表怎么写

```ts
export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: () => ({ name: appMode === 'teacher' ? 'teacher' : 'practice' }) },

  // ---------- 公开页：不写 requiresAuth，就是游客可用 ----------
  { path: '/practice',  name: 'practice',  component: Practice,           meta: { title: '练习', mode: 'student' } },
  { path: '/problems',  name: 'problems',  component: ProblemListView,    meta: { title: '题库', mode: 'student' } },
  { path: '/problems/:id(\\d+)', name: 'problem', component: ProblemPracticeView, /* … */ },

  // ---------- 需要登录：只加一个字段 ----------
  { path: '/assignments', name: 'assignments', component: AssignmentsView,
    meta: { title: '我的作业', mode: 'student', requiresAuth: true } },
  { path: '/mistakes',    name: 'mistakes',    component: MistakesView,
    meta: { title: '错题本',   mode: 'student', requiresAuth: true } },
  { path: '/profile',     name: 'profile',     component: ProfileView,
    meta: { title: '画像',     mode: 'student', requiresAuth: true } },
  { path: '/plan',        name: 'plan',        component: PlanView,
    meta: { title: '复习',     mode: 'student', requiresAuth: true } },

  // ---------- 教师页：requiresTeacher 已隐含 requiresAuth ----------
  { path: '/teacher',        name: 'teacher',        component: TeacherOverviewView,
    meta: { title: '教学概览', mode: 'teacher', group: 'teacher-overview', requiresTeacher: true } },
  // … 其余 5 个教师面板同理

  // ---------- 认证页：始终可达，且**永不**要求登录 ----------
  { path: '/login',    name: 'login',    component: LoginView,
    meta: { title: '登录', hidden: true, guestOnly: true } },
  { path: '/register', name: 'register', component: RegisterView,
    meta: { title: '注册', hidden: true, guestOnly: true } },

  { path: '/:pathMatch(.*)*', redirect: () => ({ name: appMode === 'teacher' ? 'teacher' : 'practice' }) }
]
```

**`guestOnly: true` 是必须的防御**：登录页自己绝不能要求登录。
曾经出现过"登录页守卫把自己重定向到登录页"的死循环，这个标记就是它的解药。

---

## 四、鉴权拦截逻辑

### 4.1 三条铁律

```
1. 只有 sessionReady 之后才判断       —— 否则刷新时把已登录用户弹走
2. 未登录才拦，且只拦声明过的页         —— 不做隐式拦截
3. 登录页永不拦，且不参与角色判定        —— 防重定向死循环
```

### 4.2 认证守卫

```ts
// router/guards/auth.ts
import type { Router } from 'vue-router'
import { sessionReady, isLoggedIn, isTeacher } from '../../api/session'
import { landingName } from '../landing'
import { appMode } from '../../runtime/mode'

export function installAuthGuard(router: Router): void {
  router.beforeEach(async (to) => {
    // 铁律 1：会话状态未知时一律放行。
    // 误判"未登录"的代价是把已登录用户弹去登录页（看起来像每次刷新都掉登录），
    // 比"多显示一帧未登录界面"严重得多。
    if (!sessionReady.value) return true

    // 认证页自己不受任何约束（铁律 3）。
    if (to.meta.guestOnly) return true

    const needsAuth = to.meta.requiresTeacher || to.meta.requiresAuth
    if (!needsAuth) return true
    if (isLoggedIn.value) {
      // 教师页额外要求角色；学生访问 → 回自己的首页，不弹登录页
      // （人已经登录了，再让他登录一次是说不通的）。
      if (to.meta.requiresTeacher && !isTeacher.value) {
        return { name: landingName(appMode, 'student') }
      }
      return true
    }

    // 未登录 → 去登录页。固定回首页，不记原目标（按你的选择）。
    return { name: 'login' }
  })
}
```

**为什么"角色不符"是回首页而不是回登录页**：
如果学生点了教师链接被弹到登录页，他会以为"我没登录"，然后反复登录——而他本来就是登录状态。
回首页是诚实的："这里你去不了"，且不制造"登录坏了"的错觉。

### 4.3 统一安装

```ts
// router/guards/index.ts
import type { Router } from 'vue-router'
import { installAuthGuard } from './auth'
import { installEntryGuard } from './entry'

/**
 * 守卫的安装顺序即执行顺序：
 *   1. entry —— 先判"你是不是走错了端"（跨端问题比登录问题更根本）
 *   2. auth  —— 再判"这页你能不能进"
 * 反过来会让跨端用户在登录页反复弹回。
 */
export function installGuards(router: Router): void {
  installEntryGuard(router)
  installAuthGuard(router)
}
```

`main.ts` 只改一行：

```diff
- import { installEntryGuard } from './router/entryGuard'
+ import { installGuards } from './router/guards'
  …
- installEntryGuard(router)
+ installGuards(router)
```

### 4.4 业务页面变成什么样

**改造前**（每个教师面板都要写）：

```ts
import { isTeacher, logout } from '../../api/session'
if (!isTeacher.value) return
```

**改造后**：删掉。路由已经保证"能渲染这一页 = 你是教师"。

`TeacherGate.vue` **保留**，但降级为纯展示组件（服务端 403 的兜底提示），
不再是"路由之外的第二个门禁"。理由见下面第六节的边界说明。

---

## 五、预期的收益（能挡掉的 bug 类型）

| 风险 | 现在为什么可能发生 | 拆分后为什么不会 |
|---|---|---|
| **新页面漏写鉴权** | 判断散在 8 处，新增页面靠"记得抄" | 路由表加一个字段，漏写=不拦（可见、可测），不会静默漏 |
| **刷新即登出** | 若守卫不看 `sessionReady`，首帧把"未知"当"未登录" | 铁律 1 显式处理，一行注释说明为什么 |
| **登录页重定向死循环** | 守卫若统一要求登录，登录页自己也会被拦 | `guestOnly` 白名单 |
| **已登录用户被要求重新登录** | 角色不符时若统一弹登录页 | 角色不符 → 回首页，语义正确 |
| **8 处判断逐渐不一致** | 6 个面板各写一遍，改一处漏三处 | 规则只有一份 |
| **测试里的假 router 崩守卫** | `createRouter` 的 mock 没有 `beforeEach` | 守卫装到 `guards/`，`router/index.ts` 保持纯净（**沿用项目现有做法**） |
| **跨端登录串号** | 教师网址登录学生账号 | 已有 `entryGuard` 继续生效，且顺序在 auth 之前 |
| **未登录时页面闪烁内容** | 页面先渲染再被守卫弹走 | 守卫在导航阶段就拦下，页面根本不挂载 |

**一句话**：把"谁能看这一页"从**运行时分散判断**变成**路由表上的声明式字段**，
于是它可以被一眼看全、被测试覆盖、被新增页面自动继承。

---

## 六、边界与不做的部分（重要）

### 6.1 前端鉴权不是安全边界

必须说清楚：**前端的 `requiresAuth` 只是"体验"，不是"权限"。**
任何人打开开发者工具都能改内存里的标志位。

真正的边界在服务端 —— 项目现在是**后端 403 兜底**（`app/api/teacher.py`），
这是对的，**继续保持**。前端这块的作用是：不让用户看到注定要失败的空页面，
以及不让他误以为"登录坏了"。

### 6.2 为什么 `requiresTeacher` 和 `mode: 'teacher'` 不能互相替代

| | `mode: 'teacher'` | `requiresTeacher: true` |
|---|---|---|
| 作用时机 | **构建期**（web 端拆包） | **运行期**（导航守卫） |
| 拦的是谁 | 学生网址上根本不存在这条路由 | 学生**账号**访问教师**网址** |
| Electron（`both`） | 不分端，不生效 | 生效 |

两者互补，都要有。这也是为什么现有的 `mode` 字段**不能删**。

### 6.3 本次不做

- **不引入 Pinia 存会话**：`api/session.ts` 的单例已经够用，
  且 `AGENTS.md` 说"没有别的东西要存"。为迁而迁是净负债。
- **不做"记住原目标页"**：你已选择固定回首页。若将来要加，
  在 `login` 守卫里带 `?redirect=` 并在 `LoginView` 成功后消费即可，
  但要注意"开放重定向"风险（只接受站内路径）。→ 已记录为后续可选。
- **不动 `sessionReady` 语义**、**不动后端 403**。

---

## 七、迁移步骤（每步都可独立验证）

1. **建 `router/meta.ts`**：只加类型声明与两个判定函数，`routes` 加字段。
   此时无行为变化（无人读这些字段）→ 跑 `vue-tsc` + 现有测试应全绿。
2. **迁 `entryGuard.ts` → `guards/entry.ts`**，建 `guards/index.ts`。
   `main.ts` 改一行。→ 跑 `landing.spec.ts` 与侧边栏测试。
3. **加 `guards/auth.ts`** + `guestOnly` 标记。→ 手工验证 4 条路径：
   游客开 `/practice`（放行）、游客开 `/mistakes`（去登录）、
   学生开 `/teacher`（回学生首页）、教师开 `/teacher`（放行）。
4. **登录/注册页迁入 `views/auth/`**，抽 `AuthLayout.vue`。
   纯搬运，改 import 路径。→ 跑 `RegisterView.spec` / `LoginView` 相关用例。
5. **清理业务页面**：删掉 6 个教师面板的 `if (!isTeacher.value) return`
   与不再用到的 `logout` 导入。→ 跑前端全量测试。
6. **写守卫的单测**（新建 `guards/auth.spec.ts`）：覆盖上面 4 条路径 +
   "sessionReady 为 false 时放行" 这一条关键回归。

**回滚点**：第 3 步之前的所有改动都无行为变化，可以随时停。

---

## 八、需要你确认的两个取舍

1. **`/practice` 与 `/problems`（游客可刷题）** 我按**公开**处理。
   若你认为"刷题也要登录"，把它们加 `requiresAuth: true` 即可 —— 一个字段。
2. **`TeacherGate.vue` 保留为展示层**（不做删除）。它是服务端 403 的
   用户可读版本，删了之后学生误入教师页会看到原始错误码。
