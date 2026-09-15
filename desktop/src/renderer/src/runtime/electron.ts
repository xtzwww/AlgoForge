/**
 * The Electron implementation of {@link Runtime}.
 *
 * This is the only module in the renderer allowed to touch
 * `window.algoforge` — the bridge `src/preload/index.ts` exposes. That
 * is the point of the split: a web build aliases this file away
 * entirely (see `runtime/index.ts`), so the bridge cannot be reached
 * from a browser bundle even by accident.
 */

import type { Runtime, SidecarInfo } from './types'

export const runtime: Runtime = {
  kind: 'electron',
  displayName: '桌面版',
  capabilities: {
    windowReveal: true,
    nativeFileAccess: true
  },

  async getSidecarInfo(): Promise<SidecarInfo | null> {
    return window.algoforge.sidecar.getInfo()
  },

  onReveal(callback: () => void): void {
    // Guarded even though the electron bundle "knows" the bridge exists:
    // `dist/index.html` can be opened directly in a browser during UI
    // work, and the app should come up unanimated rather than throw.
    window.algoforge?.app?.onReveal(callback)
  }
}
