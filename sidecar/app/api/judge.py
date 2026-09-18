"""Judge API endpoint.

POST /api/v1/judge
  Body, cases by problem id:
  {
    "code": "<source code>",
    "language": "cpp",       // optional, default "cpp"
    "problem_id": 7,         // stored cases + the problem's own limits
    "time_limit": 5.0,       // optional, seconds — overrides the problem
    "mem_limit_mb": 512,     // optional, MB — overrides the problem
    "record": true           // optional, default true
  }

  Body, inline cases (self-test):
  {
    "code": "<source code>",
    "language": "cpp",       // optional, default "cpp"
    "test_cases": [{"input": "...", "expected_output": "..."}],
    "time_limit": 10.0,      // optional, seconds, default 10
    "mem_limit_mb": 256,     // optional, MB, default 256
    "record": true           // optional, default true
  }

  Response: {
    "status": "AC" | "WA" | "TLE" | "MLE" | "CE" | "RE",
    "compile_output": "...",
    "passed": 2,
    "total": 3,
    "problem_id": 7,         // null in inline mode
    "test_cases": [{ "status", "input", "expected_output", "actual_output", ... }]
  }

``problem_id`` was added by PU-12. It is the mode the product uses: the
server reads the hidden cases itself, so the client never receives them.
``test_cases`` remains for ad-hoc self-testing, where there is no problem
row to point at. Exactly one of the two is required; the rules and the
error messages live in :mod:`app.sandbox.judge_input` so this route and
``WS /ws/run-log`` cannot drift apart.

``record`` was added by PU-8, to back the practice page's 运行/提交
split. ``True`` (default) persists the verdict; ``False`` computes and
returns it without writing anything. See the field's docstring.

The REST twin of ``WS /ws/run-log``. It shares the same pipeline but
returns one finished response instead of streaming progress, which is
what tests and simple clients want.
"""

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api import LanguageId
from app.api.auth import current_user
from app.models.user import User
from app.process_trace import parse_process_trace, view_for
from app.records import (
    SubmissionContext,
    analyse_submission,
    record_submission_quietly,
    will_analyse,
)
from app.sandbox.judge import JudgeResult, judge
from app.sandbox.judge_input import JudgeInputError, resolve
from app.sandbox.pool import run_in_sandbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["judge"])

#: Indirection over :func:`app.records.record_submission_quietly`,
#: mirroring the hook in ``app/api/ws.py``. Tests replace this to
#: exercise the judge route without a database; production keeps the real
#: function.
record_judge_outcome = record_submission_quietly


class TestCaseIn(BaseModel):
    input: str = ""
    expected_output: str


class JudgeRequest(BaseModel):
    code: str
    #: Language the code is written in. A bad id is a 422, not a 500.
    language: LanguageId = "cpp"
    #: Catalogue problem to pull cases from. Mutually exclusive with
    #: ``test_cases``.
    problem_id: Optional[int] = None
    #: Inline cases for self-testing. Mutually exclusive with
    #: ``problem_id``.
    test_cases: Optional[list[TestCaseIn]] = None
    # Per-case wall-clock timeout in seconds. In ``problem_id`` mode this
    # overrides the problem's own limit; in inline mode it is the limit.
    time_limit: Optional[float] = Field(default=None, ge=0.1, le=60.0)
    # Per-case memory ceiling in MB. Same override rule as ``time_limit``.
    mem_limit_mb: Optional[int] = Field(default=None, ge=1, le=2048)
    #: Whether to persist the verdict. ``True`` (the default) is a
    #: submit: the row lands in ``submissions`` and a failure is filed in
    #: the mistake book. ``False`` is a scratch run — verdict computed
    #: and returned, nothing written and nothing scored (PU-8). Parsed by
    #: :func:`app.sandbox.judge_input.parse_record_flag` so this route and
    #: ``WS /ws/run-log`` cannot disagree about the default.
    record: bool = True
    #: The editor's behaviour trace for the session that produced this
    #: code (C1). Typed ``Any``, not ``ProcessTrace``, on purpose: a
    #: malformed trace is dropped with a log line rather than turning
    #: into a 422, because telemetry must never cost the student the
    #: verdict they are waiting for. See :func:`parse_process_trace`.
    process_trace: Any = None


class TestCaseOut(BaseModel):
    status: str
    input: str
    expected_output: str
    actual_output: str
    stderr: str
    exit_code: int


