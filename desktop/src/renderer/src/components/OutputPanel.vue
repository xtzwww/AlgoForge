<script setup lang="ts">
/**
 * OutputPanel — the judge's progress and verdict, rendered (P1-16, PU-8, P2-8).
 *
 * Layout, top to bottom:
 *
 *   ┌── Progress header (only while running) ──────────────┐
 *   │  ▓▓▓▓▓▓░░░░  用例 3 / 8   · 当前 #3 编译中/运行中      │
 *   ├───────────────────────────────────────────────────────┤
 *   │  Verdict summary: [AC] 通过 5 / 5                     │
 *   │  Per-case cards: [WA] 用例 2  期望/实际 diff           │
 *   ├───────────────────────────────────────────────────────┤
 *   │  Agent 分析: 错因 + 诊断 / 调了哪些工具 / 工具返回什么  │
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
 * **What P2-8 added.** The Agent streams what it did while it works:
 * `agent_start`, one `agent_tool` per tool call, then `agent_done`. Its
 * events follow `judge_done` rather than preceding it (9e7911b): the
 * verdict is ready in milliseconds and the analysis can take minutes, so
 * holding the verdict behind it was exactly the delay that split exists
 * to remove. Two consequences shape this file:
 *
 * * The Agent section is the only part of the panel that describes
 *   something other than judging, so it says what the tools *returned*
 *   rather than only that they ran. "Agent 调用了 run_code" is not
 *   information; "run_code 输出 21" is.
 * * The progress header has to survive past the last case. The bar is
 *   full by then, and the old `completedCases < totalCases` guard hid the
 *   phase line at exactly the moment the user most needs to know that
 *   something is still happening.
 *
 * **Why the diagnosis arrives on the event.** `agent_done` carries the
 * analysis prose, which is what lets this page show the *same text* the
 * mistake book stores rather than a second rendering of the same fields.
 * Fetching the entry instead would need the submission's mistake-book id
 * — a second request, a second failure mode, and a window in which the
 * two pages disagree about what the Agent said. Until this, the practice
 * page showed only the `error_type` label, so the one page where the
 * student meets the mistake was the one page that could not tell them
 * what to do about it.
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
import type { FeedbackSection } from '../api/mistake_book'
import LayeredFeedback from './LayeredFeedback.vue'
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
    case 'agent_start':
      return '判题结束，Agent 正在分析错因…'
    case 'agent_tool':
      return 'Agent 正在分析错因…'
    case 'agent_done':
      return '分析完成，正在收尾…'
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

// --- Agent phase (P2-8) --------------------------------------------------

/**
 * Whether the stream mentioned the Agent at all.
 *
 * A passing submission, a scratch run and a run with no problem row never
 * reach the analysis, so they never emit these events. Gating the whole
 * section on this keeps the panel from showing an empty "Agent 分析" box
 * that would read as "the analysis was skipped for an unknown reason".
 */
const hasAgentPhase = computed<boolean>(() => props.events.some((e) => e.type.startsWith('agent_')))

/** The Agent's tool calls, in the order the stream reported them. */
const agentToolEvents = computed<JudgeEvent[]>(() =>
  props.events.filter((e) => e.type === 'agent_tool')
)

/**
 * True while the analysis is in flight — started, not yet finished.
 *
 * Derived from the events rather than from `isRunning`, because the judge
 * itself has already finished by then: every case is done and the bar is
 * full, yet the run is not over.
 */
const agentRunning = computed<boolean>(() => {
  let started = false
  let done = false
  for (const ev of props.events) {
    if (ev.type === 'agent_start') started = true
    if (ev.type === 'agent_done') done = true
  }
  return started && !done
})

/** The finished analysis, or `null` while it is still running. */
const agentDone = computed<JudgeEvent | null>(() => {
  for (let i = props.events.length - 1; i >= 0; i -= 1) {
    if (props.events[i].type === 'agent_done') return props.events[i]
  }
  return null
})

/**
 * The diagnosis as the three sections the sidecar already split it into.
 *
 * `agent_done` carries them separately (`error_detail` / `hint` /
 * `suggested_fix`) precisely so no client has to parse the `【错在哪】`
 * labels out of the joined prose — see `api/ws.ts`. This page is the one
 * where the student meets the mistake, so it gets the same gated reveal
 * the mistake book uses rather than the whole answer at once.
 */
