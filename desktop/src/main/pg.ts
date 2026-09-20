/**
 * Dev-only helper: make sure the local PostgreSQL is running.
 *
 * Why this exists
 * ---------------
 * `AGENTS.md` §一 locks the business database to **PostgreSQL** (not
 * SQLite), and `sidecar/.env` points `DATABASE_URL` at it. PostgreSQL
 * does not start by itself, and the resulting failure is nearly
 * impossible to read from the outside:
 *
 *  - `/health` does not touch the database, so it returns 200;
 *  - the main process therefore decides the engine is ready and opens
 *    the window normally;
 *  - every endpoint that *does* query the database (题库 / 错题本 / 教师端)
 *    hangs waiting for a server that is not there;
 *  - the renderer times out and reports **Network Error**, with nothing
 *    in any log to point at.
 *
 * So the app looks healthy and every page is broken. This module removes
 * that state by starting the database before the sidecar is spawned.
 *
 * Dev only, and deliberately so
 * -----------------------------
 * Guarded by `app.isPackaged`: a packaged build talks to a real,
 * separately-managed PostgreSQL and must not go looking for a zip install
 * on the user's disk. The path is overridable with `PG_HOME` and the
 * helper gives up quietly when the install is not there — this is a
 * convenience for a developer's machine, not a deployment mechanism.
 *
 * Two traps, both of which make this fail silently
 * ------------------------------------------------
 * 1. **A stale `postmaster.pid`.** After a hard kill PostgreSQL refuses
 *    to start because it thinks a server is already running, and its log
 *    is empty. It is only safe to delete once we have confirmed there is
 *    no postgres process and nothing listening on 5432.
 * 2. **`COMSPEC`.** HKLM's AutoRun is `chcp 65001` on this machine, so
 *    anything launched through cmd.exe prints an extra line on stdout —
 *    and PostgreSQL self-checks by reading the **first line** of
 *    `postgres -V`, so it refuses to start ("could not locate matching
 *    postgres executable"). `nocmd.exe` is a COMSPEC replacement that
 *    spawns the target directly. Changing the registry needs admin, so
 *    we point the child's COMSPEC at the wrapper instead.
 */

import { spawn, spawnSync, type ChildProcess } from 'child_process'
import * as net from 'net'
import { existsSync, unlinkSync } from 'fs'
import { join } from 'path'
import { app } from 'electron'

const PG_PORT = 5432
/**
 * How long to wait for the database to accept connections.
 *
 * Generous because of crash recovery: after a hard kill PostgreSQL fsyncs
 * the whole data directory before it will answer, and on this machine that
 * has been measured at **well over a minute** (and it gets worse with every
 * hard kill, since each one schedules another full sync). `pg_isready`
 * reports "rejecting connections" the entire time, so the only options are
 * to wait or to give up — hence a real timeout rather than a fixed sleep.
 *
 * The steady state is a couple of seconds: {@link stopPostgres} shuts the
 * server down with `-m fast`, and a clean shutdown needs no recovery.
 */
const READY_TIMEOUT_MS = 180_000

let startedByUs: ChildProcess | null = null

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** Is anything accepting connections on 127.0.0.1:5432? */
function isListening(): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket()
    socket.setTimeout(1000)
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
    socket.connect(PG_PORT, '127.0.0.1')
  })
}

/**
 * Ask `pg_isready`, which is the only honest readiness check here.
 *
 * A raw TCP connect is **wrong** during crash recovery: PostgreSQL binds
 * the port and then refuses connections until it has finished syncing the
 * data directory (up to ~40s after a hard kill). A socket check would
 * report "up" while every query still hangs — which is precisely the
 * silent failure this module exists to prevent. `pg_isready` returns
 * non-zero for "rejecting connections".
 */
function pgIsReady(home: string): boolean {
  const exe = join(home, 'pgsql', 'bin', 'pg_isready.exe')
  if (!existsSync(exe)) return false
  const result = spawnSync(exe, ['-h', '127.0.0.1', '-p', String(PG_PORT)], {
    windowsHide: true,
    stdio: 'ignore',
  })
  return result.status === 0
}

