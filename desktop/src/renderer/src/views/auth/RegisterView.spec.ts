// @vitest-environment jsdom
/**
 * RegisterView 的组件测试。
 *
 * 这个页面是**唯一一个陌生人能直接到达的写操作入口**，所以用例按
 * 「漏掉之后后果多严重」排，不按代码顺序。
 *
 * **第一条：界面上不能有任何选择角色的控件。**
 *
 * 服务端那条已经在 `tests/test_registration.py` 里钉死了（注册永远只造学生）。
 * 这里钉的是另一半：界面上**不能出现一个看起来能选角色的东西**。理由不是
 * 「反正服务端会拒绝」，而是 —— 一个勾选框会让人以为存在这条路，而它不
 * 存在；更糟的是，哪天服务端那层判断被改坏，那个控件就从谎言变成入口。
 * 所以这里查的是「没有任何 name/id 像角色的输入控件」，而不是「角色字段
 * 被忽略了」。
 *
 * **第二条：本地校验省掉一次往返，但服务端才是权威。**
 *
 * 两次密码不一致、密码太短这类事在浏览器里就能判断，不该发请求。但
 * 「用户名被占用」「注册已关闭」「注册太频繁」只有服务端知道，所以服务端
 * 返回的 detail 必须原样显示 —— 它比任何按状态码猜出来的文案都具体。
 *
 * **第三条：连不上 ≠ 密码错。**
 *
 * 没有 response 表示请求根本没到服务端，那是另一个问题、另一个修法。
 * 报成「用户名或密码不正确」会让人去找一个不存在的错别字。
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

/**
 * 由 mock 工厂填充；`vi.hoisted` 里拿不到 `ref`。
 *
 * `state` **必须是真 ref，不能是普通属性**。这一点踩过一次，值得写下来：
 * 如果写成 `state: { user: null }`，那么 mock 里的 `computed(() =>
 * harness.state.user)` 就**没有任何响应式依赖**，它会在第一次被读取时把
 * 值缓存下来，之后**永不重新求值**。症状极具误导性 —— 单独跑「已登录要跳转」
 * 那条用例是绿的（它是那个文件里第一个读它的），整个文件一起跑就红，
 * 而且红的用例看起来像是组件没写 watch。
 */
const harness = vi.hoisted(() => ({
  state: {} as {
    ready: { value: boolean }
    user: { value: null | { id: number; username: string; display_name: string; role: string } }
  },
  registerCalls: [] as {
    username: string
    password: string
    displayName: string
    role: string
  }[],
  replaceCalls: [] as unknown[],
  /** 让 `register` 抛指定错误。 */
  failWith: null as unknown,
  /** 让 `register` 永远不返回，用来观察「提交中」的状态。 */
  pending: false,
  /** 跨端指路调用（见下方 mock 的 navigation 模块）。 */
  goToCalls: [] as string[]
}))

vi.mock('../../api/session', async () => {
  const { computed, ref } = await import('vue')
  const ready = ref(true)
  const user = ref<null | { id: number; username: string; display_name: string; role: string }>(
    null
  )
  harness.state = { ready, user }

  return {
    sessionReady: computed(() => ready.value),
    sessionUser: computed(() => user.value),
    register: async (
      username: string,
      password: string,
      displayName: string,
      role = 'student'
    ) => {
      harness.registerCalls.push({ username, password, displayName, role })
      // 一个永不 resolve 的 promise，而不是「抛一个 promise」：在 async 函数里
      // `throw somePromise` 是把那个 promise 当作**拒绝原因**，函数会立刻
      // reject —— 只有 `return` 一个 thenable 才会被采纳。所以挂住必须靠
      // await 一个不返回的 promise。
      if (harness.pending) await new Promise<void>(() => {})
      if (harness.failWith) throw harness.failWith
      return {
        id: 7,
        username,
        display_name: displayName || username,
        role: 'student'
      }
    }
  }
})

vi.mock('../../runtime/navigation', () => ({
  goTo: (url: string) => {
    harness.goToCalls.push(url)
  }
}))

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
    useRouter: () => ({
      replace: async (target: unknown) => {
        harness.replaceCalls.push(target)
      }
    })
  }
})

import RegisterView from './RegisterView.vue'

/**
 * 本文件里挂载过的组件，统一在 afterEach 卸载。
 *
 * 不卸载的话，上一个用例的 watcher 仍然活着，下一个用例改 `harness.state`
 * 时会把它一起触发 —— 「已登录要跳转」那条就因此收到了三个 `replace` 调用，
 * 其中两个来自前面用例留下的组件。这类失败很难读，因为每一个单独的调用
 * 看起来都是对的。
 */
