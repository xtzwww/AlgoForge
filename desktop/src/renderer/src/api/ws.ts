/**
 * WebSocket client for the judge progress stream (P1-15 / P1-16).
 *
 * Connects to `WS /ws/run-log`, sends one JSON request, and exposes
 * the streamed events as an async iterator + a callback. The caller
 * assembles the final result from the terminal `judge_done` event
 * plus the per-case `case_done` events collected along the way.
 */

import type { TestCaseIn } from './judge'
import type { HintLadder, Variant } from './mistake_book'
import type { ProcessTracePayload, ProcessView } from '../edit-trace'
import { sessionToken } from './session'
import { runtime } from '../runtime'

export type JudgeEventType =
  | 'compile_start'
  | 'compile_done'
  | 'case_start'
  | 'case_done'
  | 'agent_start'
  | 'agent_step'
  | 'agent_tool'
  | 'agent_done'
  | 'judge_done'
  | 'judge_error'

export interface JudgeEvent {
  type: JudgeEventType
  // compile_done
  success?: boolean
  output?: string
  // case_start / case_done
  index?: number
  input?: string
  // case_done
  status?: string
  actual_output?: string
  exit_code?: number
  timed_out?: boolean
  memory_exceeded?: boolean
  // judge_done / judge_error
  passed?: number
  total?: number
  compile_output?: string
  // judge_error
  error?: string

  /**
   * judge_done: whether an analysis follows this event.
   *
   * The sidecar records the verdict first and analyses afterwards, so
   * `judge_done` means "the verdict is in" rather than "the stream is
   * over". This flag is the difference. Without it a client would have to
   * guess, and guessing wrong either closes the socket on events still in
   * flight or waits forever on events that were never coming.
   *
   * False for an accepted run, a scratch run, and a run with no problem
   * row — every case where no Agent is involved at all.
   */
  analysing?: boolean

  // --- Process observation (C1) --------------------------------------
  /**
   * judge_done: the edit-behaviour observation for this submission, or
   * null when the client sent no trace.
   *
   * Rides the verdict instead of arriving as its own event because it is
   * a fact about the submission the verdict just described, known at the
   * same moment, and the client is already waiting on this message.
   */
  process?: ProcessView | null

