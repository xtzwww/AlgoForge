// @vitest-environment jsdom
/**
 * TeacherRosterView 的视图测试（2026-09-27，#4 测试补全）。
 *
 * 这个面板是名册 + 班级的"读"页（写入动作在弹窗里，jsdom 下不测），
 * 钉的事：
 *   1. **名册数字来自接口**。副标题与每行的提交/正确率/错题都由
 *      `getStudents` 驱动。
 *   2. **按班筛选是"按 id 缓存名单"的客户端过滤**。第一次筛某个班要
 *      拉 `listClassMembers`，同一班再筛不再拉；没有缓存名单时兜底显示
 *      全量（避免筛选瞬间变空）。
 *   3. **空态分两种**：一个学生都没有 vs 筛到空班，文案不同、动作不同。
 *
 * 交互类（创建班级、添加学生、批量导入、删除成员/班级）都在 NModal /
 * NDropdown / useDialog 里，测试环境不渲染弹层，不测（app 里有人眼路径）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { h, nextTick } from 'vue'
import { NDialogProvider, NSelect } from 'naive-ui'
import TeacherRosterView from './TeacherRosterView.vue'
import type { StudentOut } from '../../api/teacher'
import type { ClassroomOut } from '../../api/classroom'

vi.mock('vue-router', async () => {
  const { defineComponent, h: h2 } = await import('vue')
  return {
    RouterLink: defineComponent({
      name: 'RouterLink',
      props: { to: { type: [String, Object], required: false } },
      setup: (_props, { slots }) => () => h2('a', { class: 'router-link' }, slots.default?.())
    }),
    useRouter: () => ({ push: vi.fn() })
  }
})

vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/classroom', () => ({
  createClassroom: vi.fn(),
  deleteClassroom: vi.fn(),
  listClassMembers: vi.fn(),
  listClassrooms: vi.fn(),
  removeClassMember: vi.fn()
}))

vi.mock('../../api/teacher', () => ({
  createStudent: vi.fn(),
  getStudents: vi.fn(),
  importStudents: vi.fn()
}))

const classroomsApi = vi.mocked(await import('../../api/classroom'))
const teacherApi = vi.mocked(await import('../../api/teacher'))

const students: StudentOut[] = [
  {
    user_id: 1,
    username: 'zhangsan',
    display_name: '张三',
    submissions: 10,
    accepted: 8,
    accuracy: 0.8,
    mistakes: 2,
    flagged_submissions: 0,
    weakest_tag: null
  },
  {
    user_id: 2,
    username: 'lisi',
    display_name: '李四',
    submissions: 4,
    accepted: 1,
    accuracy: 0.25,
    mistakes: 3,
    flagged_submissions: 1,
    weakest_tag: '数组'
  }
]

const rooms: ClassroomOut[] = [
  {
    id: 11,
    name: '三班',
    member_count: 1,
    invite_code: 'ABC123',
    teacher_id: 1,
    created_at: '2026-09-01T00:00:00Z'
  }
]

/** 三班的成员名单 = 张三（user_id 1）。 */
function seedClassMembers(): void {
  classroomsApi.listClassMembers.mockResolvedValue([
    { user_id: 1, display_name: '张三', username: 'zhangsan', joined_at: '2026-09-01T00:00:00Z' }
  ])
}

async function rawMount(): Promise<VueWrapper> {
  const wrapper = mount({
    render: () => h(NDialogProvider, null, { default: () => h(TeacherRosterView) })
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

async function mountView(): Promise<VueWrapper> {
  teacherApi.getStudents.mockResolvedValue(students)
  classroomsApi.listClassrooms.mockResolvedValue(rooms)
  return rawMount()
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TeacherRosterView', () => {
  it('渲染名册行与副标题（提交/正确率/错题来自接口）', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('2 名学生 · 1 个班级')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('zhangsan')
    expect(wrapper.text()).toContain('80.0%')
    expect(wrapper.text()).toContain('25.0%')
    // 成员名单是懒加载缓存：初始未拉任何班级名单，班级列一律「未加入」。
    expect(wrapper.text()).toContain('未加入')
  })

  it('一个学生都没有时显示建号引导空态', async () => {
    teacherApi.getStudents.mockResolvedValue([])
    classroomsApi.listClassrooms.mockResolvedValue([])
    const wrapper = await rawMount()

    expect(wrapper.text()).toContain('还没有学生账号')
  })

  it('按班筛选：第一次筛该班拉成员名单，列表收窄到该班成员', async () => {
    seedClassMembers()
    const wrapper = await mountView()

    const select = wrapper.findAllComponents(NSelect)[0]
    select.vm.$emit('update:value', '11')
    await flushPromises()

    expect(classroomsApi.listClassMembers).toHaveBeenCalledWith(11)
    // 只有张三在三班：成员名单拉取后张三标出班级、李四保持未加入。
    expect(wrapper.text()).toContain('只看「三班」的 1 名学生')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).not.toContain('李四')
  })

  it('同一班第二次筛不再拉成员名单（缓存生效）', async () => {
    seedClassMembers()
    const wrapper = await mountView()
    const select = wrapper.findAllComponents(NSelect)[0]

    select.vm.$emit('update:value', '11')
    await flushPromises()
    classroomsApi.listClassMembers.mockClear()

    // 切回全部再切回三班：名单应来自缓存。
    select.vm.$emit('update:value', '__all__')
    await flushPromises()
    select.vm.$emit('update:value', '11')
    await flushPromises()

    expect(classroomsApi.listClassMembers).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('只看「三班」的 1 名学生')
  })

  it('筛选提示行的「显示全部」把过滤复位', async () => {
    seedClassMembers()
    const wrapper = await mountView()
    const select = wrapper.findAllComponents(NSelect)[0]

    select.vm.$emit('update:value', '11')
    await flushPromises()
    expect(wrapper.text()).toContain('只看「三班」')

    await wrapper.findAll('button').find((b) => b.text().includes('显示全部'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('只看「三班」')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('李四')
  })

  it('筛到一个空班级显示对应的空态文案', async () => {
    seedClassMembers()
    const wrapper = await mountView()

    // 三班实际有张三，但给一份空成员名单 → 缓存里三班 = 空 → 空态。
    classroomsApi.listClassMembers.mockResolvedValue([])
    const select = wrapper.findAllComponents(NSelect)[0]
    select.vm.$emit('update:value', '11')
    await flushPromises()

    expect(wrapper.text()).toContain('这个班里还没有学生')
  })
})
