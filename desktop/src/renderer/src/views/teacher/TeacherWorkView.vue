<script setup lang="ts">
/**
 * TeacherWorkView —— 教学运营：作业（2026-09-26 由 TeacherView 拆出）。
 *
 * 这个面板回答"我布置了什么、谁还没交"，以及"我设的 AI 强度有没有被用
 * 到"。后者（AI 使用记录）从原来那个大页面的第八块搬进来，因为脱离作业
 * 单独看它没有落点——它衡量的正是作业上那个档位设置。
 *
 * 内部层级
 * --------
 *   一级  作业列表（含每份的完成情况，点开才算）
 *   二级  「谁没交」——**提到统计表之前**
 *   三级  「布置作业」表单（点按钮才展开，不占常驻版面）
 *
 * 为什么"谁没交"要提到表格前面（2026-09-26 调整）
 * ----------------------------------------------
 * 拆分前它是统计表格底下一行 `还没交（3 人）：…`。但老师打开一份作业之
 * 后，**唯一会立刻行动**的就是这条：去看那三个人的名字。逐题正确率是
 * 备课时看的，交没交是下课前看的。顺序反了。
 *
 * 作业本身就是「一组题目 + 截止时间」。谁交了、谁没交是**临时算出来的**
 * （名册减去有提交的人），不是存在库里的状态——少一处需要跟着每次提交
 * 同步的地方。
 */

import { computed, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NDropdown,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NTag,
  useDialog
} from 'naive-ui'
import type { DropdownOption, SelectOption } from 'naive-ui'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import { isTeacher, logout } from '../../api/session'
import { listClassrooms, type ClassroomOut } from '../../api/classroom'
import {
  createAssignment,
  deleteAssignment,
  duplicateAssignment,
  getAssignmentStats,
  getStudentReport,
  listAssignments,
  type AssignmentOut,
  type AssignmentStats,
  type StudentReport
} from '../../api/assignments'
import {
  getAssistanceUsage,
  listLevels,
  type AssistancePolicy,
  type AssistanceUsage
} from '../../api/assistance'
import { getStudents, type StudentOut } from '../../api/teacher'
import { listProblems, type ProblemSummary } from '../../api/problems'
import AssistLevelPicker from '../../components/AssistLevelPicker.vue'
import { useTeacherPanel } from './useTeacherPanel'
import { dateText, messageOf, percent, progressText } from './format'

const loading = ref(false)
const errorMsg = ref('')

/** 危险操作的确认框，见 `confirmDanger`。需要 `App.vue` 里的 `NDialogProvider`。 */
const dialog = useDialog()

const assignments = ref<AssignmentOut[]>([])
/** 打开着统计的那份作业的 id，`null` 表示都收起。 */
const openAssignmentId = ref<number | null>(null)
const assignmentStats = ref<AssignmentStats | null>(null)
const assignmentLoading = ref(false)

const addingAssignment = ref(false)
const newAssignmentTitle = ref('')
/**
 * 选中的题目 id，**按点选顺序**。
 *
 * 顺序有意义：它就是题目在这份作业里呈现的顺序（服务端也照这个顺序
 * 返回统计）。所以用数组而不是 Set——Set 的迭代顺序虽然也保插入序，
 * 但语义上它表达的是"属于"，会被后来的人误当成可以随便重排。
 *
 * 2026-09-26 之前这里是一个字符串 `"1,2,3"`，靠手打编号。见下面
 * `problems` 那一整段的说明。
 */
const newAssignmentProblemIds = ref<number[]>([])
const newAssignmentDue = ref('')
const assignmentBusy = ref(false)

/**
 * 正在编写的这份作业的辅助强度（阶段 5）。
 *
 * 存**档位数字**或 `null`。`null` 表示"还没选，用服务端默认"，提交时翻成
 * `undefined` 让服务端定。于是**默认值只有一份，在服务端**。一个预填了
 * 客户端 3 的选择器今天看着一模一样，默认值移动的那天就会静默地不一致。
 *
 * 2026-09-26 从字符串哨兵改成数字：选择器不再是 `<NSelect>`，而是一个
 * 直接把档号发出来的刻度控件，哨兵那一层（`'__default__'`）没有存在
 * 理由了 —— 它的全部用途是在 NSelect 里把"默认"和"未选择"分开，而这里
 * 两者本来就是一回事（都是 `null`）。
 *
 * 发布成功后重置回 `null`，让下一份作业从服务端的默认值开始，而不是继承
 * 上一份。要老师自己记得复位的话，迟早会误发一份五档作业。
 */
const newAssignmentLevel = ref<number | null>(null)

/**
 * 这份作业布置给谁（PU-13）。值是选项表里的字符串（`'__all__'` 或班级 id），
 * "所有人"是**故意的默认**：还没想过班级这件事的老师，应该可以先发一组
 * 练习题，而不必先建一个班。
 */
const newAssignmentClassId = ref<string | null>('__all__')

const classrooms = ref<ClassroomOut[]>([])

async function loadClassrooms(): Promise<void> {
  try {
    classrooms.value = await listClassrooms()
  } catch {
    // 退化成"没有选择器"：作业仍然可以发给所有人，也就是班级存在之前的
    // 那个状态。为少一个选项而弹一条错误横幅，会挡住一个本来能用的操作。
    classrooms.value = []
  }
}

/** 五档强度，给选择器用。取一次——那张表是静态的。 */
const assistanceLevels = ref<AssistancePolicy[]>([])

async function loadAssistanceLevels(): Promise<void> {
  try {
    assistanceLevels.value = await listLevels()
  } catch {
    // 同样退化成"没有选择器"：没有这张表，服务端仍会套用它自己的默认值，
    // 作业还是能发——只是调不了档。
    assistanceLevels.value = []
  }
}

/**
 * 服务端默认档位是哪一档。
 *
 * 取**档位表正中间**那一项而不是硬编码 3：这个常量在服务端只有一份
 * （`app/assistance.py` 的 `DEFAULT_ASSISTANCE_LEVEL`），在这里再写一个
 * 数字就会漂移。中间档是服务端的选取规则（见 `normalize_level`：配置
 * 缺失时偏向严格），这里跟着它的规则走，而不是跟着它的当前值走。
 *
 * 它唯一的用途是给选择器渲染「使用默认（N 档）」那句话。老师看到的
 * 默认值因此是"服务端将要用的那一档"，而不是一个客户端猜的数。
 */
