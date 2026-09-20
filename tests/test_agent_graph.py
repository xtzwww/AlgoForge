"""Tests for the LangGraph analysis orchestration (阶段 8 · `AGENTS.md` S4).

Two seams are stubbed in every test, and neither is incidental:

* the **gateway**, via ``patch.object(analyzer, "chat_completion", ...)`` —
  the same seam ``conftest._no_real_llm_calls`` guards, patched on top of
  it. Scripting replies rather than calling models is what makes the retry
  tests possible at all.
* the **retriever**, replaced by :class:`FakeRetriever`. Milvus needs
  Docker, which this machine cannot run, and the graph's job is to *call*
  a retriever correctly — not to be a Milvus client.

What is under test is the wiring and the decisions: what the knowledge base
is searched with, that retrieved cards reach the prompt, when the
self-check sends the work back around, and that nothing inside the graph
turns a failure into an exception. The nodes' own behaviour — prompt
rendering, JSON validation, the parse-failure retry — is already covered by
``tests/test_analyzer.py`` and is deliberately not re-tested here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent import analyzer
from app.agent import graph as graph_module
from app.agent.graph import GraphOutcome, build_analysis_graph, run_analysis_graph
from app.llm.gateway import LLMConnectionError
from app.rag.retriever import RetrievedCard

# Every prose field is comfortably over graph.MIN_PROSE_CHARS, so the
# self-check accepts this on the first look.
VALID = (
    '{"error_type":"overflow","error_detail":"第 4 行的 a * b 在 int 内先溢出，'
    '所以乘积变成负数","hint":"看看乘法两个操作数各自能装下多大的数",'
    '"suggested_fix":"让运算之前就是 64 位整数"}'
)

# Parses cleanly and carries a legal label, but every prose field is below
# graph.MIN_PROSE_CHARS. This is the only failure the self-check is designed
# to catch: the schema cannot, because it only requires the fields to be
# non-empty. ``hint`` and ``suggested_fix`` must still differ — the schema
# rejects a reply where they are identical, and such a reply would exercise
# analyzer's parse-failure retry instead of this node's branch.
THIN = (
    '{"error_type":"overflow","error_detail":"溢出","hint":"查类型",'
    '"suggested_fix":"用 long"}'
)

# Well-formed *and* arguably the wrong label. The self-check must let this
# through — see test_a_wrong_but_complete_answer_is_not_retried.
COMPLETE_BUT_MAYBE_WRONG = (
    '{"error_type":"inverted_condition","error_detail":"循环的终止条件写反了，'
    '所以最后一个元素没有被处理","hint":"把循环边界和元素个数对照着数一遍",'
    '"suggested_fix":"把终止条件改成 <= 并重新验证边界"}'
)

CARD = RetrievedCard(
    card_id="binary-search",
    title="二分查找与数值逼近",
    text="## 要点\n\nmid 的取整方向决定边界会不会少一个元素。",
    score=0.91,
    tags=("二分", "数组"),
)

STATEMENT = "读入两个整数，输出它们的乘积。"


class FakeRetriever:
    """A retriever that records what it was asked and returns canned cards.

    Records the call rather than only answering it: the interesting
    assertion is usually *what the graph searched for*, and a stub that
    only returned cards would let a wrong query pass unnoticed.
    """

    def __init__(
        self,
        cards: tuple[RetrievedCard, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.cards = list(cards)
        self.error = error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def search(
        self,
        query: str,
        tags: tuple[str, ...] = (),
        limit: int = 3,
    ) -> list[RetrievedCard]:
        self.calls.append((query, tuple(tags)))
        if self.error is not None:
            raise self.error
        return list(self.cards)


def _stub_llm(*replies: Any) -> Any:
    """Patch the gateway to return *replies* in order.

    ``side_effect`` is used uniformly so one reply and several behave the
    same way, and so a test can script "thin answer, then a good one".
    """
    return patch.object(analyzer, "chat_completion", side_effect=list(replies))


def _run(retriever: Any = None, **kwargs: Any) -> GraphOutcome:
    """Run one analysis with the required positional arguments filled in."""
    params: dict[str, Any] = {
        "statement": STATEMENT,
        "verdict": "WA",
        "retriever": retriever,
    }
    params.update(kwargs)
    return run_analysis_graph(
        "int a, b; cin >> a >> b; cout << a * b;", "3 7", "21", "0", **params
    )


def _prompt_sent(mock: MagicMock, index: int = 0) -> str:
    """The user prompt from the *index*-th gateway call."""
    return mock.call_args_list[index][0][0][0]["content"]


# --------------------------------------------------------------- the shape


def test_the_graph_is_four_nodes_with_one_conditional_branch() -> None:
    """AGENTS.md §三 S4 pins this shape: 读代码 → 检索 → 分析 → 自检,
    with at least one conditional branch. Asserting it here means a
    refactor that quietly drops the branch fails instead of passing.
    """
    compiled = build_analysis_graph(FakeRetriever())
    topology = compiled.get_graph()

    assert {"read_code", "retrieve", "analyse", "self_check"} <= set(topology.nodes)

    outgoing = {(edge.source, edge.target) for edge in topology.edges}
    assert ("__start__", "read_code") in outgoing
    assert ("read_code", "retrieve") in outgoing
    assert ("retrieve", "analyse") in outgoing
    assert ("analyse", "self_check") in outgoing
    # The branch: the check can end the run or send it back to analyse.
    assert ("self_check", "__end__") in outgoing
    assert ("self_check", "analyse") in outgoing


def test_langgraph_is_the_orchestrator() -> None:
    """AGENTS.md §二.2 locks the orchestration to LangGraph. The compiled
    type is the cheapest proof that this is a real graph and not a hand
    rolled if/else wearing the module's name.
    """
    compiled = build_analysis_graph(FakeRetriever())

    assert type(compiled).__name__ == "CompiledStateGraph"


# ------------------------------------------------------------- retrieval


def test_the_statement_is_what_the_knowledge_base_is_searched_with() -> None:
    """Searching with the student's own code would retrieve cards about the
    mistake they made rather than the concept they got wrong — and that
    mistake is what the model is being asked to work out.
    """
    retriever = FakeRetriever()

    with _stub_llm(VALID):
        _run(retriever)

    assert retriever.calls[0][0] == STATEMENT


def test_without_a_statement_the_code_is_searched_with() -> None:
    """An ad-hoc run against inline test cases carries no statement. A rough
    query beats no query, but it must not be an empty string.
    """
    retriever = FakeRetriever()

    with _stub_llm(VALID):
        _run(retriever, statement="")

    assert retriever.calls[0][0] == "int a, b; cin >> a >> b; cout << a * b;"


def test_the_problem_tags_are_passed_to_the_retriever() -> None:
    """The tags are the filter hint that lets a backend narrow the corpus
    before the vector search. Dropping them here would make the retriever's
    tag parameter permanently dead code.
    """
    retriever = FakeRetriever()

    with _stub_llm(VALID):
        _run(retriever, tags=("枚举", "循环"))

    assert retriever.calls[0][1] == ("枚举", "循环")


def test_retrieved_cards_reach_the_model_prompt() -> None:
    """The point of the retrieve node: without this assertion the node could
    fetch cards and silently drop them on the floor, which is exactly what a
    broken ``extra_context`` wiring looks like.
    """
    retriever = FakeRetriever(cards=(CARD,))

    with _stub_llm(VALID) as mock:
        _run(retriever)

    prompt = _prompt_sent(mock)
    assert CARD.title in prompt
    assert "mid 的取整方向" in prompt


def test_no_cards_means_no_context_block() -> None:
    """An empty retrieval must inject nothing at all — not a heading, not a
    placeholder — because the model would read a placeholder as retrieved
    knowledge. See ``retriever.format_context``.
    """
    with _stub_llm(VALID) as empty_call:
        outcome = _run(FakeRetriever(cards=()))
    with _stub_llm(VALID) as filled_call:
        _run(FakeRetriever(cards=(CARD,)))

    assert outcome.context == ""
    assert outcome.retrieved == []
    assert CARD.title not in _prompt_sent(empty_call)
    # ...and the same run *with* a card does carry it, so the assertion
    # above is not passing merely because the prompt never includes cards.
    assert CARD.title in _prompt_sent(filled_call)


def test_a_raising_retriever_degrades_instead_of_failing_the_run() -> None:
    """The knowledge base is an enrichment. Losing a diagnosis because
    retrieval broke would be the bad trade app.rag.retriever refuses to
    make, and the graph must not reintroduce it.
    """
    retriever = FakeRetriever(error=RuntimeError("milvus went away"))

    with _stub_llm(VALID):
        outcome = _run(retriever)

    assert outcome.ok
    assert outcome.context == ""
    assert outcome.retrieved == []


# ------------------------------------------------------------- the branch


def test_a_complete_answer_is_accepted_without_a_retry() -> None:
    retriever = FakeRetriever(cards=(CARD,))

    with _stub_llm(VALID) as mock:
        outcome = _run(retriever)

    assert outcome.ok
    assert outcome.retries == 0
    assert outcome.notes == []
    assert mock.call_count == 1


def test_a_thin_answer_is_sent_back_around_the_loop() -> None:
    """The branch firing: a bare label with no prose behind it goes back to
    ``analyse``, and the good second answer is what gets returned.
    """
    with _stub_llm(THIN, VALID) as mock:
        outcome = _run(FakeRetriever())

    assert outcome.ok
    assert outcome.retries == 1
    assert mock.call_count == 2
    assert str(outcome.analysis.error_type) == "overflow"
    assert outcome.notes, "the self-check must record why it retried"


def test_the_retry_carries_the_criticism_into_the_next_prompt() -> None:
    """A retry that re-sends the identical prompt is a second opinion, not a
    correction. This is the assertion that keeps the loop from degenerating
    into "ask again and hope".
    """
    with _stub_llm(THIN, VALID) as mock:
        _run(FakeRetriever())

    first = _prompt_sent(mock, 0)
    second = _prompt_sent(mock, 1)

    assert "上一次作答的问题" not in first
    assert "上一次作答的问题" in second
    assert "error_detail" in second


def test_the_retry_budget_is_respected() -> None:
    """With max_retries=1 the loop runs at most twice. An unbounded loop
    here would be paid for by a student waiting on ``judge_done``.
    """
    with _stub_llm(THIN, THIN, THIN) as mock:
        outcome = _run(FakeRetriever())

    assert mock.call_count == 2
    assert outcome.retries == 1


def test_a_still_thin_answer_is_kept_rather_than_discarded() -> None:
    """Out of budget: the thin analysis is still returned. Discarding it
    would trade a usable hint for nothing, the same call ``analyzer`` makes
    when its own retry does not help.
    """
    with _stub_llm(THIN, THIN):
        outcome = _run(FakeRetriever())

    assert outcome.ok
    assert outcome.retries == 1
    assert outcome.notes
    assert str(outcome.analysis.error_type) == "overflow"


def test_max_retries_zero_makes_the_branch_terminal() -> None:
    """The branch must be configurable off, and turning it off must not
    change the verdict — only the number of paid calls.
    """
    with _stub_llm(THIN) as mock:
        outcome = _run(FakeRetriever(), max_retries=0)

    assert mock.call_count == 1
    assert outcome.retries == 0
    assert outcome.ok
    assert outcome.notes, "the complaint is still recorded without a retry"


def test_a_negative_retry_budget_is_rejected() -> None:
    """Silently clamping would hide a caller's arithmetic mistake."""
    with pytest.raises(ValueError, match="max_retries"):
        build_analysis_graph(FakeRetriever(), max_retries=-1)


