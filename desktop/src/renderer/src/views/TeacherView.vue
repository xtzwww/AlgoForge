<script setup lang="ts">
/**
 * TeacherView — the class learning console (阶段 3).
 *
 * Four states, and the first three are not error states:
 *
 *   1. 会话恢复中  — the startup round trip has not answered yet.
 *   2. 未登录      — a signpost to `/login`（表单以前长在这里）.
 *   3. 已登录但不是教师 — a plain statement and a way out.
 *   4. 教师        — the console itself.
 *
 * Three questions, in the order a teacher asks them
 * -------------------------------------------------
 *   下节课讲什么  → 错因分布（按出现次数降序）
 *   谁需要我      → 学生清单（按正确率升序）
 *   这个标记可信吗 → 存疑提交（带证据）
 *
 * The ordering *is* the content in the first two: a teacher should not
 * have to find the largest row themselves. Both come pre-sorted from the
 * server, and this page deliberately does not re-sort them — a second
 * sort is a second opinion about what matters, and the two would drift.
 *
 * Why the login moved to its own route
 * ------------------------------------
 * This page used to carry its own login form, and the reasoning was that
 * a `/login` route needs a guard deciding where to send you afterwards,
 * and a guard is a place to get the role question wrong.
 *
 * That reasoning still holds — which is why there is still no guard. What
 * changed is that the *student* side needed a login too, and two forms on
 * two pages is one form too many: somebody told "go and log in" should
 * not have to work out which of them is the real one. The form now lives
 * in `views/LoginView.vue`, and this page points at it.
 *
 * The boundary is unchanged, and was never here: every teacher route
 * refuses an unauthenticated request server-side (see
 * `app/api/teacher.py`). The card below is a signpost, not a gate.
 *
 * Why the drill-down is a panel and not a route
 * ---------------------------------------------
 * `/teacher/students/3` as a URL would imply it is shareable and
 * bookmarkable — it is not, it is a teacher looking over one shoulder at
 * one student. A selected row keeps the class context on screen, which
 * is the whole point of looking at one student *from* a class view.
 *
 * The null group in 错因分布
 * --------------------------
 * Entries whose analysis has not landed arrive as `label === null` and
 * are rendered as 「未分析」 rather than dropped. That is deliberate and
 * it is a real reading: if 未分析 is the largest group, the analysis is
 * behind, and a console that hid those rows would turn an outage into
 * "a quiet week".
 */

import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AttentionAlerts from '../components/AttentionAlerts.vue'
import PageHeader from '../components/PageHeader.vue'
import { masteryColorFor } from '../assets/tokens'
import { isLoggedIn, isTeacher, logout, sessionReady, sessionUser } from '../api/session'
import { listClassrooms, type ClassroomOut } from '../api/classroom'
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
} from '../api/assignments'
import {
  getAssistanceUsage,
  listLevels,
  type AssistancePolicy,
  type AssistanceUsage
} from '../api/assistance'
import {
  createStudent,
  getAlerts,
  getErrorDistribution,
  getFlaggedSubmissions,
  getKnowledgeStats,
  getOverview,
  getStudents,
  getStudentTimeline,
  importStudents,
  reviewFlag,
  type AlertOut,
  type ClassOverview,
  type ErrorCount,
  type FlaggedSubmission,
  type ImportedStudent,
  type ImportStudentsResult,
  type KnowledgeStatOut,
  type StudentOut,
  type TimelineEntryOut
} from '../api/teacher'

const loading = ref(false)
const errorMsg = ref('')

const overview = ref<ClassOverview | null>(null)
const distribution = ref<ErrorCount[]>([])
const knowledge = ref<KnowledgeStatOut[]>([])
const students = ref<StudentOut[]>([])
const flagged = ref<FlaggedSubmission[]>([])
/** Who needs attention, and why (阶段 6). Empty is a valid answer. */
const alerts = ref<AlertOut[]>([])

/** The student whose timeline is open, or `null` for the class view. */
const selected = ref<StudentOut | null>(null)
const timeline = ref<TimelineEntryOut[]>([])
const timelineLoading = ref(false)

// --- Login ------------------------------------------------------------
//
// The form itself is `views/LoginView.vue` — see the docstring for why it
// is no longer here. This page only has to offer a way to get there.

async function submitLogout(): Promise<void> {
  await logout()
  overview.value = null
  distribution.value = []
  knowledge.value = []
  students.value = []
  flagged.value = []
  alerts.value = []
  assignments.value = []
  openAssignmentId.value = null
  assignmentStats.value = null
  selected.value = null
}

// --- Loading ----------------------------------------------------------

/** Whether the console can render at all — the gate for state 4. */
const canSeeConsole = computed<boolean>(() => isTeacher.value)

async function load(): Promise<void> {
  if (!isTeacher.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const [ov, dist, know, stud, flag, warn] = await Promise.all([
      getOverview(),
      getErrorDistribution(),
      getKnowledgeStats(),
      getStudents(),
      getFlaggedSubmissions(50, showReviewed.value),
      getAlerts()
    ])
    overview.value = ov
    distribution.value = dist
    knowledge.value = know
    students.value = stud
    flagged.value = flag
    alerts.value = warn
    await Promise.all([
      loadAssignments(),
      loadAssistanceUsage(),
      loadAssistanceLevels(),
      loadClassrooms()
    ])
    // A student who has left the roster (deleted, re-roled) must not
    // leave their timeline open behind them.
    if (selected.value && !stud.some((s) => s.user_id === selected.value?.user_id)) {
      selected.value = null
      timeline.value = []
    }
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载学情数据')
  } finally {
    loading.value = false
  }
}

// --- Who needs attention (阶段 6) --------------------------------------

/** The roster section, scrolled to when an alert name is clicked. */
const rosterSection = ref<HTMLElement | null>(null)

/**
 * Open a student straight from an alert.
 *
 * Scrolling matters here: the drill-down this opens lives further down
 * the page, and a click that changed something off-screen would look
 * like a click that did nothing.
 */
