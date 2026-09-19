"""LangGraph orchestration of the analysis path (阶段 8 · `AGENTS.md` S4).

:func:`app.agent.analyzer.analyze_error` is one model call wrapped in a
validate-and-retry loop. S4 asks for that single call to become a *state
graph*: read the submission, retrieve knowledge, analyse, then check the
answer — with the check able to send the work back around.

Four nodes and one branch
-------------------------
::

    START → read_code → retrieve → analyse → self_check ─┬→ END  (accept)
                             ↑                           ├→ END  (give up)
                             └──────── retry ────────────┘

``retrieve`` sits *before* ``analyse`` so retrieved cards reach the model
as context on the first attempt rather than costing a second round trip.
That step cannot fail the run: :func:`app.rag.retriever.build_retriever`
never raises and degrades to
:class:`~app.rag.retriever.NullRetriever` when Milvus is unreachable, so
there is no ``try`` around the *building* of it here — see that module's
docstring for why degrading beats failing.

Why the self-check is deterministic
-----------------------------------
``self_check`` does **not** ask a model whether the analysis is good. A
second opinion would add a paid call to every submission, spent while a
student waits for ``judge_done``. What it checks instead is whether the
answer is *degenerate* — a label with no prose behind it — which
:class:`~app.agent.schemas.ErrorAnalysisResult` deliberately cannot catch:
the schema enforces that the three prose fields are non-empty and nothing
more, because "is this sentence any good" is not a schema question.

So the retry is bounded and rare by construction. It fires on an empty or
stub answer, never on a merely *wrong* one; judging wrongness is S6's
reflection loop, and that one does cost a model call.

Retries carry the failure back
------------------------------
Re-sending the identical prompt makes a retry a second opinion rather
than a correction. ``self_check``'s note is folded into
``extra_context``, so the next attempt sees what was wrong with the last
one — the same reasoning ``analyzer`` applies to its parse-failure retry.

Not yet wired into the judging path
-----------------------------------
This module is additive: ``records.analyse_submission`` still calls
``analyze_error`` directly. S4's completion definition is that the graph
*exists and runs end to end* (``AGENTS.md`` §三), so wiring it into
``records.py`` is a separate, deliberate step — that file is on the
student-facing latency path, and swapping the orchestrator underneath it
should not ride along with the graph's introduction.

Retrieval is a no-op until an embedder is configured
----------------------------------------------------
:func:`build_analysis_graph` builds a retriever through
:func:`app.rag.retriever.build_retriever` when none is passed, and with no
embedder configured that function returns
:class:`~app.rag.retriever.NullRetriever`. So today ``retrieve`` finds
nothing and ``context`` is ``""``, exactly as the S4 sketch says
("检索节点（占位）"). The node is real and tested; it is the *wiring of an
embedding model* that is still missing.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.analyzer import AnalysisOutcome, analyze_error
from app.agent.schemas import ErrorAnalysisResult
from app.rag.retriever import (
    RetrievedCard,
    Retriever,
    build_retriever,
    format_context,
)

logger = logging.getLogger(__name__)

#: Self-check retries allowed beyond the first analysis. One is enough for
#: the only failure it detects (a stub answer): a model that produced a
#: bare label once rarely produces a full analysis on a second look, and
#: each retry is another paid call in front of a waiting student.
DEFAULT_MAX_RETRIES = 1

#: Shortest prose field worth showing a student. Deliberately low — this
#: is a "did the model actually answer" floor, not a style rule, and a
#: high bar here would turn a terse-but-correct analysis into a retry.
MIN_PROSE_CHARS = 8

#: Values :func:`build_analysis_graph`'s router maps to a destination.
#: Constants rather than bare strings so the router and the edge map
#: cannot drift — a typo in either would otherwise show up as
#: ``InvalidUpdateError`` naming a node, not the string that was wrong.
ACCEPT = "accept"
RETRY = "retry"
GIVE_UP = "give_up"


class AnalysisState(TypedDict, total=False):
    """The graph's shared state.

    ``total=False`` because a node returns only what it changed: requiring
    every key on every return would make each node restate the inputs it
    merely passed through, and one of those restatements would eventually
    drop a field.

    The three ``Annotated`` keys accumulate across a retry instead of
    being overwritten, which is what makes the final state a record of
    everything that happened rather than just the last round. The rest
    are plain keys and the last write wins — correct for ``analysis``,
    ``decision`` and ``retries``, where only the latest value means
    anything.
    """

    # --- Inputs: the fixed facts about one submission.
    code: str
    stdin: str
    expected: str
    actual: str
    statement: str
    verdict: str
    tags: list[str]

    # --- read_code
    #: What the knowledge base is searched with. See :func:`_read_code`.
    query: str

    # --- retrieve
    retrieved: list[RetrievedCard]
    #: ``retrieved`` rendered for the prompt. ``""`` when nothing was found.
    context: str

    # --- analyse
    analysis: Optional[ErrorAnalysisResult]
    error: Optional[str]
    #: Model calls made by ``analyzer`` itself, summed over graph rounds.
    attempts: Annotated[int, operator.add]
    tool_calls: Annotated[list[Any], operator.add]

    # --- self_check
    decision: str
    #: Self-check retries already spent.
    retries: int
    #: Criticism from ``self_check``, one entry per round it complained.
    notes: Annotated[list[str], operator.add]


@dataclass
class GraphOutcome:
    """What one run of the graph produced.

    Shaped like :class:`~app.agent.analyzer.AnalysisOutcome` on purpose, so
    a caller can move from one orchestrator to the other without rewriting
    its error handling — ``ok`` and ``error`` mean the same thing in both.
    The extra fields are what the graph knows and the single call does not:
    which cards were retrieved, and whether the self-check complained.
    """

    analysis: Optional[ErrorAnalysisResult] = None
    error: Optional[str] = None
    #: Model calls made by ``analyzer``, summed across graph rounds.
    attempts: int = 0
    #: Self-check retries that actually ran (0 or 1 with the default).
    retries: int = 0
    retrieved: list[RetrievedCard] = field(default_factory=list)
    context: str = ""
    #: Criticism the self-check raised, oldest first. Non-empty does not
    #: mean the run failed — the last round may have fixed it.
    notes: list[str] = field(default_factory=list)
    tool_calls: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.analysis is not None


def _read_code(state: AnalysisState) -> dict[str, Any]:
    """Pick the text to search the knowledge base with.

    The *statement* wins over the code. Searching with the student's own
    buggy submission retrieves cards about the mistake they made rather
    than the concept they got wrong — and that mistake is exactly what the
    model is being asked to work out, so using it as the query would bias
    retrieval toward whatever the code superficially looks like it is
    doing.

    When there is no statement (an ad-hoc run against inline test cases)
    the code is all there is, and a rough query beats none.
    """
    statement = (state.get("statement") or "").strip()
    code = (state.get("code") or "").strip()
    return {"query": statement or code}


def _quality_problems(analysis: ErrorAnalysisResult) -> list[str]:
    """Reasons *analysis* is too thin to hand to a student.

    Only degeneracy, never wrongness — see the module docstring. Empty
    means "nothing to complain about", not "this analysis is correct".
    """
    problems: list[str] = []
    for name, value in (
        ("error_detail", analysis.error_detail),
        ("hint", analysis.hint),
        ("suggested_fix", analysis.suggested_fix),
    ):
        stripped = (value or "").strip()
        if len(stripped) < MIN_PROSE_CHARS:
            problems.append(f"{name} 只有 {len(stripped)} 个字符，看不出结论依据")
    return problems


def _retry_guidance(notes: Sequence[str]) -> str:
    """Render earlier criticism as an instruction for the next attempt."""
    joined = "\n".join(f"- {note}" for note in notes)
    return (
        "## 上一次作答的问题（请修正）\n\n"
        f"{joined}\n\n"
        "请重新分析，并仍按同样的 JSON 格式作答。"
    )


def build_analysis_graph(
    retriever: Retriever | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    use_tools: bool = False,
    on_event: Optional[Callable[[Any], None]] = None,
) -> Any:
    """Build the compiled read → retrieve → analyse → check graph.

    Args:
        retriever: Knowledge source for the ``retrieve`` node. Built from
            :func:`app.rag.retriever.build_retriever` when omitted, which
            degrades to a no-op retriever rather than raising.
        max_retries: Self-check retries allowed beyond the first analysis.
            Must be non-negative; 0 makes the branch terminal.
        use_tools: Offer the model P2-6's ``run_code`` / ``judge`` tools.
            Off by default, matching ``analyze_error``: the tool path costs
            several round trips per analysis.
        on_event: Sink for tool-call notifications, so a WebSocket client
            can watch the Agent work (P2-8). Passed straight through to
            ``analyze_error``; without it the graph would be the one
            orchestrator that silently drops the stream, and a caller
            switching between them would lose its progress events.

    Returns:
        A compiled graph. ``invoke`` takes an :class:`AnalysisState`; the
        accumulating keys need no seeding, so ``invoke({"code": ...})`` is
        a valid call.
    """
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")

    source: Retriever = retriever if retriever is not None else build_retriever()

    def retrieve(state: AnalysisState) -> dict[str, Any]:
        """Look the concept up in the knowledge base.

        The ``except`` is not redundant with the retriever's own
        degradation: that contract covers the Milvus-backed
        implementations, while a caller-supplied or test retriever may
        raise. Losing an entire diagnosis because the knowledge base
        hiccuped would be the same bad trade ``app.rag.retriever``
        refuses to make.
        """
        query = state.get("query") or ""
        try:
            cards = list(source.search(query, tags=state.get("tags") or []))
        except Exception as exc:  # noqa: BLE001 — degradation is the contract
            logger.warning(
                "knowledge retrieval failed, continuing without context: %s", exc
            )
            cards = []
        return {"retrieved": cards, "context": format_context(cards)}

    def analyse(state: AnalysisState) -> dict[str, Any]:
        """Run one analysis, with any previous criticism folded in."""
        extra = state.get("context") or ""
        notes = state.get("notes") or []
        if notes:
            criticism = _retry_guidance(notes)
            extra = f"{extra}\n\n{criticism}" if extra else criticism

        outcome: AnalysisOutcome = analyze_error(
            state.get("code", ""),
            state.get("stdin", ""),
            state.get("expected", ""),
            state.get("actual", ""),
            statement=state.get("statement", ""),
            verdict=state.get("verdict", "WA"),
            extra_context=extra or None,
            use_tools=use_tools,
        )
        return {
            "analysis": outcome.result,
            "error": outcome.error,
            "attempts": outcome.attempts,
            "tool_calls": list(outcome.tool_calls),
        }

    def self_check(state: AnalysisState) -> dict[str, Any]:
        """Decide whether the answer is worth handing to a student."""
        analysis = state.get("analysis")

        if analysis is None:
            # Nothing came back at all. ``analyzer`` has already retried a
            # parse failure once, and a gateway failure would fail again
            # identically — re-running here would pay twice for the same
            # answer.
            return {
                "decision": GIVE_UP,
                "notes": [f"分析未产出：{state.get('error') or '原因未知'}"],
            }

        problems = _quality_problems(analysis)
        if not problems:
            return {"decision": ACCEPT}

        note = "上一次的分析过于简略：" + "；".join(problems)
        retries = state.get("retries", 0)
        if retries >= max_retries:
            # Out of budget. Keep the thin analysis rather than discarding
            # it — the same call ``analyzer`` makes when its retry does not
            # help, and the reason this is a note and not an error.
            return {"decision": GIVE_UP, "notes": [note]}

        return {"decision": RETRY, "retries": retries + 1, "notes": [note]}

    graph = StateGraph(AnalysisState)
    graph.add_node("read_code", _read_code)
    graph.add_node("retrieve", retrieve)
    graph.add_node("analyse", analyse)
    graph.add_node("self_check", self_check)

    graph.add_edge(START, "read_code")
    graph.add_edge("read_code", "retrieve")
    graph.add_edge("retrieve", "analyse")
    graph.add_edge("analyse", "self_check")
    # The one conditional branch: a thin answer goes back to ``analyse``,
    # not to ``retrieve`` — the query has not changed, so re-embedding and
    # re-searching would return the same cards and only add latency.
    graph.add_conditional_edges(
        "self_check",
        lambda state: state.get("decision", GIVE_UP),
        {ACCEPT: END, RETRY: "analyse", GIVE_UP: END},
    )

    return graph.compile()


def run_analysis_graph(
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
    on_event: Optional[Callable[[Any], None]] = None,
) -> GraphOutcome:
    """Analyse one submission through the graph.

    Takes the same arguments as
    :func:`app.agent.analyzer.analyze_error` plus *tags* for retrieval, so
    a caller can switch orchestrators by changing the function it calls.
    ``app.agent.pipeline`` is that caller, and it is what decides between
    the two — see its docstring for why the default is the single call.

    Never raises for a model-side failure, for the same reason
    ``analyze_error`` does not: this runs inside the judging path, and a
    missing analysis is an enrichment gap, not a reason to fail a
    submission that was judged successfully.
    """
    graph = build_analysis_graph(
        retriever,
        max_retries=max_retries,
        use_tools=use_tools,
        on_event=on_event,
    )
    final: AnalysisState = graph.invoke(
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

    return GraphOutcome(
        analysis=final.get("analysis"),
        error=final.get("error"),
        attempts=final.get("attempts", 0),
        retries=final.get("retries", 0),
        retrieved=list(final.get("retrieved") or []),
        context=final.get("context", ""),
        notes=list(final.get("notes") or []),
        tool_calls=list(final.get("tool_calls") or []),
    )
