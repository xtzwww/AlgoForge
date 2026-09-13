/**
 * WebSocket client for the judge progress stream (P1-15 / P1-16).
 *
 * Connects to `WS /ws/run-log`, sends one JSON request, and exposes
 * the streamed events as an async iterator + a callback. The caller
 * assembles the final result from the terminal `judge_done` event
 * plus the per-case `case_done` events collected along the way.
 */

import type { TestCaseIn } from './judge'

export type JudgeEventType =
  | 'compile_start'
  | 'compile_done'
  | 'case_start'
  | 'case_done'
  | 'judge_done'
  | 'judge_error'

export interface JudgeEvent {
  type: JudgeEventType
  // compile_done
  success?: boolean
  output?: string
  // case_start / case_done
  index?: number
  input?: string
  // case_done
  status?: string
  actual_output?: string
  exit_code?: number
  timed_out?: boolean
  memory_exceeded?: boolean
  // judge_done / judge_error
  passed?: number
  total?: number
  compile_output?: string
  // judge_error
  error?: string
}

export interface WsJudgeRequest {
  code: string
  test_cases: TestCaseIn[]
  time_limit?: number
  mem_limit_mb?: number
}

/**
 * Open a WebSocket to /ws/run-log, send the request, and invoke
 * `onEvent` for each streamed event. Resolves when the terminal
 * `judge_done` or `judge_error` event arrives.
 *
 * The WS URL is derived from the sidecar base URL (http→ws / https→wss)
 * fetched via `window.algoforge.sidecar.getInfo()`. The auth token is
 * passed as a query param since WebSocket cannot set Authorization
 * headers from the renderer.
 */
export async function streamJudge(
  req: WsJudgeRequest,
  onEvent: (event: JudgeEvent) => void,
): Promise<JudgeEvent> {
  const info = await window.algoforge.sidecar.getInfo()
  if (!info) {
    throw new Error('Sidecar info not available. Is the sidecar running?')
  }

  // http→ws / https→wss, and the sidecar base URL already includes
  // the scheme + host + port (e.g. http://localhost:8000).
  const wsBaseUrl = info.url.replace(/^http/, 'ws')
  const wsUrl = `${wsBaseUrl}/ws/run-log?token=${encodeURIComponent(info.token)}`

  const ws = new WebSocket(wsUrl)

  return new Promise<JudgeEvent>((resolve, reject) => {
    let terminal: JudgeEvent | null = null

    ws.onopen = () => {
      ws.send(JSON.stringify(req))
    }

    ws.onmessage = (ev: MessageEvent) => {
      let msg: JudgeEvent
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '') as JudgeEvent
      } catch {
        // Ignore malformed messages — the server only sends JSON.
        return
      }
      onEvent(msg)
      if (msg.type === 'judge_done' || msg.type === 'judge_error') {
        terminal = msg
        ws.close()
      }
    }

    ws.onerror = () => {
      if (!terminal) {
        reject(new Error('WebSocket connection failed'))
      }
    }

    ws.onclose = () => {
      if (terminal) {
        resolve(terminal)
      } else if (!terminal) {
        reject(new Error('WebSocket closed before judge_done'))
      }
    }
  })
}
