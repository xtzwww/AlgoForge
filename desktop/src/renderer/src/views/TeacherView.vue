<script setup lang="ts">
/**
 * TeacherView — the class learning console (阶段 3).
 *
 * Four states, and the first three are not error states:
 *
 *   1. 会话恢复中  — the startup round trip has not answered yet.
 *   2. 未登录      — a login form, inline rather than on its own route.
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
 * Why the login is inline
 * -----------------------
 * A separate `/login` route would need a guard deciding where to send
 * you afterwards, and a guard is a place to get the role question wrong.
 * Here the page you asked for is the page you get, authenticated or not,
 * which is also the honest shape: every teacher route refuses an
 * unauthenticated request anyway (see `app/api/teacher.py`), so showing
 * the form is a convenience, never the boundary.
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
import PageHeader from '../components/PageHeader.vue'
import { masteryColorFor } from '../assets/tokens'
import { isLoggedIn, isTeacher, login, logout, sessionReady, sessionUser } from '../api/session'
import {
  createAssignment,
  deleteAssignment,
  getAssignmentStats,
  listAssignments,
  type AssignmentOut,
  type AssignmentStats
} from '../api/assignments'
import {
  createStudent,
  getAlerts,
  getErrorDistribution,
  getFlaggedSubmissions,
  getKnowledgeStats,
  getOverview,
  getStudents,
  getStudentTimeline,
  reviewFlag,
  type AlertOut,
  type ClassOverview,
  type ErrorCount,
  type FlaggedSubmission,
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

const loginName = ref('')
const loginPassword = ref('')
const loginError = ref('')
const loginBusy = ref(false)

async function submitLogin(): Promise<void> {
  if (!loginName.value || !loginPassword.value) {
    loginError.value = '请填写用户名和密码'
    return
  }
  loginBusy.value = true
  loginError.value = ''
  try {
    await login(loginName.value, loginPassword.value)
    loginPassword.value = ''
    await load()
  } catch (err) {
    // The server's message is already the whole story: it answers 401
    // with one string for both a wrong password and an unknown user, on
    // purpose, so there is nothing to add here.
    loginError.value = messageOf(err, '登录失败')
  } finally {
    loginBusy.value = false
  }
}

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
    await loadAssignments()
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

async function loadAssignments(): Promise<void> {
  try {
    assignments.value = await listAssignments()
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载作业列表')
  }
}

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
      due_at: newAssignmentDue.value ? newAssignmentDue.value : undefined
    })
    addingAssignment.value = false
    newAssignmentTitle.value = ''
    newAssignmentProblems.value = ''
    newAssignmentDue.value = ''
    await loadAssignments()
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

      <!-- 2. Nobody is logged in. -->
      <div v-else-if="!isLoggedIn" class="gate">
        <h2 class="gate-title">教师登录</h2>
        <p class="gate-body">
          学情看板需要教师账号。学生侧的练习、错题本和画像不需要登录，照常可用。
        </p>
        <form class="gate-form" @submit.prevent="submitLogin">
          <label class="field">
            <span class="field-label">用户名</span>
            <input v-model="loginName" class="input" type="text" autocomplete="username" />
          </label>
          <label class="field">
            <span class="field-label">密码</span>
            <input
              v-model="loginPassword"
              class="input"
              type="password"
              autocomplete="current-password"
            />
          </label>
          <p v-if="loginError" class="gate-error">{{ loginError }}</p>
          <button class="primary" type="submit" :disabled="loginBusy">
            {{ loginBusy ? '登录中…' : '登录' }}
          </button>
        </form>
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
            <ul class="alert-list">
              <li v-for="(alert, index) in alerts" :key="`${alert.user_id}-${index}`">
                <button type="button" class="alert-who" @click="openAlertStudent(alert)">
                  {{ alert.display_name }}
                </button>
                <span class="alert-reason">{{ alert.reason }}</span>
              </li>
            </ul>
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
                  <span v-if="a.due_at" class="assignment-meta" :class="{ overdue: a.overdue }">
                    {{ a.overdue ? '已过期 · ' : '' }}{{ dateText(a.due_at) }}
                  </span>
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

          <!-- --- Roster --- -->
          <section class="section">
            <h2 class="section-title">学生账号</h2>
            <p class="section-note">
              学生不能自己注册——花名册由任课老师建立，这也是这套系统里唯一会写入学生记录的操作。
            </p>

            <button
              v-if="!addingStudent"
              class="primary"
              type="button"
              @click="addingStudent = true"
            >
              添加学生
            </button>

            <form v-else class="roster-form" @submit.prevent="submitNewStudent">
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
  /* White rather than a token, for the same reason as Practice.vue's
   * `.btn-primary`: this is the one foreground whose job is to stay
   * legible on the accent fill, and no token expresses that
   * relationship. */
  color: #ffffff;
  background: var(--af-accent);
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.primary:hover:not(:disabled) {
  background: var(--af-accent-hover);
}

.primary:disabled {
  opacity: 0.6;
  cursor: default;
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

/* --- The attention list (阶段 6) --- */

.alert-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.alert-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--af-border);
}

.alert-list li:last-child {
  border-bottom: none;
}

.alert-who {
  padding: 0;
  font-size: 13px;
  font-family: inherit;
  font-weight: 600;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
}

.alert-who:hover {
  text-decoration: underline;
}

/* The reason is the point of the row, so it gets the room. */
.alert-reason {
  flex: 1 1 260px;
  color: var(--af-text-secondary);
}

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
