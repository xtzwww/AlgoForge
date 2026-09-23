/**
 * Tests for the sidecar's failure reporting (PU-8).
 *
 * What is at risk here is not "does the sidecar start" — no unit test can
 * answer that, and the honest check is launching the app. It is the
 * **diagnostic path**: when the sidecar does *not* start, the only thing
 * between the user and "它不工作" is the sentence this module produces.
 *
 * So the tests drive the recording and selection logic directly rather
 * than through `startSidecar()`. That is not a shortcut for convenience:
 * a real failed start only reports after `waitForHealth` times out
 * (15 seconds), so a test that went through the front door would spend
 * its budget waiting and observe nothing extra. The line-selection logic
 * — which is where the silent failures live — is reachable on its own.
 *
 * Three behaviours are pinned, all of which are wrong invisibly:
 *  - picking the **last** line of a traceback, where Python puts the
 *    cause, rather than the first;
 *  - skipping uvicorn's shutdown noise, which arrives *after* the cause
 *    and would otherwise be quoted as the reason;
 *  - bounding the tail, so an app left running does not accumulate an
 *    unbounded array of logs.
 *
 * `electron` is stubbed because `sidecar.ts` imports `app` at load time
 * and `app` is `undefined` outside an Electron process. The stub is as
 * small as it can be: this file must not pretend to test process
 * management.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { join } from 'path'

// `vi.mock` factories are hoisted above these imports, so anything they
// close over has to be created in `vi.hoisted` rather than as a normal
// top-level binding.
const stubs = vi.hoisted(() => ({
  spawn: vi.fn(),
  electronApp: {
    isPackaged: false,
    getAppPath: () => '/tmp/app',
    getPath: () => '/tmp/userData'
  },
  existsSync: vi.fn<(p: string) => boolean>(() => false),
  copyFileSync: vi.fn()
}))

vi.mock('electron', () => ({
  app: stubs.electronApp
}))

vi.mock('fs', () => ({
  existsSync: stubs.existsSync,
  copyFileSync: stubs.copyFileSync,
  unlinkSync: vi.fn()
}))

vi.mock('child_process', () => ({
  spawn: stubs.spawn,
  spawnSync: vi.fn(() => ({ status: 0, stdout: '' }))
}))

import {
  getSidecarLogTail,
  recordSidecarLog,
  resetSidecarLogTail,
  summarizeLines,
  resolveEnvFile,
  seedUserEnvFile
} from './sidecar'

beforeEach(() => {
  resetSidecarLogTail()
  stubs.spawn.mockReset()
  stubs.electronApp.isPackaged = false
  stubs.existsSync.mockReset()
  stubs.existsSync.mockReturnValue(false)
  stubs.copyFileSync.mockReset()
  // Windows-only in real use, but read by `resolveEnvFile` in packaged mode.
  ;(process as unknown as { resourcesPath: string }).resourcesPath = '/tmp/resources'
})

describe('挑出真正的原因行', () => {
  it('取 traceback 的最后一行，也就是 Python 放异常类型和消息的地方', () => {
    const lines = [
      'INFO:     Started server process [1234]',
      'INFO:     Waiting for application startup.',
      'Traceback (most recent call last):',
      '  File "app/main.py", line 3, in <module>',
      '    from app.rag import build_embedder',
      "ModuleNotFoundError: No module named 'app.rag.embedder'"
    ]
    expect(summarizeLines(lines)).toBe("ModuleNotFoundError: No module named 'app.rag.embedder'")
  })

  it('跳过 uvicorn 在原因之后打的关闭噪音', () => {
    const lines = [
      "ModuleNotFoundError: No module named 'app.rag.embedder'",
      'INFO:     Waiting for application shutdown.',
      'INFO:     Shutting down'
    ]
    // The cause is *above* the shutdown lines; quoting the last line
    // verbatim would report "Shutting down" as the reason for a failure
    // that happened before the server ever ran.
    expect(summarizeLines(lines)).toBe("ModuleNotFoundError: No module named 'app.rag.embedder'")
  })

  it('忽略尾部空行', () => {
    expect(summarizeLines(['Error: real_cause', '', '   '])).toBe('Error: real_cause')
  })

  it('超长行截断，避免把整段 traceback 塞进启动画面的一行', () => {
    const long = `Error: ${'x'.repeat(400)}`
    const result = summarizeLines([long])
    expect(result.length).toBeLessThanOrEqual(201)
    expect(result.endsWith('…')).toBe(true)
    // Still recognisable — the prefix is what identifies the error.
    expect(result.startsWith('Error: xxx')).toBe(true)
  })

  it('没有任何可用行时返回空串而不是抛错', () => {
    expect(summarizeLines([])).toBe('')
    expect(summarizeLines(['', '  '])).toBe('')
  })
})

describe('日志尾巴的采集', () => {
  it('按行拆分，CRLF 不会把两行并成一行', () => {
    recordSidecarLog('first\r\nsecond\n')
    expect(getSidecarLogTail()).toEqual(['first', 'second'])
  })

  it('只保留最近的若干行，长时间运行不会无限增长', () => {
    for (let i = 0; i < 500; i += 1) recordSidecarLog(`line ${i}\n`)
    const tail = getSidecarLogTail()
    expect(tail.length).toBeLessThanOrEqual(40)
    // Keeps the *recent* end, which is the whole point of a tail.
    expect(tail[tail.length - 1]).toBe('line 499')
  })

  it('丢弃空行，避免真实日志被空白挤出去', () => {
    recordSidecarLog('only_line\n\n\n')
    expect(getSidecarLogTail()).toEqual(['only_line'])
  })

  it('reset 之后不再含有上一轮的内容', () => {
    recordSidecarLog('old_attempt_error\n')
    resetSidecarLogTail()
    expect(getSidecarLogTail()).toEqual([])
    // And a failure from the new attempt cannot quote the old one.
    recordSidecarLog('new_attempt_error\n')
    expect(summarizeLines(getSidecarLogTail())).toBe('new_attempt_error')
  })
})

/**
 * The `.env` lookup, pinned because getting it wrong is invisible.
 *
 * electron-builder excludes dotfiles, so a packaged build has no `.env`
 * next to the sidecar and `app.config`'s source-tree default resolves to
 * nothing. Nothing raises: the engine boots on field defaults and the
 * configured LLM provider, embedding endpoint and compiler path are all
 * simply absent. So the main process has to name the file, and these
 * tests pin which one it names.
 */
