"""Three-agent collaboration over the S4 diagnosis graph (阶段 8 · S7).

``AGENTS.md`` §三 S7 asks for "错因/提示/变式题三 Agent 协作". S4 already
built the diagnosis as a state graph; S7 puts three *workers* under one
orchestrator and has them share what the first one learned.

Topology
--------
::

    START → diagnose ─┬→ hint ────┐
                      └→ variant ─┴→ assemble → END

``diagnose`` is the S4 graph — :func:`app.agent.graph.build_analysis_graph`'s
compiled product — added to this graph **as a node**, so the parent calls
the child graph instead of re-implementing its four steps. ``hint`` and
``variant`` are siblings: both read the diagnosis and neither waits for
the other, which is what a conditional edge returning a *list* of
destinations expresses. ``assemble`` is the join that waits for both and
records what the team produced.

Why the workers are siblings rather than a chain
------------------------------------------------
The hint ladder and the variant problem are independent readings of the
same diagnosis. Chaining them would make the variant wait for the hint
for no reason, and would turn a slow hint agent into a slow variant
agent. Neither is cheap to serialize: both are full model calls.

Why a worker failure does not fail the run
------------------------------------------
``AGENTS.md`` frames the whole analysis as an enrichment of a submission
that was already judged. That framing does not change when there are
three workers: a hint ladder that could not be parsed is a missing
ladder, not a broken submission. Each worker therefore absorbs its own
gateway and parse failures into ``hint_error`` / ``variant_error``, and
the run returns whatever did come back. One worker failing cannot take
the other down with it.

A worker retries a *parse* failure once, and carries it back
------------------------------------------------------------
This started as "workers do not retry", on the grounds that a rejected
hint leaves the diagnosis intact while a rejected diagnosis leaves the
student with nothing. The first real-model run of the team showed the
cost: the diagnosis came back ``overflow`` and the ladder parsed, but the
variant agent's reply was not valid JSON (a malformed object at character
654), so a section the student was owed simply went missing. Retrying
with the offending output and the parser's complaint attached — the same
correction ``analyzer`` sends — turns that into a second, usually
successful, attempt.

What is *not* retried is a gateway failure: re-sending the same request
would fail identically and the retry would be paid for twice, which is
also ``analyzer``'s call.

What the workers are given, and what they are not
-------------------------------------------------
Both workers see the diagnosis's label and ``error_detail``. Neither is
given ``suggested_fix``: the point of a hint ladder is that the student
finds the fix, and handing the hint agent the answer is exactly how a
"hint" degrades into a paraphrase of it.

The knowledge context is the diagnosis's own retrieval, reused rather
than re-queried. The query has not changed, so a second search would
return the same cards and only add an embedding round trip to a path a
student is waiting on.

Not wired into the judging path
-------------------------------
Like S4's graph, this module is additive: nothing calls it from
``records.py``. S7's completion definition is that the three agents
collaborate and a full run completes — not that the student-facing route
switched orchestrators. That swap is a separate, deliberate step on the
latency path.
"""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Optional, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.analyzer import DEFAULT_TEMPERATURE, NO_STATEMENT_NOTE
from app.agent.graph import DEFAULT_MAX_RETRIES, build_analysis_graph
from app.agent.guardrails import guarded_chat_completion
from app.agent.schemas import (
    ErrorAnalysisResult,
    HintLadderResult,
    StructuredOutputParseError,
    VariantResult,
    parse_hint_ladder,
    parse_variant,
)
from app.config import settings
from app.llm.gateway import LLMGatewayError, chat_completion
from app.prompts import loader
from app.rag.retriever import RetrievedCard, Retriever

logger = logging.getLogger(__name__)

# Node names. Constants rather than bare strings because the router, the
# edge map and the outcome all refer to them — a typo in any one would
# surface as ``InvalidUpdateError`` naming a node, not the string that was
# wrong.
DIAGNOSE = "diagnose"
HINT = "hint"
VARIANT = "variant"
ASSEMBLE = "assemble"

#: The two agent nodes, in the order the outcome reports them. ``DIAGNOSE``
#: is a subgraph and ``ASSEMBLE`` is a join; neither is a *worker* in the
#: sense S7 means.
WORKERS: tuple[str, ...] = (HINT, VARIANT)

# Prompt templates this module drives, named here so the template and the
# call site stay in one place.
HINT_PROMPT = "hint_ladder"
VARIANT_PROMPT = "variant_problem"
#: Sent after an unusable reply. Shared by both workers: the correction is
#: about JSON syntax, which has nothing to do with which agent asked.
CORRECTION_PROMPT = "json_correction"

