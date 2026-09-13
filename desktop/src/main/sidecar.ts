/**
 * Sidecar process manager.
 *
 * Spawns the Python FastAPI sidecar on a random port with a freshly
 * generated auth token, waits for the /health endpoint to respond, and
 * exposes the connection info to the renderer via the preload script.
 *
 * PU-1 changes:
 *  - Spawned with `windowsHide: true` so no console window flashes.
 *  - Shutdown kills the whole process tree. uvicorn spawns worker
 *    processes, so the old `proc.kill()` left orphans behind.
 *  - `stopSidecar()` is now async and resolves only once the process is
 *    really gone (or was force-killed), so callers can await it.
 *  - A synchronous last-resort kill is installed on `process.exit`.
 */

import { spawn, spawnSync, type ChildProcess } from 'child_process'
import * as net from 'net'
import * as crypto from 'crypto'
import { join } from 'path'
import { app } from 'electron'

export interface SidecarInfo {
  port: number
  token: string
  url: string
}

/** How long to wait for a graceful exit before force-killing (ms). */
const GRACEFUL_SHUTDOWN_MS = 4000
/** How long to wait after a force kill before giving up (ms). */
const FORCE_SHUTDOWN_MS = 2000

let sidecarProcess: ChildProcess | null = null
let sidecarInfo: SidecarInfo | null = null

/** The pid of the process tree currently being torn down (if any). */
let shuttingDownPid: number | null = null

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Ask the OS for a free TCP port on 127.0.0.1.
 */
function getAvailablePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address && typeof address === 'object') {
        const port = address.port
        server.close(() => resolve(port))
      } else {
        server.close()
        reject(new Error('Failed to obtain an available port'))
      }
    })
  })
}

/**
 * Poll the sidecar /health endpoint until it returns 200 or timeout.
 */
function waitForHealth(url: string, timeoutMs = 15000): Promise<void> {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    const check = (): void => {
      fetch(url)
        .then((res) => {
          if (res.ok) {
            resolve()
          } else if (Date.now() - start < timeoutMs) {
            setTimeout(check, 500)
          } else {
            reject(new Error(`Sidecar health check timed out after ${timeoutMs}ms`))
          }
        })
        .catch(() => {
          if (Date.now() - start < timeoutMs) {
            setTimeout(check, 500)
          } else {
            reject(new Error(`Sidecar health check timed out after ${timeoutMs}ms`))
          }
        })
    }
    check()
  })
}

/**
 * Resolve the Python interpreter and sidecar working directory.
 *
 * Development: <project>/sidecar/.venv/Scripts/python.exe
 * Production:  <resources>/sidecar/python.exe  (PyInstaller, S10)
 */
function resolvePythonPaths(): { pythonPath: string; sidecarDir: string } {
  const isDev = !app.isPackaged

  if (isDev) {
    // app.getAppPath() points to desktop/ in dev mode
    const projectRoot = join(app.getAppPath(), '..')
    return {
      pythonPath: join(projectRoot, 'sidecar', '.venv', 'Scripts', 'python.exe'),
      sidecarDir: join(projectRoot, 'sidecar'),
    }
  }

  // Production (S10 packaging) — placeholder path
  return {
    pythonPath: join(process.resourcesPath, 'sidecar', 'python.exe'),
    sidecarDir: join(process.resourcesPath, 'sidecar'),
  }
}

/**
 * Kill a process **and its children**.
 *
 * On Windows `taskkill /T` walks the tree; a plain `child.kill()` only
 * signals the direct child, which is why uvicorn workers used to
 * survive. On POSIX the sidecar is spawned `detached` so it leads its
 * own process group, letting us signal the whole group with a negative
 * pid.
 *
 * Synchronous by design: this is also called from `process.on('exit')`,
 * where no async work can run.
 *
 * @param pid   Root pid of the process tree.
 * @param force Use SIGKILL / `taskkill /F` instead of a graceful signal.
 */
function killProcessTreeSync(pid: number, force: boolean): void {
  if (process.platform === 'win32') {
    const args = ['/PID', String(pid), '/T']
    if (force) args.push('/F')
    try {
      spawnSync('taskkill', args, { windowsHide: true, stdio: 'ignore' })
    } catch {
      // taskkill missing or access denied — fall back to a plain kill.
      try {
        process.kill(pid)
      } catch {
        /* already gone */
      }
    }
    return
  }

  const signal: NodeJS.Signals = force ? 'SIGKILL' : 'SIGTERM'
  try {
    // Negative pid targets the process group created by `detached: true`.
    process.kill(-pid, signal)
  } catch {
    // No process group (e.g. spawn failed) — fall back to the direct pid.
    try {
      process.kill(pid, signal)
    } catch {
      /* already gone */
    }
  }
}

/**
 * Start the sidecar process. Returns immediately if already running.
 */
