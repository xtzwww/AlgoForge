import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import {
  startSidecar,
  stopSidecar,
  getSidecarInfo,
  installExitHook,
  onSidecarInfoChanged
} from './sidecar'
import { ensurePostgres, stopPostgres } from './pg'
import { reclaimDevPort } from './devserver'
import { createSplashWindow, setSplashStatus, setSplashDetail, closeSplash } from './splash'

// PU-1: catch abnormal exits (crash, uncaught exception) that would
// otherwise skip the orderly teardown and leave uvicorn running.
installExitHook()

/**
 * PU-1: guards the two-pass quit sequence below. `before-quit` fires,
 * we preventDefault to run async cleanup, then call app.quit() again —
 * this flag stops the second pass from re-entering cleanup.
 */
let teardownStarted = false

/** PU-2: length of the main window's fade-in, mirrored by main.css. */
const REVEAL_MS = 250

/**
 * PU-2: floor on how long the splash stays up.
 *
 * Not padding for effect — on a warm start the sidecar can be healthy in
 * well under a second, and a splash that appears and vanishes in 120ms
 * reads as a glitch rather than as a launch. This only ever *delays*
 * showing the app by a fraction of a second.
 */
const MIN_SPLASH_MS = 700

/**
 * How long to hold the splash when a startup step failed.
 *
 * A failure caption is the only place the user learns what went wrong,
 * and the splash normally disappears the moment the window can be shown
 * — which, when the sidecar dies instantly, is immediate. Without this
 * floor the explanation flashes for a few frames and is gone, leaving
 * only the downstream symptom ("every page says Network Error") and no
 * cause. Long enough to read a sentence and take a screenshot.
 */
const FAILURE_HOLD_MS = 5000

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Create the main window (hidden).
 *
 * PU-2: this no longer shows itself on `ready-to-show`. Visibility is
 * controlled by `revealMainWindow()` so the splash can hand over at the
 * right moment instead of racing it.
 */
