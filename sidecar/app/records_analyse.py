"""Error analysis for filed mistakes — the agent half of the write path.

Split out of :mod:`app.records` (T13b). This module answers "what went wrong"
and writes the answer back onto the mistake-book entry; :mod:`app.records_write`
decides *that* an entry exists. The direction of the dependency is one-way:
the write path calls :func:`_analyse_and_annotate` after it has committed the
entry, and nothing here calls back into the write path.

Session access goes through :mod:`app.records_session` — see that module for
why it is not a plain `from app.db import get_session`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import select

from app import records_session
from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS
from app.models.mistake_book import (
    ANALYSIS_DONE,
    ANALYSIS_FAILED,
    ANALYSIS_PENDING,
    MistakeBookEntry,
)
from app.models.problem import Problem
from app.models.recurrence import Recurrence, find_recurrence
from app.models.submission import Submission
from app.records_suggestion import _format_suggestion, split_suggestion
from app.records_support import (
    SubmissionContext,
    _assemble_statement,
    _first_failing_case,
    _should_archive,
)
from app.sandbox.judge import JudgeResult

logger = logging.getLogger(__name__)

def _analyse_and_annotate(
    entry: MistakeBookEntry,
    result: JudgeResult,
    context: SubmissionContext,
    statement: str = "",
    on_event: Optional[Callable[[Any], None]] = None,
) -> Optional[dict[str, Any]]:
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
        on_event: Sink for tool-call notifications, so a WebSocket client
            can watch the Agent work (P2-8). ``None`` for the REST route,
            which has no stream to put them on.

    Returns:
        The ``agent_done`` payload for the caller to send **after it
        commits**, or ``None`` when no analysis ran at all — a CE, whose
        entry is filed with nothing to analyse.

        The terminal event is returned rather than sent from here, while
        ``agent_start`` and ``agent_tool`` *are* sent from here. That
        split is deliberate: progress events are only useful as they
        happen, but a terminal event is a claim that the work is
        finished, and this function does not own the transaction that
        makes the claim true. A client released by ``agent_done`` reads
        the mistake book next, and finding the entry it was just told
        about still on ``pending`` would be the same class of lie that
        ``app/api/ws.py`` goes out of its way to avoid for ``judge_done``.
    """
    # Imported lazily: this module is imported by the judge routes, and
    # app.agent pulls in app.llm.gateway which pulls in httpx. Keeping the
    # import inside the function means a deployment that never reaches
    # this branch does not pay for importing the LLM stack... and, more
    # practically, keeps the import graph acyclic while P2-4 is landing.
    #
    # ``pipeline`` rather than ``analyzer`` since the S4/S7 wiring: it
    # picks the orchestrator from ``ANALYSIS_PIPELINE`` and returns the
    # same ``AnalysisOutcome`` either way, so nothing below this line has
    # to know which one ran. The default is ``direct``, which is
    # ``analyzer.analyze_error`` unchanged — the graph is opt-in.
    from app.agent import pipeline

    failing = _first_failing_case(result)
    if failing is None:
        # Nothing to analyse. CE is what reaches here in practice:
        # compilation failed, so the judge ran no case and there is no
        # input / expected / actual to build a prompt from. Marking it
        # failed is the honest terminal state — an entry left on
        # "pending" reads as work that is still coming and never will.
        entry.analysis_status = ANALYSIS_FAILED
        logger.info(
            "no failing case to analyse for mistake book entry %s "
            "(verdict %s); marked failed",
            entry.id,
            result.status.value,
        )
        return None

    # P2-8's event vocabulary for the analysis phase. Emitted from here
    # rather than from the transport because this function is the only
    # place that knows whether an analysis is happening at all: a
    # submission that passes, or one with no problem row, never reaches
    # the code below, and a client that showed "Agent 分析中…" for those
    # would be watching a spinner for work that was never scheduled.
    def _tool_event(record: Any) -> None:
        if on_event is None:
            return
        on_event(
            {
                "type": "agent_tool",
                "name": record.name,
                "ok": record.ok,
                "result": record.result,
            }
        )

    if on_event is not None:
        on_event({"type": "agent_start", "verdict": result.status.value})

    outcome = pipeline.analyse(
        code=context.code,
        stdin=failing.input,
        expected=failing.expected_output,
        actual=failing.actual_output,
        statement=statement,
        verdict=result.status.value,
        # P2-8: the Agent gets to run the code before deciding. The
        # text-only path stays available and is what P2-5's accuracy gate
        # measures, but the product should use the better-informed one.
        use_tools=True,
        on_event=_tool_event,
    )

    # No terminal event here: it is returned to the caller, which sends it
    # once the entry is committed. See the Returns section.
    if not outcome.ok or outcome.result is None:
        # The entry says so out loud now. Before this it kept
        # ``agent_error_type = None``, which is also what an entry that
        # was never analysed looks like — so a provider outage produced an
        # entry nobody could tell apart from an untouched one, and nothing
        # in the UI could offer to try again.
        entry.analysis_status = ANALYSIS_FAILED
        logger.info(
            "error analysis unavailable for problem %s (%s); "
            "mistake book entry %s marked failed and retryable",
            context.problem_id,
            outcome.error,
            entry.id,
        )
        return {
            "type": "agent_done",
            "ok": False,
            "error": outcome.error,
            "error_type": None,
            "error_type_description": None,
            "tools": outcome.tool_names,
            # Present and null rather than absent: the frontend branches on
            # the value, and a key that appears only on success would make
            # "no diagnosis" and "the field was renamed" look the same.
            "suggestion": None,
            "error_detail": None,
            "hint": None,
            "suggested_fix": None,
            # Zero rather than absent, for the same reason as the fields
            # above: a client that has to guess whether the key exists
            # will eventually guess wrong. No label was settled on, so
            # there is no history to claim.
            "recurrence": Recurrence(label="").to_dict(),
        }

    analysis = outcome.result
    # Formatted once and used twice. The streamed diagnosis and the stored
    # column are then the same string by construction, so the practice
    # page and the mistake book cannot describe one analysis two ways.
    suggestion = _format_suggestion(analysis)
    entry.agent_error_type = str(analysis.error_type)
    entry.agent_suggestion = suggestion
    entry.analysis_status = ANALYSIS_DONE

    # 历史联动反馈 (阶段 1): "这个错因你之前也出现过 N 次".
    #
    # Read *after* the label is written and *excluding this entry*, so
    # the count is "how many times before now" rather than "including
    # this one" — an off-by-one here is the difference between "again"
    # and "for the first time".
    recurrence = _find_recurrence(entry, context, str(analysis.error_type))

    # Split server-side rather than re-parsing the labels in the client.
    # The mistake book serves these same three fields off the stored
    # column through the same parser, so the practice page can release
    # them one at a time without the two screens ever disagreeing about
    # where one section ends and the next begins.
    parts = split_suggestion(suggestion)
    label = str(analysis.error_type)

    return {
        "type": "agent_done",
        "ok": True,
        "error": None,
        "error_type": label,
        # The label in student-facing Chinese, or '' when this build does
        # not know it — same value, same source as the mistake book's.
        "error_type_description": ROOT_CAUSE_DESCRIPTIONS.get(label, ""),
        "tools": outcome.tool_names,
        "suggestion": suggestion,
        "error_detail": parts["error_detail"],
        "hint": parts["hint"],
        "suggested_fix": parts["suggested_fix"],
        # Present and never null, so the client can render it without a
        # branch. `count == 0` is the answer "this is the first time",
        # not an absence of an answer.
        "recurrence": recurrence.to_dict(),
    }


