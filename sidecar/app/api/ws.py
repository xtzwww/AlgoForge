"""WebSocket endpoint for streaming judge progress (P1-15).

WS /ws/run-log
  1. Client connects.
  2. Client sends one JSON message:
       {
         "code": "<source code>",
         "language": "cpp",       // optional, default "cpp"
         "test_cases": [{"input": "...", "expected_output": "..."}],
         "time_limit": 10.0,      // optional, seconds, default 10
         "mem_limit_mb": 256      // optional, MB, default 256
       }
  3. Server runs the judge pipeline in a worker thread and streams
     progress events (compile_start, compile_done, case_start,
     case_done, judge_done) back as JSON messages.
  4. Server closes the connection after the final judge_done event.

``language`` was added by PU-16. Because this endpoint takes raw JSON
rather than a Pydantic model, the id is validated explicitly and a bad
one comes back as a ``judge_error`` event — the same treatment every
other malformed field gets here.

The synchronous ``judge`` function runs in a worker thread via
``asyncio.to_thread``. Its ``on_event`` callback bridges into the
event loop through ``asyncio.run_coroutine_threadsafe`` on an
``asyncio.Queue``, so the WS coroutine can ``await`` each event
without blocking the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api import validate_language_id
from app.sandbox.judge import TestCase, judge

router = APIRouter(tags=["ws"])


def _parse_request(
    payload: dict[str, Any],
) -> tuple[str, list[TestCase], float, int, str]:
    """Validate the client's WS message into judge arguments.

    Raises ``ValueError`` with a human-readable message on bad input;
    the caller turns that into a judge_error event.
    """
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("'code' must be a non-empty string")

    raw_cases = payload.get("test_cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("'test_cases' must be a non-empty list")

    cases: list[TestCase] = []
    for i, tc in enumerate(raw_cases):
        if not isinstance(tc, dict):
            raise ValueError(f"test_cases[{i}] must be an object")
        cases.append(
            TestCase(
                input=tc.get("input", "") or "",
                expected_output=tc.get("expected_output", "") or "",
            )
        )

    # Missing means "the default language"; a blank string means the same.
    # Anything else has to name a registered language, or the judge would
    # fail deep inside the sandbox instead of here.
    try:
        language = validate_language_id(payload.get("language"))
    except ValueError as exc:
        raise ValueError(f"invalid 'language': {exc}") from None

    time_limit = float(payload.get("time_limit", 10.0))
    if not (0.1 <= time_limit <= 60.0):
        raise ValueError("time_limit must be between 0.1 and 60.0 seconds")

    mem_limit_mb = int(payload.get("mem_limit_mb", 256))
    if not (1 <= mem_limit_mb <= 2048):
        raise ValueError("mem_limit_mb must be between 1 and 2048 MB")

    return code, cases, time_limit, mem_limit_mb, language


@router.websocket("/ws/run-log")
async def run_log(websocket: WebSocket) -> None:
    """Stream judge progress events to the connected client."""
    await websocket.accept()
    try:
        payload = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    except Exception:
        # Bad JSON / no JSON — tell the client and close.
        await websocket.send_json(
            {"type": "judge_error", "error": "expected a JSON request message"}
        )
        return

    try:
        code, cases, time_limit, mem_limit_mb, language = _parse_request(payload)
    except ValueError as exc:
        await websocket.send_json({"type": "judge_error", "error": str(exc)})
        return

    # Bridge between the judging worker thread and this async coroutine.
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(msg: dict[str, Any]) -> None:
        # Called from the worker thread; hop back into the event loop
        # to enqueue the message without blocking.
        asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    async def run_judge() -> None:
        try:
            await asyncio.to_thread(
                judge,
                code,
                cases,
                time_limit=time_limit,
                mem_limit_mb=mem_limit_mb,
                language=language,
                on_event=on_event,
            )
        except Exception as exc:
            # Propagate unexpected failures as a terminal event so the
            # reader loop unblocks and the client sees what happened.
            on_event({"type": "judge_error", "error": str(exc)})

    task = asyncio.create_task(run_judge())

    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
            if msg.get("type") in ("judge_done", "judge_error"):
                break
    except WebSocketDisconnect:
        # Client went away — let the judge task finish in the background.
        pass
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