function createWindow(): BrowserWindow {
  const mainWindow = new BrowserWindow({
    width: 900,
    height: 670,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#0b1020', // matches --af-canvas; avoids a white flash
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  // Forward renderer console messages to the main process terminal
  mainWindow.webContents.on('console-message', (_event, level, message) => {
    const prefix = level === 0 ? 'log' : level === 1 ? 'info' : level === 2 ? 'warn' : 'error'
    console.log(`[renderer:${prefix}] ${message}`)
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return mainWindow
}

/**
 * Show the main window with its entrance animation (PU-2).
 *
 * Two things animate, and they are deliberately separate:
 *  - the renderer's own content (a CSS scale + fade, started by the
 *    `app:reveal` message — it cannot be a load-time animation because
 *    the window is hidden until now);
 *  - the OS window itself, faded via `setOpacity` so the desktop
 *    backdrop does not pop.
 *
 * `setOpacity` is a no-op on Linux, where the window is simply shown.
 */
async function revealMainWindow(win: BrowserWindow): Promise<void> {
  if (win.isDestroyed()) return

  // Wait for the first frame so we never flash an empty surface. Bounded
  // by a timeout: a slow renderer must not be able to withhold the app.
  if (win.webContents.isLoading()) {
    await Promise.race([
      new Promise<void>((resolve) => win.once('ready-to-show', () => resolve())),
      delay(3000)
    ])
  }
  if (win.isDestroyed()) return

  win.webContents.send('app:reveal')

  if (process.platform === 'linux') {
    win.show()
    return
  }

  win.setOpacity(0)
  win.show()

  const steps = 10
  for (let i = 1; i <= steps; i++) {
    await delay(REVEAL_MS / steps)
    if (win.isDestroyed()) return
    win.setOpacity(i / steps)
  }
  win.setOpacity(1)
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(async () => {
  // Set app user model id for windows (PU-2: was the electron-vite
  // template default 'com.electron', which made the taskbar group the
  // app under a generic identity).
  electronApp.setAppUserModelId('com.algoforge.desktop')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC: expose sidecar connection info to renderer
  ipcMain.handle('sidecar:info', () => getSidecarInfo())

  // Push connection-info changes to every window.
  //
  // Needed because a restarted sidecar gets a new port and a new token.
  // The renderer's axios instance is configured once at startup from
  // `sidecar:info`; without this push it would hold a dead address for
  // the rest of the session and report "Network Error" on every page
  // while the engine is in fact running again.
  onSidecarInfoChanged((info) => {
    for (const window of BrowserWindow.getAllWindows()) {
      if (window.isDestroyed()) continue
      window.webContents.send('sidecar:info-changed', info)
    }
  })

  // --- Startup sequence (PU-2) -------------------------------------
  //
  // Previously this awaited the sidecar *before* creating the window, so
  // the user stared at nothing for the whole sidecar boot. Now the splash
  // appears immediately and the window is built in parallel; the splash
  // narrates real stages and hands over once /health is green.

  const splashShownAt = Date.now()
  createSplashWindow()
  setSplashStatus('初始化运行时…')

  // Reclaim the dev-server port before vite starts (PU-8).
  //
  // electron-vite *silently reuses* a dev server already listening on
  // 5173 instead of failing with "port in use". If that listener is a
  // leftover from a previous session, the new Electron main process is
  // handed the old server's URL and then dies on its own startup (the
  // `@electron-toolkit/utils` environment it expects is not there) —
  // leaving the OLD window frame on screen with NO sidecar behind it.
  // That is the exact shape of the "界面能开，但每一页都 Network Error"
  // report: the UI is real, the engine is gone, and nothing in either
  // log says so. Dev only; a packaged build has no dev server.
  if (is.dev) {
    const reclaimed = await reclaimDevPort((message) => setSplashStatus(message))
    if (reclaimed) {
      console.log(`[main] reclaimed dev server port (killed stale pid ${reclaimed})`)
    }
  }

  // Build the window now (hidden) so its renderer loads while the
  // sidecar is still booting.
  let mainWindow: BrowserWindow
  try {
    mainWindow = createWindow()
  } catch (err) {
    console.error('[main] failed to create window:', (err as Error).message)
    await closeSplash()
    return
  }

  setSplashStatus('启动数据库…')

  // The sidecar's DATABASE_URL points at PostgreSQL, and /health does not
  // touch the database — so without this the app would come up looking
  // healthy while every page hangs and reports "Network Error".
  const dbReady = await ensurePostgres((message) => setSplashStatus(message))

  setSplashStatus('启动分析引擎…')

  let sidecarFailed = false
  let sidecarFailureReason = ''
  try {
    const info = await startSidecar()
    console.log(`[main] sidecar ready: ${info.url} (health check passed)`)
  } catch (err) {
    // A dead sidecar means no judging, but the app is still worth
    // showing — the UI surfaces the failure and env-check explains it.
    sidecarFailed = true
    sidecarFailureReason = (err as Error).message
    console.error('[main] failed to start sidecar:', sidecarFailureReason)
  }

  if (sidecarFailed) {
    // Say why, on the screen, and hold it there long enough to read.
    //
    // The previous copy was "分析引擎未就绪，将继续启动…" — true, and
    // useless: it named no cause and no next step, so the user's only
    // recourse was to report "它不工作". The reason is knowable (the
    // sidecar's own last output, carried out by startSidecar), so show
    // it and say what to do about it.
    setSplashStatus(`分析引擎未能启动：${sidecarFailureReason || '原因不明'}`)
    setSplashDetail('数据库功能与判题将不可用。请把这一行连同终端里的 [sidecar] 日志一起截图反馈。')
    // Rule 2 of splash.ts applies: a failure is real information, not a
    // decorative stage, so it must not be flashed and taken away. See
    // FAILURE_HOLD_MS.
    await delay(FAILURE_HOLD_MS)
  } else {
    setSplashStatus(dbReady ? '准备就绪' : '数据库未就绪，将继续启动…')
    if (!dbReady) {
      setSplashDetail('题库、错题本与教师端需要数据库，相关页面可能报错。')
    }
  }

  // See MIN_SPLASH_MS.
  const elapsed = Date.now() - splashShownAt
  if (elapsed < MIN_SPLASH_MS) {
    await delay(MIN_SPLASH_MS - elapsed)
  }

  await revealMainWindow(mainWindow)
  await closeSplash()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// PU-1: tear the sidecar down before the app actually exits.
//
// The previous code called stopSidecar() and then app.quit() immediately.
// stopSidecar() only worked synchronously back then, so Electron could
// finish quitting first and leave the uvicorn process tree orphaned.
// Now we hold the quit with preventDefault() until the tree is gone.
app.on('before-quit', (event) => {
  if (teardownStarted) return // second pass — cleanup already done
  event.preventDefault()
  teardownStarted = true

  // PU-2: the splash may still be up if the user quit mid-launch.
  closeSplash()
    .catch(() => {
      /* the splash is decorative; never block quitting on it */
    })
    .then(() => stopSidecar())
    .catch((err) => {
      console.error('[main] sidecar teardown failed:', (err as Error).message)
    })
    // Only stops PostgreSQL if this process started it, and with `-m fast`
    // so the next launch does not pay 40 seconds of crash recovery.
    .then(() => stopPostgres())
    .catch((err) => {
      console.error('[main] postgres teardown failed:', (err as Error).message)
    })
    .finally(() => {
      app.quit()
    })
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
