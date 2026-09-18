/**
 * Judge API client — wraps POST /api/v1/judge.
 *
 * P1-16: the WS stream (api/ws.ts) is the primary path; this HTTP
 * client is kept as a fallback / for non-streaming callers. Both
 * share the same request/response shape.
 *
 * PU-12: cases can now come from the catalogue (``problem_id``) instead
 * of the caller. That is the preferred mode — the sidecar reads the
 * hidden cases itself, so they never reach the browser — and it also
 * picks up the problem's own time/memory limits. ``test_cases`` stays
 * for ad-hoc self-testing. Supply exactly one of the two.
 */

import { http } from './http'
import type { ProcessView } from '../edit-trace'

export interface TestCaseIn {
  input: string
  expected_output: string
}

export interface TestCaseOut {
  status: string
  input: string
  expected_output: string
  actual_output: string
  stderr: string
  exit_code: number
}

export interface JudgeResponse {
  status: string
  compile_output: string
  passed: number
  total: number
  /** Echo of the request's problem_id, or null for inline cases. */
  problem_id: number | null
  test_cases: TestCaseOut[]
  /**
   * The edit-behaviour observation (C1), or null when no trace was sent.
   *
   * Arrives with the verdict rather than needing a second call: both are
   * facts about this one submission, known at the same moment.
   */
  process?: ProcessView | null
}

export interface JudgeRequest {
  code: string
  /** Language id; omitted means the sidecar's default (C++). */
  language?: string
  /** Catalogue problem to pull cases from. Exclusive with test_cases. */
  problem_id?: number
  /** Inline cases for self-testing. Exclusive with problem_id. */
  test_cases?: TestCaseIn[]
  /** Seconds. Overrides the problem's own limit when problem_id is set. */
  time_limit?: number
  /** MB. Overrides the problem's own limit when problem_id is set. */
  mem_limit_mb?: number
  /**
   * Whether the verdict is persisted (PU-8). `true` is a submit, `false`
   * a scratch run that computes the verdict without writing history.
   * See `api/ws.ts` for the full description — the WS stream is the path
   * the practice page actually uses.
   */
  record?: boolean
}

export interface JudgeOptions {
  language?: string
  timeLimit?: number
  memLimitMb?: number
  record?: boolean
}

/**
 * Judge code against a stored problem's cases (PU-12).
 *
 * The preferred mode. Prefer this over {@link judgeCodeWithCases} for
 * anything contest-like: the hidden cases stay on the sidecar, and the
 * limits come from the problem so the client cannot loosen them.
 */
export async function judgeCode(
  code: string,
  problemId: number,
  opts?: JudgeOptions
): Promise<JudgeResponse> {
  const body: JudgeRequest = { code, problem_id: problemId }
  applyOptions(body, opts)
  const res = await http.post<JudgeResponse>('/api/v1/judge', body)
  return res.data
}

/**
 * Judge code against caller-supplied cases (the self-test path).
 *
 * Kept for the "run this snippet against input I just typed" flow, where
 * there is no problem row to point at.
 */
export async function judgeCodeWithCases(
  code: string,
  testCases: TestCaseIn[],
  opts?: JudgeOptions
): Promise<JudgeResponse> {
  const body: JudgeRequest = { code, test_cases: testCases }
  applyOptions(body, opts)
  const res = await http.post<JudgeResponse>('/api/v1/judge', body)
  return res.data
}

function applyOptions(body: JudgeRequest, opts?: JudgeOptions): void {
  if (!opts) return
  if (opts.language !== undefined) body.language = opts.language
  if (opts.timeLimit !== undefined) body.time_limit = opts.timeLimit
  if (opts.memLimitMb !== undefined) body.mem_limit_mb = opts.memLimitMb
  if (opts.record !== undefined) body.record = opts.record
}
