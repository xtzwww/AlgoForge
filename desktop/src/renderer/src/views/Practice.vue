<script setup lang="ts">
/**
 * Practice view — the coding and judging page (P1-16, PU-14, PU-8).
 *
 * Layout (desktop):
 *
 *   ┌── toolbar: language picker · [运行] [提交] ─────────────────────┐
 *   ├── Problem panel ────────┬── Editor ─────────────────────────────┤
 *   │  statement, formats,    │  Monaco                              │
 *   │  tags, samples, limits  │  OutputPanel                         │
 *   └─────────────────────────┴──────────────────────────────────────┘
 *
 * **PU-8 changed three things.**
 *
 * 1. *运行 and 提交 are separate actions.* They used to be one
 *    「运行判题」 button that did whichever half applied. Now:
 *
 *      | action | cases            | recorded | scored |
 *      |--------|------------------|----------|--------|
 *      | 运行    | the visible ones | no       | no     |
 *      | 提交    | every case       | yes      | yes    |
 *
 *    The distinction is carried by the sidecar's `record` flag (see
 *    `api/ws.ts`), not by the client pretending: a scratch run really
 *    does not write history, so iterating on a WA does not fill the
 *    mistake book with near-duplicates of the same bug. In free-run mode
 *    the two differ only in that, because there is no problem row to
 *    attribute a submission to and the page's two cases *are* the case
 *    set — 运行 is still the one that keeps the user's history clean.
 *
 * 2. *All hardcoded colours are gone.* The old stylesheet carried
 *    `#1a1a2e`, `#16213e`, `#0f3460`, `#3b82f6` and friends; every one
 *    now resolves through a PU-0 token, so retheming is a `tokens.css`
 *    edit. Verdict colours are not here at all — they belong to
 *    `VerdictBadge.vue`, which is bound to the backend's `JudgeStatus`.
 *
 * 3. *Keyboard shortcuts.* `Ctrl/Cmd + Enter` runs, `Ctrl/Cmd + Shift +
 *    Enter` submits. This is the IDE convention and the page had none.
 *    Bound on `window` rather than on the editor because the user's
 *    focus is in Monaco, whose key events do not bubble to the page.
 *
 * **Modes.** `problemId` is set → the statement, samples and limits come
 * from the catalogue and judging sends `problem_id`, so the sidecar
 * reads the hidden cases itself and they never reach this process.
 * `problemId` is null → free-run mode, the scratchpad with the page's
 * own two cases. The mode is re-resolved on every prop change.
 *
 * **PU-10 changed one thing here.** The editor no longer receives a
 * theme name: `CodeEditor.vue` defaults to the AlgoForge theme
 * registered by `monaco-theme.ts`, and this page's job is now only to
 * hand over the language. The default language still comes from
 * `pickDefaultLanguage()`, which reads the sidecar's toolchain probe —
 * i.e. from what this machine can actually build, not from a constant.
 *
 * **This page still does not know the router exists** (PU-7): it takes
 * `problemId` and emits `exit`; `views/ProblemPracticeView.vue` is the
 * adapter that turns `exit` into a navigation. Its title is published
 * for the topbar's breadcrumb instead, via `usePageCrumb`.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { NButton, NSelect, useDialog } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import AssistPanel from '../components/AssistPanel.vue'
import CodeEditor from '../components/CodeEditor.vue'
import DebugPanel from '../components/DebugPanel.vue'
import OutputPanel from '../components/OutputPanel.vue'
import ProblemStatement from '../components/ProblemStatement.vue'
import VerdictBadge from '../components/VerdictBadge.vue'
import { listLevels, type AssistancePolicy } from '../api/assistance'
import {
  closeSession,
  createSession,
  getAvailability,
  setBreakpoints as putBreakpoints,
  step as stepDebug,
  type DebugAction,
  type DebugSession
} from '../api/debug'
import { runCases, submitCases, submitProblem, type JudgeEvent } from '../api/ws'
import type { JudgeResponse, TestCaseIn, TestCaseOut } from '../api/judge'
import { describeRequestError, statusOf } from '../api/errors'
import { getMyAssignments } from '../api/assignments'
import { getProblem, getProblemTestCases, listProblems, type ProblemDetail } from '../api/problems'
import { getLanguageOptions, pickDefaultLanguage, type LanguageOption } from '../api/languages'
import { hasNativeHighlighting, type MonacoLanguageId } from '../monaco-theme'
import { editTrace } from '../edit-trace'
import { publishPageCrumb } from '../composables/usePageCrumb'

const props = withDefaults(
  defineProps<{
    /**
     * Catalogue problem to practise, or null/absent for free-run mode.
     * Negated as `problemId?: number | null` so the shell can bind a
     * `ref<number | null>` directly without an extra wrapper.
     */
    problemId?: number | null
    /**
     * Homework this attempt belongs to, when there is one (阶段 5).
     *
     * It is the **only** channel through which the assistance level is
     * chosen: the level lives on the assignment row and the server looks
     * it up, so this page cannot ask for more help than the teacher set.
     * Free-run has no assignment and therefore falls on the default
     * level — which is why the prop is nullable rather than required.
     */
    assignmentId?: number | null
  }>(),
  { problemId: null, assignmentId: null }
)

const emit = defineEmits<{
  (e: 'exit'): void
  /** 用户点了「下一题」：跳到指定的题目 id（保持同一份作业的上下文）。 */
  (e: 'go-next', problemId: number): void
}>()

/**
 * Imperative confirmation dialogs.
 *
 * `window.confirm` is a project red line (AGENTS.md §二) — it blocks the
 * renderer, its styling is outside the theme system, and it cannot be
 * driven by a test. `useDialog()` returns a promise-based replacement;
 * `App.vue` hosts the `NDialogProvider` it needs.
 */
const dialog = useDialog()

// --- Free-run fallback problem (the pre-PU-14 built-in A + B) ---
/**
 * The scratchpad's case set. Only used when there is no `problemId`,
 * which is why it stays a literal: it is not a catalogue problem and
 * pretending otherwise (by seeding it into the database) would put a
 * non-problem in the problem list.
 */
const FREE_RUN_TITLE = '自由运行'
const FREE_RUN_DESCRIPTION = `自由运行模式：没有关联题库题目，测试用例由本页提供。

读入两个整数 a 和 b，输出它们的和。

输入格式：
  一行，两个整数 a 和 b，用空格分隔。

输出格式：
  一行，一个整数，表示 a + b 的结果。`

const FREE_RUN_CASES: TestCaseIn[] = [
  { input: '3 5', expected_output: '8' },
  { input: '10 20', expected_output: '30' }
]

/** Free-run limits: the sidecar's own defaults for inline cases. */
const FREE_RUN_TIME_LIMIT = 10.0
const FREE_RUN_MEM_LIMIT_MB = 256

/**
 * The problem's language when it has no preference of its own.
 * C++ keeps the free-run buffer's default solution valid.
 */
const DEFAULT_PROBLEM_LANGUAGE = 'cpp'

// --- Problem state (PU-14) ---
const problem = ref<ProblemDetail | null>(null)
/** Bumped per load so a slow fetch cannot overwrite a newer one. */
let loadToken = 0
const problemLoading = ref(false)
/**
 * Why the statement is missing, in words a student can act on.
 *
 * **Not `err.message`.** That renders `Request failed with status code
 * 404`, which names neither what is missing nor what to do about it —
 * and the 404 case has one overwhelmingly common cause worth stating:
 * re-importing the corpus (`tools/load_problems.py --replace`) deletes
 * and re-inserts every row, so auto-increment ids jump forward while
 * assignments, the mistake book and browser history still hold the old
 * ones. A link from an assignment then lands on a problem that is not
 * there any more. See `api/errors.ts`.
 */