#: Attempts allowed per worker call. Two, matching ``analyzer``: a reply
#: that cannot be parsed is usually one bad character away from being
#: usable, and showing the model its own output is what makes the second
#: attempt a correction rather than a second opinion. Each attempt is a
#: paid call in front of a waiting student, so there is no third.
WORKER_ATTEMPTS = 2

#: How much of an unusable reply to put in the log line. Enough to see
#: the trailing comma; short enough that the log stays readable.
RAW_IN_LOG_CHARS = 500

#: Substituted for an empty retrieval, for the same reason
#: ``analyzer.NO_STATEMENT_NOTE`` exists: a blank block reads to the model
#: as "the knowledge base says nothing", which is a claim retrieval never
#: made.
NO_KNOWLEDGE_NOTE = "（本次没有检索到知识卡片，请只依据上面的代码与错因作答。）"


class TeamState(TypedDict, total=False):
    """The parent graph's shared state.

    It is a **superset of** :class:`app.agent.graph.AnalysisState`, and
    that is a requirement rather than a coincidence: ``diagnose`` is the
    compiled analysis graph used as a node, so every key that subgraph
    writes must exist as a channel here. A key added to ``AnalysisState``
    and not to this class fails at *runtime*, inside LangGraph, with an
    error naming a channel — which is why ``tests/test_agent_team.py``
    asserts the superset relation directly.

    The accumulating keys (``Annotated`` with ``operator.add``) are the
    ones written by more than one node. ``hint``/``variant`` and their
    errors are plain: each is written by exactly one node.
    """

    # --- Inputs: the fixed facts about one submission.
    code: str
    stdin: str
    expected: str
    actual: str
    statement: str
    verdict: str
    tags: list[str]

    # --- What the diagnose subgraph writes back.
    query: str
    retrieved: list[RetrievedCard]
    context: str
    analysis: Optional[ErrorAnalysisResult]
    error: Optional[str]
    #: Model calls made by ``analyzer``, plus every call the two workers
    #: made — retries included. I.e. what the whole team spent on this
    #: submission.
    attempts: Annotated[int, operator.add]
    tool_calls: Annotated[list[Any], operator.add]
    decision: str
    retries: int
    notes: Annotated[list[str], operator.add]

    # --- The two workers.
    hint: Optional[HintLadderResult]
    hint_error: Optional[str]
    variant: Optional[VariantResult]
    variant_error: Optional[str]

    #: The agent nodes that actually executed. A worker that ran and
    #: failed is in here but not in ``produced``, which is how a degraded
    #: run is distinguishable from one whose branch never fired. The
    #: subgraph and the join are not agents and are not in here. Order is
    #: not guaranteed between the two parallel workers.
    workers: Annotated[list[str], operator.add]
    #: Workers that came back with something usable. Written by the join,
    #: so a non-empty value is also evidence that the join ran.
    produced: list[str]


@dataclass
class TeamOutcome:
    """What one run of the team produced.

    ``diagnosis`` is the same object
    :func:`app.agent.graph.run_analysis_graph` returns, so a caller can
    read the team's result with the code it already has.

    Three levels of "it worked", because collapsing them loses the only
    information a degraded run carries:

    * :attr:`ok` — a diagnosis was produced. This is the gate: without it
      no worker ran at all.
    * :attr:`complete` — all three agents produced something.
    * the per-worker ``*_error`` fields — *why* a worker that ran came
      back empty.
    """

    diagnosis: Optional[ErrorAnalysisResult] = None
    hint: Optional[HintLadderResult] = None
    variant: Optional[VariantResult] = None

    #: Why the diagnosis failed, when it did.
    error: Optional[str] = None
    hint_error: Optional[str] = None
    variant_error: Optional[str] = None

    retrieved: list[RetrievedCard] = field(default_factory=list)
    context: str = ""
    #: Self-check complaints from the diagnosis, oldest first.
    notes: list[str] = field(default_factory=list)
    #: Model calls the whole team made.
    attempts: int = 0
    #: The agent nodes that actually ran. See ``TeamState.workers``.
    workers: list[str] = field(default_factory=list)
    #: Of :attr:`workers`, the ones that produced usable output.
    produced: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """The diagnosis came back — the precondition for anything else."""
        return self.diagnosis is not None

    @property
    def complete(self) -> bool:
        """Every agent in the team produced output.

        ``ok and not complete`` is the interesting state: a usable
        diagnosis with a missing hint ladder or variant. It is a
        degradation, not a failure, and the caller decides whether to
        show it.
        """
        return (
            self.diagnosis is not None
            and self.hint is not None
            and self.variant is not None
        )