const defaultLevelPolicy = computed<AssistancePolicy | null>(
  () => assistanceLevels.value[Math.floor(assistanceLevels.value.length / 2)] ?? null
)

/**
 * 「布置给」下拉的选项（2026-09-26 从 `<option>` 元素改过来）。
 *
 * `null` 必须显式作为一个选项存在，不能靠"没选就是 null"：NSelect 里
 * `null` 是**一个可选值**，不是"空"。省掉它，老师就回不到"所有人"了。
 * `key` 用 `null` 会让 Vue 的 key 分组出问题，所以给字符串哨兵。
 *
 * 类型写 `SelectOption` 而不是让它推断：推断出的 `value: number | null`
 * 落到 NSelect 的联合类型上会被当成 `SelectIgnoredOption`（那个分支要求
 * 有 `type` 字段），报一个和真正问题无关的错。
 */
const classroomOptions = computed<SelectOption[]>(() => [
  { label: '所有人（不限定班级）', value: '__all__' },
  ...classrooms.value.map((room) => ({ label: room.name, value: String(room.id) }))
])

function classFromOption(raw: string | number | null): number | null {
  if (raw === null || raw === '__all__') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

/* ===== 选题器（2026-09-26） =====
 *
 * 之前这里是一个文本框，占位符写着「1,2,3」——老师得先记住题目编号，
 * 或者切到题库页抄下来再回来打。那件事有三个问题：
 *
 *   1. 编号不是人记的东西。题库里题目的**标题**才是（「最长上升子序列」），
 *      编号是数据库的产物。
 *   2. 打错一个数字不会被拦住。它会作为一次 422 回来，而老师从这里没法
 *      处理那个 422——他只知道"发不出去"。
 *   3. 想"把动态规划那类题一起布置下去"这件事，靠手打编号做不到。
 *
 * 换成列表勾选 + 搜索 + 知识点/难度筛选。所有筛选都走服务端已有的
 * `/api/v1/problems` 参数（`q` / `knowledge_tag` / `difficulty`），
 * 不新增接口，也不在客户端过滤——题库会长大，客户端全量拉下来
 * 迟早会变成一次很慢的请求。
 */

/** 当前这一页题目（服务端分页）。 */
const problems = ref<ProblemSummary[]>([])
const problemsLoading = ref(false)
const problemsError = ref('')
const problemTotal = ref(0)
const PROBLEM_PAGE_SIZE = 20
/** 已经拉取到的页数，用于"加载更多"。 */
const problemPagesLoaded = ref(0)

/** 搜索框内容。走服务端的标题子串匹配，不是客户端过滤。 */
const problemQuery = ref('')
const problemTag = ref<string | null>(null)
const problemDifficulty = ref<string | null>(null)

/**
 * 已经知道的知识点标签。
 *
 * 服务端没有"列出全部标签"的接口，而从语料看标签是有限且高频复用的
 * （数组 / 循环 / 动态规划 / 字符串 …，55 道题只用到三十来个）。
 * 所以做法是：每拉一页题目就把见过的标签累积起来。老师翻两页就能看到
 * 大部分标签，而且**只会出现真的有题目的标签**——一个筛出来是空的
 * 选项比没有这个选项更让人困惑。
 */
const knownTags = ref<string[]>([])

/** 标签下拉的选项。 */
const tagOptions = computed<SelectOption[]>(() => [
  { label: '全部知识点', value: '' },
  ...[...knownTags.value].sort().map((t) => ({ label: t, value: t }))
])

/** 难度下拉的选项。 */
const difficultyOptions: SelectOption[] = [
  { label: '全部难度', value: '' },
  { label: '简单', value: 'easy' },
  { label: '中等', value: 'medium' },
  { label: '困难', value: 'hard' }
]

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: '简单',
  medium: '中等',
  hard: '困难'
}

/** 并发保护：快速改筛选时，先发的请求可能后回来。 */
let problemsRequestSeq = 0

/**
 * 拉题目列表。
 *
 * `append` 为真时是"加载更多"（页码递增）；为假时是重新筛选（回到第 1 页，
 * 并且**清空列表**——不清的话旧筛选的结果会和新的混在一起，看着像筛选没生效）。
 */
async function loadProblems(append = false): Promise<void> {
  if (!isTeacher.value) return
  problemsLoading.value = true
  problemsError.value = ''
  const seq = ++problemsRequestSeq
  const page = append ? problemPagesLoaded.value + 1 : 1
  try {
    const res = await listProblems({
      page,
      page_size: PROBLEM_PAGE_SIZE,
      q: problemQuery.value.trim() || undefined,
      knowledge_tag: problemTag.value || undefined,
      difficulty: (problemDifficulty.value || undefined) as 'easy' | 'medium' | 'hard' | undefined
    })
    if (seq !== problemsRequestSeq) return
    problems.value = append ? [...problems.value, ...res.items] : res.items
    problemTotal.value = res.total
    problemPagesLoaded.value = page
    // 见过的标签并进已知集合。用 Set 去重后回写，否则每次翻页都会
    // 在数组里堆一批重复项。
    const seen = new Set([...knownTags.value, ...res.items.flatMap((p) => p.knowledge_tags)])
    knownTags.value = [...seen]
  } catch (err) {
    if (seq !== problemsRequestSeq) return
    problemsError.value = messageOf(err, '无法加载题库')
  } finally {
    if (seq === problemsRequestSeq) problemsLoading.value = false
  }
}

/** 筛选条件变了：重拉第一页。 */
function reloadProblems(): void {
  problemPagesLoaded.value = 0
  void loadProblems(false)
}

/** 搜索防抖。每敲一个字都发请求既浪费也会让列表闪。 */
let searchTimer: ReturnType<typeof setTimeout> | undefined
function onProblemQueryChange(): void {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(reloadProblems, 300)
}