const problemError = ref('')
/** Extra line under {@link problemError}: what the student can do next. */
const problemErrorHint = ref('')
/**
 * True when the problem is *gone* rather than merely unreachable.
 *
 * Drives a different panel: "this problem no longer exists" plus a way
 * back to the catalogue, instead of a retry button that can only fail
 * again.
 */
const problemMissing = ref(false)

/** Collapses the statement column to give the editor the full width. */
const statementCollapsed = ref(false)

/**
 * True when a catalogue problem is driving the page.
 *
 * Derived from the prop rather than from `problem.value`, so the header
 * and the loading branch can render "problem N is loading" instead of
 * momentarily claiming free-run mode.
 */
const isProblemMode = computed<boolean>(() => props.problemId !== null)

const problemTitle = computed<string>(
  () => problem.value?.title ?? (isProblemMode.value ? `题目 #${props.problemId}` : FREE_RUN_TITLE)
)

const problemDescription = computed<string>(
  () => problem.value?.description ?? (isProblemMode.value ? '' : FREE_RUN_DESCRIPTION)
)

/**
 * Samples to display beside the statement.
 *
 * In problem mode these come from the problem record's `samples` field
 * — what the author marked as displayable. Falling back to the visible
 * `test_cases` only happens if a problem has samples missing but
 * sample-flagged cases present, which the loader's shape checks should
 * make impossible; it is here so a stale row cannot render a statement
 * with no examples at all.
 */
const displayCases = computed<TestCaseIn[]>(() => {
  if (!isProblemMode.value) return FREE_RUN_CASES
  const p = problem.value
  if (!p) return []
  if (p.samples.length > 0) {
    return p.samples.map((s) => ({ input: s.input, expected_output: s.output }))
  }
  return p.test_cases.map((c) => ({
    input: c.input,
    expected_output: c.expected_output
  }))
})

/**
 * Cases that 运行 executes in problem mode.
 *
 * The *visible* ones — `ProblemDetail.test_cases` is already filtered to
 * `is_sample = True` by the sidecar. Sending them inline rather than
 * sending `problem_id` with `record: false` is deliberate: the visible
 * cases are what the user can see and reason about, so a scratch run has
 * no surprises in it, and the hidden cases stay out of the loop until
 * the user actually submits.
 *
 * If a problem somehow has no visible cases, this is empty and 运行 is
 * disabled with an explanation rather than silently falling back to the
 * hidden set.
 */
const runCasesForProblem = computed<TestCaseIn[]>(() => {
  const p = problem.value
  if (!p) return []
  return p.test_cases.map((c) => ({
    input: c.input,
    expected_output: c.expected_output
  }))
})

/**
 * Expected outputs for the *judging* cases, keyed by case ordinal.
 *
 * In problem mode the cases live on the sidecar and this page never sees
 * the hidden ones, so `caseResults` cannot fill in an expected output
 * from a local array the way free-run mode does. It fetches the full
 * case list once per problem instead — acceptable for this local
 * single-user deployment, and exactly the call `api/problems.ts` marks
 * as the one a web deployment must drop.
 *
 * Used for the submitted verdict's per-case diffs; a scratch run
 * resolves its expected outputs from `runCasesForProblem` instead, since
 * it knows exactly which cases it sent.
 */
const expectedByOrdinal = ref<Map<number, string>>(new Map())

async function loadExpectedOutputs(problemId: number): Promise<void> {
  const list = await getProblemTestCases(problemId)
  const map = new Map<number, string>()
  for (const c of list.test_cases) map.set(c.ordinal, c.expected_output)
  expectedByOrdinal.value = map
}

// --- 下一题 + 完成进度（2026-09-27）-------------------------------------
//
// 做题页过去是"一题到底"：做完了没有下一题，也没有"这轮做到哪了"的反馈。
// 现在从数据源拉出顺序：
//   - 作业模式：`getMyAssignments()` 里那份作业的 `problem_ids` 是老师排的
//     顺序，`solved_problem_ids` 是已经做对的。下一题 = 当前题之后第一个
//     没做对的；没有就从头找第一个没做对的（复习永远有路可走）；全对则
//     显示"全部完成"。
//   - 题库/自由练习：`listProblems()` 的目录顺序（一页最多 100 道），
//     下一题就是目录里的下一道。不做"跳过已做对"的轮转 —— 题库顺序不是
//     教学编排，按目录推进即可。

/** 导航所需的顺序与进度。`null` = 拉不到（非阻塞降级，照常做题）。 */
interface NavInfo {
  ids: number[]
  solvedIds: ReadonlySet<number>
  total: number
  solvedCount: number
}

const navInfo = ref<NavInfo | null>(null)

async function loadNav(): Promise<void> {
  if (props.problemId === null) {
    navInfo.value = null
    return
  }
  try {
    if (props.assignmentId !== null) {
      const all = await getMyAssignments()
      const row = all.find((a) => a.id === props.assignmentId)
      navInfo.value = row
        ? {
            ids: row.problem_ids,
            solvedIds: new Set(row.solved_problem_ids),
            total: row.problem_count,
            solvedCount: row.solved
          }
        : null
      return
    }
    const page = await listProblems({ page_size: 100 })
    navInfo.value = {
      ids: page.items.map((p) => p.id),
      solvedIds: new Set(),
      total: 0,
      solvedCount: 0
    }
  } catch {
    // 拉不到导航不阻塞做题：按钮隐藏，页面照常。错误不弹横幅 —— 这是
    // 附属功能，判题才是主体。
    navInfo.value = null
  }
}

const currentNavIndex = computed<number>(() => {
  const ids = navInfo.value?.ids ?? []
  return props.problemId === null ? -1 : ids.indexOf(props.problemId)
})

/**
 * 下一题的 id。null = 没有了（按钮禁用并换成"完成"文案）。
 * 作业模式：优先当前题之后第一个没做对的，其次从头找第一个没做对的，
 * 全对 → null。题库模式：目录顺序的下一道，没有就 null。
 */
const nextProblemId = computed<number | null>(() => {
  const nav = navInfo.value
  if (!nav || nav.ids.length === 0) return null
  const idx = currentNavIndex.value
  if (props.assignmentId !== null) {
    for (let i = idx + 1; i < nav.ids.length; i++) {
      if (!nav.solvedIds.has(nav.ids[i])) return nav.ids[i]
    }
    for (const id of nav.ids) {
      if (!nav.solvedIds.has(id)) return id
    }
    return null
  }
  if (idx >= 0 && idx + 1 < nav.ids.length) return nav.ids[idx + 1]
  return null
})

/** 作业模式的完成进度（0–100）。题库模式没有进度概念，不显示。 */
const progressPercent = computed<number | null>(() => {
  const nav = navInfo.value
  if (props.assignmentId === null || !nav || nav.total === 0) return null
  return Math.round((nav.solvedCount / nav.total) * 100)
})

/** 作业模式里的当前位置："第 2/5 题"。导航拉不到时不显示。 */
const navPositionText = computed<string | null>(() => {
  const nav = navInfo.value
  if (props.assignmentId === null || !nav || nav.total === 0) return null
  const idx = currentNavIndex.value
  if (idx < 0) return null
  return `第 ${idx + 1}/${nav.total} 题`
})

const nextButtonLabel = computed<string>(() => {
  if (nextProblemId.value !== null) return '下一题'
  return props.assignmentId !== null ? '全部完成' : '没有下一题'
})

/** 提交判为 AC 后刷新进度（做对一道，数字要动）。 */
function refreshNavOnPass(): void {
  if (props.assignmentId !== null) void loadNav()
}

// 换题 / 进入页面时重取导航数据。
watch(
  () => [props.problemId, props.assignmentId],
  () => {
    void loadNav()
  },
  { immediate: true }
)

