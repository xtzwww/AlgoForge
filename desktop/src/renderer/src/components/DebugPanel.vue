<script setup lang="ts">
/**
 * DebugPanel — the breakpoint debugger's face (PU-17).
 *
 * Layout, top to bottom:
 *
 *   ┌── Status line: where the program is, and why it stopped ─────┐
 *   │  ▶ 运行  ⤼ 继续  ⤓ 单步跳过  ⤵ 单步进入  ⤒ 跳出  · 结束调试  │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │  变量 (n)           │  调用栈                               │
 *   │  i          0       │  ▸ sum  main.cpp:7                    │
 *   │  n          5       │    main  main.cpp:14                  │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * **What this panel is for.** The student's question while debugging is
 * 「为什么这里不是我以为的值」, and everything here exists to answer it:
 * where execution is, which variables are visible there, and how it got
 * there. Anything that does not serve that is not on this panel.
 *
 * **Why the panel owns no state.** The sidecar owns the binary, the
 * breakpoints and the position; every control here sends an intention and
 * receives the whole new state. The panel renders `props.session` and
 * nothing else. That is what makes the panel and gdb unable to disagree —
 * there is only one copy of the truth.
 *
 * **Deliberate omissions**, each of which was considered and dropped:
 *
 * * **Reverse stepping (`reverse-continue`).** gdb supports it only with
 *   a recording target, which costs a full trace of every instruction —
 *   measured as unusably slow to start on a laptop, and it can fail
 *   outright on this toolchain. A control that sometimes works is worse
 *   than no control.
 * * **A 程序输出 section.** Debugging here is for logic errors, and the
 *   program's stdout is truncated by gdb's own buffering when it is not
 *   attached to a terminal, so what it shows would be partial and
 *   therefore misleading. The judge's output panel is where output is
 *   read from.
 * * **Editing expressions / calling functions (`print foo(1)`).** That is
 *   code execution from the panel; it belongs to the sandbox's own
 *   judgement path, not to a debugging conversation.
 */

import { computed } from 'vue'
import type { DebugAction, DebugFrame, DebugSession, DebugStop } from '../api/debug'

const props = defineProps<{
  /** The session's current state, or null while none is open. */
  session: DebugSession | null
  /** True while a step or a build is in flight. */
  busy: boolean
  /** A message to show in place of the status line. Empty when none. */
  errorMsg: string
  /**
   * A transient confirmation — "断点已保存，下次单步生效" and the like.
   * Distinct from `errorMsg` because it is not a failure and must not be
   * coloured like one.
   */
  notice: string
  /** Whether the machine has a usable g++, for the empty state. */
  available: boolean
  /** Why not, when it does not. Shown in the empty state. */
  unavailableReason: string
  /** Whether a problem is open at all — debugging needs code to debug. */
  hasCode: boolean
}>()

const emit = defineEmits<{
  (e: 'step', action: DebugAction): void
  (e: 'close'): void
}>()

const stop = computed<DebugStop | null>(() => props.session?.stop ?? null)

/** Whether the program has run to completion and can only be restarted. */
const finished = computed<boolean>(() => props.session?.finished === true)

/**
 * The reason line: where execution is, phrased for the student.
 *
 * Cases are ordered by how specific they are, not by how likely —
 * a signal in `sum` is a crash *in sum*, and reporting the second half
 * only would lose the first. `exited` is the exception: there is no
 * location to name, so it names the outcome instead.
 */
const statusText = computed<string>(() => {
  const s = stop.value
  if (!props.session) return ''
  if (!props.session.built) return '正在编译…'
  if (!s) return '准备就绪，按「运行」开始'
  switch (s.reason) {
    case 'breakpoint': {
      const where = s.function ? `${s.function}()` : '断点'
      const at = s.line !== null && s.line !== undefined ? `，第 ${s.line} 行` : ''
      return `已在 ${where} 停住${at}`
    }
    case 'signal':
      return `程序崩溃：${s.signal_description ?? s.signal ?? '未知信号'}`
    case 'exited':
      return s.exit_code === 0
        ? '程序已正常结束'
        : `程序结束，退出码 ${s.exit_code === null || s.exit_code === undefined ? '未知' : s.exit_code}`
    default:
      return '调试器停在了未知位置'
  }
})

/**
 * Tone class for the status line.
 *
 * Three outcomes with three different next actions: a crash needs the
 * student to go read the code, a normal exit means they are done, a
 * breakpoint is progress. Colouring them alike would flatten exactly the
 * distinction the line exists to make.
 */