class JudgeResponse(BaseModel):
    status: str
    compile_output: str
    passed: int
    total: int
    #: Echo of the request's ``problem_id``, or null for inline cases.
    #: Lets a client that fires off several submissions tell the
    #: responses apart without correlating by hand.
    problem_id: Optional[int] = None
    test_cases: list[TestCaseOut]
    #: The edit-behaviour observation (C1), or ``None`` when the client
    #: sent no trace. Returned here rather than left for the client to
    #: fetch because it is known at the same moment as the verdict and
    #: belongs beside it: both are facts about this one submission.
    process: Optional[dict] = None


async def _analyse_in_background(
    submission_id: int,
    result: JudgeResult,
    context: SubmissionContext,
) -> None:
    """Run the error analysis once the response has been sent.

    On a thread rather than inline because ``analyse_submission`` is
    synchronous — it opens a session and makes a blocking HTTP request,
    and either would stall the event loop for every other request for as
    long as the provider takes to answer.
    """
    try:
        await asyncio.to_thread(analyse_submission, submission_id, result, context)
    except Exception:
        logger.exception(
            "background error analysis failed for submission %s", submission_id
        )


@router.post("/judge", response_model=JudgeResponse)
async def judge_code(
    req: JudgeRequest,
    background: BackgroundTasks,
    user: Optional[User] = Depends(current_user),
) -> JudgeResponse:
    """Build and judge the submitted code against the test cases.

    The program is built once (P1-11) and reused for every test case
    (P1-12 ``run_executable`` enforces timeout + memory ceiling).
    Per-case verdicts are aggregated into an overall verdict with
    priority CE > TLE > MLE > RE > WA > AC.
    """
    # Widen the schema's list-of-models back to the raw shape the resolver
    # speaks, so both entry points share one code path and one set of
    # error messages rather than the REST route reimplementing them.
    raw_cases = (
        [tc.model_dump() for tc in req.test_cases]
        if req.test_cases is not None
        else None
    )
    try:
        resolved = resolve(
            problem_id=req.problem_id,
            test_cases=raw_cases,
            time_limit=req.time_limit,
            mem_limit_mb=req.mem_limit_mb,
        )
    except JudgeInputError as exc:
        # A malformed request or an unknown problem is the caller's
        # mistake, not a server fault. 404 when the problem is missing so
        # the client can distinguish "wrong id" from "wrong body".
        status = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None

    context = SubmissionContext(
        code=req.code,
        language=req.language,
        problem_id=resolved.problem_id,
        time_limit=resolved.time_limit,
        mem_limit_mb=resolved.mem_limit_mb,
        trace=parse_process_trace(req.process_trace),
        user_id=user.id if user is not None else None,
    )

    # Judging is blocking — it compiles and runs a subprocess per case —
    # and recording is blocking database I/O. Both go to a worker thread
    # so this coroutine does not stall the event loop for every other
    # request, which is also what the WS route does.
    def _judge_and_record() -> tuple[JudgeResult, Optional[int]]:
        outcome = judge(
            req.code,
            resolved.test_cases,
            time_limit=resolved.time_limit,
            mem_limit_mb=resolved.mem_limit_mb,
            language=req.language,
        )
        # Ordered after the verdict exists so the row can never describe
        # a run that did not finish. The hook is the quiet variant: a
        # history-write failure must not cost the user their verdict.
        #
        # ``record=False`` is PU-8's scratch run: the verdict is still
        # returned in full, it just does not become history. Skipping
        # rather than writing-and-deleting keeps the mistake book's
        # dedupe window from seeing attempts that never counted.
        #
        # ``analyse=False`` leaves the analysis to the background task
        # below. This response used to wait for it, which meant a verdict
        # that was ready in milliseconds arrived only after a paid round
        # trip that can take minutes.
        submission_id = None
        if req.record:
            submission_id = record_judge_outcome(outcome, context, analyse=False)
        return outcome, submission_id

    # Capped: see :mod:`app.sandbox.pool`. Off the loop is not the same
    # thing as at most four, and "the judge got slow" is a much harder
    # failure to diagnose than "a submission had to wait".
    result, submission_id = await run_in_sandbox(_judge_and_record)

    if submission_id is not None and will_analyse(result, context):
        # Scheduled, not awaited: the student gets the verdict now and
        # the analysis lands in the mistake book when it lands.
        background.add_task(_analyse_in_background, submission_id, result, context)

    payload = result.to_dict()
    payload["problem_id"] = resolved.problem_id
    # Reported even when ``record`` is false: the observation is about
    # the editing session, not about whether this run became history, and
    # a scratch run is exactly where a student would want the nudge.
    payload["process"] = view_for(context.trace, req.code)
    return JudgeResponse(**payload)
