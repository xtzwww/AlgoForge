import { ElectronAPI } from '@electron-toolkit/preload'

export interface SidecarInfo {
  port: number
  token: string
  url: string
}

export interface AlgoForgeAPI {
  sidecar: {
    getInfo: () => Promise<SidecarInfo | null>
    /**
     * Fires when the engine is replaced (crash + automatic restart).
     * The new engine has a **new port and a new token**; `null` means
     * the engine is currently gone.
     */
    onInfoChanged: (callback: (info: SidecarInfo | null) => void) => void
  }
  app: {
    /** PU-2: fires when the splash hands over to the main window. */
    onReveal: (callback: () => void) => void
  }
}

/** PU-2: the minimal bridge available inside the splash window. */
export interface AlgoForgeSplashAPI {
  onStatus: (callback: (text: string) => void) => void
  onFadeOut: (callback: () => void) => void
}

declare global {
  interface Window {
    electron: ElectronAPI
    algoforge: AlgoForgeAPI
    algoforgeSplash: AlgoForgeSplashAPI
  }
}
