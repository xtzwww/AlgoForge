/**
 * Teacher console API client (阶段 3).
 *
 * Wraps the seven `/api/v1/teacher/*` routes. Every one of them is
 * teacher-only *server-side* — see the guard tests in
 * `tests/test_teacher_console.py` — so this module carries no role
 * checks of its own. Adding one here would suggest the client has an
 * opinion about who gets in, which is exactly the misunderstanding the
 * backend's docstring warns against: the console ships in the same
 * bundle the students use.
 *
 * The shapes below mirror the `BaseModel`s in `app/api/teacher.py`. They
 * are hand-written rather than generated because the two sides are in
 * different languages and there is no codegen step in the build; a
 * mismatch shows up as `undefined` in a table cell, which is why
 * `TeacherView.vue` renders counts through a formatter that tolerates a
 * missing number instead of printing `undefined`.
 */

import { http } from './http'

/** The numbers at the top of the console. */
export interface ClassOverview {
  /** Accounts with the student role. */
  students: number
  submissions: number
  accepted: number
  /** `accepted / submissions`, 0.0 when nobody has submitted. */
  accuracy: number
  /** Mistake-book entries not yet reviewed. */
  open_mistakes: number
  /** Entries whose analysis has landed. */
  analysed_mistakes: number
  /**
   * Submissions whose editing pattern was flagged (C1) — all of them,
   * reviewed or not. A measurement, not a workload.
   */
  flagged_submissions: number
  /**
   * The same rows minus the ones a teacher dismissed: how much reading
   * there is to do before class. Kept separate from
   * {@link flagged_submissions} because a flag that fires on everything
   * and gets dismissed every time looks, by this number alone, like a
   * flag that never fires.
   */
  open_flags: number
}

/** One row of the "what should I teach next" answer. */
export interface ErrorCount {
  /**
   * The root-cause label, or `null` for entries whose analysis has not
   * landed. `null` is a group, not an error — see the view.
   */
  label: string | null
  /** The label in Chinese, or `''` when unknown. */
  description: string
  count: number
  /** `count` over everything counted, rounded to four places. */
  share: number
}

/** Per-tag accuracy, class-wide or for one student. */
export interface KnowledgeStatOut {
  tag: string
  total: number
  passed: number
  accuracy: number
}

/** One student, worst first. */
export interface StudentOut {
  user_id: number
  username: string
  display_name: string
  submissions: number
  accepted: number
  accuracy: number
  mistakes: number
  flagged_submissions: number
  /** The tag they have failed most often, or `null`. */
  weakest_tag: string | null
}

/** One mistake in one student's history, oldest first. */
export interface TimelineEntryOut {
  id: number
  problem_id: number | null
  status: string | null
  error_type: string | null
  error_description: string
  created_at: string | null
}

/**
 * The process signal carried on a flagged submission (C1).
 *
 * Same shape as `ProcessView` in `edit-trace.ts`, re-declared rather than
 * imported so this module does not depend on the editor's telemetry types
 * — the console reads what the server stored, and the two are allowed to
 * drift independently.
 */
export interface StoredProcessView {
  flagged: boolean
  /** One-line reading, in Chinese. */
  summary: string
  /** The measurements behind {@link summary}, each a sentence. */
  reasons: string[]
  features: Record<string, number | null>
}

/**
 * One student with a reason to look at them (阶段 6).
 *
 * There is deliberately no severity number: a score the teacher cannot
 * interrogate is a verdict, and `reason` is the part they act on.
 */
export interface AlertOut {
  user_id: number
  display_name: string
  /** `repeating` | `stuck` | `silent` — which rule fired. */
  kind: string
  /** One Chinese sentence carrying the numbers that fired it. */
  reason: string
}

/** Who needs attention, and why. Most actionable first. */
export async function getAlerts(): Promise<AlertOut[]> {
  const res = await http.get<AlertOut[]>('/api/v1/teacher/alerts')
  return res.data
}

/**
 * The teacher's verdict on a flag (阶段 5).
 *
 * `verdict` is `null` for "nobody has ruled on this yet", which is the
 * common case and must not be confused with `''` or `undefined`.
 */
export interface FlagReview {
  verdict: 'confirmed' | 'dismissed' | null
  reviewed_by: number | null
  reviewed_at: string | null
}