const diagnosis = computed<Record<FeedbackSection, string>>(() => {
  const ev = agentDone.value
  if (!ev || ev.ok !== true) return { error_detail: '', hint: '', suggested_fix: '' }
  return {
    error_detail: ev.error_detail ?? '',
    hint: ev.hint ?? '',
    suggested_fix: ev.suggested_fix ?? ''
  }
})

/**
 * Whether there is anything to reveal.
 *
 * A successful analysis whose three sections are all empty is possible —
 * the model answered with a label and nothing else. Rendering the panel
 * then would show the header and a "that was everything" note with no
 * content between them, which reads as a broken render.
 */
const hasDiagnosis = computed<boolean>(() =>
  (Object.keys(diagnosis.value) as FeedbackSection[]).some((s) => diagnosis.value[s] !== '')
)

/**
 * Whether to show the learning-process note (C1).
 *
 * Guarded on ``flagged`` alone. The sidecar always sends the whole
 * observation — a submission that was *not* flagged still carries its
 * evidence, because a teacher may want to see it — but none of that
 * belongs on the student's screen unless it says something.
 */
const processFlagged = computed<boolean>(() => props.result?.process?.flagged === true)

/**
 * 「这个错因你之前也出现过 N 次」(阶段 1 · 历史联动反馈).
 *
 * Empty when the diagnosis failed, or when this really is the first
 * time — and in the second case the panel renders *nothing*, because a
 * line that always appears teaches the student to skip it, including on
 * the day it finally says something.
 *
 * Counted across **other problems** only, which is why the wording says
 * 「在别的题上」: retrying one problem three times is ordinary, while the
 * same root cause resurfacing on three different problems is the
 * pattern worth naming.
 */
const recurrenceNote = computed<string>(() => {
  const ev = agentDone.value
  if (!ev || ev.ok !== true) return ''
  return ev.recurrence?.summary ?? ''
})

