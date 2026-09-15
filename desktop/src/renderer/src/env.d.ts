/// <reference types="vite/client" />

/**
 * Build-time variables the renderer reads.
 *
 * Declared explicitly rather than relying on `vite/client`'s index
 * signature, so a typo in a variable name is a type error instead of a
 * silently `undefined` value. Only the web target reads these; the
 * Electron target gets the same information from the main process.
 */
interface ImportMetaEnv {
  /** Which shell this bundle targets. Informational; the alias decides. */
  readonly VITE_TARGET?: 'electron' | 'web'
  /** Web target: sidecar base URL, e.g. `http://127.0.0.1:8000`. */
  readonly VITE_SIDECAR_URL?: string
  /**
   * Web target: the sidecar's token.
   *
   * Compiled into the bundle and therefore readable by anyone who loads
   * the page — acceptable only for a local or trusted-LAN deployment.
   * See `runtime/web.ts` for the boundary.
   */
  readonly VITE_SIDECAR_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
