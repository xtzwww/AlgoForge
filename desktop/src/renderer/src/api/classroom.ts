/**
 * Classrooms (PU-13).
 *
 * Two audiences, one module: a teacher creates and manages classes, a
 * student joins one with a code. They are kept together because they are
 * two ends of the same object, and splitting them would mean two files
 * that have to agree on what `ClassroomOut` is.
 *
 * Note what is *not* here: any call that lets a student create a class
 * or edit a roster. The server refuses those for a student account, and
 * a client-side wrapper would only be a second place to get that wrong.
 */

import { http } from './http'

export interface ClassroomOut {
  id: number
  name: string
  teacher_id: number
  /** What the teacher reads out. See `app/models/classroom.py`. */
  invite_code: string
  member_count: number
  created_at: string | null
}

export interface ClassMemberOut {
  user_id: number
  username: string
  display_name: string
  joined_at: string | null
}

export interface JoinClassroomResult {
  classroom: ClassroomOut
  /** True when they were already in it — not an error. */
  already_member: boolean
}

/** Create a class. Teacher only. The response carries the invite code. */
export async function createClassroom(name: string): Promise<ClassroomOut> {
  const res = await http.post<ClassroomOut>('/api/v1/teacher/classrooms', { name })
  return res.data
}

/** The classes this teacher owns, newest first. */
export async function listClassrooms(): Promise<ClassroomOut[]> {
  const res = await http.get<ClassroomOut[]>('/api/v1/teacher/classrooms')
  return res.data
}

/** One class's roster, in join order. */
export async function listClassMembers(classId: number): Promise<ClassMemberOut[]> {
  const res = await http.get<ClassMemberOut[]>(`/api/v1/teacher/classrooms/${classId}/members`)
  return res.data
}

/**
 * Take one student off the roster.
 *
 * Their submissions are untouched — being removed from a class is not
 * the same as never having done the work.
 */
export async function removeClassMember(classId: number, userId: number): Promise<void> {
  await http.delete(`/api/v1/teacher/classrooms/${classId}/members/${userId}`)
}

/** Delete a class. Its assignments survive as the "for everyone" kind. */
export async function deleteClassroom(classId: number): Promise<void> {
  await http.delete(`/api/v1/teacher/classrooms/${classId}`)
}

/**
 * Join with an invite code.
 *
 * Idempotent server-side, so submitting twice is safe: the second answer
 * has `already_member: true` rather than an error.
 */
export async function joinClassroom(inviteCode: string): Promise<JoinClassroomResult> {
  const res = await http.post<JoinClassroomResult>('/api/v1/classrooms/join', {
    invite_code: inviteCode
  })
  return res.data
}

/** The classes this account has joined. Empty for a teacher. */
export async function myClassrooms(): Promise<ClassroomOut[]> {
  const res = await http.get<ClassroomOut[]>('/api/v1/classrooms/mine')
  return res.data
}
