import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

/**
 * AlgoForge API exposed to the renderer process.
 *
 * The renderer can access sidecar connection info (URL + auth token)
 * through `window.algoforge.sidecar.getInfo()`.
 */
const algoforge = {
  sidecar: {
    getInfo: () => ipcRenderer.invoke('sidecar:info'),
    /**
     * Fired when the engine is replaced — including after a crash and
     * automatic restart.
     *
     * A restarted sidecar listens on a **new port with a new token**, so
     * without this the renderer would keep retrying the dead address for
     * the rest of the session: the engine would be running and every
     * page would still say "Network Error".
     *
     * `null` means the engine is currently gone (it just died); a fresh
     * info object means it came back.
     */
    onInfoChanged: (callback: (info: unknown) => void): void => {
      ipcRenderer.on('sidecar:info-changed', (_event, info) => callback(info))
    }
  },
  app: {
    /**
     * PU-2: fired by the main process at the moment the splash hands
     * over and the real window is revealed.
     *
     * The renderer cannot use a load-time CSS animation for its entrance:
     * the window is created hidden and waits for the sidecar health
     * check, so a load-time animation would already be over by the time
     * anyone could see it. This signal is what starts it for real.
     */
    onReveal: (callback: () => void): void => {
      ipcRenderer.on('app:reveal', () => callback())
    }
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('algoforge', algoforge)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.algoforge = algoforge
}
