"""Pick the orchestrator that analyses a failing submission (S4/S7 接线).

Until this module existed, the judging path called
:func:`app.agent.analyzer.analyze_error` directly and nothing else was
reachable from it. S4's graph, S5's retriever, S7's team and S10's
guardrails were all implemented, tested and *unplugged* — which is why
README §四 has to qualify every one of them with "尚未接入主流程".

This module is the plug. :func:`analyse` takes the same arguments as
``analyze_error`` and returns the same :class:`AnalysisOutcome`, so
``records._analyse_and_annotate`` can call it without any other change;
which orchestrator actually runs is decided by
``settings.analysis_pipeline``.

Why the default stays ``direct``
--------------------------------
The switch ships **off**, and that is the whole point of doing this as a
switch rather than as a replacement:

* The graph costs up to **4 model calls per analysis** against direct's 2
  (each round still allows ``max_attempts=2``, and the self-check branch
  may run a second round). That spend happens while a student waits for
  ``judge_done``.
* AGENTS.md §三 makes S2's "20 道真题准确率 ≥ 60%" a go/no-go line, and
  the only way to measure it is ``pytest -m eval_set`` — a paid,
  metered run that neither the daily suite nor the nightly automation
  performs. Switching the orchestrator without running that would leave
  nobody able to say whether accuracy moved.

So the wiring lands here, tested on both branches, and flipping
``ANALYSIS_PIPELINE=graph`` stays a deliberate, supervised act.

Degrade, never raise
--------------------
``analyze_error`` promises not to raise for provider problems, and the
graph makes the same promise for model-side failures — but the graph is
new code driving a third-party runtime, and ``analyse_submission`` (unlike
``record_submission``) does not swallow exceptions. A raise from
``graph.invoke`` would therefore turn a *successfully judged* submission
into a 500. :func:`_via_graph` catches broadly and reports the failure the
way ``analyze_error`` reports a gateway outage: as an outcome with
``ok`` false. Same reasoning as ``app.rag.retriever.build_retriever``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from app.agent.analyzer import (
    DEFAULT_TOOL_STEPS,
    AnalysisOutcome,
    analyze_error,
)
from app.agent.tracing import traceable_if_enabled
from app.config import settings

logger = logging.getLogger(__name__)

#: Direct call to ``analyzer.analyze_error`` — the S2 path.
DIRECT = "direct"

#: S4's LangGraph state machine, with retrieval folded in.
GRAPH = "graph"

#: S7's three-agent team: diagnosis, then hint and variant workers in
#: parallel.
TEAM = "team"

#: Everything :func:`resolved_pipeline` accepts. Ordered.
PIPELINES = (DIRECT, GRAPH, TEAM)

#: What an unset or unrecognised ``ANALYSIS_PIPELINE`` means.
DEFAULT_PIPELINE = DIRECT


def resolved_pipeline() -> str:
    """Return the configured orchestrator, defaulting to ``DIRECT``.

    Read at call time rather than at import time, so a test (or an
    operator restarting the sidecar with a different ``.env``) sees the
    change without re-importing.

    A blank value is "not configured" and returns ``DIRECT`` silently —
    ``ANALYSIS_PIPELINE=`` is a legitimate way to spell the default. A
    *non-blank* value that names no pipeline is a typo, and a typo here
    would silently pick an orchestrator the operator did not ask for, so
    it is logged. Falling back rather than raising is deliberate: this
    runs inside the judging path, where the contract is that a missing
    analysis is an enrichment gap and never a failed submission.
    """
    raw = (settings.analysis_pipeline or "").strip()
    # Case- and whitespace-insensitive: ``ANALYSIS_PIPELINE=Graph`` in a
    # ``.env`` someone edited by hand is the same intent as ``graph``, and
    # answering it with a warning would be noise.
    normalised = raw.lower()

    if normalised in PIPELINES:
        return normalised

    if raw:
        logger.warning(
            "unknown ANALYSIS_PIPELINE %r; using %r (known: %s)",
            raw,
            DEFAULT_PIPELINE,
            ", ".join(PIPELINES),
        )
    return DEFAULT_PIPELINE


@traceable_if_enabled("analyse_submission")
def analyse(
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    *,
    statement: str = "",
    verdict: str = "WA",
    extra_context: Optional[str] = None,
    max_attempts: int = 2,
    use_tools: bool = False,
    on_event: Optional[Callable[[Any], None]] = None,
    max_steps: int = DEFAULT_TOOL_STEPS,
    tags: Sequence[str] = (),
    submission_id: Optional[int] = None,
    on_step: Optional[Callable[[str, str], None]] = None,
) -> AnalysisOutcome:
    """Analyse one failing submission with the configured orchestrator.

    Signature-compatible with
    :func:`app.agent.analyzer.analyze_error` (plus *tags*, which only the
    graph can use), and returns the same :class:`AnalysisOutcome` either
    way, so callers do not branch on which orchestrator ran.

    Args:
        code: The submitted source.
        stdin: The input that exposed the failure.
        expected: The expected output for that input.
        actual: What the program actually printed.
        statement: Problem statement, when available.
        verdict: The judge verdict, passed through to the prompt.
        extra_context: Retrieved knowledge to append to the prompt.
            Honoured on ``direct``; **ignored on ``graph``**, where the
            retrieve node produces the context itself — passing both would
            append the same cards twice. Left as ``None`` (the default),
            ``direct`` retrieves its own context when an embedder is
            configured, so the default path is no longer blind to the
            knowledge base. Pass ``""`` to say "no context" on purpose.
        max_attempts: Total attempts per analysis, including the first.
            ``direct`` only; the graph runs ``analyzer`` with its own
            default. See the module docstring on the cost difference.
        use_tools: Let the model run the code before answering.
        on_event: Called with each tool call as it completes (P2-8).
            Both orchestrators support it.
        max_steps: Model round trips per attempt on the tool path.
            ``direct`` only; the graph uses ``analyzer``'s default, which
            is the same value.
        tags: Knowledge-point tags to filter retrieval by. ``graph`` only.
        submission_id: Which submission this is, for the graph's checkpoint
            key (S8). ``graph`` only; ``direct`` has no thread to name and
            ``team`` does not checkpoint. ``None`` for an unrecorded run.
        on_step: Called with ``(node, label)`` as the graph's nodes finish
            (S8 streaming). ``graph`` only — ``direct`` and ``team`` are
            opaque single calls with nothing to report between start and
            end, and inventing a step for them would be a lie the client
            cannot detect.

    Returns:
        An :class:`AnalysisOutcome`; ``ok`` is False when no analysis
        could be produced. Never raises for a model-side failure.
    """
    chosen = resolved_pipeline()
    if chosen == TEAM:
        return _via_team(
            code,
            stdin,
            expected,
            actual,
            statement=statement,
            verdict=verdict,
            use_tools=use_tools,
            on_event=on_event,
            tags=tags,
        )

    if chosen == GRAPH:
        return _via_graph(
            code,
            stdin,
            expected,
            actual,
            statement=statement,
            verdict=verdict,
            use_tools=use_tools,
            on_event=on_event,
            tags=tags,
            submission_id=submission_id,
            on_step=on_step,
        )

    # Consult the knowledge base on the default path too — before this,
    # retrieval only ever ran inside the ``graph`` branch, which nothing
    # selects by default, so a real student submission was analysed with
    # no reference material at all. See _retrieve_context.
    retrieved: list[str] = []
    if extra_context is None:
        extra_context, retrieved = _retrieve_context(statement, code, tags)

    outcome = analyze_error(
        code,
        stdin,
        expected,
        actual,
        statement=statement,
        verdict=verdict,
        extra_context=extra_context,
        max_attempts=max_attempts,
        use_tools=use_tools,
        on_event=on_event,
        max_steps=max_steps,
    )
    # Attached rather than logged and dropped, so the interface can name
    # the material an explanation drew on. Retrieval failing is invisible
    # by design — a missing enrichment must not fail a verdict — and this
    # is what keeps "invisible" from becoming "unverifiable".
    outcome.knowledge_sources = retrieved
    return outcome


def _retrieve_context(
    statement: str,
    code: str,
    tags: Sequence[str],
) -> tuple[str, list[str]]:
    """Knowledge-card context for one submission, and where it came from.

    Until 2026-09-21 the ``direct`` default made its one model call with no
    knowledge base at all: retrieval lived in the ``graph`` branch, which
    nothing selects by default, and no embedder was ever constructed. The
    code was present and tested and unreachable at the same time.

    The query is the *statement* when there is one, and the code only as a
    fallback — the same choice :func:`app.agent.graph._read_code` makes,
    for the same reason. Searching with the student's own buggy source
    retrieves cards about the mistake they made, and working out that
    mistake is exactly what the model is being asked to do; feeding it
    back biases retrieval toward whatever the code superficially looks
    like it is doing.

    Returns:
        ``(context, titles)``: *context* is a prompt block or ``""``, and
        *titles* names the cards it was built from so the caller can show
        them. Never raises: this runs inside the judging path, where a
        missing enrichment is an acceptable outcome and a failed verdict
        is not.
    """
    if not settings.rag_enabled:
        return "", []

    query = (statement or "").strip() or (code or "").strip()
    if not query:
        return "", []

    try:
        from app.rag import (  # noqa: PLC0415 — see note below
            build_embedder,
            build_retriever,
            format_context,
            resolve_milvus_uri,
        )
    except ImportError:
        # Imported here rather than at module level for the same reason
        # ``app.agent.graph`` is: the RAG stack pulls in pymilvus, and a
        # deployment with retrieval switched off should not pay for it.
        logger.info("rag stack unavailable; continuing without retrieval")
        return "", []

    embedder = build_embedder()
    if embedder is None:
        return "", []

    try:
        retriever = build_retriever(resolve_milvus_uri(), embedder=embedder)
        cards = retriever.search(query, tags=tuple(tags or ()))
    except Exception as exc:  # noqa: BLE001 — degradation is the contract
        logger.warning("knowledge retrieval failed (%s); continuing without", exc)
        return "", []

    context = format_context(cards)
    titles = [card.title for card in cards if card.title]
    if context:
        logger.info(
            "retrieved %d knowledge card(s) for this analysis: %s",
            len(cards),
            ", ".join(titles),
        )
    return context, titles


def _via_graph(
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    *,
    statement: str,
    verdict: str,
    use_tools: bool,
    on_event: Optional[Callable[[Any], None]],
    tags: Sequence[str],
    submission_id: Optional[int] = None,
    on_step: Optional[Callable[[str, str], None]] = None,
) -> AnalysisOutcome:
    """Run S4's graph and flatten its outcome into an ``AnalysisOutcome``.

    ``GraphOutcome`` is already shaped like ``AnalysisOutcome`` on purpose
    (see its docstring), so this is a field rename, not a translation.
    What the graph knows and a single call does not — ``retrieved``,
    ``context``, ``notes``, ``retries`` — is logged rather than carried
    further: the mistake-book schema has nowhere to put it, and inventing
    one is a separate change.
    """
    # Imported here, not at module level: ``app.agent.graph`` pulls in
    # langgraph and the RAG stack, and a deployment that keeps the default
    # ``direct`` pipeline has no reason to import either. records.py
    # imports *this* module lazily for the same reason, so the judge routes
    # stay light on both counts.
    from app.agent.graph import run_analysis_graph

    try:
        outcome = run_analysis_graph(
            code,
            stdin,
            expected,
            actual,
            statement=statement,
            verdict=verdict,
            tags=tags,
            use_tools=use_tools,
            on_event=on_event,
            submission_id=submission_id,
            on_step=on_step,
        )
    except Exception as exc:  # noqa: BLE001 — degradation is the contract
        # Not swallowed quietly: an exception here is a bug in the graph or
        # its runtime, and it would otherwise be invisible as "no analysis".
        logger.exception("analysis graph raised; treating analysis as unavailable")
        return AnalysisOutcome(error=f"{type(exc).__name__}: {exc}", attempts=0)

    if outcome.notes:
        logger.info(
            "analysis graph self-check raised %d note(s): %s",
            len(outcome.notes),
            "; ".join(outcome.notes),
        )

    # S8: the run's node-by-node checkpoints are filed under this key. It
    # goes to the log because nothing in the UI shows it yet, and "which
    # run produced this analysis" has to be answerable from the log alone.
    if outcome.thread_id:
        logger.info("analysis graph run recorded as thread %s", outcome.thread_id)

    return AnalysisOutcome(
        result=outcome.analysis,
        error=outcome.error,
        attempts=outcome.attempts,
        tool_calls=list(outcome.tool_calls),
    )


def _via_team(
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    *,
    statement: str = "",
    verdict: str = "WA",
    use_tools: bool = False,
    on_event: Optional[Callable[[Any], None]] = None,
    tags: Sequence[str] = (),
) -> AnalysisOutcome:
    """S7's three agents, exposed through the same switch as S4's graph.

    Same promise as :func:`_via_graph`: never raises for a model-side
    failure, and returns the same :class:`AnalysisOutcome`, so the write
    path does not know which orchestrator ran.

    The hint ladder and the variant problem ride along on the returned
    :class:`AnalysisOutcome` (T28). They used to be logged and dropped,
    because nothing downstream had a field for them — which meant the
    team's roughly three-times-higher model bill bought the student
    nothing they could see. They are still *optional*: a worker that
    failed leaves its field ``None``, and the two other pipelines leave
    both ``None``.
    """
    # Lazy for the same reason as the graph: `app.agent.team` pulls in
    # langgraph, and the default pipeline never needs it.
    from app.agent.team import run_team

    try:
        outcome = run_team(
            code,
            stdin,
            expected,
            actual,
            statement=statement,
            verdict=verdict,
            tags=tags,
            use_tools=use_tools,
        )
    except Exception as exc:  # noqa: BLE001 — degradation is the contract
        logger.exception("agent team raised; treating analysis as unavailable")
        return AnalysisOutcome(error=f"{type(exc).__name__}: {exc}", attempts=0)

    if outcome.notes:
        logger.info(
            "team diagnosis self-check raised %d note(s): %s",
            len(outcome.notes),
            "; ".join(outcome.notes),
        )

    # Which workers ran and what the run cost stays a log line: the
    # outcome has no field for a worker roster, and the two columns the
    # write path fills say what was *produced*, not who was asked. A
    # degraded run (``ok and not complete``) is otherwise invisible —
    # a missing ladder looks exactly like a pipeline that never asks for
    # one.
    if outcome.workers:
        logger.info(
            "team ran %s (produced=%s, %d calls)",
            ",".join(outcome.workers) or "-",
            ",".join(outcome.produced) or "-",
            outcome.attempts,
        )
    for label, err in (
        ("hint", outcome.hint_error),
        ("variant", outcome.variant_error),
    ):
        if err:
            logger.warning("team %s worker produced nothing: %s", label, err)

    return AnalysisOutcome(
        result=outcome.diagnosis,
        error=outcome.error,
        attempts=outcome.attempts,
        hint_ladder=outcome.hint,
        variant=outcome.variant,
    )
