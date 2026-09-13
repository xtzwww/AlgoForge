/**
 * Sidecar process manager.
 *
 * Spawns the Python FastAPI sidecar on a random port with a freshly
 * generated auth token, waits for the /health endpoint to respond, and
 * exposes the connection info to the renderer via the preload script.
 */

import { spawn, type ChildProcess } from 'child_process'
import * as net from 'net'
import * as crypto from 'crypto'
import { join } from 'path'
import { app } from 'electron'

export interface SidecarInfo {
  port: number
  token: string
  url: string
}

let sidecarProcess: ChildProcess | null = null
let sidecarInfo: SidecarInfo | null = null

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
    },
  )

  sidecarProcess.stdout?.on('data', (data) => {
    process.stdout.write(`[sidecar] ${data.toString()}`)
  })
  sidecarProcess.stderr?.on('data', (data) => {
    process.stderr.write(`[sidecar] ${data.toString()}`)
  })
  sidecarProcess.on('exit', (code, signal) => {
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
    stopSidecar()
    throw err
  }

  sidecarInfo = { port, token, url }
  console.log(`[sidecar] ready at ${url} (token=${token.slice(0, 8)}...)`)
  return sidecarInfo
}

/**
 * Kill the sidecar process if running.
 */
export function stopSidecar(): void {
  if (sidecarProcess) {
    console.log('[sidecar] stopping...')
    sidecarProcess.kill()
    sidecarProcess = null
    sidecarInfo = null
  }
}

/**
 * Get the current sidecar connection info (null if not running).
 */
export function getSidecarInfo(): SidecarInfo | null {
  return sidecarInfo
}
