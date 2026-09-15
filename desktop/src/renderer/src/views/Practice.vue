<script setup lang="ts">
/**
 * Practice view — the main coding/judging page (P1-16, PU-14).
 *
 * Layout:
 *   ┌── Problem panel (left) ──┬── Code editor + Output panel (right) ──┐
 *   │  description + cases    │  language picker + Monaco editor       │
 *   │                         │  [运行判题] button                       │
 *   │                         │  OutputPanel (WS stream + final result) │
 *   └─────────────────────────┴─────────────────────────────────────────┘
 *
 * P1-16 changes vs P1-9 (Practice.vue):
 * - Uses the WebSocket stream (P1-15) instead of POST /api/v1/judge
 *   so the user sees compile/case events arrive in real time.
 * - Renders progress through the extracted OutputPanel component
 *   instead of inlining the result markup here.
 * - Assembles the final JudgeResponse from the terminal judge_done
 *   event + the per-case case_done events.
 *
 * PU-17 adds the language picker: the catalogue of languages comes from
 * the sidecar's env-check, and switching loads that language's starter
 * template (after confirming when the buffer holds edits).
 *
 * PU-14 replaces the hardcoded A + B problem with the catalogue. Two
 * modes, selected by the `problemId` prop:
 *
 * - **Problem mode** (`problemId` set): the statement, samples and
 *   limits come from `GET /problems/{id}`, and judging sends
 *   `problem_id` so the sidecar reads the hidden cases itself — they
 *   never reach this process.
 * - **Free-run mode** (`problemId` null): the old self-test behaviour,
 *   cases typed into the page. Kept deliberately, as a scratchpad for
 *   code that has no problem row yet.
 *
 * The mode is re-resolved whenever the prop changes, because the shell
 * keeps this component alive across problem switches only by accident
 * of tab switching — `App.vue` remounts it, but the `watch` also
 * covers a future shell that does not.
 */
import { computed, onMounted, ref, watch } from 'vue'
import CodeEditor from '../components/CodeEditor.vue'
import OutputPanel from '../components/OutputPanel.vue'
import { streamJudge, type JudgeEvent, type WsJudgeRequest } from '../api/ws'
import type { JudgeResponse, TestCaseIn, TestCaseOut } from '../api/judge'
import { getProblem, getProblemTestCases, type ProblemDetail } from '../api/problems'
import { getLanguageOptions, pickDefaultLanguage, type LanguageOption } from '../api/languages'

const props = withDefaults(
  defineProps<{
    /**
     * Catalogue problem to practise, or null/absent for free-run mode.
     * Negated as `problemId?: number | null` so the shell can bind a
     * `ref<number | null>` directly without an extra wrapper.
     */
    problemId?: number | null
  }>(),
  { problemId: null }
)

const emit = defineEmits<{ (e: 'exit'): void }>()

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
const problemError = ref('')

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
 * Expected outputs for the *judging* cases, keyed by case ordinal.
 *
 * In problem mode the cases live on the sidecar and this page never
 * sees the hidden ones, so `caseResults` cannot fill in an expected
 * output from a local array the way free-run mode does. It fetches the
 * full case list once per problem instead — acceptable for this local
 * single-user deployment, and exactly the call `api/problems.ts` marks
 * as the one a web deployment must drop.
 */
const expectedByOrdinal = ref<Map<number, string>>(new Map())

async function loadExpectedOutputs(problemId: number): Promise<void> {
  const list = await getProblemTestCases(problemId)
  const map = new Map<number, string>()
  for (const c of list.test_cases) map.set(c.ordinal, c.expected_output)
  expectedByOrdinal.value = map
}

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

/** Monaco's language id, a different namespace from the sandbox's id. */
const monacoLanguage = computed<string>(() => selectedLanguage.value?.monaco_language ?? 'cpp')

const unavailableLanguages = computed<LanguageOption[]>(() =>
  languages.value.filter((o) => !o.available)
)

// --- Judge state ---
const isRunning = ref(false)
const events = ref<JudgeEvent[]>([])
const result = ref<JudgeResponse | null>(null)
const errorMsg = ref<string>('')

