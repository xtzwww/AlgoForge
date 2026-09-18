/**
 * MistakeBook API client — wraps the /api/v1/mistake-book endpoints (P1-18).
 *
 * P1-21's MistakesView uses these to load the list, filter by tag
 * client-side, mark entries as reviewed, and delete entries.
 */

import { http } from './http'

export interface MistakeEntry {
  id: number
  submission_id: number
  problem_id: number
  error_cause: string
  knowledge_tags: string[]
  reviewed: boolean
  review_count: number
  last_reviewed_at: string | null
  created_at: string | null
  /**
   * P2-4's analysis. Served since P2-24 — the fields were written to the
   * row from P2-4 on but never returned, so no client could show them.
   */
  agent_error_type: string | null
  agent_suggestion: string
  /** Whether an analysis has landed. See {@link AnalysisStatus}. */
  analysis_status: AnalysisStatus
  /**
   * `agent_suggestion` split into its three sections, plus the Chinese
   * reading of `agent_error_type`.
   *
   * The three sections are meant to be revealed in order — the fix last —
   * so the UI reads these instead of the joined `agent_suggestion`. See
   * {@link FeedbackSection}. All three are always present; a section the
   * analysis did not produce is `''`. On rows written before the split
   * existed, the whole text arrives under `error_detail` and the other
   * two are empty.
   */
  error_detail: string
  hint: string
  suggested_fix: string
  /**
   * `agent_error_type` in student-facing Chinese, or `''` when the label
   * is not one this build knows. Prefer it over the raw label for display.
   */
  error_type_description: string
}

/**
 * The three sections of an AI diagnosis, in the order they are meant to
 * be read.
 *
 * The order is the whole point: `error_detail` states what is wrong,
 * `hint` nudges without solving, and `suggested_fix` gives the answer
 * away. Showing all three at once means the student reads the last one
 * and learns nothing — which is what the UI did before this existed.
 */
export type FeedbackSection = 'error_detail' | 'hint' | 'suggested_fix'

/** Heading shown above each section when it is released. */
export const FEEDBACK_SECTION_TITLES: Record<FeedbackSection, string> = {
  error_detail: '错在哪',
  hint: '提示',
  suggested_fix: '怎么改'
}

/**
 * Button label for the step that releases `section`.
 *
 * Worded as the student's own state ("还是不会") rather than as an action
 * ("显示更多"), because the gate is meant to be a decision the student
 * makes about their own understanding, not a control they operate.
 */
export const FEEDBACK_REVEAL_LABELS: Record<FeedbackSection, string> = {
  error_detail: '看错因提示',
  hint: '还是不会，给点提示',
  suggested_fix: '还是不会，直接看怎么改'
}

/**
 * The three states an entry's analysis can be in.
 *
 * `pending` covers both "queued" and "never attempted": from the UI's
 * side they are the same thing — no result yet. `failed` is the one that
 * gives the retry button something to key off, and before P2-24 it did
 * not exist, so a provider outage looked identical to never having tried.
 */
export type AnalysisStatus = 'pending' | 'done' | 'failed'

export interface CreateMistakeEntryRequest {
  submission_id: number
  error_cause?: string
}

export interface UpdateMistakeEntryRequest {
  error_cause?: string
  reviewed?: boolean
}

/**
 * List mistake-book entries, optionally filtered.
 */
export async function listMistakes(params?: {
  problem_id?: number
  reviewed?: boolean
}): Promise<MistakeEntry[]> {
  const res = await http.get<MistakeEntry[]>('/api/v1/mistake-book', { params })
  return res.data
}

/**
 * Fetch a single entry by id.
 */
export async function getMistake(id: number): Promise<MistakeEntry> {
  const res = await http.get<MistakeEntry>(`/api/v1/mistake-book/${id}`)
  return res.data
}

/**
 * Create a new entry from a submission.
 */
export async function createMistake(req: CreateMistakeEntryRequest): Promise<MistakeEntry> {
  const res = await http.post<MistakeEntry>('/api/v1/mistake-book', req)
  return res.data
}

/**
 * Update an entry's error_cause and/or reviewed flag.
 */
export async function updateMistake(
  id: number,
  req: UpdateMistakeEntryRequest
): Promise<MistakeEntry> {
  const res = await http.patch<MistakeEntry>(`/api/v1/mistake-book/${id}`, req)
  return res.data
}

/**
 * Delete an entry.
 */
export async function deleteMistake(id: number): Promise<void> {
  await http.delete(`/api/v1/mistake-book/${id}`)
}

/**
 * Ask the sidecar to analyse an entry again (P2-24).
 *
 * Returns as soon as the retry is accepted — the sidecar re-judges the
 * submission and calls the model in the background, which can take
 * minutes. The entry comes back marked `pending`; poll {@link getMistake}
 * for the outcome.
 *
 * Rejects with 409 when the entry already has an analysis, and 400 when
 * its submission was a free run with no problem to re-judge against.
 */
export async function analyzeMistake(id: number): Promise<MistakeEntry> {
  const res = await http.post<MistakeEntry>(`/api/v1/mistake-book/${id}/analyze`)
  return res.data
}
