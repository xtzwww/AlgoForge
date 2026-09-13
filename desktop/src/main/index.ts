import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { startSidecar, stopSidecar, getSidecarInfo, installExitHook } from './sidecar'

// PU-1: catch abnormal exits (crash, uncaught exception) that would
// otherwise skip the orderly teardown and leave uvicorn running.
installExitHook()

/**
 * PU-1: guards the two-pass quit sequence below. `before-quit` fires,
 * we preventDefault to run async cleanup, then call app.quit() again —
 * this flag stops the second pass from re-entering cleanup.
 */
let teardownStarted = false

function createWindow(): void {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    width: 900,
    height: 670,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
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
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(async () => {
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC: expose sidecar connection info to renderer
  ipcMain.handle('sidecar:info', () => getSidecarInfo())

  // Start the Python sidecar before creating the window
  try {
    const info = await startSidecar()
    console.log(`[main] sidecar ready: ${info.url} (health check passed)`)
  } catch (err) {
    console.error('[main] failed to start sidecar:', (err as Error).message)
  }

  createWindow()

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

  stopSidecar()
    .catch((err) => {
      console.error('[main] sidecar teardown failed:', (err as Error).message)
    })
    .finally(() => {
      app.quit()
    })
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