def test_a_wrong_but_complete_answer_is_not_retried() -> None:
    """The boundary of the self-check, and the reason it is deterministic.

    This reply is complete and possibly mislabelled. The graph cannot tell
    without paying for a second opinion on every submission, so it does
    not try: judging wrongness is S6's reflection loop. If this test ever
    starts failing, the graph has grown an extra model call per analysis.
    """
    with _stub_llm(COMPLETE_BUT_MAYBE_WRONG) as mock:
        outcome = _run(FakeRetriever())

    assert mock.call_count == 1
    assert outcome.retries == 0
    assert outcome.ok


# ------------------------------------------------------------ failures


def test_a_gateway_failure_ends_the_run_without_retrying() -> None:
    """``analyzer`` already declined to retry a gateway failure, because
    re-sending would fail identically. The graph must not undo that by
    looping — the failure would just be paid for twice.
    """
    with _stub_llm(LLMConnectionError("timed out")) as mock:
        outcome = _run(FakeRetriever())

    assert not outcome.ok
    assert outcome.analysis is None
    assert outcome.error is not None
    assert outcome.retries == 0
    assert mock.call_count == 1


def test_an_unparseable_reply_does_not_escape_as_an_exception() -> None:
    """``analyzer`` retries a parse failure once internally, so a model that
    answers prose twice leaves the run with no analysis — and no exception
    on the judging path.
    """
    with _stub_llm("这不是 JSON。", "还是不是 JSON。") as mock:
        outcome = _run(FakeRetriever())

    assert not outcome.ok
    assert outcome.error is not None
    # Two attempts inside analyzer, and no graph-level retry on top.
    assert outcome.attempts == 2
    assert outcome.retries == 0
    assert mock.call_count == 2