describe('引擎配置文件的定位', () => {
  it('开发模式下指向仓库里的 sidecar/.env', () => {
    stubs.electronApp.isPackaged = false
    const wanted = join('/tmp/app', '..', 'sidecar', '.env')
    stubs.existsSync.mockImplementation((p) => p === wanted)
    expect(resolveEnvFile()).toBe(wanted)
  })

  it('打包模式下 userData 里的副本优先，装到只读目录也能改配置', () => {
    stubs.electronApp.isPackaged = true
    const userCopy = join('/tmp/userData', '.env')
    const shipped = join('/tmp/resources', 'sidecar', '.env')
    // Both exist; the user's copy has to win or their edits are ignored.
    stubs.existsSync.mockImplementation((p) => p === userCopy || p === shipped)
    expect(resolveEnvFile()).toBe(userCopy)
  })

  it('打包模式下没有 userData 副本时回退到随包发布的那个', () => {
    stubs.electronApp.isPackaged = true
    const shipped = join('/tmp/resources', 'sidecar', '.env')
    stubs.existsSync.mockImplementation((p) => p === shipped)
    expect(resolveEnvFile()).toBe(shipped)
  })

  it('一个都找不到时返回 undefined，而不是一个不存在的路径', () => {
    // A nonexistent path would be handed to the child as a real setting
    // and would fail deeper in, blaming the wrong thing.
    expect(resolveEnvFile()).toBeUndefined()
  })

  it('首次运行把随包配置复制到 userData', () => {
    const userCopy = join('/tmp/userData', '.env')
    const shipped = join('/tmp/resources', 'sidecar', '.env')
    // Only the shipped one exists — i.e. first run.
    stubs.existsSync.mockImplementation((p) => p === shipped)

    seedUserEnvFile()
    expect(stubs.copyFileSync).toHaveBeenCalledWith(shipped, userCopy)
  })

  it('userData 里已有配置时不覆盖，用户改过的 key 不会被冲掉', () => {
    const userCopy = join('/tmp/userData', '.env')
    const shipped = join('/tmp/resources', 'sidecar', '.env')
    stubs.existsSync.mockImplementation((p) => p === userCopy || p === shipped)

    seedUserEnvFile()
    expect(stubs.copyFileSync).not.toHaveBeenCalled()
  })
})
