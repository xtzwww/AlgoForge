<script setup lang="ts">
/**
 * OutputPanel — the judge's progress and verdict, rendered (P1-16, PU-8).
 *
 * Layout, top to bottom:
 *
 *   ┌── Progress header (only while running) ──────────────┐
 *   │  ▓▓▓▓▓▓░░░░  用例 3 / 8   · 当前 #3 编译中/运行中      │
 *   ├───────────────────────────────────────────────────────┤
 *   │  Verdict summary: [AC] 通过 5 / 5                     │
 *   │  Per-case cards: [WA] 用例 2  期望/实际 diff           │
 *   ├───────────────────────────────────────────────────────┤
 *   │  ▸ 原始事件流 (12)          ← collapsed by default     │
 *   └───────────────────────────────────────────────────────┘
 *
 * **What PU-8 changed, and why.**
 *
 * 1. *A progress header that counts cases.* The old panel printed
 *    `isRunning` as the button's label and nothing else, so a 40-case run
 *    looked identical to a hung one for its whole duration. The count
 *    comes from the stream's own `index` — `case_start` fires before the
 *    case runs, so "3 / 8" means case 3 is executing, not 3 finished.
 * 2. *The raw event log moved behind a disclosure.* It was the panel's
 *    primary content, which meant the answer to "did it pass?" was one
 *    line among twelve and the interesting `期望/实际` diff was buried
 *    under compile chatter. The log is genuinely useful when debugging a
 *    stream, so it is folded rather than deleted.
 * 3. *Verdict colours come from `VerdictBadge`.* The local
 *    `statusColor()` switch is gone; see that component for why one copy
 *    of the `JudgeStatus` palette has to exist rather than four.
 *
 * **Why the panel owns no state.** The parent streams events in and owns
 * the run; this component derives everything it shows. What it derives
 * is kept here rather than pushed up because it is purely presentational
 * — the parent has no use for "which case is currently running" except
 * to press it onto this panel.
 */
import { computed } from 'vue'
import type { JudgeEvent } from '../api/ws'
import type { JudgeResponse } from '../api/judge'
import VerdictBadge from './VerdictBadge.vue'

const props = defineProps<{
  events: JudgeEvent[]
  result: JudgeResponse | null
  errorMsg: string
  isRunning: boolean
  /**
   * True when this verdict was a scratch run (PU-8) rather than a
   * submission. Changes the summary's wording only — the verdict itself
   * is the same computation either way.
   */
  isScratch?: boolean
}>()

/**
 * The case the stream is currently on, 1-based, or `null` before the
 * first `case_start`.
 *
 * Tracks the newest `case_start` rather than counting `case_done`s: the
 * label must say "case 3 is running", and at that moment only two have
 * completed. Before compilation finishes there is no case to name, which
 * is what the `null` is for.
 */
const currentCase = computed<number | null>(() => {
  for (let i = props.events.length - 1; i >= 0; i -= 1) {
    const ev = props.events[i]
    if (ev.type === 'case_start') return (ev.index ?? 0) + 1
  }
  return null
})

/** How many cases have reported a verdict. */
const completedCases = computed<number>(
  () => props.events.filter((e) => e.type === 'case_done').length
)

/**
 * Total cases this run will execute.
 *
 * The stream never announces the case count up front — `judge()` emits
 * `case_start`/`case_done` pairs and only reveals `total` in the terminal
 * event. So while running, the best available figure is "one more than
 * the highest index seen so far", which is a lower bound that grows into
 * the real total. When the run finishes, the authoritative `total` from
 * the result replaces the estimate.
 *
 * Falling back to the estimate rather than to `0` matters: a dark
 * "0 cases" bar during a run would read as "nothing is happening".
 */
const totalCases = computed<number>(() => {
  if (props.result) return props.result.total
  let highest = -1
  for (const ev of props.events) {
    if (ev.index !== undefined) highest = Math.max(highest, ev.index)
  }
  return highest + 1
})

/** Fraction complete, clamped to [0,1] so a stale frame cannot overflow. */
const progressRatio = computed<number>(() => {
  const total = totalCases.value
  if (total <= 0) return 0
  return Math.min(1, Math.max(0, completedCases.value / total))
})

/**
 * The phase line under the bar: what the stream is doing right now.
 *
 * Read from the last event rather than from a separately-tracked flag,
 * so the text cannot disagree with the log it is summarising.
 */
