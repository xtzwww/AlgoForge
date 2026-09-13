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
