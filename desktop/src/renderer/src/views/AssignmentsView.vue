<script setup lang="ts">
/**
 * AssignmentsView — the student's homework list, and where they join a
 * class (PU-13).
 *
 * Why joining lives on this page
 * ------------------------------
 * A class is not interesting on its own: it exists so that homework can
 * be set for it. A student who has not joined one sees an empty list and
 * no explanation, which is the worst possible first screen. Putting the
 * invite-code box on the same page means the empty state *is* the fix.
 *
 * What this page does not do
 * --------------------------
 * It does not gate the homework behind a class. Assignments with no
 * `class_id` are visible to everyone (see `api/assignments.py`), which
 * is what makes this page work on a database whose rows all predate
 * classes — and what lets a teacher set practice problems for nobody in
 * particular.
 *
 * Titles are decoration
 * ---------------------
 * An assignment carries problem *ids*; the titles come from a separate
 * catalogue call. That call is allowed to fail: a row falls back to
 * 「题目 #12」, which still identifies it and still links to it. Failing
 * the whole page because a label could not be fetched would be trading
 * the content for its caption.
 */

import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import { getMyAssignments, type MyAssignmentOut } from '../api/assignments'
import { joinClassroom, myClassrooms, type ClassroomOut } from '../api/classroom'
import { listProblems } from '../api/problems'
import { isLoggedIn, sessionReady, sessionUser } from '../api/session'

const loading = ref(false)
const errorMsg = ref('')

const assignments = ref<MyAssignmentOut[]>([])
const classrooms = ref<ClassroomOut[]>([])
/** problem id → title, filled best-effort. See the docstring. */
const titles = ref<Record<number, string>>({})

const inviteCode = ref('')
const joinBusy = ref(false)
const joinMsg = ref('')
const joinError = ref('')

/** Which assignment's problem list is open, by id. */
const expandedId = ref<number | null>(null)

const signedIn = computed<boolean>(() => isLoggedIn.value)
const displayName = computed<string>(
  () => sessionUser.value?.display_name || sessionUser.value?.username || ''
)

/** Turn a failure into something the person can act on. */
function describe(err: unknown): string {
  const res = (err as { response?: { status?: number; data?: { detail?: string } } })?.response
  const detail = res?.data?.detail
  if (res?.status === 401) return '登录已过期，请重新登录'
  if (res?.status === undefined) return '无法连接到分析引擎，请确认后端已启动'
  return detail ?? `请求失败（HTTP ${res?.status}）`
}

async function loadTitles(): Promise<void> {
  const wanted = new Set<number>()
  for (const row of assignments.value) {
    for (const id of row.problem_ids) wanted.add(id)
  }
  const missing = [...wanted].filter((id) => titles.value[id] === undefined)
  if (missing.length === 0) return
  try {
    // One page of a hundred covers a term of homework; the catalogue
    // caps `page_size` here anyway.
    const page = await listProblems({ page_size: 100 })
    const next = { ...titles.value }
    for (const problem of page.items) next[problem.id] = problem.title
    titles.value = next
  } catch {
    // See the docstring: ids still identify the rows.
  }
}