def _find_recurrence(
    entry: MistakeBookEntry, context: SubmissionContext, error_type: str
) -> Recurrence:
    """Look up this mistake's prior occurrences, without letting it fail.

    Wrapped rather than called inline because the lookup is a *bonus* on
    the diagnosis: the analysis the student is waiting for has already
    landed by the time this runs, and a database that will not answer
    here must not turn a successful analysis into a failed one.

    The session comes from the entry rather than from a new parameter, so
    :func:`_analyse_and_annotate` keeps the signature its tests pin.
    """
    from sqlalchemy.orm import Session as OrmSession

    try:
        session = OrmSession.object_session(entry)
        if session is None:
            return Recurrence(label=error_type)
        return find_recurrence(
            session,
            user_id=context.user_id,
            error_type=error_type,
            problem_id=context.problem_id,
            exclude_entry_id=entry.id,
        )
    except Exception:
        logger.exception("could not look up the history for mistake %s", entry.id)
        return Recurrence(label=error_type)




def will_analyse(result: JudgeResult, context: SubmissionContext) -> bool:
    """Whether recording this verdict will be followed by an analysis.

    The stream route asks this to fill in the ``analysing`` flag on
    ``judge_done``. The client cannot infer it — an accepted run and a
    failing one look identical up to that point — and guessing wrong is
    visible in both directions: a spinner that never stops, or a socket
    closed on events still in flight.

    Deliberately approximate in one direction: it does not check that the
    problem row exists, which only the write can know. On the judge routes
    the problem was resolved moments earlier, so it does.

    The second condition is not redundant. A CE is filed in the mistake
    book but has no case to analyse (see :func:`_first_failing_case`), so
    answering "an analysis is coming" for it would leave a client waiting
    on events that were never scheduled.
    """
    return (
        _should_archive(result, context) and _first_failing_case(result) is not None
    )


