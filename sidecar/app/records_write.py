"""Persisting a judge outcome — the recording half of the write path.

Split out of :mod:`app.records` (T13b). This module decides *that* a row and
possibly a mistake-book entry exist; :mod:`app.records_analyse` decides what
went wrong. The direction matters: the write path calls the analysis **after**
committing, so a slow provider never holds a lock — and the analysis never
calls back here.

Two things are worth knowing before editing:

* `record_submission` **never raises.** A finished verdict must not turn into
  an error; losing the history row is a much smaller harm than losing the
  answer.
* Session access goes through :mod:`app.records_session` — see that module for
  why it is not a plain `from app.db import get_session`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app import records_session
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.process_trace import describe, from_trace
from app.records_analyse import _analyse_and_annotate
from app.records_support import (
    SubmissionContext,
    _assemble_statement,
    _code_fingerprint,
    _find_open_duplicate,
    _pick_output,
    _should_archive,
    _worst_measurements,
)
from app.sandbox.judge import JudgeResult

logger = logging.getLogger(__name__)

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
    on_event: Optional[Callable[[Any], None]] = None,
    analyse: bool = True,
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
        on_event: Sink for the Agent's tool-call notifications (P2-8).
            Only consulted when ``analyse`` is true.
        analyse: Run the error analysis before returning. ``True`` is the
            original behaviour. ``False`` stops once the row is committed
            and leaves the analysis to :func:`analyse_submission`, which
            is what the judge routes want: the verdict is ready in
            milliseconds and the analysis can take minutes, so waiting
            for it inside the response is a delay the student feels and
            nothing else benefits from.

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
                session,
                result,
                context,
                stdout,
                stderr,
                exec_time,
                memory_used,
                on_event=on_event,
                analyse=analyse,
            )
        with records_session.get_session() as owned:
            return _write(
                owned,
                result,
                context,
                stdout,
                stderr,
                exec_time,
                memory_used,
                on_event=on_event,
                analyse=analyse,
            )
    except Exception:
        logger.exception(
            "failed to record submission (problem_id=%s status=%s language=%s)",
            context.problem_id,
            result.status.value,
            context.language,
        )
        return None



def _apply_trace(submission: Submission, context: SubmissionContext) -> None:
    """Derive and attach the edit-behaviour features, if there is a trace.

    Called between building the row and inserting it, so the features are
    part of the same insert as the verdict — there is no window in which
    a submission exists that claims to have no process data.

    A missing trace leaves the row's ``process_*`` columns at ``None``
    rather than zeroing them: "not observed" and "observed and
    unremarkable" are different statements, and the teacher's evidence
    panel reads them differently. Collapsing them would make every
    submission made outside the editor look like a clean one.

    Derivation is cheap — a few passes over at most
    :data:`app.process_trace.MAX_EVENTS` items — which is why it happens
    inline on the write path instead of being queued.
    """
    if context.trace is None:
        return
    features = from_trace(context.trace, context.code)
    submission.apply_process(features, describe(features).flagged)


def _write(
    session: Session,
    result: JudgeResult,
    context: SubmissionContext,
    stdout: str,
    stderr: str,
    exec_time: Optional[float],
    memory_used: Optional[int],
    on_event: Optional[Callable[[Any], None]] = None,
    analyse: bool = True,
) -> int:
    """Insert the submission and, for a wrong answer, archive the mistake."""
    submission = Submission(
        problem_id=context.problem_id,
        user_id=context.user_id,
        status=result.status.value,
        code=context.code,
        language=context.language,
        stdout=stdout,
        stderr=stderr,
        exec_time=exec_time,
        memory_used=memory_used,
    )
    _apply_trace(submission, context)
    session.add(submission)
    # Assign the id before the mistake-book insert needs it as a FK.
    # flush() rather than commit() so the two rows land in one
    # transaction: a mistake pointing at a submission that was never
    # committed is the one inconsistency this must not create.
    session.flush()

    if _should_archive(result, context) and submission.id is not None:
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
                if analyse:
                    done = _analyse_and_annotate(
                        entry, result, context, _assemble_statement(problem), on_event
                    )
                    session.commit()
                    # Sent only now, so the entry this announces is already
                    # readable. See the Returns section of
                    # ``_analyse_and_annotate`` for why it does not send it
                    # itself.
                    if done is not None and on_event is not None:
                        on_event(done)
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
