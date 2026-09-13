/**
 * Review API client — wraps the /api/v1/review endpoints (P1-19).
 *
 * P1-23's PlanView uses these to load the due-review list, mark
 * review quality (0-5), and show the next scheduled review time.
 */

import { http } from './http'

export interface ReviewState {
  id: number
  entry_id: number
  repetitions: number
  easiness_factor: number
  interval_days: number
  last_reviewed_at: string | null
  due_at: string | null
  created_at: string | null
}

/**
 * List review records whose next review is due (or never started).
 */
export async function listDueReviews(limit?: number): Promise<ReviewState[]> {
  const params = limit ? { limit } : undefined
  const res = await http.get<ReviewState[]>('/api/v1/review/due', { params })
  return res.data
}

/**
 * Fetch the SRS state for a specific mistake-book entry.
 * Returns 404 if no review record exists yet (before first review).
 */
export async function getReview(entryId: number): Promise<ReviewState> {
  const res = await http.get<ReviewState>(`/api/v1/review/${entryId}`)
  return res.data
}

/**
 * Record a review with a quality score (0-5) and recompute the schedule.
 * Creates the review record implicitly on first call.
 */
export async function recordReview(
  entryId: number,
  quality: number,
): Promise<ReviewState> {
  const res = await http.post<ReviewState>(
    `/api/v1/review/${entryId}/review`,
    { quality },
  )
  return res.data
}

/**
 * Reset the SRS state for an entry to defaults.
 */
export async function resetReview(entryId: number): Promise<ReviewState> {
  const res = await http.post<ReviewState>(`/api/v1/review/${entryId}/reset`)
  return res.data
}
