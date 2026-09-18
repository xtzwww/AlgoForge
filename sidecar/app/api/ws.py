"""WebSocket endpoint for streaming judge progress (P1-15).

WS /ws/run-log
  1. Client connects.
  2. Client sends one JSON message, in one of two shapes:

     Cases from the catalogue (the product's path):
       {
         "code": "<source code>",
         "language": "cpp",     // optional, default "cpp"
         "problem_id": 7,       // server reads the stored cases
         "time_limit": 5.0,     // optional — overrides the problem
         "mem_limit_mb": 512,   // optional — overrides the problem
         "record": true         // optional, default true
       }

     Inline cases (self-test):
       {
         "code": "<source code>",
         "language": "cpp",     // optional, default "cpp"
         "test_cases": [{"input": "...", "expected_output": "..."}],
         "time_limit": 10.0,    // optional, seconds, default 10
         "mem_limit_mb": 256,   // optional, MB, default 256
         "record": true         // optional, default true
       }

     Exactly one of ``problem_id`` / ``test_cases`` is required.
  3. Server runs the judge pipeline in a worker thread and streams
     progress events (compile_start, compile_done, case_start,
     case_done, judge_done) back as JSON messages.
  4. Server closes the connection after the final judge_done event.

``record`` was added by PU-8 and is what separates 运行 from 提交 in the
practice page. ``true`` (the default) is a submit: the verdict is
written to ``submissions`` and a failure is filed in the mistake book.
``false`` is a scratch run: the verdict is computed and streamed as
usual, but nothing is persisted and nothing is scored — a run over the
visible samples is not an attempt at the problem. The terminal event is
sent either way, because the reader loop below treats ``judge_done`` as
"stop" and a withheld terminal event would hang the client. The flag's
parsing lives in :mod:`app.sandbox.judge_input` alongside the other
shared wire rules.

``language`` was added by PU-16. Because this endpoint takes raw JSON
rather than a Pydantic model, the id is validated explicitly and a bad
one comes back as a ``judge_error`` event — the same treatment every
other malformed field gets here.

``problem_id`` was added by PU-12. Cases and limits are then resolved by
:mod:`app.sandbox.judge_input`, which the REST judge route also uses —
so the two transports share one set of rules and one set of messages and
cannot drift apart. Note that resolution touches the database, so an
unknown problem surfaces as a ``judge_error`` event rather than
aborting the socket with a 404: the client has already been accepted by
that point.

The synchronous ``judge`` function runs in a worker thread via
``asyncio.to_thread``. Its ``on_event`` callback bridges into the
event loop through ``asyncio.run_coroutine_threadsafe`` on an
``asyncio.Queue``, so the WS coroutine can ``await`` each event
without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api import validate_language_id
from app.records import (
    SubmissionContext,
    analyse_submission,
    record_submission_quietly,
    will_analyse,
)
from app.sandbox.judge import judge
from app.sandbox.judge_input import (
    JudgeInputError,
    ResolvedJudgeInput,
    parse_record_flag,
    resolve,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

#: Indirection over :func:`app.records.record_submission_quietly` so tests
#: can substitute the write without a database. Both routes resolve the
#: name through here at call time; the tests in ``test_judge_recording.py``
#: replace it, which is what lets the WS and REST judge paths be tested
#: without a live PostgreSQL. Production keeps the real function.
record_judge_outcome = record_submission_quietly


def _parse_request(
    payload: dict[str, Any], session: Optional[Session] = None
) -> tuple[str, ResolvedJudgeInput, str, bool]:
    """Validate the client's WS message into judge arguments.

    Returns ``(code, resolved_input, language, record)``. Case resolution
    — which may hit the database — happens here so a bad ``problem_id``
    is reported before the judge starts, and so the caller only has to
    wrap one call.

    Args:
        payload: The raw JSON message.
        session: Session to resolve ``problem_id`` against. Left ``None``
            in production so :func:`resolve` opens its own; tests pass an
            in-memory session in. Without this the transport tests would
            need a live PostgreSQL.

    Raises ``ValueError`` with a human-readable message on bad input;
    the caller turns that into a judge_error event.
    """
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("'code' must be a non-empty string")

    # Missing means "the default language"; a blank string means the same.
    # Anything else has to name a registered language, or the judge would
    # fail deep inside the sandbox instead of here.
    try:
        language = validate_language_id(payload.get("language"))
    except ValueError as exc:
        raise ValueError(f"invalid 'language': {exc}") from None

    # Delegate the mode selection, range checks, and error wording to the
    # shared resolver. It raises JudgeInputError, a ValueError subclass,
    # so the existing ``except ValueError`` in the handler is enough.
    #
    # ``payload.get`` returns None for an absent key, which is exactly
    # what the resolver treats as "this mode was not requested". A key
    # present but set to null reads the same way, which is deliberate:
    # JSON null and "absent" both mean the client did not supply it.
    resolved = resolve(
        problem_id=payload.get("problem_id"),
        test_cases=payload.get("test_cases"),
        time_limit=payload.get("time_limit"),
        mem_limit_mb=payload.get("mem_limit_mb"),
        session=session,
    )

    return code, resolved, language, parse_record_flag(payload)


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
        code, resolved, language, record = _parse_request(payload)
    except JudgeInputError as exc:
        await websocket.send_json({"type": "judge_error", "error": str(exc)})
        return
    except ValueError as exc:
        await websocket.send_json({"type": "judge_error", "error": str(exc)})
        return

    context = SubmissionContext(
        code=code,
        language=language,
        problem_id=resolved.problem_id,
        time_limit=resolved.time_limit,
        mem_limit_mb=resolved.mem_limit_mb,
    )

    # Bridge between the judging worker thread and this async coroutine.
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(msg: dict[str, Any]) -> None:
        # Called from the worker thread; hop back into the event loop
        # to enqueue the message without blocking.
        asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    def progress_only(msg: dict[str, Any]) -> None:
        """Forward progress events, withholding the terminal one.

        ``judge()`` emits its own ``judge_done`` from inside the judging
        function, which would reach the client *before* the history row
        is written. The reader loop treats that event as "stop", so the
        client would be told the run finished while ``record_submission``
        was still executing — and a client that reads the history right
        after would legitimately find nothing.

        The terminal event is therefore held back here and sent by
        :func:`judge_and_record` once the row is committed, so "done"
        means done: verdict delivered *and* history written.

        ``judge_error`` is filtered too even though ``judge()`` does not
        emit it today — the reader loop also treats it as terminal, so
        anything terminal must come from the one place that knows the
        write has finished.
        """
        if msg.get("type") not in ("judge_done", "judge_error"):
            on_event(msg)

    def judge_and_record() -> None:
        """Judge, store the outcome, tell the client, then analyse.

        Recording is deliberately *after* the verdict is complete and
        still on this thread: it needs a synchronous session (see
        ``app/records.py``), and doing it here keeps the write off the
        event loop. ``record_submission`` never raises, so a history
        failure cannot break the stream the client is reading.

        A scratch run (``record`` false, PU-8) skips the write but not
        the terminal event. Withholding it on the no-record path would
        hang the client on a stream that had in fact finished — the
        terminal event is about the *judge* being done, not about a row
        existing.

        **The analysis runs after the terminal event, not before it.**
        P2-8 originally had it the other way round, which meant the
        verdict waited on a paid round trip: judging takes milliseconds
        and the analysis can take minutes, so the student stared at a
        finished-looking progress bar for the difference. The events the
        Agent produces still ride this same channel, they just arrive
        afterwards — and ``analysing`` on ``judge_done`` is what tells
        the reader loop and the client that the stream is not over yet.
        """
        outcome = judge(
            code,
            resolved.test_cases,
            time_limit=resolved.time_limit,
            mem_limit_mb=resolved.mem_limit_mb,
            language=language,
            on_event=progress_only,
        )

        submission_id = None
        if record:
            # ``analyse=False``: commit the rows and return. The analysis
            # is a separate, slower step below.
            submission_id = record_judge_outcome(outcome, context, analyse=False)

        analysing = submission_id is not None and will_analyse(outcome, context)
        terminal = outcome.to_dict()
        terminal["type"] = "judge_done"
        terminal["analysing"] = analysing
        on_event(terminal)

        if analysing and submission_id is not None:
            try:
                analyse_submission(
                    submission_id, outcome, context, on_event=on_event
                )
            except Exception as exc:
                # The socket is still open, so the client is waiting for
                # agent events that will now never come. ``agent_done`` is
                # what releases it — see the reader loop — so it has to be
                # sent even here.
                logger.exception(
                    "error analysis raised for submission %s", submission_id
                )
                on_event(
                    {
                        "type": "agent_done",
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_type": None,
                        "tools": [],
                    }
                )

    async def run_judge() -> None:
        try:
            await asyncio.to_thread(judge_and_record)
        except Exception as exc:
            # Propagate unexpected failures as a terminal event so the
            # reader loop unblocks and the client sees what happened.
            on_event({"type": "judge_error", "error": str(exc)})

    task = asyncio.create_task(run_judge())

    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
            kind = msg.get("type")
            if kind == "judge_error":
                break
            if kind == "agent_done":
                # What the client was still waiting for.
                break
            if kind == "judge_done" and not msg.get("analysing"):
                # Verdict in, no analysis follows. ``analysing`` is false
                # for an accepted run, a scratch run and a run with no
                # problem row — every case where no Agent is involved.
                break
    except WebSocketDisconnect:
        # Client went away — let the judge task finish in the background.
        pass
    finally:
        if not task.done():
            task.cancel()
            # Awaiting a cancelled task is how the cancellation is
            # delivered; both exception types are expected here and
            # neither is a failure. ``CancelledError`` is a
            # ``BaseException`` in 3.8+, so it is not covered by
            # ``Exception`` and has to be named separately.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
