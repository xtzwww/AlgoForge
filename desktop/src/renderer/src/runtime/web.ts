/**
 * The browser implementation of {@link Runtime}.
 *
 * There is no preload bridge here and no main process, so the sidecar's
 * address and token come from build-time environment variables instead
 * (`VITE_SIDECAR_URL` / `VITE_SIDECAR_TOKEN`, see `.env.web.example`).
 *
 * **Security boundary — read before deploying this anywhere.**
 * Anything under `VITE_` is compiled into the bundle and is readable by
 * anyone who loads the page. Putting the sidecar token there is
 * acceptable only for the deployment this slice targets: a web preview
 * on the developer's own machine or a trusted LAN, talking to a sidecar
 * that is likewise local.
 *
 * Note what is *not* protecting this deployment: the sidecar does not
 * currently verify the token. `app/main.py` installs no auth middleware
 * and `app/api/ws.py` ignores the `?token=` parameter sent below, so a
 * request carrying no credential is served (a pre-existing gap, not one
 * this module introduces). The real barriers are that the sidecar binds
 * to 127.0.0.1 and that the CORS allowlist is narrow — and CORS is a
 * browser rule, which anything that is not a browser simply ignores.
 * Since the sidecar executes submitted code, do not expose it to a
 * network until token verification exists. See `.env.web.example` for
 * the full note.
 *
 * This module deliberately does **not** touch `window.algoforge`. A web
 * build aliases the electron implementation away (see `runtime/index.ts`),
 * so the bridge is not merely unused here — it is not in the bundle.
 */

import type { Runtime, SidecarInfo } from './types'

const RAW_URL = (import.meta.env.VITE_SIDECAR_URL ?? '').trim()
const TOKEN = (import.meta.env.VITE_SIDECAR_TOKEN ?? '').trim()

export const runtime: Runtime = {
  kind: 'web',
  displayName: '网页版',
  capabilities: {
    // A browser tab is visible the moment it loads; there is no window
    // to hold back until the sidecar is healthy.
    windowReveal: false,
    // A browser can only offer a download, not a real save-to-path.
    nativeFileAccess: false
  },

  async getSidecarInfo(): Promise<SidecarInfo | null> {
    if (!RAW_URL) return null

    let parsed: URL
    try {
      parsed = new URL(RAW_URL)
    } catch {
      // A malformed build variable is a configuration error, not a
      // connectivity one. Saying so beats letting every request fail
      // with an opaque "invalid URL" from the HTTP client.
      throw new Error(`VITE_SIDECAR_URL is not a valid URL: ${JSON.stringify(RAW_URL)}`)
    }

    return {
      // Trailing slashes are stripped so the client does not build
      // `http://host:8000//api/v1/...`. Any path prefix is preserved —
      // a reverse proxy may well mount the sidecar under one.
      url: RAW_URL.replace(/\/+$/, ''),
      // Not required to be non-empty: an empty token means the sidecar
      // will answer 401, and that error names the real problem far more
      // clearly than "sidecar info not available" would.
      token: TOKEN,
      port: parsed.port ? Number(parsed.port) : parsed.protocol === 'https:' ? 443 : 80
    }
  },

  onReveal(callback: () => void): void {
    // Nothing holds the page back, but the app's entrance animation keys
    // off this signal. Firing it on the next frame keeps the two shells
    // in the same order — registered before the first paint, applied
    // after — so the animation behaves the same in both.
    requestAnimationFrame(() => callback())
  }
}