function postgresIsRunning(): boolean {
  if (process.platform !== 'win32') return false
  const result = spawnSync('tasklist', ['/FI', 'IMAGENAME eq postgres.exe'], {
    windowsHide: true,
    encoding: 'utf8',
  })
  return (result.stdout ?? '').toLowerCase().includes('postgres.exe')
}

/**
 * Start PostgreSQL if it is not already up. Resolves with whether a
 * database is reachable afterwards — never rejects, because a missing
 * local install is a warning, not a reason to abort the launch.
 */
export async function ensurePostgres(onProgress?: (message: string) => void): Promise<boolean> {
  // Production: the database is managed outside the app. See the header.
  if (app.isPackaged) return true

  if (await isListening()) {
    console.log('[pg] already listening on 5432')
    return true
  }

  const home = process.env.PG_HOME ?? 'D:/pg'
  const data = join(home, 'data')
  const bin = join(home, 'pgsql', 'bin')
  const nocmd = join(home, 'nocmd.exe')
  const postgres = join(bin, 'postgres.exe')

  if (!existsSync(postgres) || !existsSync(join(data, 'PG_VERSION'))) {
    console.warn(
      `[pg] no local PostgreSQL at ${home} (set PG_HOME to override); ` +
        'the app will start but every database query will fail'
    )
    return false
  }

  if (existsSync(join(data, 'postmaster.pid')) && !postgresIsRunning()) {
    console.log('[pg] removing stale postmaster.pid (no postgres process)')
    try {
      unlinkSync(join(data, 'postmaster.pid'))
    } catch {
      /* gone already */
    }
  }

  console.log(`[pg] starting PostgreSQL from ${home}`)
  const child = spawn(postgres, ['-D', data], {
    cwd: home,
    env: { ...process.env, COMSPEC: nocmd },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })
  startedByUs = child
  child.stdout?.on('data', (chunk) => process.stdout.write(`[pg] ${chunk.toString()}`))
  child.stderr?.on('data', (chunk) => process.stderr.write(`[pg] ${chunk.toString()}`))
  child.on('exit', (code, signal) => {
    console.warn(`[pg] exited (code=${code}, signal=${signal})`)
    if (startedByUs === child) startedByUs = null
  })

  const startedAt = Date.now()
  const deadline = startedAt + READY_TIMEOUT_MS
  let lastReport = 0
  while (Date.now() < deadline) {
    await delay(2000)
    // pg_isready, not a socket probe — see its docstring for why.
    if (pgIsReady(home)) {
      console.log(`[pg] ready after ${Math.round((Date.now() - startedAt) / 1000)}s`)
      return true
    }
    const waited = Math.round((Date.now() - startedAt) / 1000)
    if (waited - lastReport >= 10) {
      lastReport = waited
      // Say it out loud: a silent splash for three minutes reads as a hang,
      // and "the database is recovering" is something the user can wait for.
      const message = `数据库恢复中…（已等待 ${waited} 秒）`
      console.log(`[pg] ${message}`)
      onProgress?.(message)
    }
  }

  console.error('[pg] did not become ready in time; the app will have no database')
  return false
}

/**
 * Stop PostgreSQL, but only if {@link ensurePostgres} started it.
 *
 * Uses `-m fast` so the next launch does not have to run crash recovery,
 * which is what makes startup take 40 seconds instead of two.
 */
export async function stopPostgres(): Promise<void> {
  if (!startedByUs) return
  const home = process.env.PG_HOME ?? 'D:/pg'
  const pgCtl = join(home, 'pgsql', 'bin', 'pg_ctl.exe')
  if (!existsSync(pgCtl)) return

  console.log('[pg] stopping PostgreSQL (-m fast)')
  spawnSync(pgCtl, ['-D', join(home, 'data'), '-m', 'fast', 'stop'], {
    cwd: home,
    env: { ...process.env, COMSPEC: join(home, 'nocmd.exe') },
    windowsHide: true,
    stdio: 'ignore',
  })
  startedByUs = null
}
