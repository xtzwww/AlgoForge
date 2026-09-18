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