const hasMoreProblems = computed(() => problems.value.length < problemTotal.value)

/** 这道题被选中了吗。 */
function isPicked(id: number): boolean {
  return newAssignmentProblemIds.value.includes(id)
}

/**
 * 勾选 / 取消一道题。
 *
 * 取消时用 filter 保序删掉那一个，而不是 push/splice 到别处：
 * 顺序就是作业里的顺序，老师勾的先后看得见。
 */
function toggleProblem(id: number): void {
  newAssignmentProblemIds.value = isPicked(id)
    ? newAssignmentProblemIds.value.filter((x) => x !== id)
    : [...newAssignmentProblemIds.value, id]
}

/** 「全选本页」——只作用于当前列表里显示的那些，不是全库。 */
const allPagePicked = computed(
  () => problems.value.length > 0 && problems.value.every((p) => isPicked(p.id))
)

function togglePage(): void {
  if (allPagePicked.value) {
    const onPage = new Set(problems.value.map((p) => p.id))
    newAssignmentProblemIds.value = newAssignmentProblemIds.value.filter((id) => !onPage.has(id))
  } else {
    const have = new Set(newAssignmentProblemIds.value)
    const added = problems.value.map((p) => p.id).filter((id) => !have.has(id))
    newAssignmentProblemIds.value = [...newAssignmentProblemIds.value, ...added]
  }
}

/** 已选题目的详情，用于"已选 N 题"那一条的预览。 */
const pickedProblems = computed(() =>
  newAssignmentProblemIds.value.map((id) => ({
    id,
    title: problems.value.find((p) => p.id === id)?.title ?? `第 ${id} 题`
  }))
)

function unpick(id: number): void {
  newAssignmentProblemIds.value = newAssignmentProblemIds.value.filter((x) => x !== id)
}

function clearPicked(): void {
  newAssignmentProblemIds.value = []
}

/**
 * 打开弹窗时初始化选题器。
 *
 * 每次都**清空筛选**但**保留已选**（重开弹窗通常是想继续编辑上一次的
 * 选择）。同时重置分页状态，否则上一次翻到第 3 页，这次打开会直接
 * 显示"还能加载更多"但其实只有一页。
 */
function openAssignmentModal(): void {
  addingAssignment.value = true
  problemQuery.value = ''
  problemTag.value = null
  problemDifficulty.value = null
  problemPagesLoaded.value = 0
  void loadProblems(false)
}

/**
 * 危险操作的确认框，返回 `true` 表示老师确认了。
 *
 * 两个容易漏的点：`onMaskClick` / `onClose` 也**必须**结算 promise——
 * 点遮罩或按 Esc 关掉对话框时如果不 resolve，这个 await 就永远挂着，
 * 调用它的函数再也不会返回（页面看起来像卡死，但不报任何错）。
 */
function confirmDanger(title: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.warning({
      title,
      content,
      positiveText: '删除',
      negativeText: '取消',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onMaskClick: () => resolve(false),
      onClose: () => resolve(false)
    })
  })
}

/** 每份作业行右侧「更多」里的操作。收进去的只有删除——见模板注释。 */
const ASSIGNMENT_MENU: DropdownOption[] = [{ label: '删除作业', key: 'delete' }]

function onAssignmentMenu(a: AssignmentOut, key: string): void {
  if (key === 'delete') void removeAssignment(a)
}

/**
 * 次要区块是否展开。
 *
 * 两个都**默认收起**：它们是查证性的数据（"AI 到底有没有被用上"、"某个
 * 学生交了几次"），不是每次打开这个页面都要看的。三块内容平铺在同一个
 * 视觉重量上，主线会被淹掉——这就是这次调整要解决的问题。
 */
const showUsage = ref(false)
const showReport = ref(false)

/**
 * `NCollapse` 要的是一个「展开了哪些」的数组，而上面两个 ref 是
 * 「展开了吗」的布尔。用一个可写 computed 在两者之间换算，而不是把
 * 状态改成数组——数组形式会让"默认收起"变成"默认空数组"，
 * 而空数组和"取消勾选"在模板里读不出区别。
 */
const collapsedNames = computed<string[]>({
  get: () => [...(showUsage.value ? ['usage'] : []), ...(showReport.value ? ['report'] : [])],
  set: (names) => {
    showUsage.value = names.includes('usage')
    showReport.value = names.includes('report')
  }
})

async function loadAssignments(): Promise<void> {
  try {
    assignments.value = await listAssignments()
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载作业列表')
  }
}

// --- AI 使用记录（阶段 5）---

/**
 * 这个班一共用了多少 AI 帮助，跨全部作业。
 *
 * 按班级总量读、不按单份作业读，因为这个面板回答的是"我设的那个强度到底
 * 有没有被用"——而只有覆盖同一批人时两个数才可比。接口支持按作业细分，
 * 但那需要一个老师必须先操作才能看到任何东西的选择器，那正是一个面板变
 * 成没人看的路径。
 */
const assistanceUsage = ref<AssistanceUsage | null>(null)
const assistanceUsageLoading = ref(false)
const assistanceUsageError = ref('')

async function loadAssistanceUsage(): Promise<void> {
  assistanceUsageLoading.value = true
  assistanceUsageError.value = ''
  try {
    assistanceUsage.value = await getAssistanceUsage()
  } catch (err) {
    // 与 `errorMsg` 分开是有意的：这是控制台的**一块**失败了，一条页面级
    // 横幅会暗示整个面板的数据都过期了，而其余部分加载得好好的。
    assistanceUsageError.value = messageOf(err, '无法加载 AI 使用记录')
    assistanceUsage.value = null
  } finally {
    assistanceUsageLoading.value = false
  }
}

/**
 * 每个动作一行，`[标签, 次数]`，多的在前。
 *
 * 在这里排而不是在服务端排：接口返回的是一个 dict，而 Python dict 的顺序
 * 是常量的书写顺序，不是谁选过的顺序。不排序就当排名读，是在读一个巧合。
 */
const assistanceActionRows = computed<[string, number][]>(() => {
  const labeled = assistanceUsage.value?.by_action_labeled ?? {}
  return Object.entries(labeled).sort((a, b) => b[1] - a[1])
})