// --- Editor state ---
const code = ref<string>(`#include <iostream>
using namespace std;

int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
`)

/**
 * The buffer's contents as this page last set them.
 *
 * Comparing `code` against this is how the page decides whether the user
 * has typed something that a language switch would silently destroy.
 */
const pristineCode = ref<string>(code.value)

// --- Language state ---
const languages = ref<LanguageOption[]>([])
const languageId = ref<string>(DEFAULT_PROBLEM_LANGUAGE)
const catalogueError = ref<string>('')

const selectedLanguage = computed<LanguageOption | null>(
  () => languages.value.find((o) => o.id === languageId.value) ?? null
)

/** 语言下拉的选项；不可用的语言保留但禁用，与原生 <select> 语义一致。 */
const languageOptions = computed<SelectOption[]>(() =>
  languages.value.map((o) => ({
    label: o.available ? o.display_name : `${o.display_name}（不可用）`,
    value: o.id,
    disabled: !o.available
  }))
)

/**
 * Monaco's language id, a different namespace from the sandbox's id.
 *
 * PU-10: this is the sidecar's `monaco_language` passed straight through.
 * It is typed as `MonacoLanguageId` so an id Monaco has no tokenizer for
 * is a compile error rather than an unhighlighted buffer — see
 * `monaco-theme.ts` for why no substitution happens here.
 */
const monacoLanguage = computed<MonacoLanguageId>(
  () => (selectedLanguage.value?.monaco_language ?? 'cpp') as MonacoLanguageId
)

/**
 * True when Monaco has no tokenizer for the selected language.
 *
 * Every language the sidecar currently ships (`cpp`, `python`, `java`)
 * has a real grammar, so this is false in practice — it exists because
 * the *registry* is the authority and a future language might not be in
 * it. Silence would leave the user to conclude the editor is broken.
 *
 * Asked in a computed rather than at module scope because Monaco's
 * language registry is only populated once `monaco.ts` has been
 * imported, which happens when `CodeEditor.vue` is evaluated.
 */
const highlightingIsApproximate = computed<boolean>(
  () => !!selectedLanguage.value && !hasNativeHighlighting(selectedLanguage.value.monaco_language)
)

const unavailableLanguages = computed<LanguageOption[]>(() =>
  languages.value.filter((o) => !o.available)
)

// --- AI assistance (阶段 5) ---
/**
 * The five levels, as published by the sidecar.
 *
 * Fetched once and kept: the rules are static (they are a table in
 * `app/assistance.py`), so re-fetching per problem would be a request
 * that can only ever return the same bytes. The *level in force* is a
 * different thing and is not read here — it is decided per assignment on
 * the server, and this page only learns it from each reply's `level`.
 */
const assistanceLevels = ref<AssistancePolicy[]>([])

/**
 * The policy to render.
 *
 * Deliberately the **default** level rather than "the level for this
 * assignment": this page cannot know the assignment's level without
 * asking the server, and asking by guessing a number is exactly what the
 * design forbids. So the panel renders the default's shape, and every
 * action it sends carries `assignment_id` — which is what actually
 * decides. A narrower assignment turns out to need fewer buttons; the
 * server refuses the extras with an explanation, and the panel shows it.
 *
 * The honest version of this is: the client may over-offer, never
 * under-enforce.
 */
const assistancePolicy = computed<AssistancePolicy | null>(() => {
  const levels = assistanceLevels.value
  if (levels.length === 0) return null
  // The middle of the published range, not a hardcoded 3: the constant
  // lives in one place server-side and a second copy here would drift.
  const mid = levels[Math.floor(levels.length / 2)]
  return mid ?? null
})

async function loadAssistanceLevels(): Promise<void> {
  try {
    assistanceLevels.value = await listLevels()
  } catch {
    // Not surfaced as an error: a missing panel is a degraded nicety,
    // and complaining about it would put a red banner over a page whose
    // actual job — writing and judging code — is working fine.
    assistanceLevels.value = []
  }
}

/** True while code should not be edited under the user's cursor. */
const judgeBusy = computed<boolean>(() => isRunning.value)

/**
 * Put AI-authored code into the buffer (阶段 5).
 *
 * **Appended, never replacing.** Four-档 asks the model for "the part the
 * student described", so dropping it at the end of the buffer is the
 * reading that matches the request. Overwriting would destroy the work
 * that is actually being assessed and would do it without a confirmation
 * — the one way an AI feature can lose a student's code for them.
 *
 * The blank line is what keeps the paste from welding itself to the
 * previous statement; without it a fragment starting with `}` produces
 * syntactically broken code that looks like the student's own mistake.
 */
function insertAssistanceCode(snippet: string): void {
  const body = snippet.replace(/\s+$/, '')
  if (body === '') return
  code.value = `${code.value.replace(/\s+$/, '')}\n\n${body}\n`
}

/**
 * Replace the whole buffer with AI-authored code (阶段 5, 五档).
 *
 * **This is the one path that overwrites the student's work**, and it is
 * gated on purpose: 五档's "完整解法" means "give me the answer", so
 * dropping the result at the end of the buffer (as 四档 does) would leave
 * two programs welded together and neither of them runnable. Replacing is
 * the only reading that matches the request.
 *
 * It still asks first. The panel emits an event and never touches `code` —
 * so the confirmation cannot be skipped by anything inside the panel, and
 * an accidental click during a long solve cannot silently destroy a
 * half-written attempt. Skipped when the buffer is untouched (equal to
 * `pristineCode`), because then there is nothing to lose and a prompt
 * would be pure friction.
 */
async function replaceAssistanceCode(snippet: string): Promise<void> {
  const body = snippet.replace(/\s+$/, '')
  if (body === '') return

  if (code.value !== pristineCode.value) {
    const ok = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: '用 AI 的代码替换当前代码？',
        content: '你在编辑器里写的代码会被整段替换，且无法撤销。原来的内容不会保留。',
        positiveText: '替换',
        negativeText: '取消',
        // Every exit must settle the promise. Missing `onMaskClick` /
        // `onClose` is the classic way a confirm dialog leaves an await
        // hanging forever — the student clicks the mask and nothing moves.
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onMaskClick: () => resolve(false),
        onClose: () => resolve(false)
      })
    })
    if (!ok) return
  }

  code.value = `${body}\n`
  // The buffer now holds AI output rather than the student's edits, so the
  // "has the user typed since we last set it" baseline moves with it —
  // otherwise the next language switch would warn about changes the AI
  // made, not the student.
  pristineCode.value = code.value
}

// --- Judge state ---
/**
 * Which action is in flight, or `null` when idle.
 *
 * A single field rather than an `isRunning` boolean plus an
 * `isSubmitting` flag: the two cannot overlap (the sidecar judges one
 * run at a time on this connection), and two booleans would allow a
 * state that means nothing and would then have to be rendered.
 */
type RunKind = 'run' | 'submit'
const activeRun = ref<RunKind | null>(null)
const isRunning = computed<boolean>(() => activeRun.value !== null)
/** The kind of the last *finished* run, for the output panel's wording. */
const lastRunKind = ref<RunKind>('run')

const events = ref<JudgeEvent[]>([])
const result = ref<JudgeResponse | null>(null)
const errorMsg = ref<string>('')

// --- Breakpoint debugging (PU-17) ---------------------------------------
//
// The bottom half of the code column holds one of two things: the judge's
// output, or the debugger. They are tabs rather than two stacked panels
// because both want the full height and neither is useful at half of it —
// the output panel's per-case diffs and the debugger's variable table
// both scroll, and a 90px scrolling box is not readable.
//
// The debugger is not a *third* panel and does not take the editor's
// place: a student debugging needs the code and the variables at the same
// time, which is the whole point of the gutter marker.

