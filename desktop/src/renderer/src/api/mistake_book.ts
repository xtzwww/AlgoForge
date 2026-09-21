/**
 * MistakeBook API client — wraps the /api/v1/mistake-book endpoints (P1-18).
 *
 * P1-21's MistakesView uses these to load the list, filter by tag
 * client-side, mark entries as reviewed, and delete entries.
 */

import { http } from './http'

/**
 * One rung of S7's hint ladder.
 *
 * `level` is the rung's depth, not its index: the model numbers them 1/2/3
 * and a later phase may skip one, so the UI shows what the model said
 * rather than recomputing a position that could disagree with it.
 */
export interface HintRung {
  level: number
  text: string
}

/**
 * S7's 提示 agent output: three graded rungs plus self-check questions.
 *
 * This is **not** the same thing as {@link FeedbackSection}. Both are
 * progressive, and both are about hints, which is exactly why they are two
 * components and not one:
 *
 * - `FeedbackSection` (错在哪 / 提示 / 怎么改) is *one* diagnosis cut into
 *   three parts of a single answer. Opening the last one ends the
 *   exercise — there is nothing after it.
 * - `HintLadder` is *three attempts at the same thing*, each a bit more
 *   concrete. Rung 1 points at the area, rung 3 is nearly the answer, and
 *   the student stops at whichever rung unsticks them.
 *
 * Merging them would mean either the ladder's rungs become "sections" that
 * end in the fix (so the ladder stops being a ladder), or the diagnosis's
 * fix becomes just another rung (so there is no final answer at all).
 */
export interface HintLadder {
  levels: HintRung[]
  /** Questions the student answers to check they actually got it. */
  self_check: string[]
}

/**
 * One practice problem S7's 变式题 agent wrote from the mistake.
 *
 * Mirrors the sidecar's `VariantOut`. `sample_input` / `sample_output` are
 * strings shown verbatim rather than parsed: they are for reading, and the
 * judge's test-case shape is a different contract.
 */
export interface Variant {
  statement: string
  sample_input: string
  sample_output: string
  knowledge_points: string[]
  /** How this differs from the problem the student just failed. */
  difference: string
}

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
  /**
   * T34: S7's two worker outputs (columns added by migration
   * `e3a71c58b2d4`, served since T28).
   *
   * Both are empty for every pipeline that does not run those agents —
   * which is the default (`direct`) — so a client must treat their
   * presence as the exception, not the rule, and render nothing at all
   * rather than an empty labelled panel.
   *
   * `hint_ladder` is a single object (one ladder per analysis); `variants`
   * is a list even though the team currently produces one, because the
   * column is a list and a client that iterates will not need changing if
   * a later phase asks for more.
   */
  hint_ladder: HintLadder | null
  variants: Variant[]
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
 *
 * `error_type` filters on the agent's label (P2-21) — equality against
 * `agent_error_type`, so it never matches free text the student typed
 * into `error_cause`. An unrecognised label is an empty list, not an
 * error.
 *
 * The mistake book page does **not** pass it: that page loads everything
 * once and filters in a computed, because its filter *options* are
 * derived from the loaded entries. See `MistakesView.vue`. The parameter
 * is here for callers that hold no list of their own.
 */
export async function listMistakes(params?: {
  problem_id?: number
  reviewed?: boolean
  error_type?: string
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
