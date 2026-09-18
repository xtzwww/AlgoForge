"""Resolve judge inputs from either a problem id or inline test cases (PU-12).

Why this module exists
----------------------
The judge accepts test cases in two shapes, and both the REST
(``POST /api/v1/judge``) and WebSocket (``WS /ws/run-log``) entry points
accept the same two shapes with identical rules. Putting the rules in one
place means the two transports cannot drift apart, and it keeps
``app/api/*`` free of data-layer selection logic (AGENTS.md section 4).

The two modes
-------------
* **By problem id** — ``{"problem_id": 7}``. The server reads the cases
  and limits from the database, so the client never has to receive the
  hidden cases at all. This is the mode the product uses; see
  the module docstring for why judging was designed this way from
  the start instead of being retrofitted when the app grows a web front
  end.
* **Inline cases** — ``{"test_cases": [...]}``. The caller supplies the
  cases in the request. This is the self-test path: a user trying their
  code against ad-hoc input they just typed has no problem row to point
  at.

Exactly one of the two must be present. Accepting both would leave the
precedence rule ("which one wins?") to be guessed, and accepting neither
leaves the judge with nothing to run against.

A third, independent rule also lives here: :func:`parse_record_flag`,
which reads the 运行-vs-提交 switch (PU-8). It is here for the same
reason as the rest — two transports, one meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.problem import Problem
from app.models.test_case import TestCase as TestCaseRow
from app.sandbox.judge import TestCase


class JudgeInputError(ValueError):
    """A caller-supplied judge request cannot be resolved.

    Subclasses ``ValueError`` so existing callers that catch
    ``ValueError`` (the WS parser turns it into a ``judge_error`` event,
    Pydantic turns it into a 422) keep working unchanged, while code that
    wants to distinguish "bad request" from an unexpected crash can catch
    this class specifically.
    """


@dataclass
class ResolvedJudgeInput:
    """The judge arguments a request resolved to.

    ``test_cases`` is never empty: a problem with no cases (or an inline
    list that came back empty) is an error rather than a vacuous AC.

    ``time_limit`` / ``mem_limit_mb`` are the values the judge should
    use. ``mem_limit_mb`` is spelled to match :func:`judge`'s parameter
    name and differs from the model's ``memory_limit`` column on purpose
    — one is a per-run ceiling in MB, the other is catalogue metadata.
    """

    test_cases: list[TestCase]
    time_limit: float
    mem_limit_mb: int
    #: Set when resolution went through ``problem_id``; the REST route
    #: uses it to label the response. ``None`` for inline cases.
    problem_id: Optional[int] = None


def parse_inline_cases(raw_cases: Any) -> list[TestCase]:
    """Validate and convert a raw ``test_cases`` value into judge cases.

    Accepts only a non-empty list of objects. The per-case fields default
    to ``""`` because an empty stdin is legitimate (a program that prints
    a constant has no input) and Pydantic/mypy should not have to care.

    Raises:
        JudgeInputError: Not a list, empty, or an element is not an object.
    """
    if not isinstance(raw_cases, list) or not raw_cases:
        raise JudgeInputError("'test_cases' must be a non-empty list")

    cases: list[TestCase] = []
    for i, tc in enumerate(raw_cases):
        if not isinstance(tc, dict):
            raise JudgeInputError(f"test_cases[{i}] must be an object")
        cases.append(
            TestCase(
                input=tc.get("input", "") or "",
                expected_output=tc.get("expected_output", "") or "",
            )
        )
    return cases


def resolve(
    *,
    problem_id: Optional[int] = None,
    test_cases: Any = None,
    time_limit: Optional[float] = None,
    mem_limit_mb: Optional[int] = None,
    session: Optional[Session] = None,
) -> ResolvedJudgeInput:
    """Resolve a judge request into concrete cases and limits.

    Args:
        problem_id: Catalogue id to pull cases from. Mutually exclusive
            with *test_cases*.
        test_cases: Inline cases, used when *problem_id* is absent.
        time_limit: Overrides the problem's limit. For inline cases it is
            the limit outright; for ``problem_id`` mode it is an opt-in
            override, which is what lets a user retry a TLE submission
            with a longer limit without editing the problem.
        mem_limit_mb: Same override rule as *time_limit*.
        session: Session to use in ``problem_id`` mode. Defaults to
            :func:`app.db.get_session`. Tests pass their in-memory
            session here rather than monkeypatching this module.

    Returns:
        A :class:`ResolvedJudgeInput` with at least one case.

    Raises:
        JudgeInputError: Both modes supplied, neither supplied, the
            problem does not exist, it has no cases, or an override is
            out of range.
    """
    has_problem = problem_id is not None
    has_inline = test_cases is not None

    if has_problem and has_inline:
        raise JudgeInputError(
            "'problem_id' and 'test_cases' are mutually exclusive — send "
            "one to use the problem's stored cases, or the other to try "
            "ad-hoc input"
        )
    if not has_problem and not has_inline:
        raise JudgeInputError("one of 'problem_id' or 'test_cases' is required")

    if has_inline:
        cases = parse_inline_cases(test_cases)
        # Inline mode has no problem row to inherit limits from, so the
        # caller's values are authoritative and the defaults apply.
        return ResolvedJudgeInput(
            test_cases=cases,
            time_limit=_validated_time_limit(
                time_limit if time_limit is not None else 10.0
            ),
            mem_limit_mb=_validated_mem_limit(
                mem_limit_mb if mem_limit_mb is not None else 256
            ),
            problem_id=None,
        )

    assert problem_id is not None  # narrowed by has_problem above
    return _resolve_from_problem(
        problem_id, time_limit=time_limit, mem_limit_mb=mem_limit_mb, session=session
    )


def _resolve_from_problem(
    problem_id: int,
    *,
    time_limit: Optional[float],
    mem_limit_mb: Optional[int],
    session: Optional[Session],
) -> ResolvedJudgeInput:
    """Load a problem's cases and limits from the database."""

    def _run(active: Session) -> ResolvedJudgeInput:
        problem = active.get(Problem, problem_id)
        if problem is None:
            raise JudgeInputError(f"problem {problem_id} not found")

        stmt = (
            select(TestCaseRow)
            .where(TestCaseRow.problem_id == problem_id)
            .order_by(TestCaseRow.ordinal)
        )
        rows = list(active.execute(stmt).scalars().all())
        if not rows:
            raise JudgeInputError(
                f"problem {problem_id} has no test cases to judge against"
            )

        return ResolvedJudgeInput(
            test_cases=[
                TestCase(input=row.input, expected_output=row.expected_output)
                for row in rows
            ],
            # The problem's own limits are the baseline; a caller-provided
            # value overrides them (the TLE-retry case above).
            time_limit=_validated_time_limit(
                time_limit if time_limit is not None else problem.time_limit
            ),
            mem_limit_mb=_validated_mem_limit(
                mem_limit_mb if mem_limit_mb is not None else problem.memory_limit
            ),
            problem_id=problem.id,
        )

    if session is not None:
        return _run(session)
    with get_session() as owned:
        return _run(owned)


