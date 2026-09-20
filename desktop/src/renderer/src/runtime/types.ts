/**
 * The runtime contract — what the UI is allowed to assume about its shell.
 *
 * The renderer runs in two places: inside Electron, and in a plain
 * browser tab (PU-5's web build). Almost all of the code is the same in
 * both; only a handful of things genuinely differ, and they are all
 * here:
 *
 * - **Where the sidecar is.** Electron learns the port and token from
 *   the main process (it generated them when it spawned the sidecar).
 *   A browser has no main process, so it is told at build time.
 * - **Whether there is a window to reveal.** Electron hides the window
 *   until the sidecar answers its health check, then reveals it. A
 *   browser tab is already visible and there is nothing to hold back.
 *
 * Everything else — every HTTP call, every WebSocket — is shell-agnostic
 * and must not branch on the runtime. If a feature needs to behave
 * differently, the difference belongs in {@link RuntimeCapabilities} as
 * a declared capability the UI reads, not as an `if (isElectron)` buried
 * in a component.
 *
 * See `runtime/index.ts` for how the concrete implementation is chosen,
 * and why it is chosen at build time rather than at run time.
 */

/** Where the sidecar is listening, and the token to talk to it with. */
export interface SidecarInfo {
  port: number
  token: string
  /** Base URL including scheme and port, e.g. `http://localhost:8000`. */
  url: string
}

/**
 * What this shell can do, as flags the UI may branch on.
 *
 * Deliberately a small, explicit list rather than a bag of feature
 * strings: a capability here is a promise that some UI reads it, and a
 * promise that both implementations answer it. Adding one means saying
 * what the web build does instead, which is the question worth forcing.
 */
export interface RuntimeCapabilities {
  /**
   * Whether the shell owns a window it can hold hidden and then reveal.
   *
   * False in the browser, where the page is visible the moment it loads.
   * Only the entrance animation cares.
   */
  readonly windowReveal: boolean
  /**
   * Whether the shell can reach the user's filesystem — open a native
   * picker, write a file the user then keeps.
   *
   * False in the browser, which can only offer a download into whatever
   * the browser does with downloads. Any future "export to file" action
   * must disable itself and explain why rather than silently saving to
   * an unknown place.
   */
  readonly nativeFileAccess: boolean
}

/** The shell the renderer is running in. */
export interface Runtime {
  /** Which implementation this is. For display and for tests. */
  readonly kind: 'electron' | 'web'
  /** Human-facing name for the environment badge, e.g. `桌面版`. */
  readonly displayName: string
  readonly capabilities: RuntimeCapabilities

  /**
   * Resolve the sidecar's address and token.
   *
   * Resolves `null` when the runtime has no sidecar configured — the
   * browser build with no `VITE_SIDECAR_URL`. Callers are expected to
   * surface that as "the sidecar is not reachable" rather than guess a
   * default, because guessing produces a confusing connection error
   * instead of an accurate configuration error.
   */
  getSidecarInfo(): Promise<SidecarInfo | null>

  /**
   * Run *callback* when the shell is ready for the app to become
   * visible. Fires immediately (next frame) in the browser.
   */
  onReveal(callback: () => void): void

  /**
   * Run *callback* when the sidecar's address or token changes.
   *
   * Desktop only in practice: the web build reads a fixed URL from a
   * build-time variable and has no way to learn about a replacement, so
   * it never fires. It still has to exist on the interface — a capability
   * the UI reads must be answered by both implementations, and "never
   * fires" is the honest answer here, not a missing method.
   *
   * `null` means the engine is currently gone; a fresh
   * {@link SidecarInfo} means it came back — on a **new port with a new
   * token**, which is why the UI must re-configure its HTTP client
   * rather than just retry the old URL.
   */
  onSidecarInfoChanged(callback: (info: SidecarInfo | null) => void): void
}
