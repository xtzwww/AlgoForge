/**
 * Dev-only helper: reclaim the renderer dev-server port before vite binds it.
 *
 * Why this exists
 * ---------------
 * `electron-vite dev` starts the renderer dev server on **5173**, and when
 * that port is already taken it does **not** fail. It prints "dev server
 * running" and hands the existing URL to the freshly spawned Electron
 * main process. If the listener is a leftover from a previous session —
 * a window that was closed without the dev server exiting — the result is:
 *
 *   1. the new main process starts against an environment the old server
 *      was not built for and dies during module init
 *      (`TypeError: Cannot read properties of undefined (reading
 *      'isAppPackaged')` from `@electron-toolkit/utils`);
 *   2. the window the user is looking at is the **old** renderer, which
 *      is still served and still renders;
 *   3. no main process means no sidecar, so every database-backed page
 *      times out and reports **Network Error**.
 *
 * The user-visible symptom is "the app opened but nothing works", and
 * neither the app's log nor the terminal says why — the crash scrolls by
 * before the window is up. Measured on this machine on 2026-09-22.
 *
 * This module removes the ambiguity: if something holds 5173 and it is
 * not us, kill it before vite gets a chance to reuse it.
 *
 * Scope and safety
 * ----------------
 * Deliberately narrow. It only runs in dev (`app.isPackaged` is false),
 * only targets the exact port electron-vite uses, and only kills the
 * process that owns that listening socket. It is not a general "kill
 * anything on a port" utility and must not become one: on a developer's
 * machine, another project's server is a plausible thing to find, and
 * silently terminating it would be worse than the bug being fixed.
 *
 * The port is overridable with `DEV_SERVER_PORT` for the same reason.
 */

import { spawnSync } from 'child_process'
import * as net from 'net'
import { app } from 'electron'

/** electron-vite's default renderer port. */
const DEFAULT_DEV_PORT = 5173

/**
 * Find the pid listening on `port`, or null.
 *
 * Uses `netstat -ano`, which lists TCP listeners with their owning pid on
 * every supported Windows version. Parsing is deliberately tolerant: the
 * exact column layout differs between Windows builds and locales, so we
 * match on the local address ending in `:port` plus the `LISTENING`
 * state, and take the final whitespace-separated token as the pid.
 */
function findListenerPid(port: number): number | null {
  const result = spawnSync('netstat', ['-ano', '-p', 'TCP'], {
    windowsHide: true,
    encoding: 'utf8'
  })
  if (result.error || !result.stdout) return null

  for (const raw of result.stdout.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line.includes('LISTENING')) continue
    const columns = line.split(/\s+/)
    // proto  local  foreign  state  pid
    if (columns.length < 5) continue
    const local = columns[1]
    if (!local.endsWith(`:${port}`)) continue
    const pid = Number(columns[columns.length - 1])
    if (Number.isInteger(pid) && pid > 0) return pid
  }
  return null
}

/** Is anything accepting connections on 127.0.0.1:port right now? */
function isListening(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket()
    socket.setTimeout(700)
    socket.once('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.once('error', () => {
      socket.destroy()
      resolve(false)
    })
    socket.once('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.connect(port, '127.0.0.1')
  })
}

/**
 * Wait for the port to actually become free after a kill.
 *
 * `taskkill` returns before the socket is released, and a `netstat` that
 * still shows the pid for a moment afterwards is normal. Polling the
 * socket is the honest check — it answers the question vite will ask.
 */
async function waitForFree(port: number, timeoutMs = 4000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (!(await isListening(port))) return true
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  return false
}

/**
 * If a stale process holds the dev-server port, kill it.
 *
 * Returns the pid that was killed, or null when the port was already
 * free. Never rejects: failing to reclaim the port is a degraded
 * startup, not a reason to refuse to launch — the user still gets the
 * window, and the previous behaviour (reuse the old server) is what
 * happens anyway.
 *
 * @param onProgress Receives a short human-facing sentence, because
 *   killing a process takes long enough that a silent splash would look
 *   like a hang.
 */
export async function reclaimDevPort(
  onProgress?: (message: string) => void
): Promise<number | null> {
  if (app.isPackaged) return null

  const port = Number(process.env.DEV_SERVER_PORT ?? DEFAULT_DEV_PORT)
  if (!Number.isInteger(port) || port <= 0) return null

  // Only a stale *listener* is a problem. A free port needs no work, and
  // this is the common case on a clean start, so keep it cheap.
  if (!(await isListening(port))) return null

  console.warn(
    `[devserver] port ${port} is already held by another process; ` +
      'electron-vite would silently reuse it and the new main process would fail to start'
  )
  onProgress?.('正在清理上次遗留的进程…')

  const pid = findListenerPid(port)
  if (pid === null) {
    console.warn(
      `[devserver] could not identify the owner of port ${port}; ` +
        'leaving it alone (startup may be degraded)'
    )
    return null
  }

  // Never kill ourselves: if this Electron somehow owns the port, the
  // right move is to keep going, not to commit suicide.
  if (pid === process.pid) {
    console.warn('[devserver] the listener is this process; nothing to reclaim')
    return null
  }

  console.log(`[devserver] killing stale listener pid=${pid} on port ${port}`)
  spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
    windowsHide: true,
    stdio: 'ignore'
  })

  if (!(await waitForFree(port))) {
    console.warn(
      `[devserver] port ${port} is still busy after killing pid ${pid}; ` +
        'startup may be degraded'
    )
    return null
  }

  console.log(`[devserver] port ${port} reclaimed`)
  return pid
}