// --- 成绩单（阶段 5）---

/**
 * 一名学生对着**全部作业**的完成情况。
 *
 * 为什么放在作业面板而不是名册：它回答的是"这一学期他怎么样"，是一个
 * 复盘时要问的问题。名册面板管的是账号（谁有账号、密码是多少），两件事
 * 的改动节奏完全不同——名册一个学期动两次，成绩单每周都看。
 *
 * 选择器从原来那排人名 chip 改成**一个搜索框**（2026-09-26）：chip 在
 * 五个人时像按钮，在五十个人时是一堵墙，而墙的代价是老师用滚轮找人。
 */
const report = ref<StudentReport | null>(null)
const reportLoading = ref(false)
const reportError = ref('')
const reportQuery = ref('')

const students = ref<StudentOut[]>([])

async function loadStudents(): Promise<void> {
  try {
    students.value = await getStudents()
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载学生名单')
  }
}

/** 搜索命中的学生，最多给八条——下拉是选人，不是浏览名册。 */
const reportMatches = computed<StudentOut[]>(() => {
  const q = reportQuery.value.trim().toLowerCase()
  if (!q) return []
  return students.value
    .filter((s) => s.display_name.toLowerCase().includes(q) || s.username.toLowerCase().includes(q))
    .slice(0, 8)
})

async function openReport(userId: number, displayName: string): Promise<void> {
  reportLoading.value = true
  reportError.value = ''
  reportQuery.value = ''
  report.value = {
    user_id: userId,
    display_name: displayName,
    username: '',
    rows: [],
    completed: 0,
    total_assignments: 0
  }
  try {
    report.value = await getStudentReport(userId)
  } catch (err) {
    reportError.value = messageOf(err, `无法加载 ${displayName} 的成绩单`)
    report.value = null
  } finally {
    reportLoading.value = false
  }
}

function closeReport(): void {
  report.value = null
  reportError.value = ''
}

// --- 加载 ---

async function load(): Promise<void> {
  if (!isTeacher.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    await Promise.all([
      loadAssignments(),
      loadAssistanceUsage(),
      loadAssistanceLevels(),
      loadClassrooms(),
      loadStudents()
    ])
  } finally {
    loading.value = false
  }
}

function reset(): void {
  assignments.value = []
  openAssignmentId.value = null
  assignmentStats.value = null
  assistanceUsage.value = null
  report.value = null
}

const panel = useTeacherPanel(load, reset)

async function signOut(): Promise<void> {
  await logout()
  panel.reset()
}

// --- 作业操作 ---

async function toggleAssignment(assignment: AssignmentOut): Promise<void> {
  if (openAssignmentId.value === assignment.id) {
    openAssignmentId.value = null
    assignmentStats.value = null
    return
  }
  openAssignmentId.value = assignment.id
  assignmentStats.value = null
  assignmentLoading.value = true
  try {
    assignmentStats.value = await getAssignmentStats(assignment.id)
  } catch (err) {
    errorMsg.value = messageOf(err, `无法加载「${assignment.title}」的完成情况`)
  } finally {
    assignmentLoading.value = false
  }
}

async function submitAssignment(): Promise<void> {
  // 选中的题目 id，保持老师点选的顺序（那就是题目在作业里的呈现顺序）。
  const ids = [...newAssignmentProblemIds.value]
  if (ids.length === 0) {
    errorMsg.value = '至少要选一道题'
    return
  }
  assignmentBusy.value = true
  try {
    await createAssignment({
      title: newAssignmentTitle.value,
      problem_ids: ids,
      // ``datetime-local`` 给的是 "YYYY-MM-DDTHH:mm"，没有时区，正好是
      // ``datetime.fromisoformat`` 接受的形式。空串表示"没有截止时间"，
      // 而把空串发出去会被当成一个非法日期。
      due_at: newAssignmentDue.value ? newAssignmentDue.value : undefined,
      // 没碰过就**不发**（而不是发 null），让服务端的默认值生效——
      // 见 `newAssignmentLevel`。
      // 已经是一个数字或 null（见 `newAssignmentLevel`）。`null` 翻成
      // `undefined`，让服务端用它自己的默认档 —— 而不是客户端猜一个。
      assistance_level: newAssignmentLevel.value ?? undefined,
      // "所有人"不发送该字段，而不是发 null，让这件事只有一种写法——
      // 见 `newAssignmentClassId`。
      class_id: classFromOption(newAssignmentClassId.value) ?? undefined
    })
    addingAssignment.value = false
    newAssignmentTitle.value = ''
    newAssignmentProblemIds.value = []
    newAssignmentDue.value = ''
    newAssignmentLevel.value = null
    newAssignmentClassId.value = '__all__'
    await Promise.all([loadAssignments(), loadAssistanceUsage()])
  } catch (err) {
    errorMsg.value = messageOf(err, '无法发布作业')
  } finally {
    assignmentBusy.value = false
  }
}

async function removeAssignment(assignment: AssignmentOut): Promise<void> {
  // 删除有确认，复制没有——两者都发生在同一行上，所以这个差别必须在
  // **点击之前**就看得出来（见模板里删除被收进「更多」）。
  const ok = await confirmDanger(
    '删除作业',
    `删除「${assignment.title}」？学生的提交记录不会删除，但这份作业本身没了。`
  )
  if (!ok) return
  try {
    await deleteAssignment(assignment.id)
    if (openAssignmentId.value === assignment.id) {
      openAssignmentId.value = null
      assignmentStats.value = null
    }
    await loadAssignments()
  } catch (err) {
    errorMsg.value = messageOf(err, `无法删除「${assignment.title}」`)
  }
}

/**
 * 复制一份作业（阶段 5）。
 *
 * 不要确认框，这是有意的：复制只是新增一行，什么都不会毁掉——所以在一堆
 * 操作里，它是那个"错了也没关系"的，还要先问一句就是摩擦。删除有它自己
 * 的提示，理由正好相反。
 */
const duplicatingId = ref<number | null>(null)

