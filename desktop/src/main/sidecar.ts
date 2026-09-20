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

/**
 * How many times to restart the sidecar after it dies on its own.
 *
 * Bounded, and the bound is the point: a sidecar that cannot start at all
 * (a missing dependency, a broken venv) would otherwise be respawned in a
 * tight loop forever, which turns one log line into a machine that cannot
 * be used. Five attempts over ~15s of backoff is enough to ride out the
 * thing that actually happens — the process being killed under memory
 * pressure — without hiding a permanent failure.
 */
const MAX_RESTARTS = 5
const RESTART_BACKOFF_MS = [500, 1000, 2000, 4000, 8000]

let sidecarProcess: ChildProcess | null = null
let sidecarInfo: SidecarInfo | null = null

/** The pid of the process tree currently being torn down (if any). */
let shuttingDownPid: number | null = null

/**
 * Set by {@link stopSidecar} so the exit handler can tell "we killed it"
 * from "it died". Without this the teardown path would look like a crash
 * and schedule a restart of a process that is supposed to be going away.
 */
let intentionallyStopped = false

/** Restart bookkeeping. */
let restartAttempts = 0
let restartTimer: NodeJS.Timeout | null = null

/**
 * Listeners for connection-info changes.
 *
 * A restarted sidecar gets a **new port and a new token** — both are
 * generated fresh on every spawn. So the renderer cannot simply retry the
 * old URL: it has to be told the new one, or every request keeps failing
 * against a dead port while the engine is in fact running again. That is
 * exactly the failure this fixes.
 */
type InfoListener = (info: SidecarInfo | null) => void
const infoListeners: InfoListener[] = []

/** Subscribe to connection-info changes. Returns an unsubscribe function. */
export function onSidecarInfoChanged(listener: InfoListener): () => void {
  infoListeners.push(listener)
  return () => {
    const index = infoListeners.indexOf(listener)
    if (index >= 0) infoListeners.splice(index, 1)
  }
}

function emitInfo(info: SidecarInfo | null): void {
  for (const listener of [...infoListeners]) {
    try {
      listener(info)
    } catch (err) {
      console.error('[sidecar] info listener threw:', (err as Error).message)
    }
  }
}

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
/**
 * What to spawn, and with which leading arguments.
 *
 * Dev and packaged builds differ in the *program* but not in the tail
 * arguments: both get `--host` / `--port` appended by `startSidecar`, and
 * both take the token from the environment. Keeping that symmetry is what
 * lets the packaged build be a drop-in: `packaging/sidecar_main.py`
 * deliberately speaks uvicorn's flag spellings for exactly this reason.
 *
 * The packaged path is a **directory**, not a single file: the sidecar is
 * frozen with PyInstaller `onedir` (AGENTS.md §一 工具链锁定), so
 * `resources/sidecar/` holds `algoforge-sidecar/` with the exe and its
 * `_internal` dependencies inside.
 */
