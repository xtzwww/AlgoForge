/**
 * Preload for the splash window (PU-2).
 *
 * Deliberately minimal: the splash is a purely presentational window, so
 * it gets a dedicated bridge with exactly two read-only channels rather
 * than the full app preload. Nothing here gives the splash access to the
 * filesystem, the sidecar token, or the sidecar connection info.
 */

import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'

const algoforgeSplash = {
  /** Subscribe to startup stage updates pushed by the main process. */
  onStatus: (callback: (text: string) => void): void => {
    ipcRenderer.on('splash:status', (_event: IpcRendererEvent, text: string) => callback(text))
  },
  /**
   * Subscribe to the secondary line under the caption.
   *
   * Carries the consequence and the next step of a failed stage — the
   * caption says what failed, this says what it means and what to do.
   */
  onDetail: (callback: (text: string) => void): void => {
    ipcRenderer.on('splash:detail', (_event: IpcRendererEvent, text: string) => callback(text))
  },
  /** Ask the splash to play its fade-out. */
  onFadeOut: (callback: () => void): void => {
    ipcRenderer.on('splash:fade-out', () => callback())
  },
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('algoforgeSplash', algoforgeSplash)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (defined in dts)
  window.algoforgeSplash = algoforgeSplash
}