const mounted: ReturnType<typeof mount>[] = []

function mountView(): ReturnType<typeof mount> {
  const w = mount(RegisterView)
  mounted.push(w)
  return w
}

afterEach(() => {
  while (mounted.length) {
    mounted.pop()?.unmount()
  }
})

/** 填表并提交。不传的字段保持默认。 */
async function fillAndSubmit(
  w: ReturnType<typeof mount>,
  fields: Partial<Record<'username' | 'display-name' | 'password' | 'confirm-password', string>> = {}
): Promise<void> {
  const defaults: Record<string, string> = {
    username: 'zhangsan',
    'display-name': '',
    password: 'secret123',
    'confirm-password': 'secret123'
  }
  const merged = { ...defaults, ...fields }

  // 四个 NInput 的顺序即模板顺序（username → display-name → password →
  // confirm-password）。组件化后 `name` 落在 NInput 根元素上而不是内部
  // <input>，所以按索引取 input 元素。
  const INPUT_INDEX: Record<string, number> = {
    username: 0,
    'display-name': 1,
    password: 2,
    'confirm-password': 3
  }
  for (const [name, value] of Object.entries(merged)) {
    await w.findAll('input')[INPUT_INDEX[name]].setValue(value)
  }
  await w.find('form').trigger('submit')
  await flushPromises()
}

/** 把一次 axios 风格的失败包成组件看到的样子。 */
function httpError(status: number, detail?: string): unknown {
  return { response: { status, data: detail === undefined ? {} : { detail } } }
}

/**
 * 第三轮（入口即身份）起，跨端切换（学生入口点「老师」）调用
 * `goTo` 整页去那端的注册页。jsdom 的 `location.assign` 不可重定义，
 * 所以导航被 mock 成 `harness.goToCalls`；跳转 URL 的正确性由
 * `landing.spec.ts` 的 `entryUrlForIntent` 纯函数测试钉死。
 */
beforeEach(() => {
  harness.state.ready.value = true
  harness.state.user.value = null
  harness.registerCalls = []
  harness.replaceCalls = []
  harness.failWith = null
  harness.pending = false
  harness.goToCalls = []
})