async function copyAssignment(assignment: AssignmentOut): Promise<void> {
  duplicatingId.value = assignment.id
  try {
    await duplicateAssignment(assignment.id)
    await loadAssignments()
  } catch (err) {
    errorMsg.value = messageOf(err, `无法复制「${assignment.title}」`)
  } finally {
    duplicatingId.value = null
  }
}
</script>

<template>
  <div class="af-t-page">
    <div class="af-t-content">
      <PageHeader
        title="作业"
        subtitle="发布作业、看谁还没交、核对 AI 辅助强度有没有被用上"
        :loading="panel.teacher.value ? loading : undefined"
        :error="errorMsg"
        error-prefix="无法加载作业"
        @refresh="load"
      />

      <TeacherGate
        :ready="panel.ready.value"
        :is-teacher="panel.teacher.value"
        login-title="作业面板需要登录"
        login-body="布置作业、查看完成情况都记在教师账号上，登录后才会显示。"
        @sign-out="signOut"
      >
        <!-- --- 作业 --- -->
        <section class="af-t-section">
          <h2 class="af-t-section-title">作业</h2>
          <p class="af-t-section-note">
            作业就是「一组题目 + 截止时间」。谁交了、谁没交是<strong>临时算出来的</strong>
            （名册减去有提交的人），不是存在库里的状态——少一处需要跟着每次提交同步的地方。
          </p>

          <div v-if="assignments.length === 0" class="af-t-empty">还没有布置作业。</div>

          <ul v-else class="assignment-list">
            <li
              v-for="a in assignments"
              :key="a.id"
              class="assignment-item"
              :class="{ 'is-open': openAssignmentId === a.id }"
            >
              <!-- 一行三组信息，用栅格分开而不是让它们自由流：
                   左边「哪份作业」、中间「什么状态」、右边「能对它做什么」。
                   自由流（原来的写法）会让六种东西按内容长度挤在一起，
                   长标题和短标题的两行对不齐。 -->
              <div class="assignment-head">
                <button type="button" class="assignment-title" @click="toggleAssignment(a)">
                  <span class="assignment-caret">{{ openAssignmentId === a.id ? '−' : '+' }}</span>
                  {{ a.title }}
                </button>

                <div class="assignment-meta-row">
                  <span class="assignment-meta">{{ a.problem_ids.length }} 题</span>
                  <span v-if="a.due_at" class="assignment-meta" :class="{ overdue: a.overdue }">
                    {{ a.overdue ? '已过期 · ' : '' }}{{ dateText(a.due_at) }}
                  </span>
                  <!-- 生效中的档位，从行上读、不是从表单猜：学生实际拿到的东西。 -->
                  <span class="assignment-level-badge" :title="a.assistance?.description">
                    AI {{ a.assistance_level }} 档
                  </span>
                </div>

                <div class="assignment-actions">
                  <!-- 复制直接显示：点错了没后果，所以它不配得一次确认，
                       也不该藏在菜单后。 -->
                  <NButton
                    size="tiny"
                    quaternary
                    :loading="duplicatingId === a.id"
                    title="复制成一份新作业，题目和辅助强度一起带过去"
                    @click="copyAssignment(a)"
                  >
                    复制
                  </NButton>
                  <!-- 删除收进菜单：它和复制在同一行上，但一个是可逆的、
                       一个不是。大小一样地并排摆着，会让"删除"看起来和
                       "复制"一样随便——而它不该。 -->
                  <NDropdown
                    trigger="click"
                    :options="ASSIGNMENT_MENU"
                    @select="(key) => onAssignmentMenu(a, String(key))"
                  >
                    <NButton size="tiny" quaternary class="assignment-more">⋯</NButton>
                  </NDropdown>
                </div>
              </div>

              <!-- 按需加载：统计是每份作业的第二次请求，
                   打开一份的老师不该为所有作业付这个钱。 -->
              <div v-if="openAssignmentId === a.id" class="assignment-stats">
                <p v-if="assignmentLoading" class="af-t-placeholder-inline">统计中…</p>
                <template v-else-if="assignmentStats">
                  <!--
                    先讲交没交，再讲做对没做对。这是这个面板上唯一一条
                    老师会立刻行动的信息，所以它在表格之前。
                  -->
                  <p
                    class="assignment-missing"
                    :class="{ 'has-missing': assignmentStats.students_missing.length > 0 }"
                  >
                    <template v-if="assignmentStats.students_missing.length">
                      还没交（{{ assignmentStats.students_missing.length }} 人）：
                      {{ assignmentStats.students_missing.join('、') }}
                    </template>
                    <template v-else>全班都交了。</template>
                  </p>

                  <div class="af-t-table-scroll">
                    <table class="af-t-table">
                      <thead>
                        <tr>
                          <th>题目</th>
                          <th class="af-t-num">提交</th>
                          <th class="af-t-num">通过</th>
                          <th class="af-t-num">正确率</th>
                          <th class="af-t-num">做过的人数</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="p in assignmentStats.problems" :key="p.problem_id">
                          <td>第 {{ p.problem_id }} 题</td>
                          <td class="af-t-num">{{ p.submissions }}</td>
                          <td class="af-t-num">{{ p.accepted }}</td>
                          <td class="af-t-num">{{ percent(p.accuracy) }}</td>
                          <td class="af-t-num">{{ p.students }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </template>
              </div>
            </li>
          </ul>

          <NButton type="primary" @click="openAssignmentModal">布置作业</NButton>
        </section>

        <!-- --- 次要区块（2026-09-26 收成折叠） ---
             这两块原来是和「作业」平级的两个 h2 区块，视觉重量完全相同。
             但它们不是每次打开这个页面都要看的东西：
               · AI 使用记录衡量的是"档位设置有没有被用上"，是查证
               · 学生成绩单是查看某个学生的，是一次下钻
             平铺着摆，主线（今天有哪些作业、谁没交）会被淹掉。
             收起来之后，标题行仍然说明白了里面是什么、以及现在有没有内容
             ——收起不等于隐藏。 -->
        <div class="af-t-collapse-group">
          <!-- AI 使用记录（阶段 5） -->
          <NCollapse v-model:expanded-names="collapsedNames">
            <NCollapseItem name="usage">
              <template #header>
                <span class="collapse-title">AI 使用记录</span>
                <span class="collapse-hint">
                  <template v-if="assistanceUsage && assistanceUsage.total > 0">
                    共 {{ assistanceUsage.total }} 次 · 看档位有没有被用上
                  </template>
                  <template v-else-if="assistanceUsageError">读取失败</template>
                  <template v-else>还没有记录</template>
                </span>
              </template>

              <p class="af-t-section-note">
                记录了每次<strong>动作</strong>，不记录对话内容——学生问了什么不是老师要看的东西，
                「用得多不多、用的是哪一档」才是。被拒绝的请求也记：
                反复去点被禁用的功能，正是需要留意的情况。
              </p>

              <p v-if="assistanceUsageLoading" class="af-t-placeholder-inline">读取中…</p>
              <p v-else-if="assistanceUsageError" class="af-t-error-inline">
                {{ assistanceUsageError }}
              </p>
              <!-- 明确的零，不是空面板。"没人用过"和"读取失败"在都渲染成一片
                   空白时长得一样，而它们导向相反的决定。 -->
              <p
                v-else-if="!assistanceUsage || assistanceUsage.total === 0"
                class="af-t-placeholder-inline"
              >
                还没有任何 AI 使用记录。
              </p>

              <template v-else>
                <ul class="usage-list">
                  <li
                    v-for="[label, count] in assistanceActionRows"
                    :key="label"
                    class="usage-item"
                  >
                    <span class="usage-label">{{ label }}</span>
                    <span class="usage-count">{{ count }}</span>
                  </li>
                </ul>

                <div v-if="assistanceUsage.by_student.length" class="af-t-table-scroll">
                  <table class="af-t-table">
                    <thead>
                      <tr>
                        <th>学生</th>
                        <th class="af-t-num">次数</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, i) in assistanceUsage.by_student" :key="i">
                        <td>{{ row.display_name }}</td>
                        <td class="af-t-num">{{ row.count }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </NCollapseItem>

            <!-- 学生成绩单（阶段 5） -->
            <NCollapseItem name="report">
              <template #header>
                <span class="collapse-title">学生成绩单</span>
                <span class="collapse-hint">
                  <template v-if="report">
                    {{ report.display_name }} · {{ report.total_assignments }} 份作业
                  </template>
                  <template v-else>查某个学生的全部作业完成情况</template>
                </span>
              </template>

              <p class="af-t-section-note">
                一名学生对着<strong>全部作业</strong>的完成情况。错题本看不到「一道题都没做」的学生
                ——他没有错题——所以这里用作业来算，把没交的人也算进来。
              </p>

              <!-- 搜索式选择器（2026-09-26）：换掉那排人名 chip。
                   chip 在五个人时像按钮，在五十个人时是一堵墙。 -->
              <div class="report-picker">
                <NInput
                  v-model:value="reportQuery"
                  class="report-input"
                  type="text"
                  clearable
                  placeholder="输入姓名或学号查找学生"
                />
                <ul v-if="reportMatches.length" class="report-matches">
                  <li v-for="s in reportMatches" :key="s.user_id">
                    <button
                      type="button"
                      class="report-match"
                      :class="{ active: report?.user_id === s.user_id }"
                      @click="openReport(s.user_id, s.display_name)"
                    >
                      <span class="report-match-name">{{ s.display_name || s.username }}</span>
                      <span class="report-match-user">{{ s.username }}</span>
                    </button>
                  </li>
                </ul>
                <p v-else-if="reportQuery.trim()" class="af-t-placeholder-inline">
                  没有匹配的学生。
                </p>
              </div>

              <p v-if="reportLoading" class="af-t-placeholder-inline">读取中…</p>
              <p v-else-if="reportError" class="af-t-error-inline">{{ reportError }}</p>

              <template v-else-if="report">
                <p class="usage-total">
                  {{ report.display_name }}：{{ report.total_assignments }} 份作业中参与了
                  {{ report.completed }} 份
                </p>
                <div v-if="report.rows.length" class="af-t-table-scroll">
                  <table class="af-t-table">
                    <thead>
                      <tr>
                        <th>作业</th>
                        <th class="af-t-num">题目数</th>
                        <th class="af-t-num">提交</th>
                        <th class="af-t-num">通过</th>
                        <th class="af-t-num">进度</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="row in report.rows" :key="row.assignment_id">
                        <td>
                          {{ row.title }}
                          <span v-if="row.overdue" class="assignment-meta overdue">已过期</span>
                        </td>
                        <td class="af-t-num">{{ row.problem_count }}</td>
                        <td class="af-t-num">{{ row.submissions }}</td>
                        <td class="af-t-num">{{ row.accepted }}</td>
                        <!-- 未提交 / 正确率是两种读数，见 progressText -->
                        <td class="af-t-num">{{ progressText(row) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p v-else class="af-t-placeholder-inline">还没有布置过作业。</p>
                <NButton size="tiny" quaternary @click="closeReport">收起成绩单</NButton>
              </template>
            </NCollapseItem>
          </NCollapse>
        </div>
      </TeacherGate>
    </div>

    <!-- 布置作业（2026-09-26 由内联表单改为模态框）。
         改成弹窗不是为了好看：它是**一次性的写入动作**，和列表那种反复
         查看的内容不该抢同一块版面。内联展开还会把下方的"AI 使用记录"
         整块顶下去，老师点开就丢上下文。 -->
    <NModal
      v-model:show="addingAssignment"
      preset="card"
      title="布置作业"
      :style="{ width: '620px', maxWidth: '94vw' }"
      :mask-closable="!assignmentBusy"
      :close-on-esc="!assignmentBusy"
    >
      <NForm label-placement="top" :disabled="assignmentBusy" @submit.prevent="submitAssignment">
        <NFormItem label="标题">
          <NInput v-model:value="newAssignmentTitle" placeholder="例如：第 3 次上机 · 枚举" />
        </NFormItem>

        <NFormItem label="选题">
          <!-- 三段式：上方筛选、中间列表、下方已选。
               列表给固定高度（不是 `max-height`）——作业弹窗的高度会随
               题目多少变，固定高度让它稳定下来，翻页时不会整块跳。 -->
          <div class="picker">
            <div class="picker-filters">
              <NInput
                v-model:value="problemQuery"
                class="picker-search"
                clearable
                placeholder="搜索题目标题，如「动态规划」"
                @update:value="onProblemQueryChange"
                @clear="reloadProblems"
              />
              <NSelect
                v-model:value="problemTag"
                class="picker-select"
                :options="tagOptions"
                @update:value="reloadProblems"
              />
              <NSelect
                v-model:value="problemDifficulty"
                class="picker-select"
                :options="difficultyOptions"
                @update:value="reloadProblems"
              />
            </div>

            <div class="picker-head">
              <NCheckbox
                :checked="allPagePicked"
                :indeterminate="!allPagePicked && problems.some((p) => isPicked(p.id))"
                :disabled="problems.length === 0"
                @update:checked="togglePage"
              >
                全选本页
              </NCheckbox>
              <span class="picker-count">
                <template v-if="problemsLoading">读取中…</template>
                <template v-else>筛出 {{ problemTotal }} 道</template>
              </span>
            </div>

            <div class="picker-list">
              <p v-if="problemsError" class="af-t-error-inline">{{ problemsError }}</p>
              <p v-else-if="!problemsLoading && problems.length === 0" class="picker-empty">
                没有匹配的题目。换个关键词，或把筛选放宽。
              </p>
              <ul v-else class="picker-rows">
                <li v-for="p in problems" :key="p.id">
                  <label class="picker-row" :class="{ picked: isPicked(p.id) }">
                    <NCheckbox :checked="isPicked(p.id)" @update:checked="toggleProblem(p.id)" />
                    <span class="picker-row-main">
                      <span class="picker-row-title">{{ p.title }}</span>
                      <span class="picker-row-tags">
                        <NTag v-for="t in p.knowledge_tags" :key="t" size="small" :bordered="false">
                          {{ t }}
                        </NTag>
                      </span>
                    </span>
                    <span v-if="p.difficulty" class="picker-row-diff">
                      {{ DIFFICULTY_LABEL[p.difficulty] ?? p.difficulty }}
                    </span>
                  </label>
                </li>
              </ul>

              <NButton
                v-if="hasMoreProblems && !problemsLoading"
                text
                type="primary"
                size="small"
                class="picker-more"
                @click="loadProblems(true)"
              >
                加载更多（还有 {{ problemTotal - problems.length }} 道）
              </NButton>
            </div>

            <!-- 已选。它必须一直在屏幕上：翻页或改筛选之后，之前勾的题
                 可能已经不在列表里了，而老师需要知道它们还在。 -->
            <div class="picker-picked">
              <div class="picker-picked-head">
                <span>已选 {{ newAssignmentProblemIds.length }} 题</span>
                <NButton
                  v-if="newAssignmentProblemIds.length"
                  text
                  size="tiny"
                  @click="clearPicked"
                >
                  清空
                </NButton>
              </div>
              <p v-if="newAssignmentProblemIds.length === 0" class="picker-picked-empty">
                还没选题。上面勾选，或用关键词/知识点筛出一批再「全选本页」。
              </p>
              <div v-else class="picker-chips">
                <NTag
                  v-for="(p, i) in pickedProblems"
                  :key="p.id"
                  size="small"
                  closable
                  @close="unpick(p.id)"
                >
                  {{ i + 1 }}. {{ p.title }}
                </NTag>
              </div>
            </div>
          </div>
        </NFormItem>

        <NFormItem label="截止时间（可留空）">
          <!-- Naive 的 NInput 只认 text/textarea/password，没有 datetime-local。
               这里要的就是浏览器的原生日期控件（免费拿到时区处理与本地化），
               所以保留 `<input>`，只让它继承主题变量——它是**输入控件**，但
               不能交给组件库，属于宪法里那条"不手写控件级 CSS"的已知例外。 -->
          <input v-model="newAssignmentDue" class="af-t-input" type="datetime-local" />
        </NFormItem>

        <!-- 布置给谁（PU-13）。老师有班级之后才渲染：一个空的下拉只
             提供"所有人"一个选项，而那本来就是默认；一个禁用的控件
             比没有控件更糟。 -->
        <NFormItem v-if="classrooms.length" label="布置给">
          <NSelect
            v-model:value="newAssignmentClassId"
            :options="classroomOptions"
            placeholder="所有人（不限定班级）"
          />
        </NFormItem>

        <!-- AI 辅助强度（阶段 5）。档位表加载出来才渲染：没有档位表时
             一个空选择器会静默地按服务端默认发布作业，而老师以为自己
             选过了。 -->
        <NFormItem v-if="assistanceLevels.length" label="AI 辅助强度">
          <AssistLevelPicker
            v-model="newAssignmentLevel"
            :levels="assistanceLevels"
            :default-level="defaultLevelPolicy?.level ?? null"
            allow-default
          />
        </NFormItem>
      </NForm>

      <template #footer>
        <div class="af-t-modal-actions">
          <NButton :disabled="assignmentBusy" @click="addingAssignment = false">取消</NButton>
          <NButton type="primary" :loading="assignmentBusy" @click="submitAssignment">
            发布
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<script lang="ts">
export default { name: 'TeacherWorkView' }
</script>

<style scoped>
/* --- 作业（主区块） ---
 *
 * 2026-09-26 重排。原来的每行是 `display:flex; flex-wrap:wrap; gap:10px`
 * 让六种元素自由流——长标题会把后面的标签挤到下一行，短标题又都挤在一行，
 * 于是**相邻两行的对齐关系每行都不同**，整列看起来就是乱的。
 *
 * 改成三栏栅格：`1fr`（哪份作业）· `auto`（什么状态）· `auto`（能做什么）。
 * 于是所有行的第二、三栏右边缘对齐，扫视时有一条稳定的竖线。
 */
.assignment-list {
  margin: 0 0 12px;
  padding: 0;
  list-style: none;
}

.assignment-item {
  padding: 10px 12px;
  border-bottom: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  transition: background var(--af-duration-fast) var(--af-ease);
}

.assignment-item:hover {
  background: var(--af-surface);
}

.assignment-item:last-child {
  border-bottom: none;
}

/* 打开着的那份，用左侧一道强调线条标出来。统计块会在它下面展开，
 * 没有这条线时，展开的内容和上一行看不出归属关系。 */
.assignment-item.is-open {
  background: var(--af-surface);
  box-shadow: inset 2px 0 0 var(--af-accent);
}

.assignment-head {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 16px;
}

.assignment-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  min-width: 0;
  font-size: 13px;
  font-family: inherit;
  font-weight: 600;
  text-align: left;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
}

/* 展开指示符。原来没有它，老师不知道标题是可以点的——一个蓝色的词
 * 看起来像链接，不像开关。 */
.assignment-caret {
  flex: none;
  width: 13px;
  font-size: 13px;
  line-height: 1;
  text-align: center;
  color: var(--af-text-muted);
}

.assignment-title:hover {
  text-decoration: underline;
}

.assignment-meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.assignment-meta {
  font-size: 12px;
  color: var(--af-text-muted);
  font-variant-numeric: tabular-nums;
}

.assignment-meta.overdue {
  color: var(--af-danger);
}

/* 档位是个徽标而不是一段灰字：它是这份作业的**设置**，不是它的统计值，
 * 混在一排灰色元信息里会被当成"题数/日期"的同一种东西。 */
.assignment-level-badge {
  padding: 1px 7px;
  font-size: 11px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  font-variant-numeric: tabular-nums;
}

.assignment-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 「更多」比「复制」更安静：它是入口，不是操作。 */
.assignment-more {
  font-size: 14px;
  line-height: 1;
  letter-spacing: 0.1em;
}

.assignment-stats {
  margin-top: 10px;
}

/* 这块面板上最能立刻行动的一行，所以它有自己的一行、自己的语气。
   都交了是陈述，有人没交是提醒——两者不该长得一样。 */
.assignment-missing {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--af-text-muted);
}

.assignment-missing.has-missing {
  padding: 8px 12px;
  color: var(--af-text-secondary);
  border-left: 3px solid var(--af-warning);
  background: var(--af-elevated);
  border-radius: var(--af-radius-sm);
}

/* --- 选题器 --- */

.picker {
  width: 100%;
}

.picker-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.picker-search {
  flex: 1 1 200px;
  min-width: 160px;
}

.picker-select {
  flex: 0 0 132px;
  width: 132px;
}

.picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-bottom: none;
  border-radius: var(--af-radius-sm) var(--af-radius-sm) 0 0;
}