async function openAlertStudent(alert: AlertOut): Promise<void> {
  const match = students.value.find((s) => s.user_id === alert.user_id)
  if (!match) {
    // Roster and alerts come from the same request, so this is only
    // reachable if the list changed underneath this page.
    errorMsg.value = `${alert.display_name} 已不在名册中`
    return
  }
  await openStudent(match)
  rosterSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// --- Homework (阶段 4) ------------------------------------------------

const assignments = ref<AssignmentOut[]>([])
/** Which assignment's stats are open, or `null`. */
const openAssignmentId = ref<number | null>(null)
const assignmentStats = ref<AssignmentStats | null>(null)
const assignmentLoading = ref(false)

const addingAssignment = ref(false)
const newAssignmentTitle = ref('')
const newAssignmentProblems = ref('')
const newAssignmentDue = ref('')
const assignmentBusy = ref(false)

/**
 * Assistant-strength level for the homework being written (阶段 5).
 *
 * Owned here as a plain `number | null` instead of defaulting to a
 * literal 3. `null` is sent as "let the server decide", which keeps the
 * one and only copy of the default server-side — a picker that starts
 * pre-filled with a client-side 3 looks identical today and silently
 * disagrees the day the default moves.
 *
 * The value is re-set to `null` after a successful publish so the next
 * homework starts from the server's default rather than inheriting the
 * last one. A teacher who has to remember to reset it will eventually
 * publish a 五档 assignment by accident.
 */
const newAssignmentLevel = ref<number | null>(null)

/**
 * Which class the homework is for (PU-13). `null` is "everyone", and it
 * is the default on purpose: a teacher who has not thought about classes
 * yet should be able to publish practice problems without first having
 * to create one.
 */
const newAssignmentClassId = ref<number | null>(null)

/** This teacher's classes, for the picker. Empty hides the select. */
const classrooms = ref<ClassroomOut[]>([])

async function loadClassrooms(): Promise<void> {
  try {
    classrooms.value = await listClassrooms()
  } catch {
    // Degrade to "no picker": homework can still be published to
    // everyone, which is the state before classes existed. An error
    // banner here would block a working action over a missing option.
    classrooms.value = []
  }
}

/** The five levels, for the picker. Fetched once — the table is static. */
const assistanceLevels = ref<AssistancePolicy[]>([])

async function loadAssistanceLevels(): Promise<void> {
  try {
    assistanceLevels.value = await listLevels()
  } catch {
    // Degrade to "no picker" rather than an error banner: without the
    // list the server still applies its default, so homework can be
    // published — just not tuned.
    assistanceLevels.value = []
  }
}

/**
 * What the chosen level permits, in one line.
 *
 * Rendered under the select so the teacher reads the consequence at the
 * moment of choosing. The level *number* alone is not a decision anyone
 * can make: "3" means nothing until it says "可以对话，但只给提示".
 */
const selectedLevelPolicy = computed<AssistancePolicy | null>(
  () =>
    assistanceLevels.value.find((p) => p.level === newAssignmentLevel.value) ??
    assistanceLevels.value[Math.floor(assistanceLevels.value.length / 2)] ??
    null
)

async function loadAssignments(): Promise<void> {
  try {
    assignments.value = await listAssignments()
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载作业列表')
  }
}

// --- AI usage trail (阶段 5) -------------------------------------------

/**
 * How much AI help the class has taken, across every homework.
 *
 * Read as a class-wide total rather than per assignment, because the
 * question this panel answers is "is the strength I set being used at
 * all" — and the answer is only comparable once it covers the same
 * population. Per-assignment detail is available from the route but
 * would need a selector the teacher has to operate before seeing
 * anything, which is exactly how a panel ends up unread.
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
    // Kept separate from `errorMsg` on purpose: this is one panel of the
    // console failing, and a page-level banner would imply the whole
    // dashboard is stale when the rest of it loaded fine.
    assistanceUsageError.value = messageOf(err, '无法加载 AI 使用记录')
    assistanceUsage.value = null
  } finally {
    assistanceUsageLoading.value = false
  }
}

/**
 * The per-action rows, as `[label, count]`, biggest first.
 *
 * Sorted here rather than server-side because the server returns a dict
 * and Python dict order is the insertion order of the constants, not an
 * ordering anyone chose. Reading it as a ranking without sorting would
 * be reading a coincidence.
 */
const assistanceActionRows = computed<[string, number][]>(() => {
  const labeled = assistanceUsage.value?.by_action_labeled ?? {}
  return Object.entries(labeled).sort((a, b) => b[1] - a[1])
})

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
  // "1, 2,3" → [1,2,3]. Anything that is not a number is dropped
  // rather than sent, because the server's error for it is a 422 the
  // teacher cannot act on from here.
  const ids = newAssignmentProblems.value
    .split(/[,，\s]+/)
    .map((part) => Number.parseInt(part, 10))
    .filter((n) => Number.isFinite(n))
  if (ids.length === 0) {
    errorMsg.value = '至少要选一道题'
    return
  }
  assignmentBusy.value = true
  try {
    await createAssignment({
      title: newAssignmentTitle.value,
      problem_ids: ids,
      // ``datetime-local`` gives "YYYY-MM-DDTHH:mm" with no zone, which
      // is exactly what ``datetime.fromisoformat`` accepts. Empty means
      // "no deadline", and sending '' would be parsed as an invalid date.
      due_at: newAssignmentDue.value ? newAssignmentDue.value : undefined,
      // Omitted (not sent as null) when untouched, so the server's
      // default is what applies — see `newAssignmentLevel`.
      assistance_level: newAssignmentLevel.value ?? undefined,
      // Omitted for "everyone" rather than sent as null, so the request
      // has one spelling of that idea — see `newAssignmentClassId`.
      class_id: newAssignmentClassId.value ?? undefined
    })
    addingAssignment.value = false
    newAssignmentTitle.value = ''
    newAssignmentProblems.value = ''
    newAssignmentDue.value = ''
    newAssignmentLevel.value = null
    newAssignmentClassId.value = null
    await Promise.all([loadAssignments(), loadAssistanceUsage()])
  } catch (err) {
    errorMsg.value = messageOf(err, '无法发布作业')
  } finally {
    assignmentBusy.value = false
  }
}

