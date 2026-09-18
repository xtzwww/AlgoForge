"""Persist judge outcomes — the write path for ``submissions`` (PU-13).

Why this module exists
----------------------
Before PU-13 the ``submissions`` table had **no writer**. ``MistakeBookEntry``
requires a ``submission_id``, and ``app/api/mistake_book.py`` refuses a
submission that does not exist, so the mistake book could only be reached
by a client that already knew an id it had no way to obtain. That is gap 7
this module closes it.

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
* Failure to analyse is **absorbed**. The entry is still filed, and its
  ``analysis_status`` records which of the two outcomes actually
  happened: ``failed`` when the analysis ran and produced nothing,
  ``pending`` when it has not run yet. An outage costs the student the
  analysis, never the record of their mistake — and the entry can be
  retried later (:func:`reanalyse_entry`).
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
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
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
from app.process_trace import ProcessTrace, describe, from_trace
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
    from app.agent.analyzer import analyze_error

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

    outcome = analyze_error(
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


def _format_suggestion(analysis: object) -> str:
    """Join the analysis prose into the single text column.

    The three prose fields share one column rather than three because
    they are written as a unit and were, until :func:`split_suggestion`
    existed, read as a unit too. Labelled with their Chinese headings so
    the stored string is still readable when dumped straight into a
    terminal or a bug report.

    The labels are load-bearing: :func:`split_suggestion` parses them
    back out, and the frontend uses the split to release the three
    parts one at a time instead of handing the student the fix up
    front. So the format here and the parser below are one contract —
    change one, change both, and :data:`_SUGGESTION_LABELS` is the
    single source for the label strings.
    """
    detail = getattr(analysis, "error_detail", "") or ""
    hint = getattr(analysis, "hint", "") or ""
    fix = getattr(analysis, "suggested_fix", "") or ""

    parts = []
    if detail:
        parts.append(f"{_SUGGESTION_LABELS['error_detail']}{detail}")
    if hint:
        parts.append(f"{_SUGGESTION_LABELS['hint']}{hint}")
    if fix:
        parts.append(f"{_SUGGESTION_LABELS['suggested_fix']}{fix}")
    return "\n".join(parts)


#: The Chinese headings ``_format_suggestion`` writes and
#: :func:`split_suggestion` reads back. Kept as data rather than as
#: literals in both functions so a reworded heading cannot desynchronise
#: the writer from the parser.
_SUGGESTION_LABELS: dict[str, str] = {
    "error_detail": "【错在哪】",
    "hint": "【提示】",
    "suggested_fix": "【怎么改】",
}


def split_suggestion(suggestion: str) -> dict[str, str]:
    """Split ``agent_suggestion`` back into its three prose fields.

    The inverse of :func:`_format_suggestion`. Exists so the API can
    serve the parts separately and the frontend can release them one at
    a time — the point of which is pedagogical: ``hint`` is a nudge and
    ``suggested_fix`` is the answer, and presenting them together means
    the student reads the answer and never uses the nudge. The storage
    format is unchanged; only the read path gained structure.

    Parsed leniently rather than strictly, for three reasons:

    * **Old rows.** ``agent_suggestion`` is free text that predates any
      contract. A row written before a label was renamed, or by a
      different prompt version, simply won't match — and must still
      render. Anything unmatched is returned as-is under
      ``error_detail``, which is the field the UI always shows first, so
      the worst case is "the whole text appears at once" (the old
      behaviour) rather than "the text disappears".
    * **Model-authored newlines.** The prose itself may contain the
      label of a *later* section, or contain no label at all. Splitting
      on the first occurrence of each known label in order is enough to
      survive that; a strict line-by-line grammar would not.
    * **Cost of being wrong.** A false split shows a student a
      mislabelled paragraph. A lost split shows nothing. The lenient
      path degrades to the previous behaviour, so this is the safe
      direction to fail in.

    Args:
        suggestion: The stored column value. May be empty, may be
            unlabelled free text, may hold any subset of the three
            sections.

    Returns:
        A dict with keys ``error_detail``, ``hint`` and ``suggested_fix``.
        Every key is present; a section that was not found is ``""``.
        When no label matches at all, the whole input is returned under
        ``error_detail``.
    """
    if not suggestion:
        return {"error_detail": "", "hint": "", "suggested_fix": ""}

    # Locate each label, keeping only the first occurrence of each. A
    # label appearing later inside another section's prose is ignored,
    # because by then we already have that section's start.
    found: list[tuple[int, str]] = []
    # `section`, not `field`: `field` is the dataclasses import above, and
    # shadowing an import inside a loop is the kind of thing that turns
    # into a real bug the moment the function grows a `@dataclass`.
    for section, label in _SUGGESTION_LABELS.items():
        at = suggestion.find(label)
        if at != -1:
            found.append((at, section))

    if not found:
        # Unlabelled free text: hand it back whole so the UI still has
        # something to show.
        return {
            "error_detail": suggestion,
            "hint": "",
            "suggested_fix": "",
        }

    found.sort()
    result = {"error_detail": "", "hint": "", "suggested_fix": ""}
    for index, (start, section) in enumerate(found):
        body_start = start + len(_SUGGESTION_LABELS[section])
        body_end = found[index + 1][0] if index + 1 < len(found) else len(suggestion)
        result[section] = suggestion[body_start:body_end].strip()
    return result


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
        with get_session() as owned:
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


def _should_archive(result: JudgeResult, context: SubmissionContext) -> bool:
    """Whether recording this verdict files a mistake-book entry.

    One definition, used both by the write itself and by
    :func:`will_analyse`. The stream route needs the same answer *before*
    the write happens, and two copies of this condition would drift.
    """
    return result.status in _ARCHIVABLE and context.problem_id is not None


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
    with get_session() as session:
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
    with get_session() as session:
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

    with get_session() as session:
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