def _validated_time_limit(value: float) -> float:
    """Coerce and range-check a time limit, mirroring the REST bounds."""
    try:
        limit = float(value)
    except (TypeError, ValueError):
        raise JudgeInputError("time_limit must be a number") from None
    if not (0.1 <= limit <= 60.0):
        raise JudgeInputError("time_limit must be between 0.1 and 60.0 seconds")
    return limit


def parse_record_flag(payload: dict[str, Any]) -> bool:
    """Read the ``record`` flag, defaulting to ``True`` (PU-8).

    Why this is here rather than in either transport: it is a *shared
    wire rule*, exactly like the case-resolution rules above, and the
    two entry points must not disagree about what a missing key or a
    string ``"false"`` means.

    Semantics. The client-facing split is 运行 vs 提交:

    * **提交** (submit) — the full case set, verdict recorded in
      ``submissions`` and, on failure, filed in the mistake book. The
      default, because a client that says nothing wants the product's
      normal behaviour.
    * **运行** (run) — a scratch run against a few cases. The verdict is
      still computed and streamed back, but nothing is written: no
      history row, no mistake-book entry. Neither is *scoring* done —
      a scratch run over the visible samples is not an attempt at the
      problem, so counting it would pollute the profile.

    Ordering is deliberate: the two entry points are sent the same
    behaviour, and a client that omits the key gets the recording path
    rather than silently losing its history. A non-boolean is rejected
    instead of coerced — ``record: "false"`` being truthy is exactly the
    class of silent bug that would make a scratch run write history.

    Args:
        payload: The raw request object (either transport's shape).

    Returns:
        ``False`` only when the payload explicitly said so.

    Raises:
        JudgeInputError: ``record`` is present but not a boolean.
    """
    raw = payload.get("record")
    # Absent and JSON null both mean "the client did not opt out".
    if raw is None:
        return True
    if not isinstance(raw, bool):
        raise JudgeInputError("'record' must be a boolean")
    return raw


def _validated_mem_limit(value: int) -> int:
    """Coerce and range-check a memory limit, mirroring the REST bounds.

    Rejects a fractional value rather than truncating it: the WS route
    takes raw JSON, so ``2.7`` is reachable, and silently treating it as
    2 would hide the caller's mistake behind a limit they did not ask
    for. A numeric *string* is still accepted — that is unambiguous.
    """
    try:
        # ``float(...)`` first so "512" and 512 both work, then compare
        # against the integer form to catch a fractional input.
        as_float = float(value)
    except (TypeError, ValueError):
        raise JudgeInputError("mem_limit_mb must be a whole number") from None
    if as_float != int(as_float):
        raise JudgeInputError("mem_limit_mb must be a whole number")
    limit = int(as_float)
    if not (1 <= limit <= 2048):
        raise JudgeInputError("mem_limit_mb must be between 1 and 2048 MB")
    return limit