  // --- Agent phase (P2-8) --------------------------------------------
  // These arrive *after* `judge_done`: the analysis runs once the verdict
  // has been recorded and returned, so a client that stopped reading at
  // the terminal event would never see them.
  /** agent_start: the judge verdict that triggered the analysis. */
  verdict?: string
  /**
   * agent_step: which stage of the analysis just finished (S8).
   *
   * The backend's internal node name, not shown to anyone — `label` is
   * what gets rendered. Kept because a log line saying 「正在检索知识点」
   * cannot be traced back to the node that emitted it, and the two do not
   * correspond one-to-one in prose.
   */
  step?: string
  /**
   * agent_step: that stage in student-facing Chinese, e.g. 「检索知识点」.
   *
   * Only the graph pipeline emits these. The default pipeline is a single
   * model call with nothing observable between its start and its end, so
   * a client sees `agent_start` and then `agent_done` — which is honest,
   * where a fabricated step would not be.
   */
  label?: string
  /** agent_tool: which tool ran, and whether it succeeded. */
  name?: string
  ok?: boolean
  /**
   * agent_tool: what the tool returned — the same object the model was
   * given. Shape depends on the tool (`run_code` returns `stdout` /
   * `exit_code` / `timed_out`, `judge` returns a verdict and per-case
   * detail), so it is rendered generically rather than typed per tool.
   */
  result?: Record<string, unknown>
  /** agent_done: the label the Agent settled on, or null if it failed. */
  error_type?: string | null
  /**
   * agent_done: the label in student-facing Chinese, or `''` when this
   * build does not know the label. Same value and same source as the
   * mistake book's `error_type_description`.
   */
  error_type_description?: string | null
  /**
   * agent_done: the diagnosis itself — the three labelled prose sections
   * (`【错在哪】` / `【提示】` / `【怎么改】`) joined into one string, or
   * `null` when the analysis failed or produced no prose.
   *
   * This is byte-for-byte what the mistake book stores in
   * `agent_suggestion`, so the practice page and the mistake book cannot
   * describe one analysis two ways. `error_type` alone is a label: it
   * names the mistake without saying what to do about it, which is why
   * the practice page used to send the student to another page to learn
   * anything actionable.
   */
  suggestion?: string | null
  /**
   * agent_done: `suggestion` already split into its three parts by the
   * sidecar, or `null` on failure.
   *
   * Split there rather than here on purpose: the mistake book serves
   * these three off the stored column through the same parser, so the
   * two screens cannot disagree about where one section ends. A client
   * that re-parsed the labels would be a second implementation of a
   * format it does not own.
   */
  error_detail?: string | null
  hint?: string | null
  suggested_fix?: string | null
  /** agent_done: names of the tools called, in order. */
  tools?: string[]
  /**
   * agent_done: 「这个错因你之前也出现过 N 次」(阶段 1 · 历史联动反馈).
   *
   * Always present, never null — `count === 0` is the answer "this is
   * the first time on another problem", not an absence of an answer, so
   * the client can read `count` without first checking whether the key
   * exists. `summary` is the sentence the sidecar wrote; it is `''` when
   * there is nothing to say, and the UI renders nothing in that case
   * rather than a line that says "first time" every single time.
   */
  /**
   * agent_done: titles of the knowledge cards the analysis drew on, `[]`
   * when it drew on none.
   *
   * Always an array so the client renders without a branch. Empty is a
   * real answer — it means the model answered from its own recollection
   * — and carrying it is the point: retrieval degrades to "no context"
   * *silently* by design, so without a value to look at, a working
   * knowledge base and one that never ran look exactly the same.
   */
  knowledge_sources?: string[]
  recurrence?: {
    label: string
    count: number
    problem_ids: number[]
    last_at: string | null
    summary: string
  }
  /**
   * agent_done: S7's graded hint ladder, or null when no 提示 agent ran.
   *
   * The same value the mistake book stores and serves off
   * `agent_hint_ladder`, sent here so the practice page can show it the
   * moment it is produced instead of making the student go and reload the
   * mistake book to find out whether one exists. See {@link HintLadder}
   * for how this differs from the `error_detail` / `hint` /
   * `suggested_fix` triple above — that is a diagnosis, this is a ladder.
   */
  hint_ladder?: HintLadder | null
  /**
   * agent_done: the practice problems S7's 变式题 agent wrote, or `[]`.
   *
   * Always an array so the client iterates without a branch. Empty for the
   * default pipeline, which is the common case: only `ANALYSIS_PIPELINE`
   * values that run the variant worker produce any.
   */
  variants?: Variant[]
}

export interface WsJudgeRequest {
  code: string
  /**
   * Language the code is written in. Optional on the wire — the sidecar
   * falls back to its default (C++) — but PU-17 always sends it, because
   * judging C++ source as C++ when the user picked Python would report
   * nothing but CE.
   */
  language?: string
  /**
   * Catalogue problem to pull cases and limits from (PU-12). Mutually
   * exclusive with `test_cases`; exactly one is required. This is the
   * mode the product uses — the sidecar reads the hidden cases itself,
   * so they never reach the browser.
   */
  problem_id?: number
  /**
   * Inline cases for ad-hoc self-testing. Mutually exclusive with
   * `problem_id`.
   */
  test_cases?: TestCaseIn[]
  /**
   * Seconds. In `problem_id` mode this overrides the problem's own
   * limit; in inline mode it is the limit.
   */
  time_limit?: number
  /** MB. Same override rule as `time_limit`. */
  mem_limit_mb?: number
  /**
   * Whether the verdict is persisted (PU-8). `true` (the sidecar's
   * default when omitted) is a **submit**: the row lands in
   * `submissions` and a failure is filed in the mistake book. `false` is
   * a **scratch run**: the verdict is computed and streamed back exactly
   * as usual, but nothing is written and nothing is scored.
   *
   * Optional rather than required so an omitted field keeps meaning
   * "submit" — the safe direction, since a client that forgets it would
   * otherwise silently lose its history.
   */
  record?: boolean
  /**
   * The editor's behaviour trace for the session that produced this code
   * (C1) — paste events, edit sizes, timing. Omitted when there is none.
   *
   * Telemetry, and treated as such on both ends: the sidecar drops a
   * malformed trace with a log line rather than failing the request,
   * because a verdict is worth far more than a process column. A request
   * that omits it is still a perfectly good submission; the row simply
   * records no process, which the teacher side reports as "not observed"
   * rather than as a clean run.
   */
  process_trace?: ProcessTracePayload
  /**
   * The logged-in user's session token (B1), when there is one.
   *
   * In the message body rather than a header because a browser's
   * WebSocket cannot set `Authorization`. The sidecar resolves it
   * best-effort: an absent, unknown, or expired token records the
   * submission with no owner, which is a legitimate state (everything
   * written before this existed is unowned) rather than an error — a
   * verdict is worth far more than an owner column.
   */
  user_token?: string
}