const phaseLabel = computed<string>(() => {
  if (props.errorMsg) return '请求失败'
  const last = props.events[props.events.length - 1]
  if (!last) return '正在准备…'
  switch (last.type) {
    case 'compile_start':
      return '正在编译…'
    case 'compile_done':
      return last.success ? '编译完成，开始运行用例…' : '编译失败'
    case 'case_start':
      return `正在运行用例 ${(last.index ?? 0) + 1}`
    case 'case_done':
      return '继续下一个用例…'
    default:
      return '正在收尾…'
  }
})

/**
 * The panel's headline state, or `null` when there is nothing to
 * headline yet.
 *
 * `error` outranks everything: a failed request is not a verdict, and
 * showing a stale verdict badge beside the error would misattribute it.
 */
const summaryStatus = computed<string | null>(() => {
  if (props.errorMsg) return null
  return props.result?.status ?? null
})

/**
 * Whether the run produced no cases at all — a CE, which short-circuits
 * before any case executes.
 *
 * Worth distinguishing because the progress bar's arithmetic would
 * otherwise render "0 / 0" beside a 100%-looking empty bar, which reads
 * as a rendering fault rather than as "the build failed".
 */
const isCompileOnly = computed<boolean>(
  () => props.result !== null && props.result.total === 0 && !!props.result.compile_output
)

function eventLabel(type: string): string {
  switch (type) {
    case 'compile_start':
      return '开始编译'
    case 'compile_done':
      return '编译完成'
    case 'case_start':
      return '用例开始'
    case 'case_done':
      return '用例完成'
    case 'judge_done':
      return '判题完成'
    case 'judge_error':
      return '错误'
    default:
      return type
  }
}
</script>

<template>
  <div class="output-panel">
    <!-- Progress header: only meaningful while a run is in flight. -->
    <div v-if="props.isRunning" class="progress-header">
      <div
        class="progress-track"
        role="progressbar"
        :aria-valuenow="completedCases"
        :aria-valuemin="0"
        :aria-valuemax="totalCases"
        :aria-label="phaseLabel"
      >
        <div class="progress-fill" :style="{ width: `${progressRatio * 100}%` }" />
      </div>
      <div class="progress-meta">
        <span class="progress-count"> 用例 {{ completedCases }} / {{ totalCases || '?' }} </span>
        <span v-if="currentCase !== null && completedCases < totalCases" class="progress-phase">
          · {{ phaseLabel }}
        </span>
      </div>
    </div>

    <!-- Request failure -->
    <div v-if="props.errorMsg" class="error-msg">请求失败：{{ props.errorMsg }}</div>

    <!-- Verdict summary -->
    <div v-if="summaryStatus" class="result-panel">
      <div class="result-summary">
        <VerdictBadge :status="summaryStatus" size="lg" with-label />
        <span class="result-count">
          <template v-if="isCompileOnly">编译未产生任何用例</template>
          <template v-else>
            通过 {{ props.result?.passed }} / {{ props.result?.total }}
            <span v-if="props.isScratch" class="scratch-note">（试运行，未记录）</span>
          </template>
        </span>
      </div>

      <div v-if="props.result?.compile_output" class="compile-output">
        <h3>编译输出</h3>
        <pre>{{ props.result.compile_output }}</pre>
      </div>

      <ul v-if="props.result?.test_cases.length" class="tc-list">
        <li v-for="(tc, i) in props.result.test_cases" :key="i" class="tc-result">
          <div class="tc-result-header">
            <VerdictBadge :status="tc.status" size="sm" />
            <span class="tc-result-label">用例 {{ i + 1 }}</span>
          </div>
          <p v-if="tc.status === 'TLE'" class="tc-info">运行超时</p>
          <p v-else-if="tc.status === 'MLE'" class="tc-info">内存超限</p>
          <template v-else-if="tc.status !== 'AC'">
            <div class="tc-diff">
              <div class="tc-diff-row">
                <span class="tc-key">输入</span>
                <code>{{ tc.input || '(无)' }}</code>
              </div>
              <div class="tc-diff-row">
                <span class="tc-key">期望</span>
                <code>{{ tc.expected_output }}</code>
              </div>
              <div class="tc-diff-row">
                <span class="tc-key">实际</span>
                <code class="tc-actual">{{ tc.actual_output || '(无输出)' }}</code>
              </div>
            </div>
          </template>
        </li>
      </ul>
    </div>

    <!-- Empty state: running but not yet anything to show -->
    <div v-if="props.isRunning && props.events.length === 0 && !props.errorMsg" class="placeholder">
      等待判题事件…
    </div>

    <!-- Idle, nothing run yet -->
    <div v-if="!props.isRunning && !props.result && !props.errorMsg" class="placeholder">
      还没有运行结果。按 Ctrl/Cmd + Enter 试运行，或 Ctrl/Cmd + Shift + Enter 提交。
    </div>

    <!--
      The raw stream, folded away. A `<details>` element rather than a
      hand-rolled toggle so keyboard operation and the expanded/collapsed
      state come from the platform.
    -->
    <details v-if="props.events.length" class="raw-events">
      <summary>原始事件流（{{ props.events.length }} 条）</summary>
      <div class="event-stream">
        <div v-for="(ev, i) in props.events" :key="i" class="event-row">
          <span class="event-label" :class="{ 'event-label-error': ev.type === 'judge_error' }">
            {{ eventLabel(ev.type) }}
          </span>
          <span v-if="ev.type === 'compile_done'" class="event-detail">
            {{ ev.success ? '成功' : '失败' }}
          </span>
          <span v-if="ev.type === 'case_start'" class="event-detail">
            #{{ (ev.index ?? 0) + 1 }}
          </span>
          <template v-if="ev.type === 'case_done'">
            <span class="event-detail">#{{ (ev.index ?? 0) + 1 }}</span>
            <VerdictBadge :status="ev.status ?? ''" size="sm" />
          </template>
          <template v-if="ev.type === 'judge_done'">
            <VerdictBadge :status="ev.status ?? ''" size="sm" />
            <span class="event-detail">（{{ ev.passed }}/{{ ev.total }}）</span>
          </template>
          <span v-if="ev.type === 'judge_error'" class="event-detail event-detail-error">
            {{ ev.error }}
          </span>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