/** Cap a long single-line gist without cutting mid-character. */
function truncate(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}…`
}

/**
 * A one-line gist of what a tool returned.
 *
 * `result` is the tool's own object and its shape differs per tool, so
 * each field is read defensively. That is deliberate rather than lazy:
 * P2-13 adds tools this build has never seen, and the panel should show
 * something honest for those instead of `[object Object]`. The `ok: false`
 * case is checked first because it is the one shape every tool shares.
 */
function toolSummary(ev: JudgeEvent): string {
  const result = ev.result
  if (!result) return ev.ok === false ? '失败' : '完成'

  if (result.ok === false) {
    return typeof result.error === 'string' ? result.error : '工具报告失败'
  }
  if (result.timed_out === true) return '运行超时'
  if (typeof result.stdout === 'string') {
    const printed = result.stdout.trim()
    return printed ? `输出：${truncate(printed, 60)}` : '程序没有输出'
  }
  if (typeof result.status === 'string') {
    return `判题结论 ${result.status}（${String(result.passed ?? '?')}/${String(result.total ?? '?')}）`
  }
  return '完成'
}

/**
 * The tool's full result, pretty-printed for the disclosure.
 *
 * Serialised here rather than rendered field by field so a tool added
 * later is still inspectable without touching this component — the point
 * of the disclosure is to answer "what exactly came back".
 */
function toolResultJson(ev: JudgeEvent): string {
  try {
    return JSON.stringify(ev.result ?? {}, null, 2)
  } catch {
    // A circular or otherwise unserialisable value. Showing that beats
    // throwing inside a template render and blanking the whole panel.
    return '（结果无法序列化）'
  }
}

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
    case 'agent_start':
      return 'Agent 开始分析'
    case 'agent_tool':
      return 'Agent 调用工具'
    case 'agent_done':
      return 'Agent 分析完成'
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
        <!--
          The phase line stays visible during the Agent phase even though
          every case is done: the bar is full by then but the run is not
          over, and hiding the line at that moment would make a working
          Agent look like a finished run.
        -->
        <span
          v-if="currentCase !== null && (completedCases < totalCases || agentRunning)"
          class="progress-phase"
        >
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

    <!--
      Agent analysis (P2-8). Sits between the verdict and the raw log
      because it is the answer to "why did this fail?", which is the
      question the per-case diff raises but cannot answer.
    -->
    <section v-if="hasAgentPhase" class="agent-panel">
      <div class="agent-header">
        <h3>Agent 分析</h3>
        <span v-if="agentRunning" class="agent-state">分析中…</span>
        <!--
          Once the diagnosis itself is on screen (below), the header does
          not repeat the label — `LayeredFeedback` already shows it, in
          Chinese, with the raw identifier beside it. Two copies of the
          same fact is how they start to disagree.
        -->
        <span v-else-if="agentDone?.ok && hasDiagnosis" class="agent-state">分析完成</span>
        <span v-else-if="agentDone?.ok" class="agent-state">
          错因：<code>{{ agentDone.error_type ?? '（未给出）' }}</code>
        </span>
        <span v-else class="agent-state agent-state-failed">分析未完成</span>
      </div>

      <p v-if="agentDone && !agentDone.ok && agentDone.error" class="agent-error">
        {{ agentDone.error }}
      </p>

      <!--
        The diagnosis, released one section at a time.

        The same component the mistake book renders, so the screen where
        the student meets the mistake and the screen where they revisit
        it cannot disagree about what the Agent said — or about how much
        of it is handed over at once.

        The joined `suggestion` underneath is only a fallback: a sidecar
        that answered with prose but without the split. Showing nothing
        there would be worse than showing the whole answer at once.
      -->
      <LayeredFeedback
        v-if="hasDiagnosis"
        class="agent-diagnosis"
        :error-detail="diagnosis.error_detail"
        :hint="diagnosis.hint"
        :suggested-fix="diagnosis.suggested_fix"
        :error-type-description="agentDone?.error_type_description ?? ''"
        :error-type="agentDone?.error_type ?? null"
      />
      <p v-else-if="agentDone?.suggestion" class="agent-suggestion">{{ agentDone.suggestion }}</p>

      <!--
        The history line, placed *after* the diagnosis rather than before
        it: "you have made this mistake before" only means something once
        the student knows which mistake is being talked about.
      -->
      <p v-if="recurrenceNote" class="recurrence-note">{{ recurrenceNote }}</p>

      <!--
        An analysis that used no tools is a normal outcome — the model is
        allowed to answer from the code alone — so it is stated rather
        than left as a blank space the reader has to interpret.
      -->
      <p v-if="!agentToolEvents.length && !agentRunning" class="agent-note">
        Agent 没有调用工具，直接给出了结论。
      </p>

      <ul v-if="agentToolEvents.length" class="agent-tools">
        <li v-for="(ev, i) in agentToolEvents" :key="i" class="agent-tool">
          <details>
            <summary class="agent-tool-head">
              <code class="agent-tool-name">{{ ev.name }}</code>
              <span class="agent-tool-ok" :class="{ 'agent-tool-bad': ev.ok === false }">
                {{ ev.ok === false ? '失败' : '成功' }}
              </span>
              <span class="agent-tool-gist">{{ toolSummary(ev) }}</span>
            </summary>
            <pre class="agent-tool-raw">{{ toolResultJson(ev) }}</pre>
          </details>
        </li>
      </ul>
    </section>

    <!--
      The learning-process note (C1).

      A nudge, not an accusation, and it is worded that way on purpose:
      the system reports that this submission's *editing pattern* differs
      from the usual one and asks the student to check their own
      reasoning. There is no score anywhere in it — a percentage would
      read as a verdict, and a false one costs a student's trust in a way
      no correction undoes.

      Only the flagged case renders. An unflagged observation is still
      recorded and still visible to a teacher, but showing "your process
      looked fine" after every submission would train the student to
      ignore the panel, which is the one thing it must not become.
    -->
    <section v-if="processFlagged" class="process-note">
      <div class="process-note-head">
        <span class="process-note-title">学习过程提示</span>
        <span class="process-note-tag">过程特征</span>
      </div>
      <p class="process-note-summary">{{ props.result?.process?.summary }}</p>
      <details class="process-note-details">
        <summary>看看系统观察到了什么</summary>
        <ul class="process-note-reasons">
          <li v-for="(reason, i) in props.result?.process?.reasons ?? []" :key="i">
            {{ reason }}
          </li>
        </ul>
      </details>
    </section>

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
          <template v-if="ev.type === 'agent_tool'">
            <code class="event-detail">{{ ev.name }}</code>
            <span class="event-detail">{{ ev.ok === false ? '失败' : '成功' }}</span>
          </template>
          <span v-if="ev.type === 'agent_done'" class="event-detail">
            {{ ev.ok ? (ev.error_type ?? '—') : '未完成' }}
          </span>
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

/* --- agent analysis (P2-8) --- */
.agent-panel {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--af-border);
}

.agent-header {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 6px;
}

.agent-header h3 {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-muted);
}

.agent-state {
  font-size: 12px;
  color: var(--af-text-primary);
}

/* The failure colour is used only where the analysis genuinely failed;
 * "no tools were called" is a normal outcome and stays neutral. */
.agent-state-failed {
  color: var(--af-danger);
}

/*
 * Spacing for the nested feedback card, applied from here rather than
 * inside `LayeredFeedback`: how far the card sits from the tool list
 * below it is this panel's rhythm, and the card is rendered in two
 * different containers that are not the same distance from their
 * neighbours.
 */
.agent-diagnosis {
  margin-bottom: 8px;
}

/*
 * The history line. A left rule rather than a filled box, and the muted
 * text colour rather than the warning colour: it is context about the
 * student's own past, not an alarm — and it must not compete with the
 * diagnosis it is attached to.
 */
.recurrence-note {
  margin: 0 0 8px;
  padding: 6px 0 6px 10px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--af-text-secondary);
  border-left: 3px solid var(--af-border-strong);
}

.agent-error {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--af-danger);
}

/*
 * The learning-process note (C1).
 *
 * Deliberately *not* styled like a warning. `--af-danger` here would say
 * "you did something wrong", which is not a claim the system makes and
 * not one it can support: the observation is that an editing pattern
 * differs from the usual one, and the student is being asked to think,
 * not being told anything. The muted surface keeps it a note.
 */
.process-note {
  margin: 0 0 10px;
  padding: 8px 10px;
  background: var(--af-elevated);
  border-left: 2px solid var(--af-text-muted);
  border-radius: var(--af-radius-sm);
}

.process-note-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.process-note-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-primary);
}

/* "过程特征" rather than "AI 检测": the tag names what was measured,
 * not what might be concluded from it. */
.process-note-tag {
  padding: 1px 6px;
  font-size: 11px;
  color: var(--af-text-muted);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.process-note-summary {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-primary);
}

/*
 * The evidence is folded, not hidden: a student who disagrees has to be
 * able to see exactly what was counted, or the note is an unappealable
 * verdict — which is the failure mode this whole feature is designed
 * around avoiding.
 */
.process-note-details summary {
  margin-top: 4px;
  font-size: 11px;
  color: var(--af-text-muted);
  cursor: pointer;
}

.process-note-reasons {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--af-text-muted);
}

/* The diagnosis gets a surface of its own: it is this section's payload —
 * the answer to the question the per-case diff raises and cannot answer —
 * not another line of metadata. `pre-wrap` because the sidecar joins three
 * labelled sections into one string, and their line breaks are the only
 * thing separating them. */
.agent-suggestion {
  margin: 0 0 8px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border-radius: var(--af-radius-sm);
}

.agent-note {
  margin: 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

.agent-tools {
  margin: 0;
  padding: 0;
  list-style: none;
}

.agent-tool {
  margin-bottom: 4px;
}

.agent-tool-head {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 12px;
  cursor: pointer;
}

.agent-tool-name {
  padding: 1px 5px;
  font-size: 11px;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 3px;
}

.agent-tool-ok {
  color: var(--af-text-muted);
}

.agent-tool-bad {
  color: var(--af-danger);
}

/* The gist is the part that answers "what came back", so it is allowed
 * to take the remaining width and wrap rather than being clipped. */
.agent-tool-gist {
  flex: 1;
  min-width: 0;
  color: var(--af-text-muted);
  word-break: break-all;
}

.agent-tool-raw {
  margin: 4px 0 0;
  padding: 6px;
  overflow-x: auto;
  font-size: 11px;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 3px;
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
