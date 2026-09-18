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

/** The homework list, for anyone who asks. */
export async function getOpenAssignments(): Promise<AssignmentOut[]> {
  const res = await http.get<AssignmentOut[]>('/api/v1/assignments')
  return res.data
}