const statusTone = computed<string>(() => {
  const reason = stop.value?.reason
  if (props.errorMsg) return 'is-error'
  if (reason === 'signal') return 'is-error'
  if (reason === 'exited') return 'is-done'
  if (reason === 'breakpoint') return 'is-hit'
  return 'is-idle'
})

/**
 * Student-code frames only.
 *
 * gdb's stack for a `std::sort` call is mostly libstdc++ internals whose
 * function names run to hundreds of characters, and every one of them
 * pushes the student's own `main` off the visible area. The sidecar marks
 * these (`is_library`) rather than this component pattern-matching on
 * paths — the rule about what counts as library code belongs with the
 * parser that knows.
 *
 * When *every* frame is library code the filter is dropped, so a crash
 * deep inside the standard library still shows something rather than an
 * empty list.
 */
const stackFrames = computed<DebugFrame[]>(() => {
  const all = stop.value?.stack ?? []
  const own = all.filter((f) => !f.is_library)
  return own.length ? own : all
})

/** True when the stack was filtered, so the panel can say what it hid. */
const hiddenFrameCount = computed<number>(() => {
  const all = stop.value?.stack ?? []
  return all.length - stackFrames.value.length
})

const variables = computed(() => stop.value?.variables ?? [])

/**
 * How many of the variables are not yet initialised.
 *
 * Worth counting separately because it is the panel's most easily missed
 * fact, and the one a student is most likely to misread: a loop variable
 * that has not been declared yet shows whatever bytes were on the stack.
 */
const uninitialisedCount = computed<number>(
  () => variables.value.filter((v) => v.uninitialised).length
)

/**
 * Whether to offer 「继续」 rather than 「运行」.
 *
 * Restarting is only meaningful once there is something to restart, and
 * the sidecar treats `continue` after an exit as a restart — so the label
 * changes to match what will actually happen. Offering 「继续」 on a
 * finished program would be a lie the user only discovers by pressing it.
 */
const startLabel = computed<string>(() => {
  if (!props.session) return '运行'
  if (!props.session.built) return '运行'
  if (finished.value) return '重新运行'
  if (stop.value === null) return '运行'
  return '继续'
})

const startAction = computed<DebugAction>(() =>
  finished.value || stop.value === null ? 'run' : 'continue'
)

/**
 * Whether stepping controls make sense.
 *
 * False before the first run: there is no position to move from, and the
 * sidecar would answer with a 400. The controls are disabled rather than
 * hidden, so the bar does not reflow on every transition — a control that
 * moves under the cursor is how a student presses the wrong one.
 */
const canStep = computed<boolean>(
  () => !!props.session?.built && !finished.value && stop.value !== null
)

const canStart = computed<boolean>(() => !!props.session?.built || false)

/**
 * A variable's value as text, with the one substitution that matters.
 *
 * 尚未初始化 rather than the raw value. gdb reports the bytes on the
 * stack for a variable whose declaration has not run — measured three
 * runs of the same breakpoint giving 583006768, -75499648 and 564133088 —
 * and a student reading any of those would conclude their loop starts
 * there. The panel says what it knows instead.
 */
function variableValue(value: string, uninitialised: boolean): string {
  if (uninitialised) return '尚未初始化'
  // gdb prints an empty struct as `{...}` with nothing in it, which reads
  // as a rendering fault rather than as an empty value.
  if (value === '{}' || value === '') return '（空）'
  return value
}

/** 「第 7 行」 for the status line, or an empty string before any stop. */
const currentLineLabel = computed<string>(() => {
  const line = stop.value?.line
  return line === null || line === undefined ? '' : `第 ${line} 行`
})
</script>