/**
 * Options shared by the judge entry points below.
 *
 * `record` is set by the wrapper rather than by callers: whether a run is
 * a scratch run or a submission is the *reason* the caller picked that
 * wrapper, so letting it also pass a flag would allow
 * `submitProblem(..., { record: false })` — a contradiction the types
 * should prevent rather than the server should resolve.
 */
export interface JudgeStreamOptions {
  language?: string
  timeLimit?: number
  memLimitMb?: number
  /**
   * The edit-behaviour trace to attach (C1). Passed here rather than as a
   * separate argument so every entry point carries it without growing a
   * positional parameter list — and so a caller cannot attach a trace to
   * one kind of run and forget it on another.
   */
  processTrace?: ProcessTracePayload
}

function applyOptions(req: WsJudgeRequest, opts?: JudgeStreamOptions): void {
  if (!opts) return
  if (opts.language !== undefined) req.language = opts.language
  if (opts.timeLimit !== undefined) req.time_limit = opts.timeLimit
  if (opts.memLimitMb !== undefined) req.mem_limit_mb = opts.memLimitMb
  if (opts.processTrace !== undefined) req.process_trace = opts.processTrace
}

/**
 * Build a request against a stored problem's cases (PU-12).
 *
 * `record` decides submit vs scratch run (PU-8): the case set is the
 * same either way — the sidecar reads the stored cases including the
 * hidden ones — and only the persistence differs.
 */
function problemRequest(
  code: string,
  problemId: number,
  record: boolean,
  opts?: JudgeStreamOptions
): WsJudgeRequest {
  const req: WsJudgeRequest = { code, problem_id: problemId, record }
  applyOptions(req, opts)
  return req
}

/**
 * Build a request against caller-supplied cases.
 *
 * The scratch-run path is the only one that uses this in the practice
 * page: it sends the problem's *visible* samples, so the user gets a
 * fast, non-punitive run and the hidden cases stay uninvolved.
 */
function inlineRequest(
  code: string,
  testCases: TestCaseIn[],
  record: boolean,
  opts?: JudgeStreamOptions
): WsJudgeRequest {
  const req: WsJudgeRequest = { code, test_cases: testCases, record }
  applyOptions(req, opts)
  return req
}

/** Submit against a stored problem: full case set, verdict recorded. */
export function submitProblem(
  code: string,
  problemId: number,
  onEvent: (event: JudgeEvent) => void,
  opts?: JudgeStreamOptions
): Promise<JudgeEvent> {
  return streamJudge(problemRequest(code, problemId, true, opts), onEvent)
}

/**
 * There is deliberately no `runProblem`.
 *
 * A scratch run against a *stored* problem would have to send
 * `problem_id` plus `record: false`, which would execute the hidden cases
 * too — the sidecar has no reason to exclude them, and a scratch run the
 * user cannot see the contents of is not a scratch run. The practice page
 * therefore sends the problem's **visible** cases inline instead (see
 * `runCases`), which is the same request shape as free-run mode. Adding
 * this wrapper would only invite the version that leaks the hidden set.
 */