.picker-count {
  font-size: 12px;
  color: var(--af-text-muted);
  font-variant-numeric: tabular-nums;
}

/* 固定高度而不是 max-height：弹窗高度不随筛选结果跳动。 */
.picker-list {
  height: 236px;
  overflow-y: auto;
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: 0 0 var(--af-radius-sm) var(--af-radius-sm);
}

.picker-rows {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 整行可点，不只是那个小方块——复选框只有 16px，让它成为唯一的
 * 命中目标会让这个列表用起来很累。 */
.picker-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--af-border);
}

.picker-rows li:last-child .picker-row {
  border-bottom: none;
}

.picker-row:hover {
  background: var(--af-elevated);
}

/* 选中的行整行上色，而不是只在复选框上打勾：翻一屏的时候，
 * 勾在哪里比行底色难扫。 */
.picker-row.picked {
  background: var(--af-accent-subtle);
}

.picker-row-main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.picker-row-title {
  font-size: 13px;
  line-height: 1.4;
  color: var(--af-text-primary);
}

.picker-row-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.picker-row-diff {
  flex: none;
  font-size: 11px;
  color: var(--af-text-muted);
}

.picker-empty,
.picker-more {
  display: block;
  margin: 14px auto;
}

.picker-empty {
  margin: 0;
  padding: 24px 12px;
  font-size: 13px;
  text-align: center;
  color: var(--af-text-muted);
}

