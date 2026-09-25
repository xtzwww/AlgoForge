/**
 * Debug API client (PU-17) — wraps `/api/v1/debug/*`.
 *
 * A debug session is a *conversation with state*, so this client is
 * deliberately thin and holds nothing. The sidecar owns the binaries, the
 * breakpoints and the position in the program; every call here sends an
 * intention ("move one line in") and gets back the whole new state. That
 * is what stops the editor's gutter from drifting away from what gdb will
 * actually honour.
 *
 * The five routes:
 *
 * | function | route |
 * |---|---|
 * | {@link getAvailability} | `GET  /debug/availability` |
 * | {@link createSession}   | `POST /debug/sessions` |
 * | {@link setBreakpoints}  | `PUT  /debug/sessions/{id}/breakpoints` |
 * | {@link step}            | `POST /debug/sessions/{id}/step` |
 * | {@link closeSession}    | `DELETE /debug/sessions/{id}` |
 *
 * Types mirror the sidecar's `to_dict` payloads exactly. They are written
 * out rather than generated, so the field that a panel needs cannot be
 * quietly absent — `display_function` in particular exists because gdb
 * prints argument *values* in the signature, and the trimming rule lives
 * with the parser (see `app/sandbox/debugger.py`).
 */

import { http } from './http'

/** The debug actions the sidecar accepts, mirrored from `api/debug.py`. */
export type DebugAction = 'run' | 'continue' | 'next' | 'step' | 'finish' | 'until'

/** Why a program stopped. Drives which controls make sense next. */
export type StopReason = 'breakpoint' | 'signal' | 'exited' | 'unknown'

export interface DebugAvailability {
  available: boolean
  /** Absolute path to the matched g++, when available. */
  compiler?: string | null
  /** Absolute path to the matched gdb, when available. */
  gdb?: string | null
  /**
   * Why debugging is unavailable. Names the expected files, because
   * "调试不可用" on its own gives a student nothing to act on.
   */
  reason?: string | null
}

export interface DebugVariable {
  name: string
  value: string
  kind: string
  /**
   * True when the variable is in scope but has not been assigned yet.
   *
   * Render these as 尚未初始化 rather than as the value: gdb reports
   * whatever bytes happen to be on the stack before the declaration runs
   * (measured: three runs of the same breakpoint on `for (int i = …)`
   * gave 583006768, -75499648 and 564133088). Showing that as a number
   * would teach a student their loop starts at 583006768.
   */
  uninitialised: boolean
}

export interface DebugFrame {
  level: number
  /** The full signature as gdb printed it, argument values included. */
  function: string
  /**
   * The bare name to render. Use this, not {@link function} —
   * `function` is `sum (n=5)` or, for a container,
   * `sum (v=std::vector of length 5, capacity 5 = {...})`.
   */
  display_function: string
  /** `main.cpp`, with the sandbox scratch path stripped. */
  file: string
  line: number
  address?: string | null
  /** True for compiler/library frames — usually hidden in the panel. */
  is_library: boolean
}

export interface DebugStop {
  reason: StopReason
  file?: string | null
  line?: number | null
  function?: string | null
  breakpoint_number?: number | null
  signal?: string | null
  signal_description?: string | null
  exit_code?: number | null
  variables: DebugVariable[]
  stack: DebugFrame[]
  /** The stop line's source text, echoed by gdb. */
  source_line?: string | null
  /**
   * True when this same (file, line) was reached earlier in the run.
   *
   * This is what separates "the declaration on this line has not run yet"
   * from "it ran, and this is a later iteration" — both stop on the
   * identical `for (int i = …)` line. Without it a loop variable would
   * read as uninitialised forever.
   */
  line_visited_before: boolean
}

export interface DebugSession {
  session_id: string
  breakpoints: number[]
  /** False when the session exists but has not been built yet. */
  built: boolean
  /** True once the program has run to completion. */
  finished: boolean
  /** Where the program is, or null before the first step. */
  stop: DebugStop | null
}

export interface BreakpointsResult {
  session_id: string
  breakpoints: number[]
  /**
   * Always `"next_step"`. Breakpoints are a full replace and the program
   * is not running between steps, so a new breakpoint cannot cause an
   * immediate stop — the panel says "下次单步生效" rather than implying
   * the program will re-stop now.
   */
  takes_effect: string
}

/** Whether this machine has a matched g++/gdb pair. Cheap; call on mount. */
export async function getAvailability(): Promise<DebugAvailability> {
  const res = await http.get<DebugAvailability>('/api/v1/debug/availability')
  return res.data
}

/**
 * Open a session. Compiles the code, so this is the slow call.
 *
 * Compiling here rather than on the first step is deliberate: the student
 * finds out their code does not compile *before* the panel switches into
 * debugging, at the moment they expect a compile result, rather than as a
 * confusing failure of the first step. A compile error surfaces as a
 * rejected promise with the tidied message in `detail`.
 */
export async function createSession(
  code: string,
  breakpoints: number[] = [],
  stdin = ''
): Promise<DebugSession> {
  const res = await http.post<DebugSession>('/api/v1/debug/sessions', {
    code,
    stdin,
    breakpoints
  })
  return res.data
}

/**
 * Replace the session's breakpoints.
 *
 * A full replace, not add/remove deltas: the gutter already holds the
 * whole truth for the current file, and reconciling deltas on the server
 * invites drift between the two.
 */
export async function setBreakpoints(
  sessionId: string,
  lines: number[]
): Promise<BreakpointsResult> {
  const res = await http.put<BreakpointsResult>(
    `/api/v1/debug/sessions/${encodeURIComponent(sessionId)}/breakpoints`,
    { lines }
  )
  return res.data
}

/**
 * Move the program and get the new state back.
 *
 * `run` restarts from the beginning (and clears the step history); the
 * sidecar decides that, so this client does not special-case it. A failed
 * step rejects with a 400 and a message — the session itself may still be
 * good, so do not discard it on that basis alone.
 */
export async function step(sessionId: string, action: DebugAction): Promise<DebugSession> {
  const res = await http.post<DebugSession>(
    `/api/v1/debug/sessions/${encodeURIComponent(sessionId)}/step`,
    { action }
  )
  return res.data
}

/**
 * Close a session and delete its working directory.
 *
 * Idempotent by design: closing an unknown id answers `closed: false`
 * rather than 404, so cleanup after an expiry is not reported as an
 * error. The return value says whether there was anything to close.
 */
export async function closeSession(sessionId: string): Promise<boolean> {
  const res = await http.delete<{ session_id: string; closed: boolean }>(
    `/api/v1/debug/sessions/${encodeURIComponent(sessionId)}`
  )
  return res.data.closed
}
