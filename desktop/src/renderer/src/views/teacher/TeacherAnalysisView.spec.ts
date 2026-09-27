// @vitest-environment jsdom
/**
 * TeacherAnalysisView 的视图测试（2026-09-27，#4 测试补全）。
 *
 * 这个面板是"读 + 下钻"型页面，钉的事：
 *   1. **错因/知识点/学生三张表都来自接口**，且知识点与学生都按正确率
 *      升序（最弱在最上面）——排序是产品语义，不是巧合。
 *   2. **「未分析」是缺席不是原因**。当未分析成为最大的一组时，页面要
 *      明确说"这是分析在补跑，不代表学生的问题"（文件头原话）；否则不
 *      出现这句自我解释。
 *   3. **点学生行下钻时间线**：拉 `getStudentTimeline`，渲染条目，再点
 *      收起。
 *
 * 图表组件（BarChart/PieChart）桩掉：它们内部引 ECharts，jsdom 下不
 * 渲染；桩保留 props 以便断言数据透传。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import TeacherAnalysisView from './TeacherAnalysisView.vue'
import type { ErrorCount, KnowledgeStatOut, StudentOut } from '../../api/teacher'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() })
}))

vi.mock('../../api/session', () => ({
  sessionReady: { value: true },
  isTeacher: { value: true },
  isLoggedIn: { value: true },
  sessionUser: { value: null },
  logout: vi.fn()
}))

vi.mock('../../api/teacher', () => ({
  getErrorDistribution: vi.fn(),
  getKnowledgeStats: vi.fn(),
  getStudents: vi.fn(),
  getStudentTimeline: vi.fn()
}))

// 图表桩：不渲染 ECharts，保留可读的 class 供断言透传。
vi.mock('../../components/PieChart.vue', () => ({
  default: { name: 'PieChartStub', template: '<div class="pie-stub" />' }
}))
vi.mock('../../components/BarChart.vue', () => ({
  default: { name: 'BarChartStub', template: '<div class="bar-stub" />' }
}))

const api = vi.mocked(await import('../../api/teacher'))

/** 错因分布：未分析在最前且是最大组（触发"分析还在补跑"提示）。 */
const distribution: ErrorCount[] = [
  { label: null, description: '', count: 12, share: 0.6 },
  { label: 'integer_overflow', description: '整数溢出', count: 8, share: 0.4 }
]

const knowledge: KnowledgeStatOut[] = [
  { tag: '动态规划', total: 20, passed: 14, accuracy: 0.7 },
  { tag: '数组', total: 10, passed: 2, accuracy: 0.2 }
]

const students: StudentOut[] = [
  {
    user_id: 1,
    username: 'zhangsan',
    display_name: '张三',
    submissions: 5,
    accepted: 1,
    accuracy: 0.2,
    mistakes: 4,
    flagged_submissions: 2,
    weakest_tag: '数组'
  }
]

async function mountView(
  dist: ErrorCount[] = distribution,
  know: KnowledgeStatOut[] = knowledge,
  stud: StudentOut[] = students
): Promise<VueWrapper> {
  api.getErrorDistribution.mockResolvedValue(dist)
  api.getKnowledgeStats.mockResolvedValue(know)
  api.getStudents.mockResolvedValue(stud)
  const wrapper = mount(TeacherAnalysisView)
  await flushPromises()
  await nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TeacherAnalysisView', () => {
  it('渲染错因分布表：行、次数、占比，以及两个图表桩', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('错因分布')
    expect(wrapper.text()).toContain('整数溢出')
    expect(wrapper.text()).toContain('未分析')
    expect(wrapper.text()).toContain('60.0%')
    expect(wrapper.find('.pie-stub').exists()).toBe(true)
    expect(wrapper.find('.bar-stub').exists()).toBe(true)
  })

  it('未分析是最大的一组时显示「分析还在补跑」自我说明', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('分析还在补跑')
  })

  it('未分析不是最大组时不显示自我说明', async () => {
    const wrapper = await mountView([
      { label: 'integer_overflow', description: '整数溢出', count: 8, share: 0.8 },
      { label: null, description: '', count: 2, share: 0.2 }
    ])
    expect(wrapper.text()).not.toContain('分析还在补跑')
  })

  it('知识点表按正确率升序（最弱在前）且数字来自接口', async () => {
    const wrapper = await mountView()

    const rows = wrapper.findAll('.af-t-table')[1].findAll('tbody tr')
    expect(rows[0].text()).toContain('数组')
    expect(rows[0].text()).toContain('20.0%')
    expect(rows[1].text()).toContain('动态规划')
    expect(rows[1].text()).toContain('70.0%')
  })

  it('没有错题记录时显示空态', async () => {
    const wrapper = await mountView([])
    expect(wrapper.text()).toContain('还没有错题记录')
  })

  it('点学生行拉时间线并渲染；再点收起', async () => {
    api.getStudentTimeline.mockResolvedValue([
      {
        id: 1,
        problem_id: 12,
        status: 'WA',
        error_type: 'integer_overflow',
        error_description: '整数溢出',
        created_at: '2026-09-20T10:00:00Z'
      }
    ])
    const wrapper = await mountView()

    // 学生清单是第三张表（错因 / 知识点 / 学生）。
    const row = wrapper.findAll('.af-t-table')[2].findAll('tbody tr')[0]
    await row.trigger('click')
    await flushPromises()

    expect(api.getStudentTimeline).toHaveBeenCalledWith(1)
    expect(wrapper.text()).toContain('张三 的错题时间线')
    expect(wrapper.text()).toContain('题目 12')

    // 收起：再点一次同一行（openStudent 对选中者 toggle 关闭）。
    await wrapper.findAll('button').find((b) => b.text().includes('收起'))!.trigger('click')
    await nextTick()
    // 注意学生清单的说明文案含「错题时间线」，所以断言带姓名的标题消失。
    expect(wrapper.text()).not.toContain('张三 的错题时间线')
  })
})