def analyse_submission(
    submission_id: int,
    result: JudgeResult,
    context: SubmissionContext,
    *,
    on_event: Optional[Callable[[Any], None]] = None,
) -> bool:
    """Analyse the mistake-book entry archived for a recorded submission.

    The second half of what :func:`record_submission` used to do in one
    call, split out so the caller can choose *when* to pay for it. The
    analysis is a network round trip that may take minutes; the verdict it
    annotates was ready in milliseconds.

    Args:
        submission_id: The submission whose entry should be annotated. The
            entry is looked up by it rather than passed in, so a caller
            holding only the id — a retry endpoint, say — needs nothing
            else.
        result: The verdict, for the failing case's I/O. Held in memory
            because the submission row does not store expected output, so
            this cannot be reconstructed from the database alone.
        context: The request details (code, language, problem).
        on_event: Sink for the Agent's progress events (P2-8).

    Returns:
        True when an analysis was attempted, False when there was nothing
        to analyse — an accepted run, a scratch run, or an entry that no
        longer exists. ``False`` is not a failure; the stream route reads
        it as "no further events are coming".

    Note:
        Unlike :func:`record_submission`, this does not swallow
        exceptions. The row is committed by the time it runs, so there is
        no user-visible verdict left to protect, and a caller that wants
        to retry needs to know it failed.
    """
    with records_session.get_session() as session:
        entry = session.execute(
            select(MistakeBookEntry).where(
                MistakeBookEntry.submission_id == submission_id
            )
        ).scalar_one_or_none()
        if entry is None:
            return False

        statement = ""
        if context.problem_id is not None:
            problem = session.get(Problem, context.problem_id)
            if problem is not None:
                statement = _assemble_statement(problem)

        done = _analyse_and_annotate(entry, result, context, statement, on_event)
        session.commit()
        # After the commit, so a client released by ``agent_done`` finds
        # the entry it names already readable.
        if done is not None and on_event is not None:
            on_event(done)
        return True


def _mark_analysis_failed(entry_id: int) -> None:
    """Mark an entry's analysis failed, tolerating a row that is gone.

    Used only on the retry path's early exits, where the alternative is
    leaving the entry on the ``pending`` that :func:`reanalyse_entry`
    just wrote. An entry deleted mid-retry is not an error worth raising
    over — there is nothing left to annotate.
    """
    with records_session.get_session() as session:
        entry = session.get(MistakeBookEntry, entry_id)
        if entry is not None:
            entry.analysis_status = ANALYSIS_FAILED
            session.commit()


def reanalyse_entry(entry_id: int) -> bool:
    """Re-run the analysis for an entry, by re-judging its submission.

    The retry path. A failed analysis used to be permanent: the failure
    was a log line, the entry stayed indistinguishable from one that had
    never been tried, and nothing could ask for it again. A provider
    outage therefore cost that mistake its diagnosis for good.

    Why this re-judges instead of replaying what was stored
    ------------------------------------------------------
    The analysis needs the failing case's input, expected output and
    actual output. The submission row keeps only the *actual*
    (``stdout``); the other two live in ``test_cases``, which does not
    record which case failed. Re-judging is the only way to recover the
    triple, and it is deterministic — same code, same cases, same limits,
    so the case that failed before is the case that fails now. The
    alternative was storing the triple on the entry, which would mean
    three columns duplicating something the judge produces on demand.

    The cost is a real compile-and-run, which is why the route schedules
    this on a background task rather than inside the request.

    Args:
        entry_id: The mistake-book entry to retry.

    Returns:
        True when an analysis was attempted, False when there was nothing
        to retry — a missing entry, a submission that is gone, a free run
        with no problem to re-judge against, or a problem whose cases no
        longer resolve. False is not a failure of this call; it means no
        further work is possible, and the entry's status says so.

    Note:
        Writes ``pending`` before doing any work, so a UI that polls sees
        the retry start rather than a stale terminal state. Exceptions
        propagate: unlike :func:`record_submission` there is no
        user-visible verdict left to protect, and the caller scheduled
        this deliberately.
    """
    # Imported lazily for the same reason ``_analyse_and_annotate`` does
    # it: this module is imported by the judge routes, and the sandbox
    # pulls in the compiler machinery that a never-retrying deployment
    # has no reason to load.
    from app.sandbox.judge import judge
    from app.sandbox.judge_input import JudgeInputError, resolve

    with records_session.get_session() as session:
        entry = session.get(MistakeBookEntry, entry_id)
        if entry is None:
            return False
        submission = session.get(Submission, entry.submission_id)
        if submission is None or submission.problem_id is None:
            return False
        submission_id = submission.id
        code = submission.code
        language = submission.language
        problem_id = submission.problem_id
        entry.analysis_status = ANALYSIS_PENDING
        session.commit()

    try:
        resolved = resolve(problem_id=problem_id)
    except JudgeInputError:
        # The problem was deleted or left without cases. There is no
        # input to re-judge against, so no amount of retrying helps.
        logger.warning(
            "cannot re-judge problem %s for mistake book entry %s; "
            "marking the analysis failed",
            problem_id,
            entry_id,
            exc_info=True,
        )
        _mark_analysis_failed(entry_id)
        return False

    try:
        result = judge(
            code,
            resolved.test_cases,
            time_limit=resolved.time_limit,
            mem_limit_mb=resolved.mem_limit_mb,
            language=language,
        )
        context = SubmissionContext(
            code=code,
            language=language,
            problem_id=problem_id,
            time_limit=resolved.time_limit,
            mem_limit_mb=resolved.mem_limit_mb,
        )
        return analyse_submission(submission_id, result, context)
    except Exception:
        # The entry was moved to pending above. Leaving it there would
        # read as work in progress that is never coming, so it is marked
        # before the exception continues on its way to the caller's log.
        _mark_analysis_failed(entry_id)
        raise