/**
 * Which panel owns the bottom half.
 *
 * A tab and not a toggle, because "the judge ran and I want to look at
 * its output now" is a thing that happens constantly and must not require
 * closing the debug session.
 */
const bottomTab = ref<'output' | 'debug'>('output')

/** The open debug session, or null. The sidecar owns its real state. */
const debugSession = ref<DebugSession | null>(null)
const debugBusy = ref(false)
const debugLoading = ref(false)
/** A failed debug action. Kept separate from the judge's `errorMsg`. */
const debugError = ref<string>('')
/** A transient confirmation — "断点已保存" — never styled as an error. */
const debugNotice = ref<string>('')

/**
 * Whether this machine can debug, from `GET /debug/availability`.
 *
 * Defaults to `false` with a reason, so a failed check shows the
 * explanation rather than a control bar whose buttons all fail: the
 * loading state must not look like the available state.
 */
const debugAvailable = ref(false)
const debugUnavailableReason = ref('正在检查调试环境…')

/**
 * The breakpoint lines, kept as a `Set` for membership and exposed sorted.
 *
 * The editor compares a serialised form of this (see its watcher), so the
 * array identity changing on every render is harmless — but the *order*
 * has to be stable or every render would look like a change.
 */
const breakpoints = ref<Set<number>>(new Set())
const breakpointLines = computed<number[]>(() => [...breakpoints.value].sort((a, b) => a - b))

/** Where the debugger is stopped, for the editor's current-line tint. */
const debugCurrentLine = computed<number | null>(() => debugSession.value?.stop?.line ?? null)

/** True once a session exists, which is what the 调试 tab needs to show. */
const hasDebugSession = computed<boolean>(() => debugSession.value !== null)

/**
 * Which of the two bottom panels is on screen.
 *
 * Falls back to the output panel when there is no session: the 调试 tab is
 * not offered until one exists, and a tab strip that is showing a tab it
 * does not offer is a state nothing can return from.
 */
const activeBottomTab = computed<'output' | 'debug'>(() =>
  hasDebugSession.value ? bottomTab.value : 'output'
)

/**
 * Check the toolchain once, on mount.
 *
 * Cheap by construction — the sidecar's probe is a file-existence test,
 * not a launch — so it runs on every page load rather than being cached
 * across the app. A stale "unavailable" after a student installs the
 * toolchain would be worse than the repeated check it saves.
 */
async function loadDebugAvailability(): Promise<void> {
  try {
    const info = await getAvailability()
    debugAvailable.value = info.available
    if (!info.available) {
      debugUnavailableReason.value = info.reason ?? '调试环境不可用。'
    }
  } catch {
    // A failed *check* is not a failed *toolchain*, but from this page
    // the two are indistinguishable and the safe reading is "assume
    // unavailable" — offering a button that errors is worse.
    debugAvailable.value = false
    debugUnavailableReason.value = '无法确认调试环境是否可用，请稍后重试或联系老师。'
  }
}

/**
 * Turn a thrown request into something a student can act on.
 *
 * axios puts the sidecar's own `detail` on `response.data`, and those
 * messages are written for this exact purpose (a compile error names the
 * line; an unavailable toolchain names the files). Falling back to
 * `err.message` keeps a network failure from rendering as `[object
 * Object]`.
 */
function describeDebugError(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (err instanceof Error && err.message) return err.message
  return '调试请求失败。'
}

/**
 * Open a session, compiling the code with debug info.
 *
 * Compiling at open rather than on the first step is the sidecar's
 * choice, and this page relies on it: a compile error arrives here, while
 * the student is looking at the moment they expected a build, instead of
 * as a confusing failure of 单步跳过.
 */
async function onDebugStart(): Promise<void> {
  if (debugBusy.value || debugLoading.value) return
  debugLoading.value = true
  debugError.value = ''
  debugNotice.value = ''

  try {
    const session = await createSession(code.value, breakpointLines.value)
    debugSession.value = session
    bottomTab.value = 'debug'
  } catch (err) {
    debugError.value = describeDebugError(err)
  } finally {
    debugLoading.value = false
  }
}

/**
 * Toggle a breakpoint from the editor's gutter, and tell the sidecar.
 *
 * The optimistic update is deliberate — the dot appears on click, not
 * after a round trip — and the request follows to make it true. If that
 * request fails, the local set is *not* rolled back: the next session
 * sends the whole set again, and reverting would move the dot away from
 * under the student's cursor while they read an error message.
 */
async function toggleBreakpoint(line: number): Promise<void> {
  const next = new Set(breakpoints.value)
  if (next.has(line)) next.delete(line)
  else next.add(line)
  breakpoints.value = next

  if (!debugSession.value) return

  try {
    await putBreakpoints(
      debugSession.value.session_id,
      [...next].sort((a, b) => a - b)
    )
    debugError.value = ''
    debugNotice.value = '断点已保存，下次单步生效'
  } catch (err) {
    debugError.value = describeDebugError(err)
    debugNotice.value = ''
  }
}

/** Move the program and adopt the returned state. */
async function onDebugStep(action: DebugAction): Promise<void> {
  const session = debugSession.value
  if (!session || debugBusy.value) return
  debugBusy.value = true
  debugError.value = ''
  debugNotice.value = ''

  try {
    debugSession.value = await stepDebug(session.session_id, action)
  } catch (err) {
    // The session survives most step failures (a timeout, a missing
    // binary), so it is kept: the position and variables on screen are
    // still true and worth more than an empty panel.
    debugError.value = describeDebugError(err)
  } finally {
    debugBusy.value = false
  }
}

/**
 * Close the session and go back to the output panel.
 *
 * Called both by the panel's 结束调试 and by unmount, so the `finally`
 * matters: the local state must be cleared even when the request fails,
 * or a session the sidecar has already dropped (expired, evicted) would
 * leave this page convinced it still has one.
 */
async function onDebugClose(): Promise<void> {
  const session = debugSession.value
  debugSession.value = null
  debugBusy.value = false
  debugError.value = ''
  debugNotice.value = ''
  bottomTab.value = 'output'
  if (!session) return

  try {
    await closeSession(session.session_id)
  } catch {
    // Cleanup is best-effort: the store has its own TTL and cap, and an
    // error here is not something the student can act on.
  }
}

/**
 * Drop a session whose code no longer matches.
 *
 * Switching language replaces the buffer with a starter template, which
 * is a different program: every breakpoint line refers to code that is
 * gone and the compiled binary is the old source. Keeping the session
 * would show variables for a program that is not on screen — the exact
 * kind of quiet wrongness this panel must not produce.
 */
watch(code, () => {
  if (debugSession.value) void onDebugClose()
})

onMounted(async () => {
  // Language catalogue and problem load are independent; run them
  // together so a slow env-check does not delay the statement. The
  // assistance rules are on the same footing — a failure there must not
  // be able to delay the code page. The debug probe joins them because it
  // is a file-existence check, not a launch.
  await Promise.all([
    loadLanguages(),
    loadProblem(),
    loadAssistanceLevels(),
    loadDebugAvailability()
  ])
  window.addEventListener('keydown', onKeydown)
})

watch(
  () => props.problemId,
  () => {
    void loadProblem()
  }
)

/**
 * PU-7: publish this page's breadcrumb label for the topbar.
 *
 * `/problems/:id` carries an id, not a name, and this page is what
 * fetched the name — so it hands the label to the shell rather than
 * letting the topbar fetch the same problem a second time. Free-run mode
 * publishes `null`, which tells the topbar to use the route's own title
 * (练习). See `composables/usePageCrumb.ts`.
 *
 * `immediate` because the first load happens in `onMounted`, and without
 * it the crumb would stay on the route's generic 题目 until the problem
 * changed.
 */
