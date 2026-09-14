"""Persist judge outcomes — the write path for ``submissions`` (PU-13).

Why this module exists
----------------------
Before PU-13 the ``submissions`` table had **no writer**. ``MistakeBookEntry``
requires a ``submission_id``, and ``app/api/mistake_book.py`` refuses a
submission that does not exist, so the mistake book could only be reached
by a client that already knew an id it had no way to obtain. That is gap 7
in ``docs/PHASE-UX.md``; this module closes it.

Where it sits
-------------
Not in ``app/sandbox/`` — judging is pure sandbox work and must not know
about the database. Not in ``app/api/`` — AGENTS.md section 4 keeps route
modules free of business logic. Callers in ``app/api/`` pass in a
``JudgeResult`` and a :class:`SubmissionContext`; everything about what
gets stored lives here.

Threading
---------
Judging runs in a worker thread (``asyncio.to_thread`` from both
``app/api/judge.py`` and ``app/api/ws.py``). Every session this module
uses is therefore a **synchronous** one from :func:`app.db.get_session`,
never an async session — an async session bound to the event loop would
not be usable from that thread at all.

Recording is best-effort
------------------------
:func:`record_submission` never raises. A subscription failure must not
turn a finished verdict into an error: the user's job is to learn from
the verdict, and losing the history row is a much smaller harm than
losing the answer. Failures are logged at ERROR with the context needed
to reconstruct the row.

Automatic mistake-book archiving
--------------------------------
README promises that wrong answers are filed automatically, and that
claim was false at the code level. PU-13 makes the choice explicit and
implements it (option A in the slice definition): a WA submission with a
problem gets a :class:`MistakeBookEntry` with an empty ``error_cause``,
which Phase 2 / S2 fills in with the agent's analysis.

Archiving is **idempotent per problem and code**. Resubmitting the same
wrong code would otherwise pile up identical entries, which is noise the
user has to clean by hand. The dedupe key is (problem, language, code)
among entries with no ``error_cause`` yet — see
:func:`_has_open_duplicate`. Once the agent has annotated an entry it is
no longer "open" and a fresh identical mistake gets its own row, because
then the entries differ in what they say.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.sandbox.judge import JudgeResult, JudgeStatus

logger = logging.getLogger(__name__)

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


def _has_open_duplicate(session: Session, problem_id: int, fingerprint: str) -> bool:
    """Is there already an un-annotated mistake for this code?

    "Open" means ``error_cause`` is still empty — the state PU-13 leaves
    an auto-archived entry in. Once the agent fills that in, the entry
    carries a specific analysis and a later identical failure deserves
    its own row.
    """
    stmt = (
        select(MistakeBookEntry.id)
        .where(MistakeBookEntry.problem_id == problem_id)
        .where(MistakeBookEntry.error_cause == "")
        .where(MistakeBookEntry.error_cause_fingerprint == fingerprint)
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _archive_mistake(
    session: Session, submission: Submission, problem: Problem
) -> Optional[MistakeBookEntry]:
    """File a mistake-book entry for a wrong submission, if it is new.

    Returns the entry (existing or created), or None when the submission
    should not be archived.
    """
    assert submission.problem_id is not None  # caller checks
    fingerprint = _code_fingerprint(
        str(submission.problem_id), submission.language, submission.code
    )

    if _has_open_duplicate(session, submission.problem_id, fingerprint):
        logger.debug(
            "skipping duplicate mistake for problem %s", submission.problem_id
        )
        return None

    entry = MistakeBookEntry(
        submission_id=submission.id,
        problem_id=submission.problem_id,
        # Left empty on purpose: the agent fills this in during S2, and an
        # empty value is exactly what marks the entry as not yet analysed.
        error_cause="",
        # Snapshot the tags now, so re-tagging the problem later does not
        # retroactively rewrite this entry's history.
        knowledge_tags=list(problem.knowledge_tags or []),
        error_cause_fingerprint=fingerprint,
    )
    session.add(entry)
    return entry


def record_submission(
    result: JudgeResult,
    context: SubmissionContext,
    *,
    session: Optional[Session] = None,
) -> Optional[int]:
    """Store a finished judge run and return the new submission's id.

    Call this *after* judging, from the worker thread doing the judging or
    from a thread that owns the session passed in.

    Args:
        result: The verdict to store.
        context: The request-level details (code, language, problem).
        session: Session to write through. Defaults to a fresh synchronous
            session from :func:`app.db.get_session`. Tests pass an
            in-memory session; production leaves it out.

    Returns:
        The id of the stored submission, or ``None`` if nothing could be
        stored. ``None`` is a normal outcome, not an error — see the
        module docstring on why recording is best-effort.

    Note:
        This function swallows every exception. It is called on the path
        that delivers a verdict the user just waited for, and a database
        problem must not turn that into a failed request. Failures are
        logged at ERROR.
    """
    stdout, stderr = _pick_output(result)
    exec_time, memory_used = _worst_measurements(result)

    try:
        if session is not None:
            return _write(
                session, result, context, stdout, stderr, exec_time, memory_used
            )
        with get_session() as owned:
            return _write(
                owned, result, context, stdout, stderr, exec_time, memory_used
            )
    except Exception:
        logger.exception(
            "failed to record submission (problem_id=%s status=%s language=%s)",
            context.problem_id,
            result.status.value,
            context.language,
        )
        return None


def _write(
    session: Session,
    result: JudgeResult,
    context: SubmissionContext,
    stdout: str,
    stderr: str,
    exec_time: Optional[float],
    memory_used: Optional[int],
) -> int:
    """Insert the submission and, for a wrong answer, archive the mistake."""
    submission = Submission(
        problem_id=context.problem_id,
        status=result.status.value,
        code=context.code,
        language=context.language,
        stdout=stdout,
        stderr=stderr,
        exec_time=exec_time,
        memory_used=memory_used,
    )
    session.add(submission)
    # Assign the id before the mistake-book insert needs it as a FK.
    # flush() rather than commit() so the two rows land in one
    # transaction: a mistake pointing at a submission that was never
    # committed is the one inconsistency this must not create.
    session.flush()

    if (
        result.status in _ARCHIVABLE
        and context.problem_id is not None
        and submission.id is not None
    ):
        problem = session.get(Problem, context.problem_id)
        if problem is not None:
            _archive_mistake(session, submission, problem)

    session.commit()
    return submission.id


def record_submission_quietly(
    result: JudgeResult, context: SubmissionContext, **kwargs: object
) -> Optional[int]:
    """Record a verdict, guaranteeing that nothing escapes.

    The judge routes call this instead of :func:`record_submission`. The
    guarantee has to live at the call site rather than relying on a
    comment about the callee: the user is waiting for a verdict they
    already earned, and no failure on the history-write path — including
    one neither this module nor the caller anticipated — may turn that
    into an error response.

    :func:`record_submission` already swallows its own failures, so this
    is double protection for the database case and the *only* protection
    for anything raised before or around that call.
    """
    try:
        return record_submission(result, context, **kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.exception(
            "recording raised past its own guard (problem_id=%s status=%s)",
            context.problem_id,
            result.status.value,
        )
        return None
