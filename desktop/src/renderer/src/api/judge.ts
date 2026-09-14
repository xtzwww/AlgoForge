/**
 * Judge API client — wraps POST /api/v1/judge.
 *
 * P1-16: the WS stream (api/ws.ts) is the primary path; this HTTP
 * client is kept as a fallback / for non-streaming callers. Both
 * share the same request/response shape.
 */

import { http } from './http'

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
  test_cases: TestCaseOut[]
}

export interface JudgeRequest {
  code: string
  /** Language id; omitted means the sidecar's default (C++). */
  language?: string
  test_cases: TestCaseIn[]
  time_limit?: number
  mem_limit_mb?: number
}

/**
 * Submit code + test cases to the sidecar for judging (HTTP fallback).
 * The primary path in P1-16 is the WebSocket stream; this is kept for
 * callers that only want the final result.
 */
export async function judgeCode(
  code: string,
  testCases: TestCaseIn[],
  opts?: { language?: string; timeLimit?: number; memLimitMb?: number },
): Promise<JudgeResponse> {
  const body: JudgeRequest = { code, test_cases: testCases }
  if (opts?.language !== undefined) body.language = opts.language
  if (opts?.timeLimit !== undefined) body.time_limit = opts.timeLimit
  if (opts?.memLimitMb !== undefined) body.mem_limit_mb = opts.memLimitMb
  const res = await http.post<JudgeResponse>('/api/v1/judge', body)
  return res.data
}