watch(problemTitle, (title) => publishPageCrumb(isProblemMode.value ? title : null), {
  immediate: true
})

// Leaving the page must clear the label, or the next route would inherit
// a crumb naming a problem it is not showing — and the keyboard handler
// must not outlive the page. An open debug session must not outlive it
// either: it holds a compiled binary in a scratch directory, and nothing
// on another page can close it.
onUnmounted(() => {
  publishPageCrumb(null)
  window.removeEventListener('keydown', onKeydown)
  void onDebugClose()
})

/**
 * Fetch the language catalogue and pick a default.
 *
 * The buffer starts as a working solution to the *free-run* problem, so
 * C++ is left alone when it is available; any other default would show
 * C++ source in a Python editor.
 */
async function loadLanguages(): Promise<void> {
  try {
    const options = await getLanguageOptions()
    languages.value = options

    const preferred = pickDefaultLanguage(options)
    if (!preferred) {
      catalogueError.value =
        '未检测到任何可用的编译环境，暂时无法判题。请检查下方各语言不可用的原因。'
      return
    }

    languageId.value = preferred.id
    if (preferred.id !== DEFAULT_PROBLEM_LANGUAGE) {
      applyTemplate(preferred)
    }
  } catch (err) {
    catalogueError.value = `无法获取语言列表：${(err as Error).message}`
  }
}

/**
 * Load the problem named by the prop, or clear back to free-run mode.
 *
 * `loadToken` guards against out-of-order responses: clicking through
 * the catalogue faster than the sidecar answers would otherwise let an
 * earlier problem's statement land on screen after the later one's.
 */
async function loadProblem(): Promise<void> {
  const problemId = props.problemId
  const token = ++loadToken

  // Any previous verdict belongs to a different problem.
  events.value = []
  result.value = null
  errorMsg.value = ''
  expectedByOrdinal.value = new Map()

  problemError.value = ''
  problemErrorHint.value = ''
  problemMissing.value = false

  if (problemId === null) {
    problem.value = null
    problemError.value = ''
    problemLoading.value = false
    return
  }

  problemLoading.value = true
  try {
    const [detail] = await Promise.all([getProblem(problemId), loadExpectedOutputs(problemId)])
    // A newer load started while this one was in flight; its result wins.
    if (token !== loadToken) return
    problem.value = detail
  } catch (err) {
    if (token !== loadToken) return
    problem.value = null
    const friendly = describeRequestError(err, `题目 #${problemId}`)
    problemError.value = friendly.message
    problemErrorHint.value = friendly.hint ?? ''
    // Only 404 means the row is really absent. A timeout must not claim
    // the problem is deleted — that would talk a student out of a problem
    // that is still there.
    problemMissing.value = statusOf(err) === 404
  } finally {
    if (token === loadToken) problemLoading.value = false
  }
}

/** Replace the buffer with *option*'s starter template. */
function applyTemplate(option: LanguageOption): void {
  pristineCode.value = option.default_template
  code.value = option.default_template
  events.value = []
  result.value = null
  errorMsg.value = ''
}

/**
 * Handle a picker change.
 *
 * Written as an `update:value` handler rather than `v-model` because the
 * switch can be declined: when the buffer holds user edits we ask first,
 * and if the answer is no the control has to snap back to the language
 * the app is actually still using. The NSelect is bound with `:value`
 * (not `v-model`), so declining leaves `languageId` untouched and the
 * picker shows the old selection again.
 */
async function onLanguageChange(value: string | number | null): Promise<void> {
  const option = languages.value.find((o) => o.id === value)
  if (!option || option.id === languageId.value) return

  if (code.value !== pristineCode.value) {
    // `useDialog`, not `window.confirm` — see the note on `dialog` above.
    const confirmed = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: `切换到 ${option.display_name}？`,
        content: '当前代码会被该语言的起始模板替换，未保存的修改将丢失。',
        positiveText: '替换',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onMaskClick: () => resolve(false),
        onClose: () => resolve(false)
      })
    })
    if (!confirmed) return
  }

  languageId.value = option.id
  applyTemplate(option)
}

/**
 * Whether a given action can start right now.
 *
 * Shared by the buttons and the shortcuts so the two can never disagree
 * about when an action is available — a shortcut that fires while its
 * button is disabled is the classic version of that bug.
 */
const canSubmit = computed<boolean>(
  () => !isRunning.value && !!selectedLanguage.value && !problemLoading.value && !problemError.value
)

/**
 * 运行 is additionally blocked when there is nothing to run against.
 *
 * In problem mode that means the problem has no visible cases. Free-run
 * always has its two, so this never blocks there.
 */
const canRun = computed<boolean>(
  () => canSubmit.value && (isProblemMode.value ? runCasesForProblem.value.length > 0 : true)
)

/** Why 运行 is unavailable, when it is. `''` when it is fine. */
const runBlockedReason = computed<string>(() => {
  if (!canSubmit.value || !isProblemMode.value) return ''
  if (runCasesForProblem.value.length > 0) return ''
  return '这道题没有公开样例，请直接提交。'
})

/**
 * Keyboard shortcuts: the IDE convention.
 *
 * `Ctrl/Cmd + Enter` runs, `Ctrl/Cmd + Shift + Enter` submits — the more
 * destructive/complete action needs the extra modifier, matching how
 * VS Code's Jupyter and most online judges bind it.
 *
 * `e.preventDefault()` matters more than usual here: Monaco maps plain
 * `Enter` itself, and a canvas-based editor can also swallow the event,
 * so an un-prevented default would insert a newline into the buffer.
 */
function onKeydown(e: KeyboardEvent): void {
  if (!(e.ctrlKey || e.metaKey) || e.key !== 'Enter') return
  e.preventDefault()
  if (e.shiftKey) {
    if (canSubmit.value) void submit()
  } else if (canRun.value) {
    void run()
  }
}

/**
 * Cases a run will execute, and the expected output lookup for its
 * results.
 *
 * The two are computed together because they have to agree: a mismatch
 * between what was sent and what the panel looks up would render a
 * diff against the wrong expected output, which is worse than no diff.
 */
interface RunPlan {
  request: (onEvent: (ev: JudgeEvent) => void) => Promise<JudgeEvent>
  /** Expected output for the case at 0-based *index* of this run. */
  expectedAt: (index: number) => string
  /** Input for the case at 0-based *index*, when the page knows it. */
  inputAt: (index: number) => string
}

/**
 * Build the plan for *kind*, or `null` when it cannot be built.
 *
 * Where the case set comes from is the whole of the 运行/提交 difference:
 *
 * - **submit in problem mode** — `problem_id`, so the sidecar resolves
 *   every case including the hidden ones and the client never sees them.
 * - **run in problem mode** — the visible cases inline. Sending them
 *   rather than `problem_id` is what keeps the hidden cases out of a
 *   scratch run; the sidecar would otherwise have no reason to exclude
 *   them.
 * - **free-run, either kind** — the page's own two cases. There is no
 *   problem row, so both are inline requests and they differ only in the
 *   `record` flag.
 */
