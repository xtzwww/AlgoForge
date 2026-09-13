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
}

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
export async function listMistakes(
  params?: { problem_id?: number; reviewed?: boolean },
): Promise<MistakeEntry[]> {
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
export async function createMistake(
  req: CreateMistakeEntryRequest,
): Promise<MistakeEntry> {
  const res = await http.post<MistakeEntry>('/api/v1/mistake-book', req)
  return res.data
}

/**
 * Update an entry's error_cause and/or reviewed flag.
 */
export async function updateMistake(
  id: number,
  req: UpdateMistakeEntryRequest,
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
