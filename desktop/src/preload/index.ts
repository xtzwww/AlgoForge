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
