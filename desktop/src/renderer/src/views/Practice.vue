<script setup lang="ts">
/**
 * Practice view — the main coding/judging page (P1-16).
 *
 * Layout:
 *   ┌── Problem panel (left) ──┬── Code editor + Output panel (right) ──┐
 *   │  description + cases    │  Monaco editor                          │
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
 */
import { ref } from 'vue'
import CodeEditor from '../components/CodeEditor.vue'
import OutputPanel from '../components/OutputPanel.vue'
import { streamJudge, type JudgeEvent } from '../api/ws'
import type { JudgeResponse, TestCaseIn, TestCaseOut } from '../api/judge'

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

// --- Judge state ---
const isRunning = ref(false)
const events = ref<JudgeEvent[]>([])
const result = ref<JudgeResponse | null>(null)
const errorMsg = ref<string>('')

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

      <!-- Right: code editor + run button + output panel -->
      <section class="code-panel">
        <div class="editor-wrapper">
          <CodeEditor v-model="code" language="cpp" theme="vs-dark" />
        </div>

        <div class="actions">
          <button class="run-btn" :disabled="isRunning" @click="runJudge">
            {{ isRunning ? '判题中...' : '运行判题' }}
          </button>
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

.editor-wrapper {
  flex: 1;
  min-height: 300px;
  border: 1px solid #374151;
  border-radius: 8px;
  overflow: hidden;
}

.actions {
  margin: 12px 0;
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