def test_blank_code_costs_no_model_call() -> None:
    """A blank submission is not analysable, and ``analyzer`` says so before
    spending a call. The graph must preserve that.
    """
    with _stub_llm(VALID) as mock:
        outcome = run_analysis_graph(
            "", "", "", "", statement=STATEMENT, retriever=FakeRetriever()
        )

    assert not outcome.ok
    assert outcome.attempts == 0
    assert mock.call_count == 0


# ---------------------------------------------------------- S8: memory


def test_a_second_run_does_not_inherit_the_first_ones_state() -> None:
    """The reason :func:`~app.agent.memory.thread_id_for` takes a run token.

    LangGraph loads a thread's last checkpoint and applies the new input on
    top of it, so two runs sharing a thread would share state — and the
    accumulating keys (``attempts``, ``notes``) would keep growing. With a
    per-run token each run starts clean.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    checkpointer = InMemorySaver()

    with _stub_llm(VALID, VALID):
        first = _run(checkpointer=checkpointer, submission_id=7)
        second = _run(checkpointer=checkpointer, submission_id=7)

    assert first.attempts == 1
    assert second.attempts == 1
    assert first.thread_id != second.thread_id


def test_the_run_thread_is_published_on_the_outcome() -> None:
    """It names the run's checkpoints. Nothing in the UI shows it yet, so
    the log is its only consumer — it has to leave this function somehow.
    """
    with _stub_llm(VALID):
        outcome = _run(submission_id=7)

    assert outcome.thread_id.startswith("submission:7:")


def test_an_unrecorded_run_still_gets_a_thread() -> None:
    """``submission_id`` is optional; the checkpointer is not. An ad-hoc
    run against inline cases must not fail for lacking an id.
    """
    with _stub_llm(VALID):
        outcome = _run()

    assert outcome.thread_id.startswith("submission:none:")


def test_the_checkpointer_and_store_reach_the_compiled_graph() -> None:
    """Both are accepted and handed to ``compile`` rather than quietly
    dropped — a parameter that is plumbed but not passed is the failure
    mode this test exists for.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    checkpointer = InMemorySaver()
    store = InMemoryStore()

    graph = build_analysis_graph(checkpointer=checkpointer, store=store)

    assert graph.checkpointer is checkpointer
    assert graph.store is store