/*
 * Colours are PU-0 tokens throughout — this file holds no hex literal.
 * `--af-verdict-*` reach the badges via their own component; everything
 * here is surface/text/border.
 */
.output-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

/* --- progress --- */
.progress-header {
  margin-bottom: 12px;
}

.progress-track {
  height: 6px;
  overflow: hidden;
  background: var(--af-elevated);
  border-radius: 999px;
}

.progress-fill {
  height: 100%;
  background: var(--af-accent);
  border-radius: 999px;
  transition: width var(--af-duration-base) var(--af-ease);
}

.progress-meta {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--af-text-secondary);
}

.progress-count {
  font-variant-numeric: tabular-nums;
}

.progress-phase {
  color: var(--af-text-muted);
}

/* --- errors --- */
.error-msg {
  padding: 8px 10px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-danger) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 45%, transparent);
  border-radius: var(--af-radius-sm);
}

/* --- verdict summary --- */
.result-summary {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.result-count {
  font-size: 14px;
  color: var(--af-text-secondary);
}

.scratch-note {
  color: var(--af-text-muted);
}

.compile-output {
  margin-bottom: 12px;
}

.compile-output h3 {
  margin: 0 0 6px 0;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.compile-output pre {
  max-height: 220px;
  padding: 10px;
  margin: 0;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

/* --- per-case cards --- */
.tc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.tc-result {
  padding: 10px;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.tc-result-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tc-result-label {
  font-size: 13px;
  color: var(--af-text-secondary);
}

.tc-info {
  margin: 6px 0 0 0;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.tc-diff {
  margin-top: 8px;
  font-size: 12px;
}

.tc-diff-row {
  display: flex;
  gap: 8px;
  margin: 3px 0;
}

.tc-key {
  flex-shrink: 0;
  width: 32px;
  color: var(--af-text-muted);
}

.tc-diff code {
  padding: 1px 5px;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 3px;
}

/* The actual output is the one the user has to read, so it is the one
 * that carries a hint of the failure colour. */
.tc-actual {
  border-left: 2px solid color-mix(in srgb, var(--af-danger) 60%, transparent);
}

/* --- raw event log --- */
.raw-events {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--af-border);
}

.raw-events summary {
  font-size: 12px;
  color: var(--af-text-muted);
  cursor: pointer;
}

.event-stream {
  margin-top: 8px;
}

.event-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
  font-size: 12px;
}

.event-label {
  min-width: 64px;
  color: var(--af-text-muted);
}

.event-label-error {
  color: var(--af-verdict-wa);
}

.event-detail {
  color: var(--af-text-secondary);
}

.event-detail-error {
  color: var(--af-verdict-wa);
}

.placeholder {
  padding: 12px;
  font-size: 13px;
  text-align: center;
  color: var(--af-text-muted);
}
</style>