def _statement_block(statement: str) -> str:
    """The statement, or the explicit note that there is none."""
    text = (statement or "").strip()
    return text if text else NO_STATEMENT_NOTE


def _knowledge_block(context: str) -> str:
    """Retrieved cards as a prompt block, or a note that nothing was found."""
    text = (context or "").strip()
    return text if text else NO_KNOWLEDGE_NOTE


def _render_hint_prompt(state: TeamState, diagnosis: ErrorAnalysisResult) -> str:
    """Assemble the hint agent's prompt for one diagnosed submission."""
    return loader.render(
        HINT_PROMPT,
        verdict=state.get("verdict", "WA"),
        stdin=state.get("stdin", ""),
        expected_output=state.get("expected", ""),
        actual_output=state.get("actual", ""),
        statement=_statement_block(state.get("statement", "")),
        code=state.get("code", ""),
        error_type=str(diagnosis.error_type),
        error_detail=diagnosis.error_detail,
        knowledge_context=_knowledge_block(state.get("context", "")),
    )


def _render_variant_prompt(state: TeamState, diagnosis: ErrorAnalysisResult) -> str:
    """Assemble the variant agent's prompt for one diagnosed submission."""
    return loader.render(
        VARIANT_PROMPT,
        statement=_statement_block(state.get("statement", "")),
        code=state.get("code", ""),
        error_type=str(diagnosis.error_type),
        error_detail=diagnosis.error_detail,
        knowledge_context=_knowledge_block(state.get("context", "")),
    )


def _correction_message(previous: str, problem: str) -> str:
    """Render the follow-up sent after a worker's reply cannot be parsed.

    Lives in a template file like every other prompt (``AGENTS.md`` §二.1);
    only the *decision* to send it is Python.
    """
    return loader.render(CORRECTION_PROMPT, previous=previous, problem=problem)


def _ask_worker(
    prompt: str,
    parser: Any,
    label: str,
    *,
    max_attempts: int = WORKER_ATTEMPTS,
) -> tuple[Any, Optional[str], int]:
    """Make up to ``max_attempts`` model calls for one worker.

    Returns ``(result, error, calls)`` — ``calls`` is what was actually
    spent, so the outcome's attempt count stays honest when a retry fires.

    Never raises: the caller is a graph node on a path a student is
    waiting on, and a worker that cannot answer is a missing section of
    the feedback rather than a reason to fail the run.

    A parse failure retries with the offending output and the parser's
    complaint attached, so the second attempt is a correction rather than
    a second opinion. A *gateway* failure does not retry — re-sending the
    same request would fail identically — which is ``analyzer``'s call
    too. ``label`` names the worker in the log line, and the unusable
    reply is logged with it: without that, "not valid JSON at position
    654" is a finding nobody can act on.
    """
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]

    # Same switch as the analyser: the team is a batch of text calls, so
    # `guarded_chat_completion` (text-only, no tools) is the right entry.
    call = guarded_chat_completion if settings.llm_guardrails else chat_completion

    for attempt in range(1, max_attempts + 1):
        try:
            raw = call(
                messages,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout,
            )
        except LLMGatewayError as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s agent produced nothing: %s", label, error)
            return None, error, attempt

        try:
            return parser(raw), None, attempt
        except StructuredOutputParseError as exc:
            error = str(exc)
            logger.warning(
                "%s agent reply could not be parsed: %s\nraw: %s",
                label,
                error,
                raw[:RAW_IN_LOG_CHARS],
            )
            if attempt >= max_attempts:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": _correction_message(raw, error)},
            ]

    return None, error, attempt


def _hint(state: TeamState) -> dict[str, Any]:
    """The 提示 agent: a three-rung ladder for the diagnosed bug."""
    diagnosis = state.get("analysis")
    if diagnosis is None:
        # The router stops the run before this, so the branch is only
        # reachable by a caller that drives the node directly. Guarding
        # anyway: an ``AttributeError`` raised inside a graph run is far
        # harder to read than an empty worker record.
        return {"workers": [HINT]}

    result, error, calls = _ask_worker(
        _render_hint_prompt(state, diagnosis), parse_hint_ladder, HINT
    )
    return {
        "hint": result,
        "hint_error": error,
        "workers": [HINT],
        "attempts": calls,
    }