def test_every_node_can_announce_itself() -> None:
    """A node with no entry in ``NODE_LABELS`` would be announced to the
    student under its internal name — and ``read_code`` is not a thing to
    show anyone. The label table is checked against the real topology so
    it cannot fall behind a node being added.
    """
    compiled = build_analysis_graph(FakeRetriever())
    nodes = {n for n in compiled.get_graph().nodes if not n.startswith("__")}

    assert set(graph_module.NODE_LABELS) == nodes


def test_the_nodes_announce_themselves_as_they_finish() -> None:
    """S8's streaming: ``on_step`` hears about each node, in order.

    ``read_code`` and ``retrieve`` are the ones that matter most — they
    are the stretch where the UI can currently only show a spinner, and
    they are cheap, so a student sees movement immediately after the
    verdict.
    """
    steps: list[tuple[str, str]] = []

    with _stub_llm(VALID):
        _run(on_step=lambda name, label: steps.append((name, label)))

    assert [name for name, _ in steps] == [
        "read_code",
        "retrieve",
        "analyse",
        "self_check",
    ]
    assert all(label for _, label in steps)


def test_a_retry_reports_the_extra_round() -> None:
    """The loop is the part a student would otherwise never see: the graph
    went back and asked again, and the only evidence in the log was a
    note. Streaming makes the second ``analyse`` visible.
    """
    steps: list[str] = []

    with _stub_llm(THIN, VALID):
        _run(on_step=lambda name, _label: steps.append(name))

    assert steps.count("analyse") == 2


def test_the_event_sink_reaches_the_model_call() -> None:
    """P2-8's ``agent_tool`` events on the graph path.

    Regression: ``build_analysis_graph`` accepted ``on_event`` and
    documented it as "passed straight through to ``analyze_error``" while
    never forwarding it, so the graph was the one orchestrator that
    silently dropped the stream. The docstring was true; the code was not.
    """
    from app.agent.analyzer import AnalysisOutcome

    sink = object()
    with patch.object(
        graph_module, "analyze_error", return_value=AnalysisOutcome(attempts=0)
    ) as mock:
        _run(on_event=sink)

    assert mock.call_args.kwargs["on_event"] is sink


def test_the_outcome_reports_what_happened() -> None:
    """``GraphOutcome`` is what a caller inspects after the fact, so the
    accumulating state has to survive the graph boundary intact.
    """
    retriever = FakeRetriever(cards=(CARD,))

    with _stub_llm(THIN, VALID):
        outcome = _run(retriever, tags=("二分",))

    assert outcome.attempts == 2
    assert outcome.retries == 1
    assert [card.card_id for card in outcome.retrieved] == [CARD.card_id]
    assert outcome.context
    assert outcome.ok
    assert outcome.tool_calls == []