/* 已选区块。和列表用一条分隔线断开，因为它在视觉上属于"结果"，
 * 不属于上面那个"可选项池子"。 */
.picker-picked {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--af-border-strong);
}

.picker-picked-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.picker-picked-empty {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-muted);
}

.picker-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

/* --- 次要区块（折叠） --- */

.af-t-collapse-group {
  margin-bottom: 24px;
  border-top: 1px solid var(--af-border);
  padding-top: 4px;
}

/* 折叠标题行：左边是区块名（和主区块的 h2 同字号但更轻），
 * 右边一句现在有什么。收起时这一行就是全部信息——
 * 所以它不是"隐藏"，是"摘要"。 */
.collapse-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.collapse-hint {
  margin-left: 10px;
  font-size: 12px;
  font-weight: 400;
  color: var(--af-text-muted);
}

/* --- AI 使用记录 / 成绩单（都在折叠块里，所以标题行本身不在这里管） --- */
.usage-total {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.usage-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 0 12px 0;
  padding: 0;
  list-style: none;
}

.usage-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  max-width: 320px;
  font-size: 13px;
}

.usage-label {
  color: var(--af-text-secondary);
}

.usage-count {
  font-variant-numeric: tabular-nums;
  color: var(--af-text-primary);
}

/* --- 成绩单选择器 --- */
.report-picker {
  margin-bottom: 12px;
}

.report-input {
  width: 100%;
  max-width: 320px;
}

.report-matches {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 6px 0 0;
  padding: 4px;
  max-width: 320px;
  list-style: none;
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
}

.report-match {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  padding: 5px 8px;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  color: var(--af-text-primary);
  background: none;
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.report-match:hover {
  background: var(--af-elevated);
}

/* 打开着的那名学生保持高亮——下面挂着一整学期的作业，
   忘了这是谁的成绩单是最容易犯的错。 */
.report-match.active {
  color: var(--af-accent);
}

.report-match-user {
  margin-left: auto;
  font-size: 12px;
  color: var(--af-text-muted);
}
</style>
