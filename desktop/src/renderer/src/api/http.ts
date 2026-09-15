/**
 * HTTP client — axios instance configured for the AlgoForge sidecar.
 *
 * The base URL and auth token come from the active {@link runtime}, so
 * this module has no opinion about whether it is running under Electron
 * or in a browser. Call `initHttpClient()` once during app bootstrap
 * before making any API requests.
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
  http.defaults.headers.common['Authorization'] = `Bearer ${info.token}`

  console.log(`[http] client configured (${runtime.kind}): baseURL=${info.url}`)
}

// Request interceptor: ensure Authorization header is always present
http.interceptors.request.use(
  (config) => {
    // The token is already set in defaults; this is a safety net.
    return config
  },
  (error) => Promise.reject(error)
)

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