<template>
  <div class="debug-panel">
    <!-- No toolchain: say what is missing rather than offering a dead button. -->
    <div v-if="!props.available" class="debug-empty">
      <p class="debug-empty-title">这台电脑暂时无法使用断点调试</p>
      <p class="debug-empty-body">
        {{ props.unavailableReason || '没有找到配套的调试工具链。' }}
      </p>
      <p class="debug-empty-hint">
        判题和 AI 分析不受影响，可以照常做题。需要调试时请把这段说明发给老师。
      </p>
    </div>

    <!-- Toolchain present but no code yet. -->
    <div v-else-if="!props.hasCode" class="debug-empty">
      <p class="debug-empty-title">还没有可以调试的代码</p>
      <p class="debug-empty-body">先在编辑器里写下你的程序，再来这里打断点逐步运行。</p>
    </div>

    <template v-else>
      <!-- Control bar. -->
      <div class="debug-bar">
        <button
          class="debug-btn debug-btn-primary"
          :disabled="props.busy || !canStart"
          @click="emit('step', startAction)"
        >
          {{ startLabel }}
        </button>
        <button
          class="debug-btn"
          :disabled="props.busy || !canStep"
          title="执行一行，遇到函数调用不进入"
          @click="emit('step', 'next')"
        >
          单步跳过
        </button>
        <button
          class="debug-btn"
          :disabled="props.busy || !canStep"
          title="执行一行，遇到函数调用进入函数内部"
          @click="emit('step', 'step')"
        >
          单步进入
        </button>
        <button
          class="debug-btn"
          :disabled="props.busy || !canStep"
          title="一直执行到当前函数返回"
          @click="emit('step', 'finish')"
        >
          跳出
        </button>
        <span class="debug-spacer" />
        <button class="debug-btn" :disabled="props.busy" @click="emit('close')">结束调试</button>
      </div>

      <!--
        Status line. The location is the panel's headline fact, so it sits
        directly under the controls and carries the tone.
      -->
      <div class="debug-status" :class="statusTone">
        <span class="debug-status-text">{{ statusText }}</span>
        <span v-if="currentLineLabel && stop?.reason === 'breakpoint'" class="debug-status-where">
          {{ currentLineLabel }}
        </span>
        <span v-if="props.busy" class="debug-status-busy">执行中…</span>
      </div>

      <!--
        A failed step is reported here rather than replacing the panel:
        the session survives most failures (a timeout, a missing binary),
        so the position and variables above are still valid and worth
        keeping on screen.
      -->
      <div v-if="props.errorMsg" class="debug-msg debug-msg-error">{{ props.errorMsg }}</div>
      <div v-else-if="props.notice" class="debug-msg debug-msg-notice">{{ props.notice }}</div>

      <!--
        The source line gdb reported, echoed so a student can see the
        exact statement the position refers to without counting lines in
        the editor — and so a mismatch between what they see and what ran
        is visible rather than hidden.
      -->
      <div v-if="stop?.source_line" class="debug-source">
        <code>{{ stop.source_line }}</code>
      </div>

      <div class="debug-tables">
        <!-- 变量 -->
        <section class="debug-section">
          <h3 class="debug-section-title">
            变量
            <span class="debug-count">{{ variables.length }}</span>
          </h3>
          <p v-if="!variables.length" class="debug-section-empty">
            {{ stop?.reason === 'exited' ? '程序已结束，变量不再可见。' : '这里没有可见的局部变量。' }}
          </p>
          <table v-else class="debug-table">
            <tbody>
              <tr v-for="v in variables" :key="`${v.kind}:${v.name}`">
                <td class="debug-name">{{ v.name }}</td>
                <td class="debug-value" :class="{ 'is-unset': v.uninitialised }">
                  {{ variableValue(v.value, v.uninitialised) }}
                </td>
              </tr>
            </tbody>
          </table>
          <!--
            The uninitialised note is an explanation, not a warning: it
            tells the student why a number they expected to see is not
            there, which is otherwise indistinguishable from a broken
            debugger.
          -->
          <p v-if="uninitialisedCount" class="debug-section-note">
            标着「尚未初始化」的变量所在的语句还没有执行到，现在读它们会读到内存里的随机内容，
            所以这里不显示具体的值。再单步几次就会出来了。
          </p>
        </section>

        <!-- 调用栈 -->
        <section class="debug-section">
          <h3 class="debug-section-title">
            调用栈
            <span class="debug-count">{{ stackFrames.length }}</span>
          </h3>
          <p v-if="!stackFrames.length" class="debug-section-empty">还没有调用栈信息。</p>
          <ol v-else class="debug-stack">
            <li
              v-for="(frame, i) in stackFrames"
              :key="`${frame.level}:${frame.address ?? i}`"
              class="debug-frame"
              :class="{ 'is-current': i === 0 }"
            >
              <!--
                `display_function`, not `function`: gdb prints argument
                *values* in the signature, so `function` is
                `sum (n=5)` — and appending this panel's own parentheses
                would render `sum (n=5)()`.
              -->
              <span class="debug-frame-fn">{{ frame.display_function }}()</span>
              <span class="debug-frame-at">{{ frame.file }}:{{ frame.line }}</span>
            </li>
          </ol>
          <p v-if="hiddenFrameCount" class="debug-section-note">
            另外 {{ hiddenFrameCount }} 层是标准库内部调用，已折叠。
          </p>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
