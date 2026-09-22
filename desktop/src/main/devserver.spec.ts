/**
 * Tests for {@link import('./devserver')} (PU-8).
 *
 * The module exists to stop a specific, very confusing failure: a
 * leftover dev server holds port 5173, `electron-vite` silently reuses
 * it, the new Electron main process dies during init, and the user sees
 * the *old* window with no engine behind it. Measured on 2026-09-22.
 *
 * Two of its behaviours are load-bearing and fail quietly if wrong:
 *
 *  - **Identifying the listener's pid** from `netstat` output. The
 *    column layout differs between Windows builds and locales, so the
 *    parser matches on structure (`LISTENING` + a local address ending
 *    in `:port`) and takes the last column. A parser that grabbed the
 *    wrong column would kill an unrelated process, or none at all.
 *  - **Never killing this process.** If the listener were somehow our
 *    own pid, the correct action is to carry on — not to commit suicide
 *    a second before the app finishes starting.
 *
 * Process management itself (`spawnSync('taskkill', …)`) is *not* tested
 * here: its real behaviour is only observable on a machine with a stale
 * listener, which is the acceptance check the user runs by launching the
 * app. What is testable is the decision of *what* to kill.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const stubs = vi.hoisted(() => ({
  spawnSync: vi.fn(),
  /** Drives the `net`-based socket probe. */
  listening: { value: false }
}))

vi.mock('electron', () => ({
  app: { isPackaged: false }
}))

vi.mock('child_process', () => ({
  spawnSync: stubs.spawnSync
}))

vi.mock('net', () => {
  class FakeSocket {
    private handlers: Record<string, ((...args: unknown[]) => void)[]> = {}
    setTimeout(): this {
      return this
    }
    once(event: string, cb: (...args: unknown[]) => void): this {
      ;(this.handlers[event] ??= []).push(cb)
      return this
    }
    destroy(): void {
      /* nothing to release */
    }
    connect(): void {
      // The connect outcome is what the module reasons about; the timer
      // fires synchronously here so the promise settles without a real
      // socket or a real wait.
      queueMicrotask(() => {
        const event = stubs.listening.value ? 'connect' : 'error'
        for (const cb of this.handlers[event] ?? []) cb()
      })
    }
  }
  return { Socket: FakeSocket, createServer: vi.fn() }
})

import { reclaimDevPort } from './devserver'

/** Shape of one `netstat -ano -p TCP` line. */
function netstatLine(port: number, pid: number, state = 'LISTENING'): string {
  return `  TCP    127.0.0.1:${port}       0.0.0.0:0              ${state}       ${pid}`
}

beforeEach(() => {
  stubs.spawnSync.mockReset()
  stubs.listening.value = false
})

describe('dev server 端口回收', () => {
  it('端口空闲时什么都不做，也不调用 taskkill', async () => {
    stubs.listening.value = false
    stubs.spawnSync.mockReturnValue({ status: 0, stdout: '', error: undefined })

    const killed = await reclaimDevPort()

    expect(killed).toBeNull()
    // The common case must stay cheap — no netstat call, no kill.
    expect(stubs.spawnSync).not.toHaveBeenCalled()
  })

  it('端口被占用时杀掉占用者，并返回它的 pid', async () => {
    stubs.listening.value = true
    stubs.spawnSync.mockImplementation((cmd: string) => {
      if (cmd === 'netstat') {
        return { status: 0, stdout: netstatLine(5173, 9876), error: undefined }
      }
      // taskkill: the follow-up probe must now see the port free.
      stubs.listening.value = false
      return { status: 0, stdout: '', error: undefined }
    })

    const killed = await reclaimDevPort()

    expect(killed).toBe(9876)
    const killCall = stubs.spawnSync.mock.calls.find((c) => c[0] === 'taskkill')
    expect(killCall).toBeDefined()
    expect(killCall?.[1]).toEqual(['/PID', '9876', '/T', '/F'])
  })

  it('从 netstat 的多行输出里挑出监听目标端口的那个 pid', async () => {
    stubs.listening.value = true
    stubs.spawnSync.mockImplementation((cmd: string) => {
      if (cmd === 'netstat') {
        return {
          status: 0,
          stdout: [
            netstatLine(5173, 1111),
            netstatLine(5432, 2222),
            // Same port, foreign address — must not be mistaken for the
            // listener even though it appears in the same list.
            `  TCP    127.0.0.1:5173   127.0.0.1:60000    ESTABLISHED   3333`
          ].join('\r\n'),
          error: undefined
        }
      }
      stubs.listening.value = false
      return { status: 0, stdout: '', error: undefined }
    })

    expect(await reclaimDevPort()).toBe(1111)
  })

  it('监听者是本进程时不动手', async () => {
    stubs.listening.value = true
    stubs.spawnSync.mockImplementation((cmd: string) => {
      if (cmd === 'netstat') {
        return { status: 0, stdout: netstatLine(5173, process.pid), error: undefined }
      }
      throw new Error('taskkill must not be called for our own pid')
    })

    const killed = await reclaimDevPort()

    expect(killed).toBeNull()
    expect(stubs.spawnSync.mock.calls.some((c) => c[0] === 'taskkill')).toBe(false)
  })

  it('认不出占用者时宁可不杀，也不误杀', async () => {
    stubs.listening.value = true
    stubs.spawnSync.mockImplementation((cmd: string) => {
      if (cmd === 'netstat') {
        // No LISTENING line at all — e.g. an unparseable locale, or the
        // process exited between the probe and the listing.
        return { status: 0, stdout: 'garbage output', error: undefined }
      }
      throw new Error('taskkill must not be called without a known pid')
    })

    expect(await reclaimDevPort()).toBeNull()
  })

  it('杀掉之后端口仍未释放时如实返回 null，不谎报成功', async () => {
    stubs.listening.value = true
    stubs.spawnSync.mockImplementation((cmd: string) => {
      if (cmd === 'netstat') {
        return { status: 0, stdout: netstatLine(5173, 5555), error: undefined }
      }
      // taskkill succeeds, but the socket stays bound — a real case when
      // the owner respawns or the OS has not released it yet.
      return { status: 0, stdout: '', error: undefined }
    })

    // Bounded wait, so this must not hang the test run.
    const killed = await reclaimDevPort()
    expect(killed).toBeNull()
  }, 15000)
})