describe('RegisterView', () => {
  // --- 1. 界面不能暗示「可以选角色」 --------------------------------

  it('页面上不存在任何能选择角色的控件', async () => {
    const w = mountView()

    // 任何 value 像角色的 input / select / radio / checkbox 都不行。
    const suspects = w.findAll('input, select, textarea').filter((node) => {
      const el = node.element as HTMLInputElement
      const haystack = [
        el.getAttribute('name') ?? '',
        el.getAttribute('id') ?? '',
        el.getAttribute('value') ?? '',
        el.getAttribute('placeholder') ?? ''
      ]
        .join(' ')
        .toLowerCase()
      return /role|角色|teacher|教师|admin|管理员/.test(haystack)
    })

    expect(suspects).toHaveLength(0)
    // 顺带确认文案里没有「教师」这个选项在表单区域出现（页脚那句说明性
    // 文案是允许的，它说的是「需要老师账号请让老师操作」）。
    expect(w.find('form').text()).not.toContain('教师账号')
  })

  it('页面只提供四个输入框：用户名、姓名、密码、确认密码', () => {
    const w = mountView()
    // 组件化后 name 落在 NInput 根元素而非内部 <input>，这里钉数量与
    // 占位文案（语义锚点），不依赖 name 的落点。
    expect(w.findAll('input')).toHaveLength(4)
    const placeholders = w
      .findAll('input')
      .map((node) => (node.element as HTMLInputElement).getAttribute('placeholder'))
    expect(placeholders.filter((p) => p !== null && p !== '')).toHaveLength(4)
  })

  // --- 1b. 学生/老师切换（2026-09-25 第二轮） ------------------------

  it('页面顶部提供学生/老师两个切换按钮，默认选中学生', async () => {
    const w = mountView()
    const buttons = w.findAll('button').filter((b) => b.text() === '学生' || b.text() === '老师')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].attributes('aria-pressed')).toBe('true')
    expect(buttons[1].attributes('aria-pressed')).toBe('false')
  })

  it('学生入口点「老师」= 去教师端注册页（指路，不在本页注册教师号）', async () => {
    const w = mountView()
    await w.findAll('button').find((b) => b.text() === '老师')!.trigger('click')
    await flushPromises()

    // 入口即身份：跨端切换是整页指路，不改变本页要注册的角色。
    expect(harness.goToCalls).toEqual(['teacher.web.html#/register'])
    expect(harness.registerCalls).toHaveLength(0)
    // 意图跟随了点击（文案变教师版），但表单提交仍只认当前入口的学生。
    expect(w.text()).toContain('自己创建一个教师账号')
  })

  it('同端切换（学生↔学生）不跳转，文案与请求角色跟随意图', async () => {
    const w = mountView()
    // 点「老师」被 stub 拦下（不真导航），意图仍变 teacher；再点回学生。
    await w.findAll('button').find((b) => b.text() === '老师')!.trigger('click')
    await flushPromises()
    await w.findAll('button').find((b) => b.text() === '学生')!.trigger('click')
    await flushPromises()

    expect(harness.goToCalls).toEqual(['teacher.web.html#/register'])
    expect(w.text()).toContain('自己创建一个学生账号')

    await fillAndSubmit(w, { username: 'again_student' })
    expect(harness.registerCalls[0].role).toBe('student')
  })

  // --- 2. 本地校验：能不发请求就不发 ---------------------------------

  it('两次密码不一致时不发请求', async () => {
    const w = mountView()
    await fillAndSubmit(w, { password: 'secret123', 'confirm-password': 'secret124' })

    expect(harness.registerCalls).toHaveLength(0)
    expect(w.text()).toContain('不一致')
  })

  it('密码太短时不发请求', async () => {
    const w = mountView()
    await fillAndSubmit(w, { password: '12345', 'confirm-password': '12345' })

    expect(harness.registerCalls).toHaveLength(0)
    expect(w.text()).toContain('6 位')
  })

  it('用户名太短时不发请求', async () => {
    const w = mountView()
    await fillAndSubmit(w, { username: 'a' })

    expect(harness.registerCalls).toHaveLength(0)
    expect(w.text()).toContain('用户名长度')
  })

  it('用户名超长时不发请求', async () => {
    const w = mountView()
    await fillAndSubmit(w, { username: 'x'.repeat(33) })

    expect(harness.registerCalls).toHaveLength(0)
  })

  it('什么都不填时不发请求', async () => {
    const w = mountView()
    await fillAndSubmit(w, { username: '', password: '', 'confirm-password': '' })

    expect(harness.registerCalls).toHaveLength(0)
    expect(w.text()).toContain('请填写')
  })

  // --- 3. 成功路径 ---------------------------------------------------

  it('注册成功后跳到学生首页', async () => {
    const w = mountView()
    await fillAndSubmit(w)

    expect(harness.registerCalls).toHaveLength(1)
    expect(harness.replaceCalls).toEqual([{ name: 'assignments' }])
  })

  it('学生端入口发出的注册请求带 role=student', async () => {
    // 测试环境是学生端入口（`runtime/mode.ts` 按地址判定，vitest 里是
    // jsdom 默认地址），所以页面按端请求的就是学生角色 —— 服务端白名单
    // 只认字面 "teacher"，这里发的值必须恰好匹配其一。
    const w = mountView()
    await fillAndSubmit(w)

    expect(harness.registerCalls[0].role).toBe('student')
  })

  it('提交前把用户名和姓名的首尾空格去掉', async () => {
    const w = mountView()
    await fillAndSubmit(w, { username: '  zhangsan  ', 'display-name': '  张三  ' })

    expect(harness.registerCalls[0].username).toBe('zhangsan')
    expect(harness.registerCalls[0].displayName).toBe('张三')
  })

  it('密码不被 trim —— 前后空格是密码的一部分', async () => {
    const w = mountView()
    await fillAndSubmit(w, { password: '  padded  ', 'confirm-password': '  padded  ' })

    expect(harness.registerCalls[0].password).toBe('  padded  ')
  })

  it('姓名留空时原样传空串，由服务端兜底成用户名', async () => {
    const w = mountView()
    await fillAndSubmit(w, { 'display-name': '' })

    expect(harness.registerCalls[0].displayName).toBe('')
  })

  // --- 4. 服务端的拒绝要原样说出来 ----------------------------------

  it('409 说明用户名被占用', async () => {
    harness.failWith = httpError(409, '该用户名已被占用')
    const w = mountView()
    await fillAndSubmit(w)

    expect(w.text()).toContain('占用')
    expect(harness.replaceCalls).toHaveLength(0)
  })

  it('429 把服务端给的等待时间显示出来', async () => {
    harness.failWith = httpError(429, '注册过于频繁，请 1800 秒后再试')
    const w = mountView()
    await fillAndSubmit(w)

    expect(w.text()).toContain('1800 秒')
  })

  it('403 说明本机关闭了自助注册', async () => {
    harness.failWith = httpError(403, '本机已关闭自助注册，请联系老师创建账号')
    const w = mountView()
    await fillAndSubmit(w)

    expect(w.text()).toContain('关闭自助注册')
  })

  it('400 原样透传服务端的具体原因', async () => {
    harness.failWith = httpError(400, '用户名只能包含字母、数字、下划线、连字符和点')
    const w = mountView()
    await fillAndSubmit(w)

    expect(w.text()).toContain('下划线')
  })

  it('连不上后端时不说成密码错', async () => {
    // 没有 response 表示请求根本没到服务端。
    harness.failWith = new Error('Network Error')
    const w = mountView()
    await fillAndSubmit(w)

    const text = w.text()
    expect(text).toContain('无法连接')
    expect(text).not.toContain('密码不正确')
  })

  it('服务端没给 detail 时也不崩，退回一句能读的话', async () => {
    harness.failWith = httpError(500)
    const w = mountView()
    await fillAndSubmit(w)

    expect(w.text()).toContain('500')
  })

  // --- 5. 已登录的人不该看到注册表单 ---------------------------------

  it('已登录时直接跳到对应首页，不显示表单', async () => {
    harness.state.user.value = {
      id: 1,
      username: 'zhangsan',
      display_name: '张三',
      role: 'student'
    }
    mountView()
    await flushPromises()

    expect(harness.replaceCalls).toEqual([{ name: 'assignments' }])
  })

  it('会话还没就绪时不跳转 —— 那时还不知道有没有登录', async () => {
    harness.state.ready.value = false
    mountView()
    await flushPromises()

    expect(harness.replaceCalls).toHaveLength(0)
  })

  it('角色就绪后落点跟随当前端——学生入口下任何角色都落学生首页', async () => {
    // 注册页只会产出学生，但这个 watch 是复用 LoginView 的那段，所以它必须
    // 对任何角色都成立 —— 否则哪天服务端真的返回了教师，页面会跳错。
    //
    // 2026-09-25 第三轮（入口即身份）起，真实系统里教师账号在**学生端网址**
    // 的已登录会话会被 `router/index.ts` 的全局守卫与 `main.ts` 的会话
    // watch 拦下（登出 + 送回登录页 + entryErr 提示），不会停留在学生界面；
    // 本用例钉的只是组件 watch 自身的落点逻辑不回退（它不知道守卫存在）。
    // 测试环境是学生端入口，`landingName` 因此让任何角色都落「我的作业」。
    harness.state.user.value = {
      id: 1,
      username: 'teacher1',
      display_name: '老师',
      role: 'teacher'
    }
    mountView()
    await flushPromises()

    expect(harness.replaceCalls).toEqual([{ name: 'assignments' }])
  })

  // --- 6. 失败之后还能再试 -------------------------------------------

  it('失败后按钮恢复可用，可以再提交一次', async () => {
    harness.failWith = httpError(409, '该用户名已被占用')
    const w = mountView()
    await fillAndSubmit(w)

    // 失败的提交不能把按钮留在禁用状态 —— 那等于「错了就没法再试」，
    // 而对注册来说「换个名字再来」正是客户端唯一能做的事。
    expect(w.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
    expect(w.find('[role="alert"]').exists()).toBe(true)

    harness.failWith = null
    await fillAndSubmit(w, { username: 'other' })

    expect(harness.registerCalls).toHaveLength(2)
    // 断言放在跳转之后：如果第二次提交又失败了，先红的是这条，而页面上
    // 残留的报错文字会说明它为什么失败。
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(harness.replaceCalls).toEqual([{ name: 'assignments' }])
  })

  it('提交中按钮禁用，防止一次点击建出两个号', async () => {
    // 挂住 register，于是组件停在 busy 状态。
    harness.pending = true
    const w = mountView()

    await fillAndSubmit(w)

    expect(harness.registerCalls).toHaveLength(1)
    expect(w.find('button[type="submit"]').attributes('disabled')).toBeDefined()

    // 再点一次也不能发出第二个请求。
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(harness.registerCalls).toHaveLength(1)
  })
})