/** A flagged submission, with the evidence that flagged it. */
export interface FlaggedSubmission {
  id: number
  problem_id: number | null
  status: string | null
  created_at: string | null
  /** Display name of the owner, or `''` for an unowned row. */
  student: string
  process: StoredProcessView
  /**
   * The style-consistency reading (C2), or `null` when it was not made
   * (no history, unowned row). Same shape as {@link StoredProcessView}:
   * numbers plus the words that explain them. A submission can carry
   * both `process` and `style` flags — they are independent readings.
   */
  style: StoredProcessView | null
  review: FlagReview
}

export async function getOverview(): Promise<ClassOverview> {
  const res = await http.get<ClassOverview>('/api/v1/teacher/overview')
  return res.data
}

/** Per-tag accuracy. Omit `userId` for the whole class. */
export async function getKnowledgeStats(userId?: number): Promise<KnowledgeStatOut[]> {
  const res = await http.get<KnowledgeStatOut[]>('/api/v1/teacher/knowledge-stats', {
    params: userId === undefined ? {} : { user_id: userId }
  })
  return res.data
}

/** Mistake counts per root cause, most common first. */
export async function getErrorDistribution(userId?: number): Promise<ErrorCount[]> {
  const res = await http.get<ErrorCount[]>('/api/v1/teacher/error-distribution', {
    params: userId === undefined ? {} : { user_id: userId }
  })
  return res.data
}

/** Every student, worst first. */
export async function getStudents(): Promise<StudentOut[]> {
  const res = await http.get<StudentOut[]>('/api/v1/teacher/students')
  return res.data
}

/** One student's mistakes, oldest first. */
export async function getStudentTimeline(userId: number): Promise<TimelineEntryOut[]> {
  const res = await http.get<TimelineEntryOut[]>(`/api/v1/teacher/students/${userId}/timeline`)
  return res.data
}

/**
 * Submissions carrying a process flag, newest first.
 *
 * Dismissed rows are left out unless `includeReviewed` is set: the point
 * of dismissing one is that it stops asking for attention.
 */
export async function getFlaggedSubmissions(
  limit = 50,
  includeReviewed = false
): Promise<FlaggedSubmission[]> {
  const res = await http.get<{ items: FlaggedSubmission[] }>(
    '/api/v1/teacher/submissions/flagged',
    { params: { limit, include_reviewed: includeReviewed } }
  )
  return res.data.items
}

/**
 * Confirm or dismiss the flag on one submission (阶段 5).
 *
 * `cleared` takes the verdict back — a misclick must be recoverable, and
 * a dismissal with no undo would quietly hide a real flag forever.
 */
export async function reviewFlag(
  submissionId: number,
  verdict: 'confirmed' | 'dismissed' | 'cleared'
): Promise<FlaggedSubmission> {
  const res = await http.post<FlaggedSubmission>(
    `/api/v1/teacher/submissions/${submissionId}/review`,
    { verdict }
  )
  return res.data
}

export interface CreateStudentRequest {
  username: string
  password: string
  display_name?: string
}

/**
 * Add a student to the roster.
 *
 * Rejects with 409 for a taken username — the one failure the caller can
 * act on, so it is worth catching separately from a generic error.
 */
export async function createStudent(req: CreateStudentRequest): Promise<SessionUserLike> {
  const res = await http.post<SessionUserLike>('/api/v1/teacher/students', req)
  return res.data
}

/** The subset of a user the create route returns. */
export interface SessionUserLike {
  id: number
  username: string
  display_name: string
  role: string
}

export interface ImportStudentsRequest {
  /** Raw CSV text. Two columns (username,display_name) or three (…,password). */
  csv: string
  /** Used for rows that did not carry their own password. */
  default_password?: string
  /** When true the first non-empty line is treated as a column header. */
  has_header?: boolean
}

/** The outcome of one CSV row. */
export interface ImportedStudent {
  username: string
  display_name: string
  ok: boolean
  /** Empty when `ok`. */
  error: string
}

export interface ImportStudentsResult {
  created: number
  skipped: number
  /** One entry per data row, in file order. */
  results: ImportedStudent[]
}

/**
 * Bulk-add students from pasted CSV text.
 *
 * Per-row results rather than all-or-nothing: one malformed line must
 * not stop a whole class from being imported. Duplicates inside the
 * file itself are reported too, not just collisions with existing
 * usernames — a name pasted twice would otherwise look like a success.
 *
 * Rejects only when nothing at all could be parsed (422).
 */
export async function importStudents(req: ImportStudentsRequest): Promise<ImportStudentsResult> {
  const res = await http.post<ImportStudentsResult>('/api/v1/teacher/students/import', req)
  return res.data
}
