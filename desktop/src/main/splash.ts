/**
 * Splash screen window (PU-2).
 *
 * Shown immediately on launch so the user sees branded progress instead
 * of a blank desktop while the sidecar boots. The main window is created
 * in parallel (hidden) and only revealed once the sidecar passes its
 * health check.
 *
 * Two rules shape this module:
 *  1. The splash must never be the reason startup fails — every call is
 *     defensive, and losing the splash is preferable to blocking launch.
 *  2. Progress is *real*. The caption reflects actual startup stages
 *     pushed from index.ts; the bar is indeterminate precisely because a
 *     fabricated percentage would be a lie.
 */

import { BrowserWindow } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'

/** Splash dimensions and the fade-out duration used by splash.css. */
const SPLASH_WIDTH = 420
const SPLASH_HEIGHT = 280
const FADE_OUT_MS = 200

let splashWindow: BrowserWindow | null = null

/**
 * Create and show the splash window.
 *
 * Returns the window, or ``null`` if creation failed — callers must treat
 * a null splash as "carry on without it".
 */
export function createSplashWindow(): BrowserWindow | null {
  if (splashWindow && !splashWindow.isDestroyed()) return splashWindow

  try {
    splashWindow = new BrowserWindow({
      width: SPLASH_WIDTH,
      height: SPLASH_HEIGHT,
      // Frameless + transparent so splash.css can round the corners.
      frame: false,
      transparent: true,
      resizable: false,
      movable: true,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      // Not focusable: the splash must not steal focus from whatever the
      // user was doing, and it is not interactive.
      focusable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      show: false,
      backgroundColor: '#00000000',
      webPreferences: {
        preload: join(__dirname, '../preload/splash.js'),
        sandbox: false,
        contextIsolation: true,
        nodeIntegration: false,
      },
    })

    // Purely presentational — never let it navigate anywhere.
    splashWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))

    splashWindow.once('ready-to-show', () => {
      splashWindow?.show()
    })

    splashWindow.on('closed', () => {
      splashWindow = null
    })

    // Dev serves the renderer from the Vite dev server (electron-vite
    // starts it before Electron, so this URL is live by now). Production
    // loads the built file next to index.html.
    if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
      splashWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}/splash.html`)
    } else {
      splashWindow.loadFile(join(__dirname, '../renderer/splash.html'))
    }
  } catch (err) {
    console.error('[splash] failed to create splash window:', (err as Error).message)
    splashWindow = null
  }

  return splashWindow
}

/**
 * Update the caption under the progress bar.
 *
 * ``stage`` is the text shown verbatim, so pass already-localised copy.
 * No-ops when the splash is gone.
 */
export function setSplashStatus(stage: string): void {
  if (!splashWindow || splashWindow.isDestroyed()) return
  splashWindow.webContents.send('splash:status', stage)
}

/**
 * Fade the splash out and destroy it.
 *
 * Resolves after the window is gone. Safe to call more than once, and
 * safe to call when the splash never existed.
 */
export async function closeSplash(): Promise<void> {
  const win = splashWindow
  if (!win || win.isDestroyed()) {
    splashWindow = null
    return
  }

  try {
    win.webContents.send('splash:fade-out')
    // Let CSS run the fade before tearing the window down; destroying
    // immediately would make the splash vanish instead of dissolve.
    await new Promise((resolve) => setTimeout(resolve, FADE_OUT_MS))
  } catch {
    // Sending can fail if the window died between the checks — fall
    // through to destruction either way.
  }

  if (!win.isDestroyed()) {
    win.destroy()
  }
  splashWindow = null
}

/** Whether a live splash window exists. */
export function isSplashVisible(): boolean {
  return splashWindow !== null && !splashWindow.isDestroyed()
}