export async function startSidecar(): Promise<SidecarInfo> {
  if (sidecarInfo) return sidecarInfo

  const port = await getAvailablePort()
  const token = crypto.randomBytes(32).toString('hex')
  const { pythonPath, sidecarDir } = resolvePythonPaths()

  console.log(`[sidecar] starting on port ${port}`)
  console.log(`[sidecar] python: ${pythonPath}`)
  console.log(`[sidecar] cwd: ${sidecarDir}`)

  const isWindows = process.platform === 'win32'

  sidecarProcess = spawn(
    pythonPath,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: sidecarDir,
      env: {
        ...process.env,
        SIDECAR_PORT: String(port),
        SIDECAR_TOKEN: token,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      // PU-1: never flash a console window. Without this, spawning a
      // console-subsystem exe (python.exe) briefly opens a black box.
      windowsHide: true,
      // PU-1: on POSIX this makes the child a process-group leader so
      // `kill(-pid)` can take uvicorn's workers down with it. On Windows
      // `detached` would allocate a new console — the opposite of what
      // we want; `taskkill /T` covers the tree there instead.
      detached: !isWindows,
    },
  )

  sidecarProcess.stdout?.on('data', (data) => {
    process.stdout.write(`[sidecar] ${data.toString()}`)
  })
  sidecarProcess.stderr?.on('data', (data) => {
    process.stderr.write(`[sidecar] ${data.toString()}`)
  })
  sidecarProcess.on('exit', (code, signal) => {
    // Ignore exits caused by our own teardown — stopSidecar() owns the
    // state machine in that case and clears the handles itself.
    if (shuttingDownPid !== null && sidecarProcess?.pid === shuttingDownPid) {
      return
    }
    console.log(`[sidecar] exited (code=${code}, signal=${signal})`)
    sidecarProcess = null
    sidecarInfo = null
  })
  sidecarProcess.on('error', (err) => {
    console.error('[sidecar] failed to spawn:', err.message)
    sidecarProcess = null
    sidecarInfo = null
  })

  const url = `http://127.0.0.1:${port}`
  try {
    await waitForHealth(`${url}/health`)
  } catch (err) {
    console.error('[sidecar] health check failed:', (err as Error).message)
    await stopSidecar()
    throw err
  }

  sidecarInfo = { port, token, url }
  console.log(`[sidecar] ready at ${url} (token=${token.slice(0, 8)}...)`)
  return sidecarInfo
}

/**
 * Stop the sidecar and its whole process tree.
 *
 * Sends a graceful signal first, waits up to
 * :data:`GRACEFUL_SHUTDOWN_MS`, then force-kills whatever is left and
 * waits up to :data:`FORCE_SHUTDOWN_MS`. Resolves once the tree is gone
 * or the force-kill window has elapsed — it never rejects, so callers
 * can `await` it in a shutdown path without guarding.
 */
export async function stopSidecar(): Promise<void> {
  const proc = sidecarProcess
  const pid = proc?.pid

  // Nothing to do: never started, already exited, or no usable pid.
  if (!proc || pid === undefined || proc.exitCode !== null || proc.signalCode !== null) {
    sidecarProcess = null
    sidecarInfo = null
    return
  }

  console.log(`[sidecar] stopping (pid=${pid})...`)
  shuttingDownPid = pid

  const exited = new Promise<void>((resolve) => {
    if (proc.exitCode !== null || proc.signalCode !== null) {
      resolve()
      return
    }
    proc.once('exit', () => resolve())
  })

  // 1. Graceful: SIGTERM / taskkill without /F.
  killProcessTreeSync(pid, false)

  const gracefulTimedOut = await Promise.race([
    exited.then(() => false),
    delay(GRACEFUL_SHUTDOWN_MS).then(() => true),
  ])

  // 2. Force: SIGKILL / taskkill /F.
  if (gracefulTimedOut) {
    console.warn(`[sidecar] graceful shutdown timed out, force-killing tree (pid=${pid})`)
    killProcessTreeSync(pid, true)
    await Promise.race([exited, delay(FORCE_SHUTDOWN_MS)])
  }

  sidecarProcess = null
  sidecarInfo = null
  shuttingDownPid = null
  console.log('[sidecar] stopped')
}

/**
 * Last-resort synchronous cleanup.
 *
 * Registered on `process.exit`, where async work is impossible. By this
 * point `stopSidecar()` should have run; this only catches abnormal
 * exits (uncaught exception, crash) that skip the normal teardown.
 */
export function installExitHook(): void {
  process.on('exit', () => {
    const pid = sidecarProcess?.pid
    if (pid !== undefined) {
      killProcessTreeSync(pid, true)
    }
  })
}

/**
 * Get the current sidecar connection info (null if not running).
 */
export function getSidecarInfo(): SidecarInfo | null {
  return sidecarInfo
}