function buildPlan(kind: RunKind): RunPlan | null {
  const language = languageId.value
  const problemId = props.problemId

  /**
   * The edit-behaviour trace, attached to every request (C1).
   *
   * Snapshotted here rather than at submit time only, because a scratch
   * run carries it too: the sidecar ignores it when it does not record,
   * and one code path that always attaches it cannot drift from a second
   * that sometimes does.
   *
   * The snapshot is a copy — the session keeps running, so the next
   * submission describes strictly more work than this one, which is what
   * makes a second attempt look different from a first.
   */
  const trace = editTrace.snapshot()

  if (problemId === null) {
    const cases = FREE_RUN_CASES
    const opts = {
      language,
      timeLimit: FREE_RUN_TIME_LIMIT,
      memLimitMb: FREE_RUN_MEM_LIMIT_MB,
      processTrace: trace
    }
    return {
      request: (onEvent) =>
        kind === 'submit'
          ? submitCases(code.value, cases, onEvent, opts)
          : runCases(code.value, cases, onEvent, opts),
      expectedAt: (i) => cases[i]?.expected_output ?? '',
      inputAt: (i) => cases[i]?.input ?? ''
    }
  }

  if (kind === 'submit') {
    const expected = expectedByOrdinal.value
    return {
      request: (onEvent) =>
        submitProblem(code.value, problemId, onEvent, { language, processTrace: trace }),
      expectedAt: (i) => expected.get(i + 1) ?? '',
      // The stream echoes the input on `case_done`, so this is a fallback.
      inputAt: () => ''
    }
  }

  const cases = runCasesForProblem.value
  if (cases.length === 0) return null
  return {
    request: (onEvent) => runCases(code.value, cases, onEvent, { language, processTrace: trace }),
    expectedAt: (i) => cases[i]?.expected_output ?? '',
    inputAt: (i) => cases[i]?.input ?? ''
  }
}

/**
 * Kick off a run and collect its verdict.
 *
 * `kind` is captured for the whole run rather than read from
 * `activeRun` later: the user can start the other action the moment this
 * one finishes, and a stale frame from the first run must not relabel
 * the second one's panel.
 */
async function start(kind: RunKind): Promise<void> {
  const plan = buildPlan(kind)
  if (!plan || isRunning.value) return

  activeRun.value = kind
  lastRunKind.value = kind
  events.value = []
  result.value = null
  errorMsg.value = ''

  const caseResults: TestCaseOut[] = []

  try {
    /**
     * Turn a terminal event into the panel's state.
     *
     * Called from the stream rather than from the awaited result, because
     * the promise now resolves after the *analysis*: the sidecar records
     * the verdict, sends `judge_done`, and only then runs the Agent. A
     * panel that waited for the promise would hold a verdict ready in
     * milliseconds behind a paid round trip that can take minutes, which
     * is the delay this split exists to remove.
     */
    const applyVerdict = (ev: JudgeEvent): void => {
      if (ev.type === 'judge_error') {
        errorMsg.value = ev.error ?? 'unknown error'
        return
      }
      result.value = {
        status: ev.status ?? 'RE',
        compile_output: ev.compile_output ?? '',
        passed: ev.passed ?? 0,
        total: ev.total ?? 0,
        problem_id: props.problemId,
        test_cases: caseResults,
        // The edit-behaviour observation (C1), carried on the terminal
        // event. Null when the sidecar had no trace to work from, which
        // the panel reports as nothing rather than as a clean run.
        process: ev.process ?? null
      }
    }

    await plan.request((ev: JudgeEvent) => {
      events.value.push(ev)

      if (ev.type === 'case_done' && ev.index !== undefined) {
        caseResults.push({
          status: ev.status ?? 'RE',
          input: ev.input ?? plan.inputAt(ev.index),
          expected_output: plan.expectedAt(ev.index),
          actual_output: ev.actual_output ?? '',
          stderr: ev.timed_out
            ? `Time Limit Exceeded`
            : ev.memory_exceeded
              ? `Memory Limit Exceeded`
              : '',
          exit_code: ev.exit_code ?? 0
        })
      }

      // Every `case_done` precedes this, so `caseResults` is complete by
      // the time the verdict arrives.
      if (ev.type === 'judge_done' || ev.type === 'judge_error') {
        applyVerdict(ev)
        // 提交判 AC 后刷新作业进度：做对一道，工具栏的"已完成 X/N"要跟着
        // 动。在流回调里判（而不是等 await 之后读 `result.value`）：事件
        // 自带 status，不依赖对 ref 的收窄。
        if (kind === 'submit' && ev.type === 'judge_done' && ev.status === 'AC') {
          refreshNavOnPass()
        }
      }
    })

    // `start` cleared both of these, so reaching here with neither set
    // means the stream ended without a verdict — a socket that closed
    // early. Saying so beats leaving the panel blank.
    if (!result.value && !errorMsg.value) {
      errorMsg.value = '判题流意外结束，没有收到结果'
    }
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    activeRun.value = null
  }
}

/** 运行: run against the visible cases, record nothing. */
function run(): Promise<void> {
  // Counted in the trace as "asked for a verdict". Not counted for
  // 提交: the backend reports this as runs *before* the submission, and
  // including the submission itself would make the number a lie.
  editTrace.recordRun()
  return start('run')
}

/** 提交: run against every case, record the verdict. */
function submit(): Promise<void> {
  return start('submit')
}
</script>

