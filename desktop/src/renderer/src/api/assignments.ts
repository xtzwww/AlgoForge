/**
 * Homework (阶段 4).
 *
 * Two of these five routes are teacher-only and one is deliberately
 * open. That split is the design, not an oversight: publishing homework
 * is a teacher action, *reading* it is not — a student who cannot see
 * the list cannot do the work, and the desktop client runs without
 * logging in.
 *
 * There is no per-student status anywhere in these shapes. "Who has not
 * done it" is the roster minus the people who submitted, computed on the
 * server at read time; see `app/models/assignment_stats.py`.
 */

import { http } from './http'
import type { AssistancePolicy } from './assistance'

export interface AssignmentOut {
  id: number
  title: string
  description: string
  problem_ids: number[]
  due_at: string | null
  /** Computed server-side so no client has to invent its own idea of late. */
  overdue: boolean
  created_by: number | null
  created_at: string | null
  /**
   * How much AI help this homework allows (阶段 5). The number is the
   * authority; {@link assistance} is the same rule already expanded, so
   * a list can render the label without a second request per row.
   */
  assistance_level: number
  /** The policy behind {@link assistance_level}. */
  assistance: AssistancePolicy
}

export interface AssignmentProblemStat {
  problem_id: number
  submissions: number
  accepted: number
  accuracy: number
  students: number
}

export interface AssignmentStats {
  assignment: AssignmentOut
  /** In the order the teacher listed them, not sorted. */
  problems: AssignmentProblemStat[]
  students_who_submitted: number
  /** The actionable part of the screen. */
  students_missing: string[]
}

export interface CreateAssignmentRequest {
  title: string
  description?: string
  problem_ids: number[]
  /** ISO datetime, or omit for no deadline. */
  due_at?: string
  /**
   * 1–5, defaulting server-side to 3 (阶段 5). Omit to accept the
   * default rather than duplicating the constant here: a client that
   * hardcodes "3" drifts the day the default changes, and the drift
   * shows up as homework that quietly allows more help than intended.
   */
  assistance_level?: number
  /**
   * Which class to set it for (PU-13). Omit for "everyone" — the
   * practice-problem case, which needs no class to exist.
   */
  class_id?: number
}

/** Publish homework. Teacher only — 403 for anyone else. */
export async function createAssignment(req: CreateAssignmentRequest): Promise<AssignmentOut> {
  const res = await http.post<AssignmentOut>('/api/v1/teacher/assignments', req)
  return res.data
}

/** Every assignment, newest first. Teacher only. */
export async function listAssignments(): Promise<AssignmentOut[]> {
  const res = await http.get<AssignmentOut[]>('/api/v1/teacher/assignments')
  return res.data
}

/** How the class is doing on one. Teacher only. */
export async function getAssignmentStats(id: number): Promise<AssignmentStats> {
  const res = await http.get<AssignmentStats>(`/api/v1/teacher/assignments/${id}/stats`)
  return res.data
}

/** Un-publish. Submissions are history and are not touched. */
export async function deleteAssignment(id: number): Promise<void> {
  await http.delete(`/api/v1/teacher/assignments/${id}`)
}

/** Rename / re-date on copy. Both optional — see the route. */
export interface DuplicateAssignmentRequest {
  title?: string
  /** ISO datetime. Omit for "no deadline on the copy". */
  due_at?: string
}

/**
 * Copy one assignment into a new one (阶段 5).
 *
 * The problem set and the assistance level are copied server-side, so
 * the two things a teacher always changes — title and deadline — are the
 * only two this takes. Leaving `title` out produces 「<原名>（副本）」.
 */
export async function duplicateAssignment(
  id: number,
  req: DuplicateAssignmentRequest = {}
): Promise<AssignmentOut> {
  const res = await http.post<AssignmentOut>(`/api/v1/teacher/assignments/${id}/duplicate`, req)
  return res.data
}

/** One row of a student's report: how they did on one assignment. */
export interface StudentAssignmentRow {
  assignment_id: number
  title: string
  due_at: string | null
  overdue: boolean
  problem_count: number
  submissions: number
  accepted: number
  /**
   * Accepted problems over the assignment's problems, or `null` when
   * nothing was submitted at all.
   *
   * The `null` is the point. It is not the same as `0`, which means
   * "submitted and got none of them" — keep them apart when rendering,
   * because "hasn't started" and "is stuck" call for different actions.
   */
  progress: number | null
  last_submitted_at: string | null
}

/** One student against every assignment, set or not. */
export interface StudentReport {
  user_id: number
  display_name: string
  username: string
  rows: StudentAssignmentRow[]
  /** Assignments with at least one submission. */
  completed: number
  total_assignments: number
}

/**
 * The per-student report (阶段 5). Teacher only.
 *
 * Every assignment is in `rows`, including ones the student never
 * touched — that is the reason the route exists, since a student with no
 * submissions has no mistakes and so is invisible in the mistake book.
 */
export async function getStudentReport(userId: number): Promise<StudentReport> {
  const res = await http.get<StudentReport>(`/api/v1/teacher/students/${userId}/report`)
  return res.data
}

/** The homework list, for anyone who asks. */
export async function getOpenAssignments(): Promise<AssignmentOut[]> {
  const res = await http.get<AssignmentOut[]>('/api/v1/assignments')
  return res.data
}

/**
 * One assignment as the student it was set for sees it (PU-13).
 *
 * Flat rather than nesting {@link AssignmentOut}: the list screen renders
 * every field, so a nested object would only add a level to reach
 * through.
 */
export interface MyAssignmentOut {
  id: number
  title: string
  description: string
  due_at: string | null
  overdue: boolean
  assistance_level: number
  class_id: number | null
  /** `null` is the "for everyone" kind. */
  classroom_name: string | null
  problem_ids: number[]
  problem_count: number
  /** Distinct problems passed at least once. */
  solved: number
  /** Which ones — used to tick the individual rows. */
  solved_problem_ids: number[]
  submissions: number
  /**
   * `solved / problem_count`, or `null` when nothing was submitted.
   *
   * Render `null` and `0` differently: "has not started" and "started
   * and passed none of them" call for different actions from the person
   * reading it.
   */
  progress: number | null
  last_submitted_at: string | null
}

/**
 * The assignments I should see, with my own progress (PU-13).
 *
 * Homework set for a class I have not joined is absent — that is the
 * point of assigning it to a class. Homework with no class is visible to
 * everyone, which is also what keeps this useful on a database whose
 * rows all predate classes.
 *
 * Requires a signed-in account: without one there is no "my" to compute.
 */
export async function getMyAssignments(): Promise<MyAssignmentOut[]> {
  const res = await http.get<MyAssignmentOut[]>('/api/v1/my/assignments')
  return res.data
}
