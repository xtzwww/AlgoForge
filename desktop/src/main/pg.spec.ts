/**
 * Tests for the "is there a database for us to start" decision.
 *
 * This is the decision behind a bug that cost a lot of time: a packaged
 * build used to skip {@link ensurePostgres} outright (`if (app.isPackaged)
 * return true`), on the assumption that a real install is managed outside
 * the app. On this machine nothing else starts PostgreSQL, so the app came
 * up, `/health` answered 200 because it does not touch the database, and
 * every page that queried it answered **500**.
 *
 * What matters to pin is therefore the **guard**, not the process
 * management: whether a packaged build decides it has a database to start.
 * The honest test for "does PostgreSQL actually start" is launching the
 * app, and no unit test should pretend otherwise. So these tests mock the
 * spawn and assert only whether it was reached.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { join } from 'path'

const stubs = vi.hoisted(() => ({
  electronApp: { isPackaged: true },
  existsSync: vi.fn<(p: string) => boolean>(() => false),
  spawn: vi.fn(() => ({ stdout: { on: vi.fn() }, stderr: { on: vi.fn() }, on: vi.fn() })),
  spawnSync: vi.fn(() => ({ status: 0, stdout: '' })),
}))

vi.mock('electron', () => ({ app: stubs.electronApp }))

vi.mock('fs', () => ({
  existsSync: stubs.existsSync,
  unlinkSync: vi.fn(),
}))

vi.mock('child_process', () => ({
  spawn: stubs.spawn,
  spawnSync: stubs.spawnSync,
}))

// A socket that always fails to connect, so `isListening()` is false and
// the start path is actually reached.
vi.mock('net', () => ({
  Socket: class {
    setTimeout = vi.fn()
    // Fails synchronously, so `isListening()` resolves false and the
    // start path is actually reached.
    once = vi.fn((event: string, cb: () => void) => {
      if (event === 'error') cb()
    })
    connect = vi.fn()
    destroy = vi.fn()
  },
}))

import { ensurePostgres, hasLocalPostgres } from './pg'

const HOME = 'D:/pg'
// Built with `join`, not written as literals: on Windows `join` normalises
// to backslashes, so a hand-written `D:/pg/...` would never compare equal
// and the test would pass for the wrong reason (both sides false).
const POSTGRES = join(HOME, 'pgsql', 'bin', 'postgres.exe')
const PG_VERSION = join(HOME, 'data', 'PG_VERSION')

beforeEach(() => {
  stubs.electronApp.isPackaged = true
  stubs.existsSync.mockReset()
  stubs.existsSync.mockReturnValue(false)
  stubs.spawn.mockReset()
  stubs.spawn.mockReturnValue({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
  })
  stubs.spawnSync.mockReset()
  stubs.spawnSync.mockReturnValue({ status: 0, stdout: '' })
  process.env.PG_HOME = HOME
})

describe('本机有没有可启动的 PostgreSQL', () => {
  it('postgres.exe 与 PG_VERSION 都在才算有', () => {
    stubs.existsSync.mockImplementation((p) => p === POSTGRES || p === PG_VERSION)
    expect(hasLocalPostgres()).toBe(true)
  })

  it('只有 postgres.exe、没有初始化过的数据目录时算没有', () => {
    // A zip install that was never `initdb`-ed: starting it would fail,
    // and it must not be mistaken for "the database is managed elsewhere".
    stubs.existsSync.mockImplementation((p) => p === POSTGRES)
    expect(hasLocalPostgres()).toBe(false)
  })

  it('什么都没有时算没有', () => {
    expect(hasLocalPostgres()).toBe(false)
  })
})

describe('打包版是否启动本机数据库', () => {
  it('本机有数据库时必须启动 —— 跳过它就会让每个页面 500', async () => {
    stubs.electronApp.isPackaged = true
    // Everything the start path looks for is present.
    stubs.existsSync.mockReturnValue(true)

    await expect(ensurePostgres()).resolves.toBe(true)

    // The regression this guards: a packaged build that short-circuits on
    // `isPackaged` never calls this, PostgreSQL never runs, and the app
    // looks healthy while every database query fails.
    expect(stubs.spawn).toHaveBeenCalled()
    const [program] = stubs.spawn.mock.calls[0] as unknown as [string, string[]]
    expect(program).toContain('postgres.exe')
  })

  it('本机没有数据库时不碰它，交给外部管理', async () => {
    stubs.electronApp.isPackaged = true
    stubs.existsSync.mockReturnValue(false)

    await expect(ensurePostgres()).resolves.toBe(true)
    expect(stubs.spawn).not.toHaveBeenCalled()
  })
})
