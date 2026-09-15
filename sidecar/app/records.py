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
:func:`_find_open_duplicate`. Once the agent has annotated an entry it is
no longer "open" and a fresh identical mistake gets its own row, because
then the entries differ in what they say.

P2-4: automatic error analysis
------------------------------
A filed entry is immediately handed to ``app.agent.analyzer.analyze_error``
and the result is written back to ``agent_error_type`` /
``agent_suggestion``. Three points about how that is scheduled:

* Analysis runs **after** the entry is flushed and committed, so a slow
  provider never holds a database lock for the duration of an HTTP
  request. The commit after annotation is a second, separate transaction.
* Failure to analyse is **absorbed**. The entry is still filed with
  ``agent_error_type = None``, which is the honest "not yet analysed"
  state and something a later phase can retry. An outage costs the
  student the analysis, never the record of their mistake.
* ``error_cause`` is **not** what the agent writes. It stays the user's
  field, and its emptiness remains the dedupe marker. A consequence
  worth knowing: an annotated entry therefore stays "open" forever and
  keeps absorbing resubmissions of the same code. That is the behaviour
  P2-4 ships, and ``tests/test_agent_analysis_recording.py`` pins it so a
  future change to the marker has to confront the interaction.
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


def _analyse_and_annotate(
    entry: MistakeBookEntry,
    result: JudgeResult,
    context: SubmissionContext,
    statement: str = "",
) -> None:
    """Run P2-3's analysis on a failing submission and fill in the entry.

    Runs **after** the entry exists, not before, for two reasons:

    1. ``analyze_error`` makes a network call to a paid API while the
       student waits. It must not sit inside the transaction that writes
       the submission — a slow or hanging provider would hold a database
       lock for the duration.
    2. The entry's own existence is what the analysis annotates. Creating
       it first also means that if annotation fails, the mistake is still
       filed and the entry keeps ``agent_error_type = None``, which is
       exactly the documented "not yet analysed" state a later phase can
       retry from.

    The failure case is deliberately silent at this level:
    ``analyze_error`` never raises for provider problems, and anything
    it does raise here is a bug that :func:`record_submission`'s outer
    guard will log rather than let reach the user.

    Args:
        entry: The (already flushed) mistake-book entry to annotate.
        result: The verdict, used for the failing case's I/O.
        context: The submission, for the source and problem id.
        statement: Problem statement, when the caller has it.
    """
    # Imported lazily: this module is imported by the judge routes, and
    # app.agent pulls in app.llm.gateway which pulls in httpx. Keeping the
    # import inside the function means a deployment that never reaches
    # this branch does not pay for importing the LLM stack... and, more
    # practically, keeps the import graph acyclic while P2-4 is landing.
    from app.agent.analyzer import analyze_error

    failing = next(
        (tc for tc in result.test_cases if tc.status != JudgeStatus.AC), None
    )
    if failing is None:
        return

    outcome = analyze_error(
        code=context.code,
        stdin=failing.input,
        expected=failing.expected_output,
        actual=failing.actual_output,
        statement=statement,
        verdict=result.status.value,
    )

    if not outcome.ok or outcome.result is None:
        logger.info(
            "error analysis unavailable for problem %s (%s); "
            "mistake book entry %s stays unanalysed",
            context.problem_id,
            outcome.error,
            entry.id,
        )
        return

    analysis = outcome.result
    entry.agent_error_type = str(analysis.error_type)
    entry.agent_suggestion = _format_suggestion(analysis)


def _format_suggestion(analysis: object) -> str:
    """Join the analysis prose into the single text column.

    The three prose fields are always displayed together and never
    queried individually, so they share one column (see the column
    comment in ``app/models/mistake_book.py``). Labelled with their
    Chinese headings so the stored string is still readable when dumped
    straight into a terminal or a bug report.
    """
    detail = getattr(analysis, "error_detail", "") or ""
    hint = getattr(analysis, "hint", "") or ""
    fix = getattr(analysis, "suggested_fix", "") or ""

    parts = []
    if detail:
        parts.append(f"【错在哪】{detail}")
    if hint:
        parts.append(f"【提示】{hint}")
    if fix:
        parts.append(f"【怎么改】{fix}")
    return "\n".join(parts)


def _archive_mistake(
    session: Session, submission: Submission, problem: Problem
) -> Optional[MistakeBookEntry]:
    """File a mistake-book entry for a wrong submission, if it is new.

    Returns the entry to annotate, or None when the submission should not
    be archived.

    On a duplicate hit this returns the **existing** open entry rather
    than None, so P2-4's analysis still runs. That is deliberate: the
    user resubmitted the same wrong code, which means the earlier
    analysis either does not exist yet or did not help. Re-analysing
    costs one API call and may succeed where the previous attempt failed
    (the entry is still "open" precisely because no analysis landed).
    Annotating an entry that already carries an analysis is impossible
    here: such an entry is no longer matched by :func:`_has_open_duplicate`.
    """
    assert submission.problem_id is not None  # caller checks
    fingerprint = _code_fingerprint(
        str(submission.problem_id), submission.language, submission.code
    )

    existing = _find_open_duplicate(session, submission.problem_id, fingerprint)
    if existing is not None:
        logger.debug(
            "reusing open mistake entry %s for problem %s",
            existing.id,
            submission.problem_id,
        )
        return existing

    entry = MistakeBookEntry(
        submission_id=submission.id,
        problem_id=submission.problem_id,
        # Left empty on purpose: P2-4's agent fills this in, and an empty
        # value is exactly what marks the entry as not yet analysed.
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
            entry = _archive_mistake(session, submission, problem)
            if entry is not None:
                # Commit the submission and the entry *before* the
                # analysis call. P2-4's analysis is a network round trip
                # to a paid API; holding this transaction open for its
                # duration would keep a database lock for as long as the
                # provider takes to answer.
                session.commit()
                _analyse_and_annotate(
                    entry, result, context, _assemble_statement(problem)
                )
                session.commit()
                return submission.id

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
