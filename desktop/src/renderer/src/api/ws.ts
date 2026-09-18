/**
 * WebSocket client for the judge progress stream (P1-15 / P1-16).
 *
 * Connects to `WS /ws/run-log`, sends one JSON request, and exposes
 * the streamed events as an async iterator + a callback. The caller
 * assembles the final result from the terminal `judge_done` event
 * plus the per-case `case_done` events collected along the way.
 */

import type { TestCaseIn } from './judge'
import { runtime } from '../runtime'

export type JudgeEventType =
  | 'compile_start'
  | 'compile_done'
  | 'case_start'
  | 'case_done'
  | 'agent_start'
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

  // --- Agent phase (P2-8) --------------------------------------------
  // These arrive *after* `judge_done`: the analysis runs once the verdict
  // has been recorded and returned, so a client that stopped reading at
  // the terminal event would never see them.
  /** agent_start: the judge verdict that triggered the analysis. */
  verdict?: string
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
  /** agent_done: names of the tools called, in order. */
  tools?: string[]
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
}

function applyOptions(req: WsJudgeRequest, opts?: JudgeStreamOptions): void {
  if (!opts) return
  if (opts.language !== undefined) req.language = opts.language
  if (opts.timeLimit !== undefined) req.time_limit = opts.timeLimit
  if (opts.memLimitMb !== undefined) req.mem_limit_mb = opts.memLimitMb
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

  return new Promise<JudgeEvent>((resolve, reject) => {
    let terminal: JudgeEvent | null = null

    ws.onopen = () => {
      ws.send(JSON.stringify(req))
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
