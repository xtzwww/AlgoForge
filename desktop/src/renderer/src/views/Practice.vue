<script setup lang="ts">
/**
 * Practice view — the main coding/judging page (P1-16).
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
 *   event + the per-case case_done events (the test_cases' expected
 *   output is already known client-side, so no second HTTP call).
 *
 * PU-17 adds the language picker:
 * - The list of languages — and which of them this machine can actually
 *   build — comes from the sidecar's env-check, not from a hardcoded
 *   array. See `api/languages.ts` for why.
 * - Switching language loads that language's starter template, after
 *   confirming when the buffer holds edits the user would lose.
 * - The chosen language is sent with the judge request; judging Python
 *   source as C++ would report nothing but CE.
 *
 * The problem description and test cases are still hardcoded (A + B).
 * Replacing them with a real problem catalogue is PU-11/PU-12/PU-14 —
 * this slice deliberately left them alone, which is also why the buffer
 * starts as a *working C++ solution* rather than as `CPP_SPEC
 * .default_template`: it is what makes the page demoable today.
 */
import { computed, onMounted, ref } from 'vue'
import CodeEditor from '../components/CodeEditor.vue'
import OutputPanel from '../components/OutputPanel.vue'
import { streamJudge, type JudgeEvent } from '../api/ws'
import type { JudgeResponse, TestCaseIn, TestCaseOut } from '../api/judge'
import {
  getLanguageOptions,
  pickDefaultLanguage,
  type LanguageOption,
} from '../api/languages'

// --- Problem definition (A + B) ---
const problemTitle = 'A + B Problem'
const problemDescription = `读入两个整数 a 和 b，输出它们的和。

输入格式：
  一行，两个整数 a 和 b，用空格分隔。

输出格式：
  一行，一个整数，表示 a + b 的结果。`

const testCases: TestCaseIn[] = [
  { input: '3 5', expected_output: '8' },
  { input: '10 20', expected_output: '30' },
]

/**
 * The problem's language. Hardcoded for now because the problem itself
 * is (see the header note); PU-14 will read it off the problem record,
 * at which point a problem that ships Python samples stops opening a
 * C++ editor.
 */
const DEFAULT_PROBLEM_LANGUAGE = 'cpp'

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
 * Tracking a boolean instead would go stale as soon as the user undid
 * their edits back to the original.
 */
const pristineCode = ref<string>(code.value)

// --- Language state ---
const languages = ref<LanguageOption[]>([])
const languageId = ref<string>(DEFAULT_PROBLEM_LANGUAGE)
const catalogueError = ref<string>('')

const selectedLanguage = computed<LanguageOption | null>(
  () => languages.value.find((o) => o.id === languageId.value) ?? null,
)

/**
 * Monaco's language id. The sandbox's id (`"cpp"`) and Monaco's happen to
 * match for all three languages today, but they are different namespaces:
 * the sidecar reports both and this is the one the editor wants.
 */
const monacoLanguage = computed<string>(() => selectedLanguage.value?.monaco_language ?? 'cpp')

const unavailableLanguages = computed<LanguageOption[]>(() =>
  languages.value.filter((o) => !o.available),
)

// --- Judge state ---
const isRunning = ref(false)
const events = ref<JudgeEvent[]>([])
const result = ref<JudgeResponse | null>(null)
const errorMsg = ref<string>('')

onMounted(async () => {
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
    // The buffer starts as a working solution to this page's built-in
    // problem, so leaving C++ selected leaves it untouched. Any other
    // default would otherwise show C++ source in a Python editor.
    if (preferred.id !== DEFAULT_PROBLEM_LANGUAGE) {
      applyTemplate(preferred)
    }
  } catch (err) {
    catalogueError.value = `无法获取语言列表：${(err as Error).message}`
  }
})

/** Replace the buffer with *option*'s starter template. */
function applyTemplate(option: LanguageOption): void {
  pristineCode.value = option.default_template
  code.value = option.default_template
  // The previous verdict belongs to a different language's program, so
  // leaving it on screen next to the new template would mislead.
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
      `切换到 ${option.display_name} 会用起始模板替换当前代码，未保存的修改将丢失。继续吗？`,
    )
    if (!confirmed) {
      // `languageId` is unchanged, so Vue has no reason to re-render the
      // <select>; snap the DOM back by hand or it sits on a language the
      // app did not accept.
      select.value = languageId.value
      return
    }
  }

  languageId.value = option.id
  applyTemplate(option)
}

/**
 * Run the judge via the WebSocket stream. Each streamed event is
 * appended to `events` for live display; on the terminal judge_done
 * event we assemble a full JudgeResponse from the events + the
 * client-side test cases (expected_output is already known).
 */
async function runJudge(): Promise<void> {
  isRunning.value = true
  events.value = []
  result.value = null
  errorMsg.value = ''

  // Collect per-case results as case_done events arrive.
  const caseResults: TestCaseOut[] = []

  try {
    const terminal = await streamJudge(
      {
        code: code.value,
        language: languageId.value,
        test_cases: testCases,
        time_limit: 10.0,
        mem_limit_mb: 256,
      },
      (ev: JudgeEvent) => {
        events.value.push(ev)

        // Assemble per-case results as they complete.
        if (ev.type === 'case_done' && ev.index !== undefined) {
          const tc = testCases[ev.index]
          caseResults.push({
            status: ev.status ?? 'RE',
            input: tc.input,
            expected_output: tc.expected_output,
            actual_output: ev.actual_output ?? '',
            stderr: ev.timed_out
              ? `Time Limit Exceeded`
              : ev.memory_exceeded
                ? `Memory Limit Exceeded`
                : '',
            exit_code: ev.exit_code ?? 0,
          })
        }
      },
    )

    if (terminal.type === 'judge_error') {
      errorMsg.value = terminal.error ?? 'unknown error'
    } else {
      // judge_done — assemble the final response. caseResults may be
      // empty on CE (short-circuit before any case runs).
      result.value = {
        status: terminal.status ?? 'RE',
        compile_output: terminal.compile_output ?? '',
        passed: terminal.passed ?? 0,
        total: terminal.total ?? 0,
        test_cases: caseResults,
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
      <h1>{{ problemTitle }}</h1>
    </header>

    <div class="practice-body">
      <!-- Left: problem description + test cases -->
      <section class="problem-panel">
        <h2>题目描述</h2>
        <pre class="problem-desc">{{ problemDescription }}</pre>

        <h2>测试用例</h2>
        <div v-for="(tc, i) in testCases" :key="i" class="test-case">
          <div class="tc-label">用例 {{ i + 1 }}</div>
          <div class="tc-row">
            <span class="tc-key">输入:</span>
            <code>{{ tc.input }}</code>
          </div>
          <div class="tc-row">
            <span class="tc-key">期望输出:</span>
            <code>{{ tc.expected_output }}</code>
          </div>
        </div>
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
            :disabled="isRunning || !selectedLanguage"
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
  height: 100vh;
  padding: 16px;
  box-sizing: border-box;
  color: #e5e7eb;
  background: #1a1a2e;
}

.practice-header h1 {
  margin: 0 0 12px 0;
  font-size: 22px;
}

.practice-body {
  display: flex;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.problem-panel {
  width: 360px;
  flex-shrink: 0;
  overflow-y: auto;
  padding: 12px;
  background: #16213e;
  border-radius: 8px;
}

.problem-panel h2 {
  font-size: 16px;
  margin: 16px 0 8px 0;
}

.problem-desc {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  background: #0f3460;
  padding: 10px;
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