/*
 * Colours are PU-0 tokens throughout — this file holds no hex literal,
 * for the same reason every other panel does not.
 */
.debug-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 10px 12px;
  overflow-y: auto;
  background: var(--af-surface);
}

/* --- empty states --- */

.debug-empty {
  max-width: 46ch;
  padding: 16px 4px;
}

.debug-empty-title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.debug-empty-body {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-text-secondary);
  word-break: break-word;
}

/* The reassurance is the point of this line — a student who cannot debug
 * still has to be able to do the problem. */
.debug-empty-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-text-muted);
}

/* --- control bar --- */

.debug-bar {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
  margin-bottom: 8px;
}

.debug-btn {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--af-text-primary);
  cursor: pointer;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  transition: background var(--af-duration-fast) var(--af-ease);
}

.debug-btn:hover:not(:disabled) {
  background: var(--af-canvas);
}

.debug-btn:disabled {
  color: var(--af-text-muted);
  cursor: not-allowed;
  opacity: 0.6;
}

.debug-btn-primary {
  font-weight: 600;
  color: var(--af-canvas);
  background: var(--af-accent);
  border-color: var(--af-accent);
}

.debug-btn-primary:hover:not(:disabled) {
  background: var(--af-accent-hover);
}

.debug-spacer {
  flex: 1;
}

/* --- status line --- */

/*
 * A left rule carries the tone rather than the whole row, so the text
 * stays readable and the crash case does not turn the panel red — the
 * student still has to read the variables underneath.
 */
.debug-status {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: baseline;
  padding: 6px 0 6px 10px;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--af-text-primary);
  border-left: 3px solid var(--af-border-strong);
}

.debug-status.is-hit {
  border-left-color: var(--af-accent);
}

.debug-status.is-error {
  border-left-color: var(--af-danger);
}

/*
 * A normal exit is an outcome, not a failure — and it is the one the
 * student is aiming for, so it gets the passing colour rather than the
 * muted one.
 */
.debug-status.is-done {
  border-left-color: var(--af-verdict-ac);
}

.debug-status.is-idle {
  color: var(--af-text-secondary);
}

.debug-status-where {
  color: var(--af-text-muted);
}

.debug-status-busy {
  margin-left: auto;
  font-size: 12px;
  color: var(--af-text-muted);
}

/* --- messages --- */

.debug-msg {
  flex-shrink: 0;
  padding: 6px 10px;
  margin-bottom: 8px;
  font-size: 12px;
  line-height: 1.6;
  border-radius: var(--af-radius-sm);
  word-break: break-word;
}

.debug-msg-error {
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-danger) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 45%, transparent);
}

/*
 * A confirmation is not an error. Sharing the error styling would make
 * "断点已保存" look like it failed, which is the opposite of what it says.
 */
.debug-msg-notice {
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
}

/* --- source echo --- */

.debug-source {
  flex-shrink: 0;
  padding: 5px 8px;
  margin-bottom: 8px;
  overflow-x: auto;
  font-size: 12px;
  white-space: pre;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

/* --- tables --- */

.debug-tables {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.debug-section {
  min-width: 0;
}

.debug-section-title {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-muted);
}

.debug-count {
  padding: 0 5px;
  font-size: 11px;
  font-weight: 400;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border-radius: 999px;
}

.debug-section-empty {
  margin: 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

.debug-section-note {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.65;
  color: var(--af-text-muted);
}

.debug-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.debug-table td {
  padding: 2px 0;
  vertical-align: top;
}

.debug-name {
  width: 38%;
  padding-right: 8px;
  color: var(--af-text-primary);
}

.debug-value {
  font-family: monospace;
  color: var(--af-text-secondary);
  word-break: break-all;
}

/* Muted and italic: it is an absence of information, not a value. */
.debug-value.is-unset {
  color: var(--af-text-muted);
  font-style: italic;
}

/* --- call stack --- */

.debug-stack {
  padding: 0;
  margin: 0;
  list-style: none;
}

.debug-frame {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 2px 0 2px 8px;
  font-size: 12px;
  border-left: 2px solid transparent;
}

/* The innermost frame — where execution actually is — is the one the
 * student is asking about, so it is the one that is marked. */
.debug-frame.is-current {
  border-left-color: var(--af-accent);
}

.debug-frame-fn {
  color: var(--af-text-primary);
}

.debug-frame-at {
  margin-left: auto;
  color: var(--af-text-muted);
  white-space: nowrap;
}
</style>
