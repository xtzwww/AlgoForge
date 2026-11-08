// @vitest-environment jsdom
/**
 * TeacherOverviewView 的视图测试（2026-09-27，#4 测试补全）。
 *
 * 这个面板是"读"型页面，钉的事：
 *   1. **数字必须来自接口而不是拍脑袋**。四张卡、副标题、正确率颜色都
 *      由 `getOverview` 的数据驱动——接口给了什么就渲染什么。
 *   2. **「需要关注」不是每次都出现**。空警报列表是合法答案，此时渲染
 *      一个空的区块标题是噪音（文件头文档原话）。
 *   3. **非教师不拉数据**。`useTeacherPanel` 只在 ready && teacher 时触发
 *      load。（2026-10-04 起面板自己的 `isTeacher` 保险已删，门移到了路由
 *      表——所以这一条现在是唯一钉住"加载时机"的用例。）
 *
 * TeacherGate / useTeacherPanel 读的是 session 的导出（与
 * TeacherProblemsView.spec 同模式）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import TeacherOverviewView from './TeacherOverviewView.vue'
import type { AlertOut, ClassOverview } from '../../api/teacher'

vi.mock('vue-router', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    RouterLink: defineComponent({
      name: 'RouterLink',
      props: { to: { type: [String, Object], required: false } },
      setup: (_props, { slots }) => () => h('a', { class: 'router-link' }, slots.default?.())
    }),
    useRouter: () => ({ push: vi.fn() })
  }
})

// `useTeacherPanel` 仍在模块作用域读 `sessionReady` / `isTeacher`：
// 守卫（`router/guards/auth.ts`）保证"能渲染这一页 = 是教师"，但**加载
// 时机**仍由这个 hook 负责——它要等会话往返回来才拉数据。所以这两个值
// 必须给真值，`logout` 已不再被任何教师面板引用（2026-10-04）。
vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/teacher', () => ({
  getOverview: vi.fn(),
  getAlerts: vi.fn()
}))

// 警报列表组件只渲染递进来的东西，这里桩成带 class 的容器以便断言透传。
vi.mock('../../components/AttentionAlerts.vue', () => ({
  default: { name: 'AttentionAlertsStub', template: '<div class="attention-alerts-stub" />' }
}))

const api = vi.mocked(await import('../../api/teacher'))

const overview: ClassOverview = {
  students: 32,
  submissions: 147,
  accepted: 121,
  accuracy: 0.823,
  open_mistakes: 5,
  analysed_mistakes: 3,
  open_flags: 2,
  flagged_submissions: 9
}

const alerts: AlertOut[] = [
  {
    user_id: 7,
    display_name: '张三',
    kind: 'repeating',
    reason: '连续 3 次提交未通过，错因都是「数组越界」'
  }
]

async function mountView(alertsValue: AlertOut[] = []): Promise<VueWrapper> {
  api.getOverview.mockResolvedValue(overview)
  api.getAlerts.mockResolvedValue(alertsValue)
  const wrapper = mount(TeacherOverviewView)
  await flushPromises()
  await nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TeacherOverviewView', () => {
  it('渲染接口返回的统计卡与副标题', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('32 名学生 · 147 次提交')
    expect(wrapper.text()).toContain('学生数')
    expect(wrapper.text()).toContain('总提交')
    expect(wrapper.text()).toContain('整体正确率')
    expect(wrapper.text()).toContain('82.3%')
    expect(wrapper.text()).toContain('121 / 147 通过')
    expect(wrapper.text()).toContain('待复查错题')
    expect(wrapper.text()).toContain('存疑提交')
    expect(wrapper.text()).toContain('共标记 9 条')
  })

  it('没有任何警报时不渲染「需要关注」区块', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).not.toContain('需要关注')
    expect(wrapper.find('.alerts-section').exists()).toBe(false)
  })

  it('有警报时渲染区块并把列表交给 AttentionAlerts', async () => {
    const wrapper = await mountView(alerts)

    expect(wrapper.text()).toContain('需要关注')
    expect(wrapper.find('.attention-alerts-stub').exists()).toBe(true)
  })

  it('加载失败显示错误而不显示空数据', async () => {
    api.getOverview.mockRejectedValue(new Error('连接被拒绝'))
    api.getAlerts.mockResolvedValue([])
    const wrapper = mount(TeacherOverviewView)
    await flushPromises()

    expect(wrapper.text()).toContain('无法加载学情：连接被拒绝')
    expect(wrapper.text()).not.toContain('名学生')
  })

  it('非教师时不拉数据（会话就绪但角色不对）', async () => {
    // 2026-10-04 起面板里不再有 `if (!isTeacher.value) return`——门在路由
    // 表上（`meta.requiresTeacher`）。但"何时拉数据"这条不变量仍然成立，
    // 而且执行点现在**只剩一个**：`useTeacherPanel` 只在
    // `sessionReady && isTeacher` 时调 load。这条用例钉的就是它。
    //
    // 为什么还要留着：`useTeacherPanel` 是六个面板共用的唯一加载时机
    // 判断，它坏了 = 六个面板一起坏，而且症状是"空白页"，不报错。
    ;(vi.mocked(await import('../../api/session')).isTeacher as { value: boolean }).value = false
    const wrapper = mount(TeacherOverviewView)
    await flushPromises()

    expect(api.getOverview).not.toHaveBeenCalled()
    expect(api.getAlerts).not.toHaveBeenCalled()
    wrapper.unmount()
    // 还原，避免污染同文件后续用例（本用例是最后一个，但保险起见恢复）。
    ;(vi.mocked(await import('../../api/session')).isTeacher as { value: boolean }).value = true
  })
})
