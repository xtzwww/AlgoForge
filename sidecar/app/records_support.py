"""Pure helpers for the submission write path — no session of their own.

Split out of :mod:`app.records` (T13) because this half of that module
never touches the database on its own initiative: every function either
is pure computation or takes the ``Session`` as an argument. That makes
them the safe half to separate — see the module docstring of
:mod:`app.records` for why the other half is *not* split the same way.

What lives here
---------------
* :class:`SubmissionContext` — the non-verdict inputs of a judge run.
* Output and measurement selection (:func:`_pick_output`,
  :func:`_worst_measurements`) — what gets stored for a verdict.
* Code fingerprinting and open-duplicate lookup
  (:func:`_code_fingerprint`, :func:`_find_open_duplicate`) — the dedupe
  key for automatic mistake-book archiving.
* Statement assembly (:func:`_assemble_statement`) and failing-case
  selection (:func:`_first_failing_case`) — the inputs the analysis is
  built from.

Why ``_ARCHIVABLE`` is here
---------------------------
It is the verdict set the write path consults, and it is a constant
about judging rather than about recording, so it travels with the code
that reads verdicts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.process_trace import ProcessTrace
from app.sandbox.judge import JudgeResult, JudgeStatus

#: Verdicts that get filed in the mistake book. Everything except AC:
#: a TLE or a CE is just as much a mistake the user wants to revisit, and
#: limiting this to WA would silently drop the cases the agent is most
#: useful for.
_ARCHIVABLE = frozenset(
    {
        JudgeStatus.WA,
        JudgeStatus.TLE,
        JudgeStatus.MLE,
        JudgeStatus.CE,
        JudgeStatus.RE,
    }
)

@dataclass
class SubmissionContext:
    """Everything about a judge run that is not part of the verdict.

    Grouped into a dataclass rather than passed as seven positional
    arguments, because the two call sites (REST and WebSocket) have the
    same set and a misordered argument would be a silent data corruption
    — swapping ``stdout`` and ``stderr`` type-checks fine.
    """

    code: str
    language: str
    #: Problem the run was made against, or None for a free run.
    problem_id: Optional[int] = None
    #: Accepted for symmetry and future use; the per-anchor time limit is
    #: not stored on the row, but callers that know it should pass it so
    #: the signature does not change later.
    time_limit: Optional[float] = None
    mem_limit_mb: Optional[int] = None
    #: Overrides recorded only when set.
    tags: list[str] = field(default_factory=list)
    #: The editor's behaviour trace for the session that produced this
    #: submission (C1), or ``None`` when the caller has none — which is
    #: the normal case for the REST route, for an older client, and for
    #: anything submitted outside the editor. ``None`` is recorded as
    #: "no process observed", never as a clean process.
    trace: Optional[ProcessTrace] = None
    #: Who submitted it (B1), or ``None`` for an unowned run. Carried
    #: here rather than resolved inside the write so both entry points
    #: decide it the same way and the router — which is where the
    #: credential is available — stays the only place that reads it.
    user_id: Optional[int] = None


def _pick_output(result: JudgeResult) -> tuple[str, str]:
    """Choose which case's output to store.

    The first failing case is the informative one — it is where the
    program went wrong, and it is what the user needs to see to fix the
    code. When everything passed, the last case is stored so the field is
    not empty for no reason.

    CE is special: compilation produced no cases at all, so the compiler
    diagnostics go in ``stderr`` instead, which is where a user looks for
    them.
    """
    if result.status == JudgeStatus.CE:
        return "", result.compile_output

    failing = next(
        (tc for tc in result.test_cases if tc.status != JudgeStatus.AC), None
    )
    chosen = failing if failing is not None else (
        result.test_cases[-1] if result.test_cases else None
    )
    if chosen is None:
        return "", ""
    return chosen.actual_output, chosen.stderr


def _worst_measurements(result: JudgeResult) -> tuple[Optional[float], Optional[int]]:
    """Aggregate per-case timings into one (seconds, megabytes) pair.

    The **worst** time and the **largest** memory across cases, not the
    sum: a limit is per case, so the sum is not a quantity any limit
    applies to and would grow with the case count for no reason.

    Returns ``(None, None)`` when nothing was measured — a CE run has no
    cases, and a run whose cases were all killed before a sample was
    taken has no memory figure. Reporting 0 for those would state "ran
    instantly, used no memory", which is a different and false claim.
    """
    times = [tc.exec_time for tc in result.test_cases if tc.exec_time is not None]
    mems = [tc.memory_used for tc in result.test_cases if tc.memory_used is not None]
    return (max(times) if times else None, max(mems) if mems else None)


def _code_fingerprint(*parts: str) -> str:
    """Stable short digest used to compare two submissions' code.

    Hashed rather than compared directly because the comparison happens
    in SQL: two long sources stored in ``Text`` columns would have to be
    compared in full for every candidate row, and on SQLite a mismatch
    between how the two drivers normalize line endings would make the
    comparison unreliable. A digest makes the comparison a fixed-size
    string equality and sidesteps the newline question entirely.

    The parts are joined with a NUL, which cannot appear in any of them.
    """
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _find_open_duplicate(
    session: Session, problem_id: int, fingerprint: str
) -> Optional[MistakeBookEntry]:
    """Return the existing un-annotated entry for this code, if any.

    "Open" means ``error_cause`` is still empty — the state PU-13 leaves
    an auto-archived entry in. Once the agent annotates an entry it is no
    longer open, and a later identical failure gets its own row.

    Returns the entry itself rather than a bool because P2-4 annotates
    whichever entry it ends up with; re-querying it afterwards would be a
    second round trip for a value already in hand.
    """
    stmt = (
        select(MistakeBookEntry)
        .where(MistakeBookEntry.problem_id == problem_id)
        .where(MistakeBookEntry.error_cause == "")
        .where(MistakeBookEntry.error_cause_fingerprint == fingerprint)
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _has_open_duplicate(session: Session, problem_id: int, fingerprint: str) -> bool:
    """Boolean form of :func:`_find_open_duplicate`.

    Kept as its own function because the question "is this a duplicate?"
    is asked on its own in tests and reads better than ``is not None``
    at a call site.
    """
    return _find_open_duplicate(session, problem_id, fingerprint) is not None


def _assemble_statement(problem: Problem) -> str:
    """Flatten a :class:`Problem`'s statement fields into one string.

    PU-11 stores a statement as three columns (``description``,
    ``input_format``, ``output_format``) plus a samples list, because that
    is how the UI renders it. The agent wants one text block, and it
    wants the input/output format rules specifically: a large share of
    the ``io_format_error`` label depends on the model having seen them.

    Empty sections are skipped rather than emitted as blank headings, and
    the whole thing degrades to "" when the problem has no statement at
    all — ``analyze_error`` then substitutes its own explicit "题面未提供"
    note, which is a claim the model can act on.
    """
    parts = []

    description = (problem.description or "").strip()
    if description:
        parts.append(description)

    input_format = (problem.input_format or "").strip()
    if input_format:
        parts.append(f"输入格式：\n{input_format}")

    output_format = (problem.output_format or "").strip()
    if output_format:
        parts.append(f"输出格式：\n{output_format}")

    return "\n\n".join(parts)


def _first_failing_case(result: JudgeResult) -> Optional[Any]:
    """The case the analysis is built around, or None when there is none.

    The analysis needs one case's input, expected output and actual
    output. "Failing" means anything that is not AC — a TLE has no wrong
    output but is still the case the student needs explained.

    Shared with :func:`will_analyse` so that "an analysis will follow
    this verdict" and "there is a case to analyse" cannot drift apart.
    A CE is what makes the distinction visible: the entry is filed, but
    the judge ran no case, so no analysis can be built for it.
    """
    return next(
        (tc for tc in result.test_cases if tc.status != JudgeStatus.AC), None
    )
