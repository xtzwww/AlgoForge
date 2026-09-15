/**
 * Submission history client — wraps /api/v1/submissions (PU-13).
 *
 * Read-only by design: the sidecar writes a submission when it judges,
 * so there is no create call here to invent history with.
 *
 * PU-14 uses this for the catalogue's "已通过" column. That is a
 * client-side join — one page of history per problem would be a request
 * per row — so the list view fetches a generous page of the user's own
 * submissions once and folds it into a per-problem best verdict. See
 * `fetchVerdictsByProblem` for the aggregation and why it is shaped
 * that way.
 */

import { http } from './http'

/** One history row. No source, no captured output. */
export interface SubmissionSummary {
  id: number
  problem_id: number | null
  status: string
  language: string
  /** Null for CE, which runs nothing. Render "not measured", not 0. */
  exec_time: number | null
  memory_used: number | null
  created_at: string | null
}

export interface SubmissionList {
  items: SubmissionSummary[]
  total: number
  page: number
  page_size: number
}

export interface ListSubmissionsParams {
  page?: number
  /** Capped server-side at 100. */
  page_size?: number
  problem_id?: number
  /** Verdict filter, e.g. `"WA"`. Validated server-side. */
  status?: string
  language?: string
}

/** A submission in full, including source and captured output. */
export interface SubmissionDetail extends SubmissionSummary {
  code: string
  stdout: string
  stderr: string
}

/** One page of history, newest first. */
export async function listSubmissions(params?: ListSubmissionsParams): Promise<SubmissionList> {
  const res = await http.get<SubmissionList>('/api/v1/submissions', { params })
  return res.data
}

/** One submission with its source and captured output. */
export async function getSubmission(id: number): Promise<SubmissionDetail> {
  const res = await http.get<SubmissionDetail>(`/api/v1/submissions/${id}`)
  return res.data
}

/** What the catalogue knows about one problem, verdict-wise. */
export interface ProblemVerdict {
  /** True once any submission for it reached AC. */
  passed: boolean
  /** How many submissions the fetched window holds for it. */
  attempts: number
}

/**
 * Fold a page of history into a per-problem verdict map.
 *
 * `page_size=100` is the server's ceiling, and the window is the
 * *newest* 100 submissions — so this answers "have I solved the
 * problems I have been working on recently", which is what the list
 * view is asking. A problem only touched 200 submissions ago reads as
 * unattempted. That is a deliberate exchange: the alternative is a
 * request per row, and a `COUNT`/`EXISTS` aggregate endpoint is a
 * backend change this slice does not need.
 *
 * `passed` is sticky within the window: an AC followed by later WAs
 * still reads as passed, because the question the UI asks is "did I
 * ever solve this", not "was my last attempt good".
 */
export function foldVerdicts(submissions: SubmissionSummary[]): Map<number, ProblemVerdict> {
  const verdicts = new Map<number, ProblemVerdict>()
  for (const s of submissions) {
    // Free-run submissions have no problem to attribute to.
    if (s.problem_id === null) continue
    const existing = verdicts.get(s.problem_id)
    const passed = s.status === 'AC'
    if (existing) {
      existing.attempts += 1
      existing.passed = existing.passed || passed
    } else {
      verdicts.set(s.problem_id, { passed, attempts: 1 })
    }
  }
  return verdicts
}

/**
 * Fetch the newest page of history and fold it into per-problem
 * verdicts.
 *
 * Errors propagate: the caller decides whether a missing verdict column
 * is fatal (it is not — the catalogue itself still rendered) or worth
 * surfacing.
 */
export async function fetchVerdictsByProblem(): Promise<Map<number, ProblemVerdict>> {
  const page = await listSubmissions({ page: 1, page_size: 100 })
  return foldVerdicts(page.items)
}
