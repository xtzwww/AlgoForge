/**
 * The logged-in person (B1) — one reactive singleton.
 *
 * `http.ts` puts the *sidecar* token on every request, and that token
 * authenticates the app, not a person: it is shared by every client and
 * knowing it must not make you a teacher. This module layers a second
 * credential on top of it.
 *
 * Why the user token replaces the header rather than riding alongside it
 * ---------------------------------------------------------------------
 * `Authorization` carries exactly one bearer credential, and the server
 * has one dependency reading it (:func:`app.api.auth.current_user`). Two
 * headers would mean two names for the same concept and a second
 * dependency to keep in step — and any route that read the wrong one
 * would silently treat a teacher as nobody.
 *
 * The override is **additive, never destructive**: the header is only
 * replaced when a user token exists. When nobody is logged in the
 * request keeps the sidecar token it was given. Deleting it instead
 * would be harmless today (nothing verifies it — see the note in
 * `app/main.py`) and a total outage the moment something does.
 *
 * Why the token is persisted
 * --------------------------
 * `localStorage`, so a refresh does not sign the teacher out in the
 * middle of a lesson. It is a bearer credential at rest in whatever
 * browser storage this shell offers, which is exactly as private as the
 * machine — acceptable for a lab machine the teacher already owns, and
 * the reason the session expires server-side after twelve hours.
 *
 * Why the user is fetched, not stored
 * -----------------------------------
 * Only the token is persisted; the profile comes from `GET /auth/me`
 * every startup. A stored copy of the profile can disagree with the
 * server about the role, and role is the one field the console's entire
 * boundary rests on — a stale `"teacher"` cached in a browser would draw
 * a console the server is about to answer 403 to.
 */

import { computed, ref } from 'vue'
import { http } from './http'

/** The two roles. Kept in sync with `app/models/user.py`. */
export type Role = 'student' | 'teacher'

export interface SessionUser {
  id: number
  username: string
  display_name: string
  role: Role
}

interface LoginResponse {
  token: string
  user: SessionUser
}

interface MeResponse {
  user: SessionUser
}

const STORAGE_KEY = 'algoforge.user-token'

/**
 * The token, or `''` for nobody.
 *
 * Not exported as a writable ref: every mutation goes through
 * {@link setSession} / {@link clearSession}, so the header, the profile
 * and the persisted copy cannot get out of step with each other.
 */
const token = ref<string>(readStoredToken())

/** The profile behind {@link token}, or `null`. */
const user = ref<SessionUser | null>(null)

/**
 * Whether {@link restoreSession} has finished.
 *
 * Distinct from "logged in": until the round trip comes back the app
 * genuinely does not know, and a router guard that treated "unknown" as
 * "logged out" would bounce a teacher to the login screen on every
 * refresh — a redirect loop that looks like a broken build.
 */
const ready = ref(false)

function readStoredToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    // Private-mode Safari and locked-down Electron contexts throw on
    // access rather than returning null. Losing the session is the right
    // degradation; breaking the boot is not.
    return ''
  }
}

/** The current token, for the WebSocket path (which cannot set headers). */
export function sessionToken(): string {
  return token.value
}

/**
 * The logged-in profile, or `null`.
 *
 * A computed rather than the ref itself: callers should not be able to
 * write the profile and leave it disagreeing with the token.
 */
export const sessionUser = computed<SessionUser | null>(() => user.value)

export const isLoggedIn = computed<boolean>(() => user.value !== null)

export const isTeacher = computed<boolean>(() => user.value?.role === 'teacher')

/** True only once the startup round trip has resolved. */
export const sessionReady = computed<boolean>(() => ready.value)

/**
 * Attach the token to every subsequent request. See the module docstring
 * for why this replaces rather than adds, and only when non-empty.
 */
http.interceptors.request.use((config) => {
  if (token.value) {
    config.headers.Authorization = `Bearer ${token.value}`
  }
  return config
})

function setSession(nextToken: string, nextUser: SessionUser): void {
  token.value = nextToken
  user.value = nextUser
  try {
    localStorage.setItem(STORAGE_KEY, nextToken)
  } catch {
    // Storage refused: the session stays valid for this page load and
    // is lost on refresh. Worth doing rather than worth failing over.
  }
}

function clearSession(): void {
  token.value = ''
  user.value = null
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // See setSession.
  }
}

/**
 * Exchange credentials for a session.
 *
 * The server answers 401 with one message for both "no such user" and
 * "wrong password" — deliberately, so this endpoint is not a username
 * oracle — which is why there is nothing useful to distinguish here and
 * the caller shows the message as-is.
 */
export async function login(username: string, password: string): Promise<SessionUser> {
  const res = await http.post<LoginResponse>('/api/v1/auth/login', { username, password })
  setSession(res.data.token, res.data.user)
  return res.data.user
}

/**
 * End this session, and keep every other device signed in.
 *
 * The server deletes the token from the credential rather than from the
 * user, so a teacher with the console open on a projector and on their
 * laptop is not signed out of both by closing one.
 *
 * A failed call still clears the local state: a session the server will
 * no longer honour is not worth keeping, and leaving it would mean the
 * UI keeps showing a console whose every request 401s.
 */
export async function logout(): Promise<void> {
  try {
    await http.post('/api/v1/auth/logout')
  } finally {
    clearSession()
  }
}

/**
 * Re-establish the session on startup, if the stored token is still good.
 *
 * Never throws: a stale or revoked token is the *expected* outcome
 * twelve hours later, and an exception here would take the whole app
 * down to explain a credential that simply expired. After this resolves
 * {@link sessionReady} is true and the answer is authoritative.
 */
export async function restoreSession(): Promise<void> {
  const stored = readStoredToken()
  if (!stored) {
    ready.value = true
    return
  }
  try {
    const res = await http.get<MeResponse>('/api/v1/auth/me')
    // Written back rather than assumed: the header interceptor already
    // sent `stored`, and a 200 here is the server saying it was worth
    // something. If it had not been, this call would have 401'd.
    setSession(stored, res.data.user)
  } catch (err) {
    // Only a **refusal** means the token is dead.
    //
    // A transport failure — the sidecar not up yet, a request fired
    // before `initHttpClient()` set the base URL, a laptop that was
    // asleep — says nothing about the credential. Clearing on those
    // would delete a perfectly good session because of a race at
    // startup, and the symptom would be "the console logs me out on
    // every launch", which looks nothing like its cause.
    if ((err as { response?: { status?: number } })?.response?.status === 401) {
      clearSession()
    }
  } finally {
    ready.value = true
  }
}