function resolveSidecarLaunch(): { program: string; args: string[]; cwd: string } {
  const isDev = !app.isPackaged

  if (isDev) {
    // app.getAppPath() points to desktop/ in dev mode
    const projectRoot = join(app.getAppPath(), '..')
    return {
      program: join(projectRoot, 'sidecar', '.venv', 'Scripts', 'python.exe'),
      args: ['-m', 'uvicorn', 'app.main:app'],
      cwd: join(projectRoot, 'sidecar'),
    }
  }

  // Production (阶段 7 · 打包): PyInstaller onedir 产物整目录随包发布。
  const exeName = process.platform === 'win32' ? 'algoforge-sidecar.exe' : 'algoforge-sidecar'
  return {
    program: join(process.resourcesPath, 'sidecar', 'algoforge-sidecar', exeName),
    args: [],
    cwd: join(process.resourcesPath, 'sidecar'),
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
 * Spawn one sidecar process and wait for it to answer.
 *
 * Separate from {@link startSidecar} because a restart must be able to
 * spawn again even though `sidecarInfo` is currently null — the public
 * entry point's "already running, return it" shortcut is right for
 * callers and wrong for the restart path.
 *
 * Every spawn mints a **new port and a new token**, so callers holding
 * the previous `SidecarInfo` must treat it as expired the moment this
 * resolves. {@link onSidecarInfoChanged} is how they find out.
 */
async function spawnSidecar(): Promise<SidecarInfo> {
  // A previous stopSidecar() disarmed the restart machinery; starting
  // again re-arms it. Without this reset, an app that stopped and then
  // restarted its sidecar would never recover from a later crash.
  intentionallyStopped = false

  const port = await getAvailablePort()
  const token = crypto.randomBytes(32).toString('hex')
  const { program, args, cwd } = resolveSidecarLaunch()

  console.log(`[sidecar] starting on port ${port}`)
  console.log(`[sidecar] program: ${program}`)
  console.log(`[sidecar] cwd: ${cwd}`)

  const isWindows = process.platform === 'win32'

  sidecarProcess = spawn(
    program,
    [...args, '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd,
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
    const ours = sidecarProcess
    sidecarProcess = null
    sidecarInfo = null

    if (intentionallyStopped) {
      console.log(`[sidecar] exited (code=${code}, signal=${signal})`)
      return
    }

    // It died on its own. This is the whole reason for the restart
    // machinery: without it the app stays up with a dead engine and
    // every page reports "Network Error" until the user quits.
    console.warn(
      `[sidecar] died unexpectedly (code=${code}, signal=${signal}, pid=${ours?.pid}); ` +
        'notifying renderer and scheduling a restart'
    )
    emitInfo(null)
    scheduleRestart()
  })
  sidecarProcess.on('error', (err) => {
    console.error('[sidecar] failed to spawn:', err.message)
    sidecarProcess = null
    sidecarInfo = null
    if (!intentionallyStopped) {
      emitInfo(null)
      scheduleRestart()
    }
  })

  const url = `http://127.0.0.1:${port}`
  try {
    await waitForHealth(`${url}/health`)
  } catch (err) {
    console.error('[sidecar] health check failed:', (err as Error).message)
    // Kill this attempt without setting `intentionallyStopped`: the
    // reason we are here may be transient (a slow first import), and a
    // restart must still be allowed to try again.
    await killCurrent()
    if (!intentionallyStopped) scheduleRestart()
    throw err
  }

  restartAttempts = 0
  sidecarInfo = { port, token, url }
  console.log(`[sidecar] ready at ${url} (token=${token.slice(0, 8)}...)`)
  emitInfo(sidecarInfo)
  return sidecarInfo
}

/**
 * Tear down whatever process is currently spawned, without declaring the
 * sidecar permanently stopped.
 *
 * Used when one spawn attempt fails: the process has to go, but the
 * restart machinery must stay armed.
 */
async function killCurrent(): Promise<void> {
  const proc = sidecarProcess
  const pid = proc?.pid
  sidecarProcess = null
  sidecarInfo = null
  if (!proc || pid === undefined || proc.exitCode !== null || proc.signalCode !== null) {
    return
  }
  shuttingDownPid = pid
  const exited = new Promise<void>((resolve) => {
    proc.once('exit', () => resolve())
  })
  killProcessTreeSync(pid, false)
  const timedOut = await Promise.race([
    exited.then(() => false),
    delay(GRACEFUL_SHUTDOWN_MS).then(() => true),
  ])
  if (timedOut) {
    killProcessTreeSync(pid, true)
    await Promise.race([exited, delay(FORCE_SHUTDOWN_MS)])
  }
  shuttingDownPid = null
}

/**
 * Bring the sidecar back after it died, with backoff.
 *
 * Attempts are bounded by {@link MAX_RESTARTS}: past that the failure is
 * treated as permanent and the UI keeps its error state, which is the
 * honest answer. Retrying forever would keep the CPU busy and the user
 * none the wiser.
 */
function scheduleRestart(): void {
  if (intentionallyStopped || restartTimer) return
  if (restartAttempts >= MAX_RESTARTS) {
    console.error(
      `[sidecar] giving up after ${restartAttempts} restart attempts; ` +
        'the analysis engine will stay unavailable until the app is restarted'
    )
    return
  }

  const waitMs = RESTART_BACKOFF_MS[Math.min(restartAttempts, RESTART_BACKOFF_MS.length - 1)]
  restartAttempts += 1
  console.log(`[sidecar] restart attempt ${restartAttempts}/${MAX_RESTARTS} in ${waitMs}ms`)

  restartTimer = setTimeout(() => {
    restartTimer = null
    void spawnSidecar()
      .then((info) => console.log(`[sidecar] restarted at ${info.url}`))
      .catch((err) => {
        console.error('[sidecar] restart failed:', (err as Error).message)
        scheduleRestart()
      })
  }, waitMs)
  restartTimer.unref?.()
}

/**
 * Start the sidecar process. Returns immediately if already running.
 */
export async function startSidecar(): Promise<SidecarInfo> {
  if (sidecarInfo) return sidecarInfo
  return spawnSidecar()
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

  // Disarm the restart machinery *first*. The exits this function causes
  // must never be mistaken for a crash — otherwise quitting the app would
  // respawn the sidecar we are in the middle of tearing down.
  intentionallyStopped = true
  if (restartTimer) {
    clearTimeout(restartTimer)
    restartTimer = null
  }

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