<template>
  <div class="practice">
    <!-- Toolbar: the only place the two actions live, so their relative
         weight (提交 is primary) reads at a glance. -->
    <header class="toolbar">
      <div class="toolbar-left">
        <h1 class="problem-title">
          <span class="title-text">{{ problemTitle }}</span>
          <span v-if="isProblemMode" class="badge badge-mode">题库 #{{ problemId }}</span>
          <span v-else class="badge badge-free">自由运行</span>
          <VerdictBadge v-if="result" :status="result.status" size="sm" />
        </h1>
      </div>

      <div class="toolbar-right">
        <!--
          作业模式的完成进度：细进度条 + "已完成 X/N"。
          题库/自由练习没有"进度"概念，只在作业模式下出现。
        -->
        <span
          v-if="progressPercent !== null"
          class="nav-progress"
          title="已做对的题目数 / 这份作业的题目总数"
        >
          <span class="nav-progress-bar">
            <span class="nav-progress-fill" :style="{ width: `${progressPercent}%` }"></span>
          </span>
          <span v-if="navPositionText" class="nav-position">{{ navPositionText }}</span>
          已完成 {{ navInfo?.solvedCount ?? 0 }}/{{ navInfo?.total ?? 0 }}
        </span>

        <NButton
          v-if="isProblemMode && (props.assignmentId === null || navInfo !== null)"
          class="btn btn-secondary btn-sm"
          size="small"
          :disabled="nextProblemId === null || isRunning"
          :title="
            nextProblemId === null
              ? props.assignmentId !== null
                ? '这份作业的所有题目都已完成'
                : '题库里没有下一道了'
              : '跳到下一题（没做对的优先）'
          "
          @click="nextProblemId !== null && emit('go-next', nextProblemId)"
        >
          {{ nextButtonLabel }}
        </NButton>

        <NButton
          v-if="isProblemMode"
          class="btn btn-text"
          size="small"
          quaternary
          @click="emit('exit')"
        >
          退出到自由运行
        </NButton>

        <label class="lang-picker">
          <span class="lang-label">语言</span>
          <NSelect
            class="lang-select"
            :value="languageId"
            :options="languageOptions"
            :disabled="isRunning || languages.length === 0"
            size="small"
            @update:value="onLanguageChange"
          />
        </label>

        <NButton
          class="btn btn-secondary"
          :disabled="!canRun"
          :loading="activeRun === 'run'"
          :title="runBlockedReason || '运行公开样例，不记录成绩（Ctrl/Cmd + Enter）'"
          @click="run"
        >
          运行
        </NButton>
        <NButton
          class="btn btn-primary"
          type="primary"
          :disabled="!canSubmit"
          :loading="activeRun === 'submit'"
          title="运行全部用例并记录成绩（Ctrl/Cmd + Shift + Enter）"
          @click="submit"
        >
          提交
        </NButton>
      </div>
    </header>

    <!--
      Disabled options cannot be opened to read their tooltip, so the
      reason — which already names the setting to change — is rendered
      as text instead of hidden behind a title attribute.
    -->
    <p v-if="catalogueError" class="notice notice-warn">{{ catalogueError }}</p>
    <ul v-else-if="unavailableLanguages.length" class="notice">
      <li v-for="opt in unavailableLanguages" :key="opt.id">
        <strong>{{ opt.display_name }} 不可用</strong>：{{ opt.detail }}
      </li>
    </ul>
    <!--
      PU-10: honesty about the tokenizer, not about the toolchain. The
      judging is real regardless; only the colours could be missing.
      False for every language the sidecar currently ships.
    -->
    <p v-if="highlightingIsApproximate" class="notice">
      {{ selectedLanguage?.display_name }} 在编辑器中暂无语法高亮（本机 Monaco
      未内置该语言的词法规则），
      <strong>判题仍然按 {{ selectedLanguage?.display_name }} 执行</strong>。
    </p>
    <p v-if="runBlockedReason" class="notice">{{ runBlockedReason }}</p>

    <div class="practice-body">
      <!-- Problem statement -->
      <section class="problem-panel" :class="{ collapsed: statementCollapsed }">
        <!-- Collapsed, the column keeps only its toggle so the editor
             gains the width; the label stays legible as a vertical strip. -->
        <div class="panel-head">
          <NButton
            class="collapse-btn"
            size="tiny"
            quaternary
            :aria-expanded="statementCollapsed ? 'false' : 'true'"
            :title="statementCollapsed ? '展开题面' : '折叠题面'"
            @click="statementCollapsed = !statementCollapsed"
          >
            <template #icon>
              <span v-if="!statementCollapsed" class="collapse-icon" aria-hidden="true">‹</span>
              <span v-else class="collapse-icon" aria-hidden="true">›</span>
            </template>
            <span class="collapse-label">{{ statementCollapsed ? '题面' : '收起' }}</span>
          </NButton>
        </div>

        <div v-if="!statementCollapsed" class="panel-body">
          <div v-if="problemLoading" class="placeholder">加载题目中…</div>
          <!--
            A missing problem gets its own panel rather than the generic
            error one: the useful action is different. Retrying cannot
            bring back a deleted row, so the student is offered a way back
            to the catalogue instead of a button that fails identically
            every time.
          -->
          <div v-else-if="problemMissing" class="placeholder placeholder-error">
            <p class="placeholder-title">找不到这道题</p>
            <p class="placeholder-text">{{ problemError }}</p>
            <p v-if="problemErrorHint" class="placeholder-text">{{ problemErrorHint }}</p>
            <NButton class="btn btn-secondary btn-sm" size="small" @click="emit('exit')">
              退出到自由运行
            </NButton>
          </div>
          <div v-else-if="problemError" class="placeholder placeholder-error">
            <p class="placeholder-title">无法加载题目 #{{ problemId }}</p>
            <p class="placeholder-text">{{ problemError }}</p>
            <p v-if="problemErrorHint" class="placeholder-text">{{ problemErrorHint }}</p>
            <NButton class="btn btn-secondary btn-sm" size="small" @click="loadProblem">
              重试
            </NButton>
          </div>

          <template v-else>
            <!--
              The whole statement block lives in its own component so it
              can be rendered and asserted without the editor, the
              WebSocket stream and the rest of this 1200-line view: it
              owns no state and issues no requests. Loading and error
              placeholders stay here because they *are* view state.
            -->
            <ProblemStatement
              :description="problemDescription"
              :input-format="problem?.input_format ?? ''"
              :output-format="problem?.output_format ?? ''"
              :knowledge-tags="problem?.knowledge_tags ?? []"
              :cases="displayCases"
              :is-problem-mode="isProblemMode"
              :time-limit="problem?.time_limit ?? null"
              :memory-limit="problem?.memory_limit ?? null"
            />
          </template>
        </div>
      </section>

      <!-- Editor + output -->
      <section class="code-panel">
        <div class="editor-wrapper">
          <!--
            No `theme` prop (PU-10): the editor defaults to the AlgoForge
            theme registered by `monaco-theme.ts`. Passing a name from
            here is how the two would drift.

            The gutter props (PU-17) are always passed, not only while
            debugging: breakpoints are set *before* starting, which is the
            order a student expects, and the markers have to survive the
            session ending.
          -->
          <CodeEditor
            v-model="code"
            :language="monacoLanguage"
            :breakpoints="breakpointLines"
            :current-line="debugCurrentLine"
            @toggle-breakpoint="toggleBreakpoint"
          />
        </div>

        <!--
          The bottom half: the judge's output, or the debugger.

          The debugger can only be reached once a session exists, and the
          tab is only offered then — see `activeBottomTab`.
        -->
        <div class="bottom-panel">
          <div class="bottom-tabs" role="tablist">
            <button
              class="bottom-tab"
              :class="{ 'is-active': activeBottomTab === 'output' }"
              role="tab"
              :aria-selected="activeBottomTab === 'output'"
              @click="bottomTab = 'output'"
            >
              判题输出
            </button>
            <button
              v-if="hasDebugSession"
              class="bottom-tab"
              :class="{ 'is-active': activeBottomTab === 'debug' }"
              role="tab"
              :aria-selected="activeBottomTab === 'debug'"
              @click="bottomTab = 'debug'"
            >
              调试
              <span v-if="breakpointLines.length" class="tab-badge">
                {{ breakpointLines.length }}
              </span>
            </button>

            <span class="bottom-tabs-spacer" />

            <!--
              The entry point. Hidden once a session exists, because from
              then on the 调试 tab is the way in and two controls doing the
              same thing is one to get out of step.
            -->
            <NButton
              v-if="!hasDebugSession"
              class="debug-start"
              size="small"
              :disabled="debugBusy || debugLoading || !debugAvailable || !code"
              :loading="debugLoading"
              :title="
                !debugAvailable ? debugUnavailableReason : '编译你的代码并逐步运行，可以打断点'
              "
              @click="onDebugStart"
            >
              开始调试
            </NButton>
          </div>

          <!--
            A failed build is reported on the tab strip rather than inside
            the debug panel: there is no session to put in a panel, and
            the message names a source line the student needs to see next
            to their code.
          -->
          <div v-if="debugError && !hasDebugSession" class="notice notice-error">
            {{ debugError }}
          </div>
          <div v-else-if="!debugAvailable && !hasDebugSession" class="notice">
            调试不可用：{{ debugUnavailableReason }}
          </div>
          <div v-else-if="debugNotice && !hasDebugSession" class="notice">
            {{ debugNotice }}
          </div>

          <OutputPanel
            v-show="activeBottomTab === 'output'"
            :events="events"
            :result="result"
            :error-msg="errorMsg"
            :is-running="isRunning"
            :is-scratch="lastRunKind === 'run'"
          />

          <DebugPanel
            v-if="activeBottomTab === 'debug'"
            :session="debugSession"
            :busy="debugBusy"
            :error-msg="debugError"
            :notice="debugNotice"
            :available="debugAvailable"
            :unavailable-reason="debugUnavailableReason"
            :has-code="!!code"
            @step="onDebugStep"
            @close="onDebugClose"
          />
        </div>
      </section>

      <!--
        AI assistance (阶段 5). Sits beside the editor, not inside it: the
        panel is a sibling column so it can be collapsed for width and so
        the editor component stays unaware of it. It never touches `code`
        itself — code can only enter the buffer through `insert`, which
        this page handles by appending (see `insertAssistanceCode`).
      -->
      <AssistPanel
        :policy="assistancePolicy"
        :assignment-id="assignmentId"
        :problem-id="problemId"
        :language="languageId"
        :prefix="code"
        :current-code="code"
        :statement="problemDescription"
        :busy="judgeBusy"
        @insert="insertAssistanceCode"
        @replace="replaceAssistanceCode"
      />
    </div>
  </div>