onMounted(async () => {
  // Language catalogue and problem load are independent; run them
  // together so a slow env-check does not delay the statement.
  await Promise.all([loadLanguages(), loadProblem()])
})

watch(
  () => props.problemId,
  () => {
    void loadProblem()
  }
)

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

  if (problemId === null) {
    problem.value = null
    problemError.value = ''
    problemLoading.value = false
    return
  }

  problemLoading.value = true
  problemError.value = ''
  try {
    const [detail] = await Promise.all([getProblem(problemId), loadExpectedOutputs(problemId)])
    // A newer load started while this one was in flight; its result wins.
    if (token !== loadToken) return
    problem.value = detail
  } catch (err) {
    if (token !== loadToken) return
    problem.value = null
    problemError.value = (err as Error).message
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
 * Written as a `change` handler rather than `v-model` because the switch
 * can be declined: when the buffer holds user edits we ask first, and if
 * the answer is no the control has to snap back to the language the app
 * is actually still using.
 */
function onLanguageChange(event: Event): void {
  const select = event.target as HTMLSelectElement
  const option = languages.value.find((o) => o.id === select.value)
  if (!option || option.id === languageId.value) return

  if (code.value !== pristineCode.value) {
    const confirmed = window.confirm(
      `切换到 ${option.display_name} 会用起始模板替换当前代码，未保存的修改将丢失。继续吗？`
    )
    if (!confirmed) {
      // `languageId` is unchanged, so Vue has no reason to re-render the
      // <select>; snap the DOM back by hand.
      select.value = languageId.value
      return
    }
  }

  languageId.value = option.id
  applyTemplate(option)
}

/**
 * What to send the judge.
 *
 * Problem mode sends `problem_id` and lets the sidecar resolve the
 * cases (hidden ones included); free-run mode sends the page's own
 * two cases. The two fields are mutually exclusive on the wire.
 */
function buildRequest(): WsJudgeRequest {
  const req: WsJudgeRequest = {
    code: code.value,
    language: languageId.value
  }
  if (props.problemId !== null) {
    req.problem_id = props.problemId
    // Limits come from the problem record; the client does not get to
    // loosen them in the mode designed to keep them server-side.
  } else {
    req.test_cases = FREE_RUN_CASES
    req.time_limit = 10.0
    req.mem_limit_mb = 256
  }
  return req
}

/**
 * Expected output for the case at *index* in the run, for display.
 *
 * Free-run mode knows its own cases; problem mode looks the ordinal up
 * in the map fetched with the problem. `index` is 0-based here while
 * ordinals start at 1.
 */
function expectedOutputAt(index: number): string {
  if (!isProblemMode.value) {
    return FREE_RUN_CASES[index]?.expected_output ?? ''
  }
  return expectedByOrdinal.value.get(index + 1) ?? ''
}

/**
 * Input for the case at *index*, for display. The stream echoes the
 * input on `case_done`, so this is only a fallback for a frame that
 * arrives without one.
 */
function inputAt(index: number): string {
  if (!isProblemMode.value) return FREE_RUN_CASES[index]?.input ?? ''
  return ''
}

/**
 * Run the judge via the WebSocket stream.
 *
 * Each streamed event is appended to `events` for live display; on the
 * terminal judge_done event we assemble a full JudgeResponse from the
 * events plus whatever expected outputs this page can see.
 */
async function runJudge(): Promise<void> {
  isRunning.value = true
  events.value = []
  result.value = null
  errorMsg.value = ''

  const caseResults: TestCaseOut[] = []

  try {
    const terminal = await streamJudge(buildRequest(), (ev: JudgeEvent) => {
      events.value.push(ev)

      if (ev.type === 'case_done' && ev.index !== undefined) {
        caseResults.push({
          status: ev.status ?? 'RE',
          input: ev.input ?? inputAt(ev.index),
          expected_output: expectedOutputAt(ev.index),
          actual_output: ev.actual_output ?? '',
          stderr: ev.timed_out
            ? `Time Limit Exceeded`
            : ev.memory_exceeded
              ? `Memory Limit Exceeded`
              : '',
          exit_code: ev.exit_code ?? 0
        })
      }
    })

    if (terminal.type === 'judge_error') {
      errorMsg.value = terminal.error ?? 'unknown error'
    } else {
      result.value = {
        status: terminal.status ?? 'RE',
        compile_output: terminal.compile_output ?? '',
        passed: terminal.passed ?? 0,
        total: terminal.total ?? 0,
        problem_id: props.problemId,
        test_cases: caseResults
      }
    }
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    isRunning.value = false
  }
}
</script>

<template>
  <div class="practice">
    <header class="practice-header">
      <div class="header-left">
        <h1>{{ problemTitle }}</h1>
        <span v-if="isProblemMode" class="mode-badge">题库 #{{ problemId }}</span>
        <span v-else class="mode-badge mode-badge-free">自由运行</span>
      </div>
      <button v-if="isProblemMode" class="exit-btn" @click="emit('exit')">退出到自由运行</button>
    </header>

    <div class="practice-body">
      <!-- Left: problem description + test cases -->
      <section class="problem-panel">
        <div v-if="problemLoading" class="panel-note">加载题目中...</div>
        <div v-else-if="problemError" class="panel-note panel-note-error">
          无法加载题目 #{{ problemId }}：{{ problemError }}
        </div>

        <template v-else>
          <h2>题目描述</h2>
          <pre v-if="problemDescription" class="problem-desc">{{ problemDescription }}</pre>

          <template v-if="problem?.input_format">
            <h2>输入格式</h2>
            <pre class="problem-desc">{{ problem.input_format }}</pre>
          </template>
          <template v-if="problem?.output_format">
            <h2>输出格式</h2>
            <pre class="problem-desc">{{ problem.output_format }}</pre>
          </template>

          <div v-if="problem && problem.knowledge_tags.length" class="tag-row">
            <span v-for="tag in problem.knowledge_tags" :key="tag" class="tag">
              {{ tag }}
            </span>
          </div>

          <h2>{{ isProblemMode ? '样例' : '测试用例' }}</h2>
          <div v-for="(tc, i) in displayCases" :key="i" class="test-case">
            <div class="tc-label">{{ isProblemMode ? '样例' : '用例' }} {{ i + 1 }}</div>
            <div class="tc-row">
              <span class="tc-key">输入:</span>
              <code>{{ tc.input }}</code>
            </div>
            <div class="tc-row">
              <span class="tc-key">期望输出:</span>
              <code>{{ tc.expected_output }}</code>
            </div>
          </div>

          <p v-if="isProblemMode" class="panel-note">隐藏用例由后端持有，判题时会一并执行。</p>
          <p v-if="problem" class="panel-note">
            时限 {{ problem.time_limit }}s · 内存 {{ problem.memory_limit }} MB
          </p>
        </template>
      </section>

      <!-- Right: language picker + code editor + run button + output panel -->
      <section class="code-panel">
        <div class="toolbar">
          <label class="lang-picker">
            <span class="lang-label">语言</span>
            <select
              class="lang-select"
              :value="languageId"
              :disabled="isRunning || languages.length === 0"
              @change="onLanguageChange"
            >
              <option
                v-for="opt in languages"
                :key="opt.id"
                :value="opt.id"
                :disabled="!opt.available"
                :title="opt.detail ?? undefined"
              >
                {{ opt.display_name }}{{ opt.available ? '' : '（不可用）' }}
              </option>
            </select>
          </label>

          <span v-if="selectedLanguage?.version" class="lang-version">
            {{ selectedLanguage.version }}
          </span>

          <button
            class="run-btn"
            :disabled="isRunning || !selectedLanguage || problemLoading || !!problemError"
            @click="runJudge"
          >
            {{ isRunning ? '判题中...' : '运行判题' }}
          </button>
        </div>

        <!--
          Disabled options cannot be opened to read their tooltip, so the
          reason — which already names the setting to change — is rendered
          as text instead of hidden behind a title attribute.
        -->
        <p v-if="catalogueError" class="lang-note lang-note-error">
          {{ catalogueError }}
        </p>
        <ul v-else-if="unavailableLanguages.length" class="lang-note">
          <li v-for="opt in unavailableLanguages" :key="opt.id">
            <strong>{{ opt.display_name }} 不可用</strong>：{{ opt.detail }}
          </li>
        </ul>

        <div class="editor-wrapper">
          <CodeEditor v-model="code" :language="monacoLanguage" theme="vs-dark" />
        </div>

        <OutputPanel
          :events="events"
          :result="result"
          :error-msg="errorMsg"
          :is-running="isRunning"
        />
      </section>
    </div>
  </div>
</template>

<style scoped>
.practice {
  display: flex;
  flex-direction: column;
  /* PU-6: was 100vh. The app shell owns the viewport now and stacks a title
   * bar above this view, so the page must fill its container instead. */
  height: 100%;
  padding: 16px;
  box-sizing: border-box;
  color: #e5e7eb;
  background: #1a1a2e;
}

.practice-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.practice-header h1 {
  margin: 0;
  font-size: 22px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mode-badge {
  padding: 2px 10px;
  font-size: 11px;
  color: #93c5fd;
  background: #1e3a5f;
  border-radius: 10px;
  white-space: nowrap;
}

.mode-badge-free {
  color: #9ca3af;
  background: #374151;
}

.exit-btn {
  padding: 6px 14px;
  font-size: 12px;
  color: #d1d5db;
  background: transparent;
  border: 1px solid #4b5563;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}

.exit-btn:hover {
  background: #374151;
}

.practice-body {
  display: flex;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.problem-panel {
  width: var(--spacing-problem-panel);
  flex-shrink: 0;
  overflow-y: auto;
  padding: 12px;
  background: #16213e;
  border-radius: 8px;
}

/* PU-6: below 900px the side-by-side editor + statement no longer fits, so
 * the two columns stack. The statement keeps a bounded height and scrolls
 * on its own — letting it size to content would push the editor off-screen.
 *
 * Stacked, the page can still be taller than the viewport (the editor has a
 * 300px floor and a short window is easy to reach), so the page itself
 * becomes the scroll container. Side by side it must not: there the panels
 * scroll internally and the editor is meant to fill the leftover height. */
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
}

.problem-panel h2 {
  font-size: 16px;
  margin: 16px 0 8px 0;
}

.problem-panel h2:first-child {
  margin-top: 0;
}

.problem-desc {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  background: #0f3460;
  padding: 10px;
  border-radius: 4px;
  margin: 0;
}

.panel-note {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.5;
  margin: 12px 0 0 0;
}

.panel-note-error {
  color: #fca5a5;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.tag {
  padding: 2px 8px;
  font-size: 11px;
  color: #93c5fd;
  background: #1e3a5f;
  border-radius: 4px;
}

.test-case {
  background: #0f3460;
  padding: 10px;
  border-radius: 4px;
  margin-bottom: 8px;
}

.tc-label {
  font-weight: bold;
  margin-bottom: 6px;
}

.tc-row {
  font-size: 13px;
  margin: 2px 0;
}

.tc-key {
  color: #9ca3af;
  margin-right: 6px;
}

.code-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* PU-17: new markup uses the PU-0 tokens rather than fresh literals, so
 * the PU-7/PU-8 visual rewrite has less to clean up. The styles above are
 * pre-existing and still hardcoded — replacing those is PU-8's job. */
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.lang-picker {
  display: flex;
  align-items: center;
  gap: 8px;
}

.lang-label {
  font-size: 13px;
  color: var(--af-text-secondary);
}

.lang-select {
  padding: 8px 10px;
  font-size: 14px;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.lang-select:disabled {
  color: var(--af-text-muted);
  cursor: not-allowed;
}

.lang-version {
  font-size: 12px;
  color: var(--af-text-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.lang-note {
  margin: 0 0 12px 0;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  list-style: none;
}

.lang-note-error {
  color: var(--af-text-primary);
  border-color: var(--af-warning);
}

.editor-wrapper {
  flex: 1;
  min-height: 300px;
  border: 1px solid #374151;
  border-radius: 8px;
  overflow: hidden;
}

.run-btn {
  padding: 10px 24px;
  font-size: 15px;
  font-weight: 600;
  color: white;
  background: #3b82f6;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}

.run-btn:hover:not(:disabled) {
  background: #2563eb;
}

.run-btn:disabled {
  background: #6b7280;
  cursor: not-allowed;
}
</style>
