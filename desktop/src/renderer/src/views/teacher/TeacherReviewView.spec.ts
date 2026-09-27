// @vitest-environment jsdom
/**
 * TeacherReviewView 的视图测试（2026-09-27，#4 测试补全）。
 *
 * 这个面板的职责是"把标记变成可反驳的东西"（文件头原话），钉的事：
 *   1. **队列与复核状态都来自接口**，默认不显示已判过的（判掉 = 停止
 *      请求关注）。
 *   2. **三个判定走 reviewFlag 且判完重载**。一次判定会挪动三个数字
 *      （队列 / 概览 open_flags / 学生存疑计数），所以提交后必须重拉而
 *      不是就地改行（文件头原话）。
 *   3. **只有判过的条目出现「撤销判定」**——它是一次误点的撤销，不是
 *      第三种判定。
 *   4. **「显示已复核」开关触发重拉**并携带该意图。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { NCheckbox } from 'naive-ui'
import TeacherReviewView from './TeacherReviewView.vue'
import type { FlaggedSubmission } from '../../api/teacher'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/teacher', () => ({
  getFlaggedSubmissions: vi.fn(),
  reviewFlag: vi.fn()
}))

const api = vi.mocked(await import('../../api/teacher'))

const openItem: FlaggedSubmission = {
  id: 21,
  problem_id: 5,
  status: 'WA',
  created_at: '2026-09-26T08:00:00Z',
  student: '张三',
  process: {
    flagged: true,
    summary: '编辑节奏异常：代码在提交前 30 秒内一次性出现',
    reasons: ['粘贴速度远快于打字速度', '无中间编译错误'],
    features: { paste_ratio: 0.9, edit_gap_sec: 30 }
  },
  review: { verdict: null, reviewed_by: null, reviewed_at: null }
}

const reviewedItem: FlaggedSubmission = {
  ...openItem,
  id: 22,
  student: '李四',
  review: { verdict: 'confirmed', reviewed_by: 1, reviewed_at: '2026-09-26T09:00:00Z' }
}

async function mountView(items: FlaggedSubmission[] = [openItem]): Promise<VueWrapper> {
  api.getFlaggedSubmissions.mockResolvedValue(items)
  const wrapper = mount(TeacherReviewView)
  await flushPromises()
  await nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TeacherReviewView', () => {
  it('渲染队列：学生、摘要、依据，未判过的没有撤销按钮', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('1 条待看')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('编辑节奏异常')
    // 依据收在 <details> 里，summary 文本可见。
    expect(wrapper.text()).toContain('查看依据')
    // 未判过 → 无撤销判定按钮。
    expect(wrapper.text()).not.toContain('撤销判定')
  })

  it('空队列显示"都看过了"空态', async () => {
    const wrapper = await mountView([])
    expect(wrapper.text()).toContain('暂无存疑提交——都看过了')
  })

  it('点「确认可疑」提交 confirmed 并重拉队列', async () => {
    api.reviewFlag.mockResolvedValue({ id: 21, verdict: 'confirmed' } as never)
    const wrapper = await mountView()
    api.getFlaggedSubmissions.mockClear()

    await wrapper.findAll('button').find((b) => b.text().includes('确认可疑'))!.trigger('click')
    await flushPromises()

    expect(api.reviewFlag).toHaveBeenCalledWith(21, 'confirmed')
    expect(api.getFlaggedSubmissions).toHaveBeenCalled()
  })

  it('点「驳回（误报）」提交 dismissed 并重拉', async () => {
    api.reviewFlag.mockResolvedValue({ id: 21, verdict: 'dismissed' } as never)
    const wrapper = await mountView()
    api.getFlaggedSubmissions.mockClear()

    await wrapper.findAll('button').find((b) => b.text().includes('驳回'))!.trigger('click')
    await flushPromises()

    expect(api.reviewFlag).toHaveBeenCalledWith(21, 'dismissed')
    expect(api.getFlaggedSubmissions).toHaveBeenCalled()
  })

  it('判过的条目显示「撤销判定」与判定徽章', async () => {
    const wrapper = await mountView([reviewedItem])

    expect(wrapper.text()).toContain('撤销判定')
    expect(wrapper.text()).toContain('确认可疑')
    expect(wrapper.text()).toContain('已确认')
  })

  it('勾选「显示已复核的」重拉并带上意图', async () => {
    const wrapper = await mountView([openItem])
    api.getFlaggedSubmissions.mockClear()

    const checkbox = wrapper.findAllComponents(NCheckbox)[0]
    checkbox.vm.$emit('update:checked', true)
    await flushPromises()

    expect(api.getFlaggedSubmissions).toHaveBeenCalledWith(50, true)
  })
})
