// @vitest-environment jsdom
/**
 * TeacherWorkView 的视图测试（2026-09-27，#4 测试补全）。
 *
 * 作业面板的核心不变式：
 *   1. **列表来自接口**，行上显示题目数、截止、生效的 AI 档位（从行上读、
 *      不是从表单猜）。
 *   2. **统计是按需加载**：点开某份作业才拉 `getAssignmentStats`——打开
 *      一份的老师不该为所有作业付第二次请求的钱（文件头原话）。
 *   3. **复制不弹确认**（可逆操作，错了也没关系）；删除收进菜单（不可逆）。
 *      这个差别必须在点击之前就看得出来。
 *   4. **发布要选题**：空题单直接拦截（'至少要选一道题'），不发请求；
 *      选了题才带 title / problem_ids / class_id 发 `createAssignment`。
 *
 * 弹窗（NModal teleport 到 body）的交互通过 document.body 定位；删除
 * （useDialog）不测，jsdom 下不渲染弹层。
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { h, nextTick } from 'vue'
import { NDialogProvider } from 'naive-ui'
import TeacherWorkView from './TeacherWorkView.vue'
import type { AssignmentOut, AssignmentStats } from '../../api/assignments'
import type { AssistancePolicy } from '../../api/assistance'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/classroom', () => ({ listClassrooms: vi.fn() }))
vi.mock('../../api/assignments', () => ({
  createAssignment: vi.fn(),
  deleteAssignment: vi.fn(),
  duplicateAssignment: vi.fn(),
  getAssignmentStats: vi.fn(),
  getStudentReport: vi.fn(),
  listAssignments: vi.fn()
}))
vi.mock('../../api/assistance', () => ({
  getAssistanceUsage: vi.fn(),
  listLevels: vi.fn()
}))
vi.mock('../../api/teacher', () => ({ getStudents: vi.fn() }))
vi.mock('../../api/problems', () => ({ listProblems: vi.fn() }))

// 选题器里的辅助强度刻度，桩成空壳（弹窗交互不测）。
vi.mock('../../components/AssistLevelPicker.vue', () => ({
  default: { name: 'AssistLevelPickerStub', template: '<div />' }
}))

const assignmentsApi = vi.mocked(await import('../../api/assignments'))
const classroomsApi = vi.mocked(await import('../../api/classroom'))
const assistanceApi = vi.mocked(await import('../../api/assistance'))

const policy: AssistancePolicy = {
  level: 3,
  label: '三档',
  student_label: '中等帮助',
  description: '中等帮助',
  completion_uses_model: true,
  allows_chat: true,
  chat_is_hint_only: true,
  allows_code_insertion: true,
  allows_full_solution: false
}

const assignment: AssignmentOut = {
  id: 5,
  title: '栈与队列',
  description: '',
  problem_ids: [10, 12, 13],
  due_at: null,
  overdue: false,
  created_by: null,
  created_at: null,
  assistance_level: 3,
  assistance: policy
}

const stats: AssignmentStats = {
  assignment,
  problems: [
    { problem_id: 10, submissions: 8, accepted: 6, accuracy: 0.75, students: 8 },
    { problem_id: 12, submissions: 5, accepted: 2, accuracy: 0.4, students: 7 }
  ],
  students_who_submitted: 8,
  students_missing: ['李四', '王五']
}

async function rawMount(): Promise<VueWrapper> {
  const wrapper = mount({
    render: () => h(NDialogProvider, null, { default: () => h(TeacherWorkView) })
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

// NModal 的 teleport 内容残留在 document.body，跨测试会互相污染
// （body 里出现两个「发布」按钮，先找到的是旧实例的）。每个测试收尾
// 时必须 unmount。
let mountedWrappers: VueWrapper[] = []

async function mountView(items: AssignmentOut[] = [assignment]): Promise<VueWrapper> {
  assignmentsApi.listAssignments.mockResolvedValue(items)
  classroomsApi.listClassrooms.mockResolvedValue([])
  assistanceApi.listLevels.mockResolvedValue([])
  assistanceApi.getAssistanceUsage.mockResolvedValue({
    total: 0,
    by_action: {},
    by_action_labeled: {},
    by_student: []
  })
  const wrapper = await rawMount()
  mountedWrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mountedWrappers = []
})

afterEach(() => {
  for (const w of mountedWrappers) w.unmount()
  mountedWrappers = []
})

describe('TeacherWorkView', () => {
  it('渲染作业列表：标题、题目数、生效的 AI 档位', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('栈与队列')
    expect(wrapper.text()).toContain('3 题')
    expect(wrapper.text()).toContain('AI 3 档')
  })

  it('空态：还没有布置作业', async () => {
    const wrapper = await mountView([])
    expect(wrapper.text()).toContain('还没有布置作业')
  })

  it('点开作业才拉统计，渲染「还没交」名单与统计表', async () => {
    assignmentsApi.getAssignmentStats.mockResolvedValue(stats)
    const wrapper = await mountView()

    expect(assignmentsApi.getAssignmentStats).not.toHaveBeenCalled()

    await wrapper.find('.assignment-title').trigger('click')
    await flushPromises()

    expect(assignmentsApi.getAssignmentStats).toHaveBeenCalledWith(5)
    // 名单在模板里跨行渲染（`join('、')` 前有换行缩进），分开断言。
    expect(wrapper.text()).toContain('还没交（2 人）')
    expect(wrapper.text()).toContain('李四、王五')
    expect(wrapper.text()).toContain('75.0%')
  })

  it('复制直接调用 duplicateAssignment（不弹确认）', async () => {
    assignmentsApi.duplicateAssignment.mockResolvedValue(assignment)
    const wrapper = await mountView()
    assignmentsApi.listAssignments.mockClear()

    await wrapper.findAll('button').find((b) => b.text().trim() === '复制')!.trigger('click')
    await flushPromises()

    expect(assignmentsApi.duplicateAssignment).toHaveBeenCalledWith(5)
    expect(assignmentsApi.listAssignments).toHaveBeenCalled()
  })

  it('发布作业：没选题直接被拦，不发请求', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('button').find((b) => b.text().includes('布置作业'))!.trigger('click')
    await flushPromises()
    await nextTick()

    expect(document.body.textContent).toContain('布置作业')

    // 弹窗 footer 里的「发布」按钮。
    const publish = Array.from(document.body.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('发布')
    )!
    publish.click()
    await flushPromises()

    expect(assignmentsApi.createAssignment).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('至少要选一道题')
  })

  it('发布作业：选了题才发请求，带 title 与题目顺序', async () => {
    assignmentsApi.createAssignment.mockResolvedValue(assignment)
    const wrapper = await mountView()

    await wrapper.findAll('button').find((b) => b.text().includes('布置作业'))!.trigger('click')
    await flushPromises()
    await nextTick()

    // 通过子组件 vm 直接给表单赋值（wrapper.vm 是 NDialogProvider 包裹
    // 组件，不是 TeacherWorkView 本身；弹窗里的 NInput 在 body 中）。
    const vm = wrapper.findComponent(TeacherWorkView).vm as unknown as {
      newAssignmentTitle: string
      newAssignmentProblemIds: number[]
    }
    vm.newAssignmentTitle = '期中练习'
    vm.newAssignmentProblemIds = [12, 10]

    const publish = Array.from(document.body.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('发布')
    )!
    publish.click()
    await flushPromises()

    expect(assignmentsApi.createAssignment).toHaveBeenCalledWith(
      expect.objectContaining({ title: '期中练习', problem_ids: [12, 10] })
    )
  })
})