def _variant(state: TeamState) -> dict[str, Any]:
    """The 变式题 agent: a transfer problem aimed at the same misconception."""
    diagnosis = state.get("analysis")
    if diagnosis is None:
        return {"workers": [VARIANT]}

    result, error, calls = _ask_worker(
        _render_variant_prompt(state, diagnosis), parse_variant, VARIANT
    )
    return {
        "variant": result,
        "variant_error": error,
        "workers": [VARIANT],
        "attempts": calls,
    }


def _assemble(state: TeamState) -> dict[str, Any]:
    """Join the two workers and record what the team actually produced.

    Its real job is to be the common successor of ``hint`` and
    ``variant``: that is what makes the graph wait for both before it
    ends. Recording the outcome is the reason it is a node and not a
    no-op join, and it is also why a degraded run is legible afterwards.
    """
    produced = [name for name in WORKERS if state.get(name) is not None]
    return {"produced": produced}


def _after_diagnosis(state: TeamState) -> list[str] | str:
    """Fan out to both workers, or stop when there is nothing to build on.

    Returning a *list* is what makes the workers siblings rather than a
    chain: LangGraph starts every destination in the same superstep.

    A failed diagnosis ends the run here rather than letting the workers
    try anyway. Neither prompt can be rendered without a label and an
    ``error_detail``, so there would be nothing to send — and inventing a
    fallback ("analyse it again, then") is what the diagnosis graph's own
    retry already decided not to do.
    """
    if state.get("analysis") is None:
        return END
    return [HINT, VARIANT]


def build_team_graph(
    retriever: Retriever | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    use_tools: bool = False,
) -> Any:
    """Build the compiled diagnose → (hint ‖ variant) → assemble graph.

    Args:
        retriever: Knowledge source for the diagnosis subgraph's
            ``retrieve`` node, and therefore for both workers' context.
            Built from :func:`app.rag.retriever.build_retriever` when
            omitted, which degrades to a no-op retriever rather than
            raising.
        max_retries: Self-check retries the diagnosis subgraph may spend.
        use_tools: Offer the diagnosis's model the ``run_code`` /
            ``judge`` tools. Off by default, matching ``analyze_error``.

    Returns:
        A compiled graph. ``invoke`` takes a :class:`TeamState`; the
        accumulating keys need no seeding, so ``invoke({"code": ...})`` is
        a valid call.
    """
    diagnosis = build_analysis_graph(
        retriever, max_retries=max_retries, use_tools=use_tools
    )

    graph = StateGraph(TeamState)
    # A compiled graph as a node: the parent calls the child, so the four
    # diagnosis steps exist once, in one graph, for both entry points.
    graph.add_node(DIAGNOSE, diagnosis)
    graph.add_node(HINT, _hint)
    graph.add_node(VARIANT, _variant)
    graph.add_node(ASSEMBLE, _assemble)

    graph.add_edge(START, DIAGNOSE)
    graph.add_conditional_edges(
        DIAGNOSE, _after_diagnosis, {HINT: HINT, VARIANT: VARIANT, END: END}
    )
    # Both workers converge on the join, which is what makes the run wait
    # for the slower of the two instead of ending at the first.
    graph.add_edge(HINT, ASSEMBLE)
    graph.add_edge(VARIANT, ASSEMBLE)
    graph.add_edge(ASSEMBLE, END)

    return graph.compile()


def run_team(
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    *,
    statement: str = "",
    verdict: str = "WA",
    tags: Sequence[str] = (),
    retriever: Retriever | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    use_tools: bool = False,
) -> TeamOutcome:
    """Run all three agents over one submission.

    Takes the same arguments as
    :func:`app.agent.graph.run_analysis_graph`, so switching from the
    diagnosis graph to the team is a change of function name.

    Never raises for a model-side failure, for the same reason
    ``analyze_error`` does not: a missing hint ladder is an enrichment
    gap, not a reason to fail a submission that was judged successfully.
    """
    graph = build_team_graph(
        retriever, max_retries=max_retries, use_tools=use_tools
    )
    final: TeamState = graph.invoke(
        {
            "code": code,
            "stdin": stdin,
            "expected": expected,
            "actual": actual,
            "statement": statement,
            "verdict": verdict,
            "tags": list(tags),
        }
    )

    return TeamOutcome(
        diagnosis=final.get("analysis"),
        hint=final.get("hint"),
        variant=final.get("variant"),
        error=final.get("error"),
        hint_error=final.get("hint_error"),
        variant_error=final.get("variant_error"),
        retrieved=list(final.get("retrieved") or []),
        context=final.get("context", ""),
        notes=list(final.get("notes") or []),
        attempts=final.get("attempts", 0),
        workers=list(final.get("workers") or []),
        produced=list(final.get("produced") or []),
    )