async function removeAssignment(assignment: AssignmentOut): Promise<void> {
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
 * Copy an assignment (阶段 5).
 *
 * No confirmation dialog, and that is deliberate: duplicating adds a row
 * and destroys nothing, so asking first would be friction on the one
 * action here that is safe to get wrong — deleting has its own prompt
 * for the opposite reason.
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

// --- Per-student report (阶段 5) ---------------------------------------

/**
 * One student against every assignment.
 *
 * Kept beside the assignment list rather than inside the student panel
 * because the question it answers is "how are they doing over the term",
 * which is a report-time question, not a per-mistake one. The mistake
 * timeline stays where it is.
 */
const report = ref<StudentReport | null>(null)
const reportLoading = ref(false)
const reportError = ref('')

async function openReport(userId: number, displayName: string): Promise<void> {
  reportLoading.value = true
  reportError.value = ''
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

/**
 * How one row reads, as text.
 *
 * `null` gets a word, not a dash: a missing progress means "never
 * submitted", and a dash would read as "no data" — i.e. the very
 * distinction the API went out of its way to preserve.
 */
function progressText(row: { progress: number | null; submissions: number }): string {
  if (row.submissions === 0) return '未提交'
  return percent(row.progress ?? 0)
}

// --- Reviewing a flag (阶段 5) ----------------------------------------

/**
 * Whether the queue also shows flags a teacher has already ruled on.
 *
 * Off by default: ruling on one is how it stops asking for attention.
 */
const showReviewed = ref(false)

/** The row whose verdict is being saved, so only that one is disabled. */
const reviewingId = ref<number | null>(null)
const reviewError = ref('')

function reviewText(verdict: string | null | undefined): string {
  if (verdict === 'confirmed') return '已确认'
  if (verdict === 'dismissed') return '已驳回'
  return ''
}

/**
 * Record a verdict, then reload.
 *
 * Reloading rather than patching the one row: a verdict moves three
 * numbers on this page — the queue, the overview's open_flags, and the
 * student's own flagged count. Updating the row in place would leave the
 * other two stale, and a console that contradicts itself is worse than
 * one that takes another moment.
 */
async function submitReview(
  item: FlaggedSubmission,
  verdict: 'confirmed' | 'dismissed' | 'cleared'
): Promise<void> {
  reviewingId.value = item.id
  reviewError.value = ''
  try {
    await reviewFlag(item.id, verdict)
    await load()
  } catch (err) {
    reviewError.value = messageOf(err, '无法保存判定')
  } finally {
    reviewingId.value = null
  }
}

async function openStudent(student: StudentOut): Promise<void> {
  if (selected.value?.user_id === student.user_id) {
    selected.value = null
    timeline.value = []
    return
  }
  selected.value = student
  timeline.value = []
  timelineLoading.value = true
  try {
    timeline.value = await getStudentTimeline(student.user_id)
  } catch (err) {
    errorMsg.value = messageOf(err, `无法加载 ${student.display_name} 的错题记录`)
  } finally {
    timelineLoading.value = false
  }
}

// --- Roster -----------------------------------------------------------

const addingStudent = ref(false)
const newName = ref('')
const newDisplayName = ref('')
const newPassword = ref('')
const addError = ref('')
const addNotice = ref('')

async function submitNewStudent(): Promise<void> {
  if (!newName.value.trim() || !newPassword.value) {
    addError.value = '用户名和密码都不能为空'
    return
  }
  addError.value = ''
  addNotice.value = ''
  try {
    const created = await createStudent({
      username: newName.value.trim(),
      password: newPassword.value,
      display_name: newDisplayName.value.trim()
    })
    addNotice.value = `已创建账号 ${created.username}，初始密码已生效`
    newName.value = ''
    newDisplayName.value = ''
    newPassword.value = ''
    addingStudent.value = false
    await load()
  } catch (err) {
    // 409 is worth its own sentence: it is the one failure here the
    // teacher can fix by typing something else, and burying it in a
    // generic message would send them looking for a network problem.
    addError.value =
      statusOf(err) === 409 ? '该用户名已被占用，换一个试试' : messageOf(err, '创建失败')
  }
}

// --- Roster import ------------------------------------------------------

const importingStudents = ref(false)
const importCsv = ref('')
const importDefaultPassword = ref('123456')
const importHasHeader = ref(true)
const importError = ref('')
const importResult = ref<ImportStudentsResult | null>(null)

/**
 * Per-row failures from the last import, so the form can show a
 * short list of exactly what to fix instead of one aggregate line.
 */
const importFailures = computed<ImportedStudent[]>(
  () => importResult.value?.results.filter((r) => !r.ok) ?? []
)

const importSample = `username,display_name
2025021120058,刘爱重
2025021120059,张三`

function fillImportSample(): void {
  importCsv.value = importSample
  importResult.value = null
  importError.value = ''
}

async function submitImportStudents(): Promise<void> {
  if (!importCsv.value.trim()) {
    importError.value = '请先粘贴名单'
    return
  }
  importError.value = ''
  importResult.value = null
  try {
    const result = await importStudents({
      csv: importCsv.value,
      default_password: importDefaultPassword.value || undefined,
      has_header: importHasHeader.value
    })
    importResult.value = result
    // Only clear the box when something landed. Re-importing a roster
    // with one bad line is the normal case, and wiping the text would
    // force the teacher to paste it again to fix that line.
    if (result.created > 0) importCsv.value = ''
    await load()
  } catch (err) {
    importError.value = messageOf(err, '导入失败')
  }
}

function closeImport(): void {
  importingStudents.value = false
  importCsv.value = ''
  importResult.value = null
  importError.value = ''
}

// --- Formatting -------------------------------------------------------

/**
 * A number that is allowed to be missing.
 *
 * The console's shapes are hand-written against the server's; if one
 * drifts, the honest output is a dash in the cell, not the word
 * `undefined` in a table a teacher is about to read aloud.
 */
function num(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—'
}

function percent(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

function messageOf(err: unknown, fallback: string): string {
  const anyErr = err as { response?: { data?: { detail?: string } }; message?: string }
  return anyErr?.response?.data?.detail ?? anyErr?.message ?? fallback
}

function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status
}

/**
 * The label shown for one row of 错因分布.
 *
 * `null` is not a missing label to be patched over — it is a group of
 * entries whose analysis has not landed, and it gets its own wording.
 */
function causeLabel(row: ErrorCount): string {
  return row.description || row.label || '未分析'
}

function isUnanalysed(row: ErrorCount): boolean {
  return row.label === null
}

function statusText(status: string | null): string {
  if (!status) return '—'
  const known: Record<string, string> = {
    AC: '通过',
    WA: '答案错误',
    TLE: '超时',
    MLE: '内存超限',
    CE: '编译错误',
    RE: '运行错误'
  }
  return known[status] ?? status
}

function dateText(value: string | null): string {
  if (!value) return '—'
  // Only the date and time to the minute: a teacher scanning a history
  // needs to know *when in the lesson*, not the milliseconds.
  return value.slice(0, 16).replace('T', ' ')
}

/** Knowledge tags, weakest first — the same order the profile page uses. */
const sortedKnowledge = computed<KnowledgeStatOut[]>(() =>
  [...knowledge.value].sort((a, b) => a.accuracy - b.accuracy)
)

/**
 * Whether the analysis is behind.
 *
 * Shown when the un-analysed group is the largest one in 错因分布. It is
 * the only case where the console should talk about *itself* rather than
 * about the class: otherwise a teacher reads "未分析 12" as a lesson
 * topic.
 */
const analysisBehind = computed<boolean>(() => {
  if (distribution.value.length === 0) return false
  return isUnanalysed(distribution.value[0]) && distribution.value[0].count > 0
})

watch(isTeacher, (now) => {
  if (now) void load()
})

onMounted(() => {
  if (canSeeConsole.value) void load()
})
</script>

<template>
  <div class="teacher-view">
    <div class="content">
      <PageHeader
        title="学情看板"
        :subtitle="overview ? `${overview.students} 名学生 · ${overview.submissions} 次提交` : ''"
        :loading="canSeeConsole ? loading : undefined"
        :error="errorMsg"
        error-prefix="无法加载学情"
        @refresh="load"
      />

      <!-- 1. Session still resolving. -->
      <div v-if="!sessionReady" class="placeholder">正在恢复登录状态…</div>

      <!-- 2. Nobody is logged in. A signpost, not a form: the form is
           the shared `/login` page, so there is exactly one of them. -->
      <div v-else-if="!isLoggedIn" class="gate">
        <h2 class="gate-title">学情看板需要登录</h2>
        <p class="gate-body">
          学情看板使用教师账号。学生侧的练习、错题本和画像不需要登录，照常可用。
        </p>
        <RouterLink class="primary gate-link" :to="{ name: 'login' }">去登录</RouterLink>
      </div>

      <!-- 3. Logged in, but not as a teacher. -->
      <div v-else-if="!isTeacher" class="gate">
        <h2 class="gate-title">此账号不是教师账号</h2>
        <p class="gate-body">
          当前登录的是「{{ sessionUser?.display_name || sessionUser?.username }}」（学生）。
          学情看板只有教师账号可以打开——这不是前端的限制，服务端每一条教师接口都会拒绝学生账号。
        </p>
        <button class="primary" type="button" @click="submitLogout">退出登录</button>
      </div>

      <!-- 4. The console. -->
      <template v-else>
        <div v-if="loading && !overview" class="placeholder">加载学情数据中…</div>

        <template v-else-if="overview">
          <!-- --- Summary --- -->
          <div class="summary">
            <div class="summary-card">
              <span class="summary-label">学生数</span>
              <span class="summary-value">{{ num(overview.students) }}</span>
              <span class="summary-hint">已建账号</span>
            </div>
            <div class="summary-card">
              <span class="summary-label">总提交</span>
              <span class="summary-value">{{ num(overview.submissions) }}</span>
              <span class="summary-hint">含重复提交</span>
            </div>
            <div class="summary-card">
              <span class="summary-label">整体正确率</span>
              <span class="summary-value" :style="{ color: masteryColorFor(overview.accuracy) }">
                {{ percent(overview.accuracy) }}
              </span>
              <span class="summary-hint"
                >{{ overview.accepted }} / {{ overview.submissions }} 通过</span
              >
            </div>
            <div class="summary-card">
              <span class="summary-label">待复查错题</span>
              <span class="summary-value">{{ num(overview.open_mistakes) }}</span>
              <span class="summary-hint">已分析 {{ overview.analysed_mistakes }} 条</span>
            </div>
            <div class="summary-card">
              <span class="summary-label">存疑提交</span>
              <span class="summary-value">{{ num(overview.open_flags) }}</span>
              <!-- Both numbers, because they say different things: the
                   first is how much reading there is to do, the second is
                   how trigger-happy the detector is. A flag that fires on
                   everything and gets dismissed every time looks, by the
                   first number alone, like one that never fires. -->
              <span class="summary-hint"> 共标记 {{ num(overview.flagged_submissions) }} 条 </span>
            </div>
          </div>

          <!-- --- 谁现在就需要我（阶段 6） --- -->
          <section v-if="alerts.length > 0" class="section">
            <h2 class="section-title">需要关注</h2>
            <p class="section-note">
              每条都写明了触发它的依据。这不是评分——没有「风险 87%」这种数字，
              因为没法追问的数字你也不会信。
            </p>
            <!--
              The list lives in its own component so it can be tested
              without the 1600-line view: it owns no state and issues no
              requests, it only renders what it is handed. The section
              wrapper and its heading stay here — "nothing to show" is
              decided by `v-if` above, and rendering an empty "需要关注"
              heading would be noise.
            -->
            <AttentionAlerts :alerts="alerts" @select="openAlertStudent" />
          </section>

          <!-- --- 下节课讲什么 --- -->
          <section class="section">
            <h2 class="section-title">错因分布</h2>
            <p class="section-note">按出现次数降序——最上面的那条就是下节课该讲的。</p>

            <div v-if="distribution.length === 0" class="empty-inline">还没有错题记录。</div>

            <template v-else>
              <!--
                The console talking about itself. Only when the
                un-analysed group outranks every real cause: otherwise
                "未分析" reads like a lesson topic.
              -->
              <p v-if="analysisBehind" class="self-note">
                未分析的条目是当前最多的一组，说明分析还在补跑——这个数字不代表学生的问题。
              </p>
              <div class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>错因</th>
                      <th class="num">次数</th>
                      <th>占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in distribution" :key="row.label ?? '__none__'">
                      <td>
                        <span class="cause" :class="{ 'cause-unknown': isUnanalysed(row) }">{{
                          causeLabel(row)
                        }}</span>
                      </td>
                      <td class="num">{{ row.count }}</td>
                      <td>
                        <div
                          class="progress-bar"
                          role="img"
                          :aria-label="`${causeLabel(row)} 占比 ${percent(row.share)}`"
                        >
                          <div
                            class="progress-fill"
                            :class="{ 'fill-unknown': isUnanalysed(row) }"
                            :style="{ width: `${Math.max(row.share * 100, 2)}%` }"
                          />
                        </div>
                        <span class="share-text">{{ percent(row.share) }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </section>

          <!-- --- 知识点 --- -->
          <section v-if="sortedKnowledge.length > 0" class="section">
            <h2 class="section-title">知识点掌握</h2>
            <p class="section-note">按正确率升序，最需要补的知识点在最上面。</p>
            <div class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>知识点</th>
                    <th class="num">提交</th>
                    <th class="num">通过</th>
                    <th class="num">正确率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="k in sortedKnowledge" :key="k.tag">
                    <td class="strong-cell">{{ k.tag }}</td>
                    <td class="num">{{ k.total }}</td>
                    <td class="num">{{ k.passed }}</td>
                    <td class="num" :style="{ color: masteryColorFor(k.accuracy) }">
                      {{ percent(k.accuracy) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- --- 谁需要我 --- -->
          <section ref="rosterSection" class="section">
            <h2 class="section-title">学生清单</h2>
            <p class="section-note">
              按正确率升序，最需要关注的学生在最上面；点一行可以看他/她的错题时间线。
            </p>

            <div v-if="students.length === 0" class="empty-inline">
              还没有学生账号。用下面的按钮添加。
            </div>

            <div v-else class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>学生</th>
                    <th class="num">提交</th>
                    <th class="num">正确率</th>
                    <th class="num">错题</th>
                    <th class="num">存疑</th>
                    <th>最薄弱知识点</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="s in students"
                    :key="s.user_id"
                    class="clickable"
                    :class="{ 'row-open': selected?.user_id === s.user_id }"
                    @click="openStudent(s)"
                  >
                    <td class="strong-cell">{{ s.display_name || s.username }}</td>
                    <td class="num">{{ s.submissions }}</td>
                    <td class="num" :style="{ color: masteryColorFor(s.accuracy) }">
                      {{ percent(s.accuracy) }}
                    </td>
                    <td class="num">{{ s.mistakes }}</td>
                    <td class="num">
                      <span v-if="s.flagged_submissions > 0" class="flag-badge">
                        {{ s.flagged_submissions }}
                      </span>
                      <span v-else>0</span>
                    </td>
                    <td>{{ s.weakest_tag ?? '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- --- Drill-down --- -->
            <div v-if="selected" class="drilldown">
              <div class="drilldown-head">
                <h3 class="drilldown-title">
                  {{ selected.display_name || selected.username }} 的错题时间线
                </h3>
                <button class="ghost" type="button" @click="openStudent(selected)">收起</button>
              </div>

              <div v-if="timelineLoading" class="placeholder">加载中…</div>
              <div v-else-if="timeline.length === 0" class="empty-inline">
                这个学生还没有错题记录。
              </div>
              <ol v-else class="timeline">
                <li v-for="entry in timeline" :key="entry.id" class="timeline-item">
                  <span class="timeline-when">{{ dateText(entry.created_at) }}</span>
                  <span class="timeline-status">{{ statusText(entry.status) }}</span>
                  <span class="timeline-cause">
                    {{ entry.error_description || entry.error_type || '未分析' }}
                  </span>
                  <span class="timeline-problem"> 题目 {{ entry.problem_id ?? '—' }} </span>
                </li>
              </ol>
            </div>
          </section>

          <!-- --- 存疑提交 --- -->
          <section class="section">
            <h2 class="section-title">存疑提交</h2>
            <p class="section-note">
              编辑行为特征提示这些提交的代码可能不是当场写出来的。这只是<strong>需要看一眼</strong>的线索，
              不是判定——点开可以看到具体依据，由你判断。
            </p>
            <p class="section-note">
              判断完请点<strong>确认</strong>或<strong>驳回</strong>：驳回的是误报，会退出这个列表；
              确认的会留在这里，方便你事后复查。判错了可以撤销。
            </p>

            <label class="queue-toggle">
              <input v-model="showReviewed" type="checkbox" @change="load" />
              显示已复核的
            </label>

            <p v-if="reviewError" class="inline-error">{{ reviewError }}</p>

            <div v-if="flagged.length === 0" class="empty-inline">
              {{ showReviewed ? '没有存疑提交记录。' : '暂无存疑提交——都看过了。' }}
            </div>

            <ul v-else class="flagged-list">
              <li v-for="item in flagged" :key="item.id" class="flagged-item">
                <div class="flagged-head">
                  <span class="flagged-who">{{ item.student || '未归属' }}</span>
                  <span class="flagged-when">{{ dateText(item.created_at) }}</span>
                  <span class="flagged-status">{{ statusText(item.status) }}</span>
                  <span
                    v-if="reviewText(item.review?.verdict)"
                    class="review-badge"
                    :class="item.review?.verdict"
                    >{{ reviewText(item.review?.verdict) }}</span
                  >
                </div>
                <p class="flagged-summary">{{ item.process?.summary ?? '' }}</p>
                <!-- The evidence: a mark a teacher cannot check is a mark
                     they will either rubber-stamp or ignore. -->
                <details v-if="item.process?.reasons?.length" class="flagged-evidence">
                  <summary>查看依据</summary>
                  <ul>
                    <li v-for="reason in item.process.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                </details>
                <!-- The review: what makes the mark a hint rather than a
                     verdict. A flag nobody can disagree with is one the
                     teacher can only rubber-stamp. -->
                <div class="review-actions">
                  <button
                    type="button"
                    class="review-btn"
                    :disabled="reviewingId === item.id"
                    @click="submitReview(item, 'confirmed')"
                  >
                    确认可疑
                  </button>
                  <button
                    type="button"
                    class="review-btn"
                    :disabled="reviewingId === item.id"
                    @click="submitReview(item, 'dismissed')"
                  >
                    驳回（误报）
                  </button>
                  <button
                    v-if="item.review?.verdict"
                    type="button"
                    class="review-btn subtle"
                    :disabled="reviewingId === item.id"
                    @click="submitReview(item, 'cleared')"
                  >
                    撤销判定
                  </button>
                </div>
              </li>
            </ul>
          </section>

          <!-- --- Homework (阶段 4) --- -->
          <section class="section">
            <h2 class="section-title">作业</h2>
            <p class="section-note">
              作业就是「一组题目 + 截止时间」。谁交了、谁没交是<strong>临时算出来的</strong>
              （名册减去有提交的人），不是存在库里的状态——少一处需要跟着每次提交同步的地方。
            </p>

            <div v-if="assignments.length === 0" class="empty-inline">还没有布置作业。</div>

            <ul v-else class="assignment-list">
              <li v-for="a in assignments" :key="a.id" class="assignment-item">
                <div class="assignment-head">
                  <button type="button" class="assignment-title" @click="toggleAssignment(a)">
                    {{ a.title }}
                  </button>
                  <span class="assignment-meta">{{ a.problem_ids.length }} 题</span>
                  <!--
                    The level in force, read off the row rather than
                    guessed from the form: what a student actually gets.
                  -->
                  <span class="assignment-meta assignment-level" :title="a.assistance?.description">
                    AI {{ a.assistance_level }} 档
                  </span>
                  <span v-if="a.due_at" class="assignment-meta" :class="{ overdue: a.overdue }">
                    {{ a.overdue ? '已过期 · ' : '' }}{{ dateText(a.due_at) }}
                  </span>
                  <button
                    type="button"
                    class="review-btn subtle"
                    :disabled="duplicatingId === a.id"
                    title="复制成一份新作业，题目和辅助强度一起带过去"
                    @click="copyAssignment(a)"
                  >
                    {{ duplicatingId === a.id ? '复制中…' : '复制' }}
                  </button>
                  <button type="button" class="review-btn subtle" @click="removeAssignment(a)">
                    删除
                  </button>
                </div>

                <!-- Loaded on demand: the stats are a second request per
                     assignment, and a teacher opening one should not pay
                     for all of them. -->
                <div v-if="openAssignmentId === a.id" class="assignment-stats">
                  <p v-if="assignmentLoading" class="placeholder-inline">统计中…</p>
                  <template v-else-if="assignmentStats">
                    <table class="data-table">
                      <thead>
                        <tr>
                          <th>题目</th>
                          <th class="num">提交</th>
                          <th class="num">通过</th>
                          <th class="num">正确率</th>
                          <th class="num">做过的人数</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="p in assignmentStats.problems" :key="p.problem_id">
                          <td>第 {{ p.problem_id }} 题</td>
                          <td class="num">{{ p.submissions }}</td>
                          <td class="num">{{ p.accepted }}</td>
                          <td class="num">{{ percent(p.accuracy) }}</td>
                          <td class="num">{{ p.students }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <p class="assignment-missing">
                      <template v-if="assignmentStats.students_missing.length">
                        还没交（{{ assignmentStats.students_missing.length }} 人）：
                        {{ assignmentStats.students_missing.join('、') }}
                      </template>
                      <template v-else>都交了。</template>
                    </p>
                  </template>
                </div>
              </li>
            </ul>

            <button
              v-if="!addingAssignment"
              class="primary"
              type="button"
              @click="addingAssignment = true"
            >
              布置作业
            </button>

            <form v-else class="roster-form" @submit.prevent="submitAssignment">
              <label class="field">
                <span class="field-label">标题</span>
                <input v-model="newAssignmentTitle" class="input" type="text" required />
              </label>
              <label class="field">
                <span class="field-label">题目编号（逗号分隔）</span>
                <input
                  v-model="newAssignmentProblems"
                  class="input"
                  type="text"
                  placeholder="1,2,3"
                />
              </label>
              <label class="field">
                <span class="field-label">截止时间（可留空）</span>
                <input v-model="newAssignmentDue" class="input" type="datetime-local" />
              </label>

              <!--
                Which class (PU-13). Only rendered once the teacher has
                one: an empty select would offer nothing but "everyone",
                which is the default anyway, and a disabled control is
                worse than no control.
              -->
              <label v-if="classrooms.length" class="field">
                <span class="field-label">布置给</span>
                <select v-model="newAssignmentClassId" class="input">
                  <option :value="null">所有人（不限定班级）</option>
                  <option v-for="room in classrooms" :key="room.id" :value="room.id">
                    {{ room.name }}
                  </option>
                </select>
              </label>

              <!--
                Assistant strength (阶段 5). Only rendered when the level
                table loaded: a select with no options would silently
                publish homework at whatever the browser falls back to.
              -->
              <label v-if="assistanceLevels.length" class="field">
                <span class="field-label">AI 辅助强度</span>
                <select v-model="newAssignmentLevel" class="input">
                  <!--
                    The empty option is "server default", not "no AI".
                    Without it the select starts on its first option and
                    the teacher's untouched publish would look like a
                    deliberate choice at the strictest level.
                  -->
                  <option :value="null">
                    使用默认（{{ selectedLevelPolicy?.level ?? '—' }} 档）
                  </option>
                  <option v-for="p in assistanceLevels" :key="p.level" :value="p.level">
                    {{ p.level }} 档 · {{ p.label }}
                  </option>
                </select>
              </label>
              <p v-if="selectedLevelPolicy" class="field-note">
                {{ selectedLevelPolicy.description }}
              </p>
              <div class="roster-actions">
                <button class="primary" type="submit" :disabled="assignmentBusy">
                  {{ assignmentBusy ? '发布中…' : '发布' }}
                </button>
                <button class="review-btn" type="button" @click="addingAssignment = false">
                  取消
                </button>
              </div>
            </form>
          </section>

          <!-- --- AI usage trail (阶段 5) --- -->
          <section class="section">
            <h2 class="section-title">AI 使用记录</h2>
            <p class="section-note">
              记录了每次<strong>动作</strong>，不记录对话内容——学生问了什么不是老师要看的东西，
              「用得多不多、用的是哪一档」才是。被拒绝的请求也记：
              反复去点被禁用的功能，正是需要留意的情况。
            </p>

            <p v-if="assistanceUsageLoading" class="placeholder-inline">读取中…</p>
            <p v-else-if="assistanceUsageError" class="error-inline">
              {{ assistanceUsageError }}
            </p>
            <!--
              An explicit zero, not an empty panel. "Nobody used it" and
              "the read failed" look identical when both render nothing,
              and they call for opposite decisions.
            -->
            <p
              v-else-if="!assistanceUsage || assistanceUsage.total === 0"
              class="placeholder-inline"
            >
              还没有任何 AI 使用记录。
            </p>

            <template v-else>
              <p class="usage-total">共 {{ assistanceUsage.total }} 次</p>
              <ul class="usage-list">
                <li v-for="[label, count] in assistanceActionRows" :key="label" class="usage-item">
                  <span class="usage-label">{{ label }}</span>
                  <span class="usage-count">{{ count }}</span>
                </li>
              </ul>

              <div v-if="assistanceUsage.by_student.length" class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>学生</th>
                      <th class="num">次数</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, i) in assistanceUsage.by_student" :key="i">
                      <td>{{ row.display_name }}</td>
                      <td class="num">{{ row.count }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </section>

          <!-- --- Per-student report (阶段 5) --- -->
          <section class="section">
            <h2 class="section-title">学生成绩单</h2>
            <p class="section-note">
              一名学生对着<strong>全部作业</strong>的完成情况。错题本看不到「一道题都没做」的学生
              ——他没有错题——所以这个页面用作业来算，把没交的人也算进来。
            </p>

            <div class="report-picker">
              <button
                v-for="s in students"
                :key="s.user_id"
                type="button"
                class="report-chip"
                :class="{ active: report?.user_id === s.user_id }"
                @click="openReport(s.user_id, s.display_name)"
              >
                {{ s.display_name }}
              </button>
            </div>

            <p v-if="reportLoading" class="placeholder-inline">读取中…</p>
            <p v-else-if="reportError" class="error-inline">{{ reportError }}</p>

            <template v-else-if="report">
              <p class="usage-total">
                {{ report.display_name }}：{{ report.total_assignments }} 份作业中参与了
                {{ report.completed }} 份
              </p>
              <div v-if="report.rows.length" class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>作业</th>
                      <th class="num">题目数</th>
                      <th class="num">提交</th>
                      <th class="num">通过</th>
                      <th class="num">进度</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in report.rows" :key="row.assignment_id">
                      <td>
                        {{ row.title }}
                        <span v-if="row.overdue" class="assignment-meta overdue">已过期</span>
                      </td>
                      <td class="num">{{ row.problem_count }}</td>
                      <td class="num">{{ row.submissions }}</td>
                      <td class="num">{{ row.accepted }}</td>
                      <!-- 未提交 / 正确率是两种读数，见 progressText -->
                      <td class="num">{{ progressText(row) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-else class="placeholder-inline">还没有布置过作业。</p>
              <button class="review-btn subtle" type="button" @click="closeReport">收起</button>
            </template>
          </section>

          <!-- --- Roster --- -->
          <section class="section">
            <h2 class="section-title">学生账号</h2>
            <p class="section-note">
              学生不能自己注册——花名册由任课老师建立，这也是这套系统里唯一会写入学生记录的操作。
            </p>

            <div class="roster-toolbar">
              <button
                v-if="!addingStudent"
                class="primary"
                type="button"
                @click="addingStudent = true"
              >
                添加学生
              </button>
              <button
                v-if="!importingStudents"
                class="ghost"
                type="button"
                @click="importingStudents = true"
              >
                批量导入名单
              </button>
            </div>

            <form v-if="addingStudent" class="roster-form" @submit.prevent="submitNewStudent">
              <label class="field">
                <span class="field-label">用户名</span>
                <input v-model="newName" class="input" type="text" placeholder="登录用" />
              </label>
              <label class="field">
                <span class="field-label">姓名</span>
                <input
                  v-model="newDisplayName"
                  class="input"
                  type="text"
                  placeholder="显示用，可留空"
                />
              </label>
              <label class="field">
                <span class="field-label">初始密码</span>
                <input v-model="newPassword" class="input" type="text" placeholder="告诉学生本人" />
              </label>
              <div class="roster-actions">
                <button class="primary" type="submit">创建</button>
                <button class="ghost" type="button" @click="addingStudent = false">取消</button>
              </div>
              <p v-if="addError" class="gate-error">{{ addError }}</p>
              <p v-if="addNotice" class="notice">{{ addNotice }}</p>
            </form>

            <!-- Bulk import. Per-row results: one bad line in a class
                 list must not stop the other forty from landing. -->
            <form
              v-if="importingStudents"
              class="roster-form"
              @submit.prevent="submitImportStudents"
            >
              <label class="field">
                <span class="field-label">
                  名单（每行一个学生，逗号分隔）
                  <button class="link-btn" type="button" @click="fillImportSample">填入示例</button>
                </span>
                <textarea
                  v-model="importCsv"
                  class="input import-textarea"
                  rows="7"
                  spellcheck="false"
                  placeholder="username,display_name&#10;2025021120058,刘爱重"
                />
                <span class="field-note">
                  两列是「用户名,姓名」；也可写三列「用户名,姓名,密码」覆盖下面的初始密码。
                </span>
              </label>
              <label class="field">
                <span class="field-label">初始密码（没写密码的行用这个）</span>
                <input v-model="importDefaultPassword" class="input" type="text" />
              </label>
              <label class="field field-inline">
                <input v-model="importHasHeader" type="checkbox" />
                <span class="field-note">第一行是表头，跳过</span>
              </label>
              <div class="roster-actions">
                <button class="primary" type="submit">导入</button>
                <button class="ghost" type="button" @click="closeImport">取消</button>
              </div>
              <p v-if="importError" class="gate-error">{{ importError }}</p>
              <div v-if="importResult" class="import-result">
                <p class="notice">
                  成功 {{ importResult.created }} 人，跳过 {{ importResult.skipped }} 人。
                </p>
                <ul v-if="importFailures.length" class="import-failures">
                  <li v-for="(f, i) in importFailures" :key="i">
                    {{ f.username || '（空用户名）' }} · {{ f.display_name }} — {{ f.error }}
                  </li>
                </ul>
              </div>
            </form>
          </section>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.teacher-view {
  height: 100%;
  overflow-y: auto;
  background: var(--af-canvas);
  color: var(--af-text-primary);
}

.content {
  max-width: var(--af-content-max);
  margin: 0 auto;
  padding: 20px 16px 32px 16px;
  box-sizing: border-box;
}

.placeholder {
  padding: 40px 16px;
  font-size: 14px;
  text-align: center;
  color: var(--af-text-muted);
}

/* --- the three non-console states --- */
.gate {
  max-width: 420px;
  padding: 24px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.gate-title {
  margin: 0 0 8px 0;
  font-size: 16px;
  font-weight: 600;
}

.gate-body {
  margin: 0 0 16px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

.gate-form,
.roster-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-size: 12px;
  color: var(--af-text-secondary);
}

.input {
  padding: 7px 10px;
  font-size: 14px;
  font-family: inherit;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
}

.input:focus-visible {
  outline: 2px solid var(--af-accent);
  outline-offset: 1px;
}

.gate-error {
  margin: 0;
  font-size: 13px;
  color: var(--af-danger);
}

.inline-error {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--af-danger);
}

.notice {
  margin: 0;
  font-size: 13px;
  color: var(--af-success);
}

.primary {
  align-self: flex-start;
  padding: 7px 14px;
  font-size: 13px;
  font-family: inherit;
  /* Text on the accent fill. Was `#ffffff` with a comment saying no token
   * expressed the relationship — `--af-text-inverse` does now, and the
   * repaint made it the only correct choice (see tokens.css). */
  color: var(--af-text-inverse);
  background: var(--af-accent);
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    box-shadow var(--af-duration-fast) var(--af-ease),
    transform var(--af-duration-fast) var(--af-ease-pop);
}

.primary:hover:not(:disabled) {
  background: var(--af-accent-hover);
  box-shadow: var(--af-glow-accent-soft);
}

.primary:active:not(:disabled) {
  transform: scale(0.97);
}

/* Consolidated (2026-09-25). There were two `.primary:disabled` rules and
 * two `.primary:hover` rules after the repaint; the surviving one changes
 * the disabled treatment from `opacity: 0.6` to a flat surface, for the
 * same reason as ProblemListView's refresh button: a dimmed accent still
 * reads as the primary action and invites a click that does nothing.
 * `cursor: not-allowed` replaces `default` to match every other disabled
 * control in the app. */
.primary:disabled {
  color: var(--af-text-muted);
  background: var(--af-elevated);
  cursor: not-allowed;
}

/* The 未登录 card's only control is a link, not a button — it navigates,
 * it does not submit. `.primary` supplies the fill; this adds the two
 * things an <a> needs to sit the way the buttons do, and which a
 * <button> gets for free. */
.gate-link {
  display: inline-block;
  margin-top: 4px;
  text-decoration: none;
}

.ghost {
  padding: 7px 14px;
  font-size: 13px;
  font-family: inherit;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

/* --- summary --- */
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.summary-card {
  flex: 1 1 140px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 14px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.summary-label {
  font-size: 12px;
  color: var(--af-text-secondary);
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.summary-hint {
  font-size: 11px;
  color: var(--af-text-muted);
}

/* --- sections --- */
.section {
  margin-bottom: 24px;
}

.section-title {
  margin: 0 0 8px 0;
  font-size: 15px;
  font-weight: 600;
}

.section-note {
  margin: 0 0 10px 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-muted);
}

/* The console talking about itself, not about the class. Muted, and with
 * a left rule rather than a background, so it does not read as data. */
.self-note {
  margin: 0 0 10px 0;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  border-left: 3px solid var(--af-warning);
  background: var(--af-elevated);
  border-radius: var(--af-radius-sm);
}

.empty-inline {
  padding: 18px;
  font-size: 13px;
  text-align: center;
  color: var(--af-text-muted);
  background: var(--af-surface);
  border: 1px dashed var(--af-border-strong);
  border-radius: var(--af-radius-md);
}

/* --- tables --- */
.table-scroll {
  overflow-x: auto;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.data-table th,
.data-table td {
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--af-border);
}

.data-table tbody tr:last-child td {
  border-bottom: none;
}

.data-table thead th {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--af-text-muted);
}

.data-table td {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.data-table .num {
  text-align: right;
  white-space: nowrap;
}

.strong-cell {
  font-weight: 600;
}

.clickable {
  cursor: pointer;
}

.clickable:hover {
  background: var(--af-elevated);
}

.row-open {
  background: var(--af-accent-subtle);
}

.cause {
  font-weight: 600;
}

/* The un-analysed group is an absence, not a cause: dashed underline
 * rather than a colour, so it does not read as a diagnosis. */
.cause-unknown {
  font-weight: 500;
  color: var(--af-text-secondary);
  border-bottom: 1px dashed var(--af-border-strong);
}

.progress-bar {
  display: inline-block;
  width: 120px;
  height: 8px;
  overflow: hidden;
  vertical-align: middle;
  background: var(--af-elevated);
  border-radius: 999px;
}

.progress-fill {
  height: 100%;
  background: var(--af-accent);
  border-radius: 999px;
  transition: width var(--af-duration-base) var(--af-ease);
}

.fill-unknown {
  background: var(--af-border-strong);
}

.share-text {
  margin-left: 8px;
  font-size: 12px;
  color: var(--af-text-muted);
}

.flag-badge {
  display: inline-block;
  min-width: 18px;
  padding: 1px 6px;
  font-size: 12px;
  color: var(--af-warning);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: 999px;
}

/* --- drill-down --- */
.drilldown {
  margin-top: 12px;
  padding: 14px 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-md);
}

.drilldown-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.drilldown-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.timeline-item {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 7px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--af-border);
}

.timeline-item:last-child {
  border-bottom: none;
}

.timeline-when {
  min-width: 120px;
  color: var(--af-text-muted);
  font-variant-numeric: tabular-nums;
}

.timeline-status {
  min-width: 72px;
  color: var(--af-text-secondary);
}

.timeline-cause {
  flex: 1 1 auto;
}

.timeline-problem {
  color: var(--af-text-muted);
}

/* --- flagged --- */
.flagged-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.flagged-item {
  padding: 12px 14px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.flagged-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  font-size: 13px;
}

.flagged-who {
  font-weight: 600;
}

.flagged-when,
.flagged-status {
  font-size: 12px;
  color: var(--af-text-muted);
}

.flagged-summary {
  margin: 6px 0 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.flagged-evidence {
  margin-top: 8px;
  font-size: 12px;
}

.flagged-evidence summary {
  cursor: pointer;
  color: var(--af-text-muted);
}

.flagged-evidence ul {
  margin: 6px 0 0 0;
  padding-left: 18px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

/* --- Homework (阶段 4) --- */

.assignment-list {
  margin: 0 0 12px;
  padding: 0;
  list-style: none;
}

.assignment-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--af-border);
}

.assignment-item:last-child {
  border-bottom: none;
}

.assignment-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
}

.assignment-title {
  padding: 0;
  font-size: 13px;
  font-family: inherit;
  font-weight: 600;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
}

.assignment-title:hover {
  text-decoration: underline;
}

.assignment-meta {
  font-size: 12px;
  color: var(--af-text-muted);
}

.assignment-meta.overdue {
  color: var(--af-danger);
}

.assignment-stats {
  margin-top: 10px;
}

/* The actionable line of the whole section, so it gets its own row. */
.assignment-missing {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.placeholder-inline {
  margin: 0;
  font-size: 13px;
  color: var(--af-text-muted);
}

/* --- AI assistance (阶段 5) --- */

/*
  `.field-note` sits under a control and explains it. Kept distinct from
  `.section-note` because the two are read at different distances: this
  one belongs to the select directly above it, and reusing the section
  style would visually detach it from the thing it describes.
*/
.field-note {
  margin: -4px 0 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

/* --- roster: the two entry points and the bulk-import form --- */

.roster-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}

/* A checkbox and its caption sit on one line, unlike the stacked
   `.field` above — the label is a sentence, not a column header. */
.field-inline {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.import-textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  resize: vertical;
  white-space: pre;
  overflow-x: auto;
}

/* Reset a `.field-note` sitting next to a checkbox back to inline flow. */
.field-inline .field-note {
  margin: 0;
}

.link-btn {
  margin-left: 6px;
  padding: 0;
  font-size: 12px;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
}

.import-result {
  margin-top: 4px;
}

/* Failures are listed rather than counted: the teacher has to fix them
   one by one, so the rows they need to edit should be on screen. */
.import-failures {
  margin: 6px 0 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

.error-inline {
  margin: 0;
  font-size: 13px;
  color: var(--af-text-primary);
}

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

.assignment-level {
  font-variant-numeric: tabular-nums;
}

/* --- Per-student report (阶段 5) --- */

.report-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.report-chip {
  padding: 3px 10px;
  font-family: inherit;
  font-size: 12px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.report-chip:hover {
  color: var(--af-text-primary);
}

/* The open student stays lit — with a term of assignments below it,
   losing track of whose report is on screen is the easy mistake. */
.report-chip.active {
  color: var(--af-text-primary);
  border-color: var(--af-accent);
}

/* --- The attention list (阶段 6) --- */
/*
  The markup and these rules now live in `components/AttentionAlerts.vue`.
  They had to move together: this file is `<style scoped>`, and a scoped
  rule reaches only a child component's **root** element — leaving
  `.alert-who` / `.alert-reason` here would have cost them their styling
  silently, with nothing in the build or the tests complaining.
*/

/* --- The review (阶段 5) --- */

.queue-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--af-text-secondary);
  cursor: pointer;
}

.review-badge {
  padding: 1px 7px;
  font-size: 11px;
  border: 1px solid var(--af-border-strong);
  border-radius: 999px;
  color: var(--af-text-secondary);
}

/* Confirmed keeps the accent's family but not its fill: it is a
 * conclusion the teacher reached, not an alert. */
.review-badge.confirmed {
  color: var(--af-accent);
  border-color: var(--af-accent);
}

/* Dismissed is deliberately quiet — it is a row that stopped asking for
 * attention, and styling it as a warning would put the attention back. */
.review-badge.dismissed {
  color: var(--af-text-muted);
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.review-btn {
  padding: 5px 12px;
  font-size: 12px;
  font-family: inherit;
  color: var(--af-text-secondary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.review-btn:hover:not(:disabled) {
  color: var(--af-text);
  border-color: var(--af-accent);
}

.review-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* "撤销判定" is the undo for a misclick, not a third verdict — it should
 * read as available without competing with the two real choices. */
.review-btn.subtle {
  color: var(--af-text-muted);
  border-style: dashed;
}

.roster-actions {
  display: flex;
  gap: 8px;
}

.roster-form {
  max-width: 420px;
  padding: 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

@media (max-width: 640px) {
  .progress-bar {
    width: 72px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .progress-fill {
    transition: none;
  }
}
</style>
