/**
 * HTTP client — axios instance configured for the AlgoForge sidecar.
 *
 * The base URL and auth token come from the active {@link runtime}, so
 * this module has no opinion about whether it is running under Electron
 * or in a browser. Call `initHttpClient()` once during app bootstrap
 * before making any API requests.
 *
 * Two credentials, two headers
 * ----------------------------
 * `X-Sidecar-Token` is set here and never replaced: it authenticates the
 * *app* to the sidecar and is the same for every user. `Authorization`
 * carries the logged-in *person*'s session token, and `session.ts`
 * overwrites it whenever somebody is signed in.
 *
 * They must not share a header. `Authorization` can hold exactly one
 * bearer credential, and the server reads it with one dependency
 * (`app.api.auth.current_user`); if the user token overwrote a shared
 * header, a teacher's request would arrive with a valid person
 * credential and no process credential at all — and would be refused for
 * the wrong reason.
 */

import axios, { type AxiosInstance } from 'axios'
import { runtime } from '../runtime'

/** The configured axios instance. `baseURL` and auth header are set by initHttpClient(). */
export const http: AxiosInstance = axios.create({
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

/**
 * Resolve the sidecar connection info from the runtime and configure
 * the axios instance (baseURL + Authorization header).
 *
 * Must be called once before any request is made.
 */
export async function initHttpClient(): Promise<void> {
  const info = await runtime.getSidecarInfo()
  if (!info) {
    throw new Error('Sidecar info not available. Is the sidecar running?')
  }

  http.defaults.baseURL = info.url
  // Shared by every user and never overwritten — see the module docstring.
  http.defaults.headers.common['X-Sidecar-Token'] = info.token
  // Also sent as `Authorization` so that routes written before the process
  // credential existed keep working. The sidecar's token check reads
  // `X-Sidecar-Token`; `session.ts` replaces this one on login.
  http.defaults.headers.common['Authorization'] = `Bearer ${info.token}`

  console.log(`[http] client configured (${runtime.kind}): baseURL=${info.url}`)
}

// Response interceptor: unwrap response data or surface errors cleanly
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      console.error(`[http] ${error.response.status} ${error.config?.url}:`, error.response.data)
    } else {
      console.error('[http] request failed:', error.message)
    }
    return Promise.reject(error)
  }
)