/** Submit against caller-supplied cases. */
export function submitCases(
  code: string,
  testCases: TestCaseIn[],
  onEvent: (event: JudgeEvent) => void,
  opts?: JudgeStreamOptions
): Promise<JudgeEvent> {
  return streamJudge(inlineRequest(code, testCases, true, opts), onEvent)
}

/** Scratch-run against caller-supplied cases — the pre-PU-8 behaviour. */
export function runCases(
  code: string,
  testCases: TestCaseIn[],
  onEvent: (event: JudgeEvent) => void,
  opts?: JudgeStreamOptions
): Promise<JudgeEvent> {
  return streamJudge(inlineRequest(code, testCases, false, opts), onEvent)
}

/**
 * Open a WebSocket to /ws/run-log, send the request, and invoke
 * `onEvent` for each streamed event.
 *
 * Resolves when the *run* is over — which is not the same moment as the
 * verdict arriving. The sidecar records the verdict, sends `judge_done`,
 * and only then runs the analysis, whose events follow on this same
 * socket. When `judge_done` carries `analysing`, that means waiting for
 * the `agent_done` that follows it.
 *
 * The WS URL is derived from the sidecar base URL (http→ws / https→wss)
 * obtained from the active {@link runtime}. The auth token is passed as
 * a query param since WebSocket cannot set Authorization headers from
 * the renderer.
 */
export async function streamJudge(
  req: WsJudgeRequest,
  onEvent: (event: JudgeEvent) => void
): Promise<JudgeEvent> {
  const info = await runtime.getSidecarInfo()
  if (!info) {
    throw new Error('Sidecar info not available. Is the sidecar running?')
  }

  // http→ws / https→wss, and the sidecar base URL already includes
  // the scheme + host + port (e.g. http://localhost:8000).
  const wsBaseUrl = info.url.replace(/^http/, 'ws')
  const wsUrl = `${wsBaseUrl}/ws/run-log?token=${encodeURIComponent(info.token)}`

  const ws = new WebSocket(wsUrl)

  // Attached here rather than by the callers: there is exactly one place
  // a judge request leaves the client, so this is the one place that can
  // forget it. A per-caller flag would mean every future entry point has
  // to remember that submissions have owners.
  const token = sessionToken()
  const payload: WsJudgeRequest = token ? { ...req, user_token: token } : req

  return new Promise<JudgeEvent>((resolve, reject) => {
    let terminal: JudgeEvent | null = null

    ws.onopen = () => {
      ws.send(JSON.stringify(payload))
    }

    ws.onmessage = (ev: MessageEvent) => {
      let msg: JudgeEvent
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '') as JudgeEvent
      } catch {
        // Ignore malformed messages — the server only sends JSON.
        return
      }
      onEvent(msg)
      if (msg.type === 'judge_done' || msg.type === 'judge_error') {
        terminal = msg
        // The verdict is not necessarily the last thing on this socket.
        // When an analysis follows, its events arrive *after* this one,
        // so closing here would cut them off — which is exactly what the
        // pre-2026-09-18 ordering made safe and this one does not.
        //
        // Only the server can tell us which case this is: an accepted run
        // and a failing one look identical at this point, so the client
        // has nothing to infer from. `analysing` is that answer.
        if (msg.analysing !== true) {
          ws.close()
        }
      } else if (msg.type === 'agent_done' && terminal !== null) {
        // The analysis is over. Close rather than wait for the server to
        // hang up, so the promise resolves at a moment we can name.
        ws.close()
      }
    }

    ws.onerror = () => {
      if (!terminal) {
        reject(new Error('WebSocket connection failed'))
      }
    }

    ws.onclose = () => {
      if (terminal) {
        resolve(terminal)
      } else if (!terminal) {
        reject(new Error('WebSocket closed before judge_done'))
      }
    }
  })
}