async function load(): Promise<void> {
  if (!isLoggedIn.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const [mine, rooms] = await Promise.all([getMyAssignments(), myClassrooms()])
    assignments.value = mine
    classrooms.value = rooms
    await loadTitles()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function submitJoin(): Promise<void> {
  const code = inviteCode.value.trim()
  if (!code || joinBusy.value) return
  joinBusy.value = true
  joinMsg.value = ''
  joinError.value = ''
  try {
    const result = await joinClassroom(code)
    joinMsg.value = result.already_member
      ? `你已经在「${result.classroom.name}」里了`
      : `已加入「${result.classroom.name}」`
    inviteCode.value = ''
    // Reload rather than splice: joining changes which assignments are
    // visible, and that set is the server's answer to compute.
    await load()
  } catch (err) {
    joinError.value = describe(err)
  } finally {
    joinBusy.value = false
  }
}

function titleFor(problemId: number): string {
  return titles.value[problemId] ?? `题目 #${problemId}`
}

function isSolved(row: MyAssignmentOut, problemId: number): boolean {
  return row.solved_problem_ids.includes(problemId)
}

/** The next problem worth opening: the first one not passed yet. */
function nextProblem(row: MyAssignmentOut): number | null {
  const pending = row.problem_ids.find((id) => !isSolved(row, id))
  return pending ?? row.problem_ids[0] ?? null
}

function progressPercent(row: MyAssignmentOut): string {
  if (row.problem_count === 0) return '0%'
  return `${Math.round((row.solved / row.problem_count) * 100)}%`
}

function dueLabel(row: MyAssignmentOut): string {
  if (!row.due_at) return '没有截止时间'
  const when = new Date(row.due_at)
  if (Number.isNaN(when.getTime())) return '没有截止时间'
  const text = `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, '0')}-${String(
    when.getDate()
  ).padStart(2, '0')} ${String(when.getHours()).padStart(2, '0')}:${String(
    when.getMinutes()
  ).padStart(2, '0')}`
  return row.overdue ? `已过截止 ${text}` : `截止 ${text}`
}

// Waits for `sessionReady`: before the startup round trip resolves the
// app does not know whether anyone is signed in, and loading on "unknown"
// would show the signed-out state to somebody who is signed in.
watch(
  () => [sessionReady.value, sessionUser.value?.id] as const,
  ([ready, id]) => {
    if (ready && id) void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="mx-auto w-full max-w-[var(--af-content-max)] p-6">
      <h1 class="text-lg font-semibold text-af-primary">我的作业</h1>

      <!-- Signed out: the page cannot compute "my" anything. -->
      <div v-if="!signedIn" class="af-glass mt-5 rounded-[var(--af-radius-lg)] p-6 text-center">
        <p class="text-sm text-af-primary">还没有登录</p>
        <p class="mt-2 text-xs leading-relaxed text-af-secondary">
          作业、进度和班级都记在你的账号上，登录后才会显示。
        </p>
        <RouterLink
          class="mt-4 inline-block rounded-[var(--af-radius-sm)] bg-af-accent px-4 py-2 text-sm font-medium af-press text-af-inverse transition-colors hover:bg-af-accent-hover"
          :to="{ name: 'login' }"
        >
          去登录
        </RouterLink>
      </div>

      <template v-else>
        <!-- ---- Classes ---- -->
        <section class="mt-5">
          <h2 class="text-sm font-medium text-af-primary">我的班级</h2>

          <div v-if="classrooms.length > 0" class="mt-2 flex flex-wrap items-center gap-2">
            <span
              v-for="room in classrooms"
              :key="room.id"
              class="rounded-full border border-af-border bg-af-elevated px-3 py-1 text-xs text-af-secondary"
            >
              {{ room.name }}
            </span>
          </div>
          <p v-else class="mt-2 text-xs text-af-secondary">
            还没有加入任何班级。输入老师给的邀请码即可加入。
          </p>

          <form class="mt-3 flex flex-wrap items-center gap-2" @submit.prevent="submitJoin">
            <NInput
              v-model:value="inviteCode"
              class="!w-40"
              type="text"
              placeholder="输入邀请码"
              maxlength="16"
              :disabled="joinBusy"
            />
            <NButton
              attr-type="submit"
              size="small"
              :loading="joinBusy"
            >
              {{ joinBusy ? '加入中…' : '加入班级' }}
            </NButton>
          </form>

          <p v-if="joinMsg" class="mt-2 text-xs text-af-success">{{ joinMsg }}</p>
          <p v-if="joinError" class="mt-2 text-xs text-af-danger" role="alert">
            {{ joinError }}
          </p>
        </section>

        <!-- ---- Assignments ---- -->
        <section class="mt-8">
          <div class="flex items-baseline justify-between gap-3">
            <h2 class="text-sm font-medium text-af-primary">作业</h2>
            <span v-if="displayName" class="text-xs text-af-muted">{{ displayName }}</span>
          </div>

          <p v-if="errorMsg" class="mt-3 text-xs text-af-danger" role="alert">
            {{ errorMsg }}
          </p>
          <p v-else-if="loading" class="mt-3 text-xs text-af-secondary">正在加载…</p>
          <p
            v-else-if="assignments.length === 0"
            class="af-glass mt-3 rounded-[var(--af-radius-md)] px-4 py-6 text-center text-xs leading-relaxed text-af-secondary"
          >
            还没有布置给你的作业。<br />
            如果老师已经布置了，先在上面用邀请码加入班级。
          </p>

          <ul v-else class="af-stagger mt-3 flex flex-col gap-3">
            <li
              v-for="row in assignments"
              :key="row.id"
              class="af-glass af-lift rounded-[var(--af-radius-lg)] p-4"
            >
              <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h3 class="text-sm font-medium text-af-primary">{{ row.title }}</h3>
                <span
                  v-if="row.classroom_name"
                  class="rounded-full border border-af-border bg-af-elevated px-2 py-0.5 text-[11px] text-af-secondary"
                >
                  {{ row.classroom_name }}
                </span>
                <span
                  v-else
                  class="rounded-full border border-af-border bg-af-elevated px-2 py-0.5 text-[11px] text-af-muted"
                >
                  所有人
                </span>
                <span
                  class="ml-auto text-[11px]"
                  :class="row.overdue ? 'text-af-danger' : 'text-af-muted'"
                >
                  {{ dueLabel(row) }}
                </span>
              </div>

              <p v-if="row.description" class="mt-2 text-xs leading-relaxed text-af-secondary">
                {{ row.description }}
              </p>

              <div class="mt-3 flex items-center gap-3">
                <div class="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-af-elevated">
                  <!-- `af-grow` scales the fill in from the left on mount
                       rather than animating `width`, which would relayout
                       every frame; the `transition-[width]` above is what
                       handles a later change (a problem gets solved). -->
                  <div
                    class="af-grow h-full rounded-full transition-[width]"
                    :class="row.progress === 1 ? 'bg-af-success' : 'bg-af-accent'"
                    :style="{ width: progressPercent(row) }"
                  />
                </div>
                <span class="shrink-0 text-xs text-af-secondary">
                  {{ row.solved }} / {{ row.problem_count }}
                </span>
                <span v-if="row.progress === null" class="shrink-0 text-[11px] text-af-muted">
                  还没开始
                </span>
              </div>

              <div class="mt-3 flex flex-wrap items-center gap-2">
                <RouterLink
                  v-if="nextProblem(row) !== null"
                  custom
                  :to="{
                    name: 'problem',
                    params: { id: nextProblem(row) },
                    query: { assignment: row.id }
                  }"
                  v-slot="{ navigate, href }"
                >
                  <NButton size="small" type="primary" tag="a" :href="href" @click="navigate">
                    {{ row.progress === null ? '开始做题' : '继续做题' }}
                  </NButton>
                </RouterLink>
                <NButton
                  size="small"
                  @click="expandedId = expandedId === row.id ? null : row.id"
                >
                  {{ expandedId === row.id ? '收起题目' : `查看题目（${row.problem_count}）` }}
                </NButton>
              </div>

              <ul v-if="expandedId === row.id" class="mt-3 flex flex-col gap-1">
                <li v-for="problemId in row.problem_ids" :key="problemId">
                  <RouterLink
                    class="flex items-center gap-2 rounded-[var(--af-radius-sm)] px-2 py-1.5 text-xs transition-colors hover:bg-af-elevated"
                    :to="{
                      name: 'problem',
                      params: { id: problemId },
                      query: { assignment: row.id }
                    }"
                  >
                    <span
                      class="w-4 shrink-0 text-center"
                      :class="isSolved(row, problemId) ? 'text-af-success' : 'text-af-muted'"
                      :title="isSolved(row, problemId) ? '已通过' : '未通过'"
                    >
                      {{ isSolved(row, problemId) ? '✓' : '·' }}
                    </span>
                    <span
                      class="truncate"
                      :class="isSolved(row, problemId) ? 'text-af-secondary' : 'text-af-primary'"
                    >
                      {{ titleFor(problemId) }}
                    </span>
                  </RouterLink>
                </li>
              </ul>
            </li>
          </ul>
        </section>
      </template>
    </div>
  </div>
</template>
