/**
 * Problems API client — wraps /api/v1/problems (PU-12).
 *
 * Two views of a problem, deliberately kept separate:
 *
 * - {@link getProblem} returns the statement, the display samples, and
 *   the **visible** test cases. That is what a problem page renders.
 * - {@link getProblemTestCases} returns **every** case, hidden ones
 *   included. That is judging input.
 *
 * The second one exists for the local single-user deployment, where the
 * user authored the problems and the sidecar is on their own machine.
 * Judging does not need it — the sidecar's own `problem_id` mode reads
 * the cases server-side (see api/judge.ts) — and a future web
 * deployment should drop this call rather than ship it to a browser.
 */

import { http } from './http'

/** A display sample: an input/output pair shown with the statement. */
export interface ProblemSample {
  input: string
  output: string
}

/** A judging case. `is_sample` distinguishes visible from hidden. */
export interface ProblemTestCase {
  id: number
  ordinal: number
  input: string
  expected_output: string
  is_sample: boolean
}

/** A catalogue row — no statement, no cases. */
export interface ProblemSummary {
  id: number
  title: string
  knowledge_tags: string[]
  difficulty: string | null
  source: string | null
  time_limit: number
  memory_limit: number
  is_active: boolean
}

/** One page of the catalogue. */
export interface ProblemList {
  items: ProblemSummary[]
  total: number
  page: number
  page_size: number
}

/** Full problem detail. `test_cases` holds visible cases only. */
export interface ProblemDetail {
  id: number
  title: string
  knowledge_tags: string[]
  description: string
  input_format: string | null
  output_format: string | null
  samples: ProblemSample[]
  time_limit: number
  memory_limit: number
  difficulty: string | null
  source: string | null
  is_active: boolean
  test_cases: ProblemTestCase[]
}

/** The complete case set for a problem (hidden cases included). */
export interface ProblemTestCaseList {
  problem_id: number
  total: number
  test_cases: ProblemTestCase[]
}

export interface ProblemCreateRequest {
  title: string
  knowledge_tags?: string[]
  description?: string
  input_format?: string | null
  output_format?: string | null
  samples?: ProblemSample[]
  time_limit?: number
  memory_limit?: number
  difficulty?: 'easy' | 'medium' | 'hard' | null
  source?: string | null
  /** Ordinals are assigned server-side from this order, starting at 1. */
  test_cases?: Array<
    Pick<ProblemTestCase, 'input' | 'expected_output'> & {
      is_sample?: boolean
    }
  >
}

export interface ProblemUpdateRequest extends ProblemCreateRequest {
  /**
   * 缺省（undefined）= 保持不变——停用/启用是高频独立操作，不该被
   * 表单每次带上。其余字段与 create 同语义：整体替换，传什么用什么。
   */
  is_active?: boolean
}

export interface ListProblemsParams {
  page?: number
  /** Capped server-side at 100. */
  page_size?: number
  /** Exact match against a tag in `knowledge_tags`. */
  knowledge_tag?: string
  difficulty?: 'easy' | 'medium' | 'hard'
  include_inactive?: boolean
  /**
   * Case-insensitive substring of the **title** (server-side `q`).
   *
   * Titles only — the statement prose is not searched, so a phrase that
   * appears only inside a description will not match. `%` and `_` in the
   * needle are escaped server-side and match literally.
   */
  q?: string
}

/**
 * List one page of the problem catalogue.
 *
 * Rows carry no statement prose and no cases, so this is safe to call
 * for a list view without dragging every problem's full text across.
 */
export async function listProblems(params?: ListProblemsParams): Promise<ProblemList> {
  const res = await http.get<ProblemList>('/api/v1/problems', { params })
  return res.data
}

/**
 * Fetch a problem's statement, samples, and **visible** test cases.
 *
 * Hidden cases are filtered out by the sidecar; this is the call a
 * problem page uses.
 */
export async function getProblem(problemId: number): Promise<ProblemDetail> {
  const res = await http.get<ProblemDetail>(`/api/v1/problems/${problemId}`)
  return res.data
}

/**
 * Fetch **every** test case for a problem, hidden ones included.
 *
 * Local-deployment judging helper. Prefer letting the sidecar resolve
 * cases via `problem_id` (see api/judge.ts) so hidden expected outputs
 * never leave the server; reach for this only when a UI genuinely has to
 * show all cases — e.g. an authoring screen.
 */
export async function getProblemTestCases(problemId: number): Promise<ProblemTestCaseList> {
  const res = await http.get<ProblemTestCaseList>(`/api/v1/problems/${problemId}/test-cases`)
  return res.data
}

/**
 * Create a problem with its cases in one transaction.
 *
 * A problem with no cases cannot be judged, so the two are created
 * together rather than leaving a half-finished problem behind if the
 * second request never happens.
 */
export async function createProblem(req: ProblemCreateRequest): Promise<ProblemDetail> {
  const res = await http.post<ProblemDetail>('/api/v1/problems', req)
  return res.data
}

/**
 * Copy a problem — statement, cases, and authoring settings — into a
 * new row.
 *
 * **Hidden cases are copied too.** That is the point: a teacher who has
 * already built a hidden case set for a problem wants the derived
 * problem to be judgeable without rebuilding it. The response is a
 * {@link ProblemDetail}, whose `test_cases` holds the *visible* cases
 * only, so calling this does not leak the hidden set back to the
 * client.
 *
 * The copy is active even if the source was not: the teacher has to be
 * able to edit what they just created, and an inactive row is one they
 * then cannot find.
 */
export async function duplicateProblem(problemId: number): Promise<ProblemDetail> {
  const res = await http.post<ProblemDetail>(`/api/v1/problems/${problemId}/duplicate`)
  return res.data
}

/**
 * Update a problem — statement, limits, tags, and cases — in one
 * transaction. Cases are **replaced wholesale**: the request order becomes
 * the new ordinal order, so a caller sends the full intended case set
 * rather than a diff.
 *
 * `is_active` omitted means "leave it unchanged"; pass `false` to retire
 * the problem (it disappears from the default listing but stays
 * resolvable by id for old submissions).
 */
export async function updateProblem(
  problemId: number,
  req: ProblemUpdateRequest
): Promise<ProblemDetail> {
  const res = await http.put<ProblemDetail>(`/api/v1/problems/${problemId}`, req)
  return res.data
}

/** Tiny response for the active toggle. */
export interface ActiveOut {
  id: number
  is_active: boolean
}

/**
 * Toggle a problem's active flag without touching its cases.
 *
 * Deliberately separate from `updateProblem`: retiring a problem through
 * the full PUT would require the caller to round-trip every field *and*
 * every hidden case back unchanged — one omission and the problem is
 * silently altered. This endpoint flips one column.
 */
export async function setProblemActive(
  problemId: number,
  isActive: boolean
): Promise<ActiveOut> {
  const res = await http.patch<ActiveOut>(
    `/api/v1/problems/${problemId}/active`,
    { is_active: isActive }
  )
  return res.data
}