</template>

<style scoped>
/*
 * No hex literals: every colour is a PU-0 token. `var(--af-text-primary)`
 * is the `:root` namespace; the `--color-af-*` spellings in `tokens.css`
 * generate utilities and would resolve to nothing here.
 */
.practice {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
  box-sizing: border-box;
  gap: 10px;
  background: var(--af-canvas);
  color: var(--af-text-primary);
}

/* --- toolbar --- */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.toolbar-left {
  min-width: 0;
}

.toolbar-right {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

/* 作业进度：一根细能量条 + 数字。与 AI 强度轴同语言 —— 都是"往右更
 * 多"的渐变填充，都用主题色做发光。 */
.nav-progress {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--af-text-muted);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  white-space: nowrap;
}

.nav-position {
  font-weight: 600;
  color: var(--af-text-primary);
}

.nav-progress-bar {
  width: 56px;
  height: 5px;
  border-radius: 999px;
  background: var(--af-border);
  overflow: hidden;
}

.nav-progress-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--af-accent) 55%, transparent),
    var(--af-accent)
  );
  box-shadow: 0 0 6px color-mix(in srgb, var(--af-accent) 60%, transparent);
  transition: width 0.25s ease;
}

.problem-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  flex-shrink: 0;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 400;
  border-radius: 999px;
  white-space: nowrap;
}

.badge-mode {
  color: var(--af-accent);
  background: var(--af-accent-subtle);
}

.badge-free {
  color: var(--af-text-secondary);
  background: var(--af-elevated);
}

.lang-picker {
  display: flex;
  align-items: center;
  gap: 6px;
}

.lang-label {
  font-size: 13px;
  color: var(--af-text-secondary);
}

/* 语言选择器控件外观由 NSelect 接管（配色调自 naive-theme.ts）。 */

/* --- buttons（2026-09-27）：全部改用 NButton，控件外观由组件库统一接管；
 * 此处仅保留布局注释头，具体按钮样式不再手写。 */

/* --- notices --- */
.notice {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  list-style: none;
}

.notice-warn {
  color: var(--af-text-primary);
  border-color: var(--af-warning);
}

/* --- body --- */
.practice-body {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
}

.problem-panel {
  display: flex;
  flex-direction: column;
  width: var(--spacing-problem-panel);
  flex-shrink: 0;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  overflow: hidden;
  transition: width var(--af-duration-base) var(--af-ease);
}

/* Collapsed: a narrow strip holding only the toggle. The width is a
 * literal rather than a token because it is this one control's size, not
 * a layout value anything else shares. */
.problem-panel.collapsed {
  width: 36px;
}

.panel-head {
  display: flex;
  justify-content: flex-end;
  padding: 6px;
  border-bottom: 1px solid var(--af-border);
}

.problem-panel.collapsed .panel-head {
  justify-content: center;
  border-bottom: none;
}

/* 折叠按钮本体是 NButton（size tiny quaternary，配色走 naive-theme.ts）。
 * 下面只补组件库表达不了的两处布局：折叠态内容竖排、图标行内对齐。 */
.collapse-icon {
  line-height: 1;
}

.problem-panel.collapsed :deep(.n-button) {
  flex-direction: column;
  padding: 8px 2px;
}

.collapse-label {
  white-space: nowrap;
}

.problem-panel.collapsed .collapse-label {
  writing-mode: vertical-rl;
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 14px 14px 14px;
}

.panel-body h2 {
  margin: 16px 0 8px 0;
  font-size: 14px;
  color: var(--af-text-secondary);
}

/*
  The statement's markup and these rules now live in
  `components/ProblemStatement.vue`. They had to move together: this file
  is `<style scoped>`, and a scoped rule reaches only a child component's
  **root** element, so every one of them would have silently stopped
  applying to the inner nodes if they had stayed here.
*/

.placeholder {
  padding: 16px 4px;
  font-size: 13px;
  color: var(--af-text-muted);
}

.placeholder-error {
  color: var(--af-text-primary);
}

/*
 * A missing/blocked problem is a small stack rather than one line: title,
 * two lines of explanation, and a button. Laid out as a column so the
 * button sits under the text instead of wrapping beside it, which is what
 * a single block of prose would do at this column width.
 */
.placeholder-title {
  margin: 0 0 6px 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.placeholder-text {
  margin: 0 0 6px 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

/* placeholder 内的按钮已是 NButton；原 .placeholder .btn 间距规则随控件
 * 迁移删除。 */

/* --- editor + output --- */
.code-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  gap: 10px;
}

.editor-wrapper {
  flex: 1;
  min-height: 260px;
  overflow: hidden;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

/*
 * The bottom half: a tab strip plus whichever panel owns it.
 *
 * A bounded height rather than `flex: 1`, so a long variable list or a
 * 40-case verdict scrolls inside this box instead of pushing the toolbar
 * off the top of the page. Both panels already scroll internally, which
 * is what makes a fixed height safe.
 */
.bottom-panel {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  height: 280px;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  background: var(--af-surface);
}

.bottom-tabs {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
  align-items: center;
  padding: 0 8px;
  border-bottom: 1px solid var(--af-border);
}

.bottom-tabs-spacer {
  flex: 1;
}

.bottom-tab {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: 7px 10px;
  font-size: 12px;
  color: var(--af-text-muted);
  cursor: pointer;
  background: none;
  border: none;
  /* The rule sits under the active tab only — a full-width underline
   * would not say which of the two is selected. */
  border-bottom: 2px solid transparent;
  transition: color var(--af-duration-fast) var(--af-ease);
}

.bottom-tab:hover {
  color: var(--af-text-secondary);
}

.bottom-tab.is-active {
  font-weight: 600;
  color: var(--af-text-primary);
  border-bottom-color: var(--af-accent);
}

/*
 * How many breakpoints are set. Worth surfacing on the strip because a
 * session with breakpoints the student forgot about is the commonest
 * reason a 运行 appears to do nothing.
 */
.tab-badge {
  padding: 0 5px;
  font-size: 11px;
  font-weight: 400;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border-radius: 999px;
}

/* 调试入口按钮改用 NButton；原 .debug-start 的手写外观、hover 与
 * disabled 态全部由组件库接管。 */

/*
 * Notices about the debug entry point, above the panel rather than
 * inside it: there is no session to render a panel for when these fire,
 * and the message names a source line the student needs next to the code.
 */
.notice {
  flex-shrink: 0;
  padding: 6px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  border-bottom: 1px solid var(--af-border);
  word-break: break-word;
}

.notice-error {
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-danger) 15%, transparent);
}

/*
 * Below 900px the statement and the editor stop fitting side by side, so
 * the two columns stack. The statement keeps a bounded height and
 * scrolls on its own — letting it size to content would push the editor
 * off-screen.
 *
 * Stacked, the page can still be taller than the viewport, so the page
 * itself becomes the scroll container. Side by side it must not: there
 * the panels scroll internally and the editor fills the leftover height.
 *
 * Declared with the breakpoint rather than as Tailwind utilities on the
 * elements: a conditional width plus a conditional flex-direction plus an
 * overflow override is a compound rule, and the sidebar's docstring
 * explains why compound rules stay in CSS (two conflicting utilities in
 * one class list are resolved by stylesheet order, not class order).
 */
@media (max-width: 899px) {
  .practice {
    overflow-y: auto;
  }

  .practice-body {
    flex-direction: column;
  }

  .problem-panel {
    width: auto;
    max-height: 40vh;
    flex-shrink: 0;
  }

  /* Collapsing buys nothing once the columns are stacked. */
  .problem-panel.collapsed {
    width: auto;
  }

  .panel-head {
    justify-content: flex-start;
  }
}
</style>
