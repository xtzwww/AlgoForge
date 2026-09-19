"""Tests for the S7 three-agent team (阶段 8 · `AGENTS.md` S7).

The team is three agents — 错因 / 提示 / 变式题 — under one orchestrator,
with the S4 diagnosis graph used as a *subgraph* node.

Two seams are stubbed, and the first one is the point of the file:

* the **gateway**. Both call sites are patched, because ``analyzer`` and
  ``team`` each hold their own reference to ``chat_completion``; patching
  one leaves the other live. ``conftest._no_real_llm_calls`` patches both
  as well — that is the safety net, and this is the scripted reply on top
  of it.
* the **retriever**, replaced by ``FakeRetriever``. Milvus needs Docker,
  which this machine cannot run.

What is under test is the *collaboration*: that the workers only run on a
diagnosis worth building on, that they run as siblings rather than in a
chain, that they are given the diagnosis and the diagnosis's retrieval,
that they are **not** given the fix, and that one worker failing cannot
take the other or the diagnosis down with it. The retry a worker spends
on an unparseable reply is covered here as well — it exists because the
first real-model run lost the variant section to exactly that. The
schemas' structural rules are unit-tested here too; the graph's own node
behaviour is covered by ``tests/test_agent_graph.py`` and
``tests/test_analyzer.py`` and is deliberately not re-tested.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from app.agent import analyzer, team
from app.agent.graph import AnalysisState, build_analysis_graph
from app.agent.schemas import (
    ErrorAnalysisParseError,
    HintLadderResult,
    StructuredOutputParseError,
    VariantResult,
    parse_error_analysis,
    parse_hint_ladder,
    parse_variant,
)
from app.agent.team import TeamOutcome, TeamState, build_team_graph, run_team
from app.llm.gateway import LLMConnectionError
from app.prompts import loader
from app.rag.retriever import RetrievedCard

# --------------------------------------------------------------- fixtures

#: A marker rather than plausible prose, so a test can assert the fix is
#: *absent* from a worker's prompt without the assertion passing because
#: the wording happened to differ. See
#: test_the_workers_are_not_given_the_fix.
FIX_MARKER = "SUGGESTED-FIX-MARKER"
DETAIL_MARKER = "DETAIL-MARKER"

# Every prose field is over graph.MIN_PROSE_CHARS (8), so the diagnosis's
# self-check accepts it on the first look.
ANALYSIS_OK = json.dumps(
    {
        "error_type": "overflow",
        "error_detail": f"{DETAIL_MARKER} 第 4 行的 a * b 在 int 内先溢出",
        "hint": "看看乘法两个操作数各自能装下多大的数",
        "suggested_fix": f"{FIX_MARKER} 让运算之前就是 64 位整数",
    },
    ensure_ascii=False,
)

# Legal label, every prose field under the self-check's floor. Scripting
# this first is how a test drives the diagnosis graph's retry branch from
# inside the team.
ANALYSIS_THIN = json.dumps(
    {
        "error_type": "overflow",
        "error_detail": "溢出",
        "hint": "查类型",
        "suggested_fix": "用 long",
    },
    ensure_ascii=False,
)

HINT_OK = json.dumps(
    {
        "levels": [
            {"level": 1, "text": "先去看乘法之前的中间结果"},
            {"level": 2, "text": "看第 4 行的 a * b 两个操作数"},
            {"level": 3, "text": "把两个操作数都转成 64 位再乘，看结果是否变化"},
        ],
        "self_check": ["两个乘数都是 10^9 时结果还对吗", "样例之外的边界试过了吗"],
    },
    ensure_ascii=False,
)

VARIANT_OK = json.dumps(
    {
        "statement": "读入两个整数，输出它们的乘积。数据范围 1 ≤ a, b ≤ 10^9。",
        "sample_input": "1000000000 1000000000",
        "sample_output": "1000000000000000000",
        "knowledge_points": ["整数溢出", "数据类型"],
        "difference": "数据范围扩大到 10^9，两个乘数相乘会超出 32 位。原解在 "
        "int 里先乘，所以在这里还会再错一次。",
    },
    ensure_ascii=False,
)

CARD = RetrievedCard(
    card_id="number-theory",
    title="数论与整数范围",
    text="## 要点\n\n两个 10^9 量级的数相乘会超出 int。",
    score=0.88,
    tags=("数论", "溢出"),
)

STATEMENT = "读入两个整数，输出它们的乘积。"


class FakeRetriever:
    """Records what it was asked, so a test can count the searches."""

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


def _as_queue(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, Exception)):
        return [value]
    return list(value)


class ScriptedLLM:
    """A gateway stand-in that answers by *which agent* asked.

    Dispatching on the prompt rather than on call order is deliberate: the
    two workers run in the same superstep, so their order is not
    guaranteed, and a stub that popped from one shared queue would make
    the tests flaky in a way that looks like a graph bug.

    Each role gets its own queue, consumed in order, so a test can script
    "thin diagnosis, then a good one" without affecting the workers. An
    ``Exception`` in a queue is raised instead of returned, which is how
    a gateway failure is scripted.
    """

    #: Headings that appear in exactly one template. ``error_analysis.md``
    #: contains neither, so anything unmatched is the diagnosis.
    MARKERS: tuple[tuple[str, str], ...] = (
        ("三级提示", team.HINT),
        ("变式题的要求", team.VARIANT),
    )

    def __init__(
        self,
        analysis: Any = None,
        hint: Any = None,
        variant: Any = None,
    ) -> None:
        self.queues: dict[str, list[Any]] = {
            "analysis": _as_queue(analysis),
            team.HINT: _as_queue(hint),
            team.VARIANT: _as_queue(variant),
        }
        #: Every conversation sent, in call order.
        self.calls: list[str] = []
        #: Conversations grouped by which agent asked for them.
        self.by_role: dict[str, list[str]] = {}

    def _role_of(self, conversation: str) -> str:
        for marker, role in self.MARKERS:
            if marker in conversation:
                return role
        return "analysis"

    def __call__(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        # The whole conversation, not just the first message: a worker's
        # retry sends ``prompt, its own bad reply, the correction``, and a
        # stub that only read ``messages[0]`` would record the retry as a
        # duplicate of the first attempt — hiding the correction it is
        # supposed to be testing.
        conversation = "\n".join(message["content"] for message in messages)
        role = self._role_of(conversation)
        self.calls.append(conversation)
        self.by_role.setdefault(role, []).append(conversation)

        queue = self.queues[role]
        if not queue:
            raise AssertionError(
                f"the {role} agent was called more often than the test scripted"
            )

        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@contextmanager
def _llm(
    analysis: Any = None,
    hint: Any = None,
    variant: Any = None,
) -> Iterator[ScriptedLLM]:
    """Point both gateway call sites at one scripted stand-in."""
    llm = ScriptedLLM(analysis, hint, variant)
    with (
        patch.object(analyzer, "chat_completion", llm),
        patch.object(team, "chat_completion", llm),
    ):
        yield llm


def _run(retriever: Any = None, **kwargs: Any) -> TeamOutcome:
    """Run one team pass with the required positional arguments filled in."""
    params: dict[str, Any] = {
        "statement": STATEMENT,
        "verdict": "WA",
        "retriever": retriever,
    }
    params.update(kwargs)
    return run_team(
        "int a, b; cin >> a >> b; cout << a * b;", "3 7", "21", "0", **params
    )


def _full_run(**kwargs: Any) -> tuple[TeamOutcome, ScriptedLLM]:
    """A run in which all three agents answer, plus the stub that fed it."""
    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK) as llm:
        return _run(FakeRetriever(cards=(CARD,)), **kwargs), llm


# ------------------------------------------------- the schemas (S7's two)

def test_a_valid_ladder_parses() -> None:
    ladder = parse_hint_ladder(HINT_OK)

    assert isinstance(ladder, HintLadderResult)
    assert [rung.level for rung in ladder.levels] == [1, 2, 3]
    assert len(ladder.self_check) == 2


def test_a_fenced_ladder_is_unwrapped() -> None:
    """Models fence JSON even when asked not to. The analysis parser
    already tolerates it; the new parsers share that code path, and this
    is the assertion that keeps them sharing it.
    """
    assert parse_hint_ladder(f"```json\n{HINT_OK}\n```").levels[0].level == 1


def test_a_ladder_needs_exactly_three_rungs() -> None:
    """Two rungs is not a graded ladder — it is a hint plus an answer."""
    two = json.dumps(
        {
            "levels": [
                {"level": 1, "text": "先看方向"},
                {"level": 2, "text": "再看位置"},
            ],
            "self_check": ["a", "b"],
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputParseError, match="levels"):
        parse_hint_ladder(two)


def test_ladder_levels_must_be_ordered_and_one_based() -> None:
    """A ladder whose rungs are numbered 0,1,2 — or repeated — has lost the
    order the feature exists to express.
    """
    zero_based = json.dumps(
        {
            "levels": [
                {"level": 0, "text": "先看方向"},
                {"level": 1, "text": "再看位置"},
                {"level": 2, "text": "最后看判据"},
            ],
            "self_check": ["a", "b"],
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputParseError, match="levels"):
        parse_hint_ladder(zero_based)


def test_ladder_rungs_must_say_different_things() -> None:
    """Restating one rung three times is the failure mode the prompt warns
    about, and it is invisible from any single field.
    """
    repeated = json.dumps(
        {
            "levels": [
                {"level": 1, "text": "看看乘法"},
                {"level": 2, "text": "看看乘法"},
                {"level": 3, "text": "看看乘法"},
            ],
            "self_check": ["a", "b"],
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputParseError, match="different"):
        parse_hint_ladder(repeated)


def test_a_ladder_rung_may_not_be_blank() -> None:
    blank = json.dumps(
        {
            "levels": [
                {"level": 1, "text": "  "},
                {"level": 2, "text": "再看位置"},
                {"level": 3, "text": "最后看判据"},
            ],
            "self_check": ["a", "b"],
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputParseError):
        parse_hint_ladder(blank)


def test_a_ladder_may_not_carry_extra_fields() -> None:
    """``extra="forbid"`` is what turns a drifting prompt into an error
    instead of a silently dropped field.
    """
    extra = json.dumps(
        {
            "levels": [
                {"level": 1, "text": "先看方向"},
                {"level": 2, "text": "再看位置"},
                {"level": 3, "text": "最后看判据"},
            ],
            "self_check": ["a", "b"],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputParseError):
        parse_hint_ladder(extra)


def test_a_valid_variant_parses() -> None:
    variant = parse_variant(VARIANT_OK)

    assert isinstance(variant, VariantResult)
    assert variant.knowledge_points == ["整数溢出", "数据类型"]


@pytest.mark.parametrize(
    "missing",
    ["statement", "sample_input", "sample_output", "difference"],
)
def test_a_variant_missing_any_required_field_is_rejected(missing: str) -> None:
    """Every one of these is shown to the student, so a variant without a
    sample or without a stated difference is not usable.
    """
    payload = json.loads(VARIANT_OK)
    payload.pop(missing)

    with pytest.raises(StructuredOutputParseError):
        parse_variant(json.dumps(payload, ensure_ascii=False))


def test_a_variant_may_not_have_blank_knowledge_points() -> None:
    payload = json.loads(VARIANT_OK)
    payload["knowledge_points"] = ["  "]

    with pytest.raises(StructuredOutputParseError):
        parse_variant(json.dumps(payload, ensure_ascii=False))


def test_the_new_parsers_share_the_analysis_error_base() -> None:
    """One base class for "the reply was unusable".

    The worker agents only want to record that and carry on; without a
    shared base they would have to import three exception types to say so.
    ``ErrorAnalysisParseError`` stays a subclass so P2-3's retry keeps
    catching it by name.
    """
    assert issubclass(ErrorAnalysisParseError, StructuredOutputParseError)

    with pytest.raises(StructuredOutputParseError):
        parse_hint_ladder("这不是 JSON。")
    with pytest.raises(StructuredOutputParseError):
        parse_variant("这不是 JSON。")
    # ...and the analysis parser still raises the specific name.
    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis("这不是 JSON。")


def test_a_parse_error_still_carries_the_raw_output() -> None:
    """The base class kept the ``raw`` payload, so a failed worker can be
    diagnosed without reproducing the call.
    """
    with pytest.raises(StructuredOutputParseError) as excinfo:
        parse_variant("模型只说了句话")

    assert excinfo.value.raw == "模型只说了句话"


# ------------------------------------------------------------- the shape


def test_the_team_is_a_subgraph_two_workers_and_a_join() -> None:
    """AGENTS.md §三 S7 pins the collaboration: 错因 / 提示 / 变式题.

    The diagnosis is a *subgraph node* rather than four inlined steps, the
    two workers hang off it, and a join collects both.
    """
    compiled = build_team_graph(FakeRetriever())
    topology = compiled.get_graph()

    assert {team.DIAGNOSE, team.HINT, team.VARIANT, team.ASSEMBLE} <= set(
        topology.nodes
    )

    outgoing = {(edge.source, edge.target) for edge in topology.edges}
    assert ("__start__", team.DIAGNOSE) in outgoing
    assert (team.DIAGNOSE, team.HINT) in outgoing
    assert (team.DIAGNOSE, team.VARIANT) in outgoing
    # The join: both workers converge, and the run ends there.
    assert (team.HINT, team.ASSEMBLE) in outgoing
    assert (team.VARIANT, team.ASSEMBLE) in outgoing
    assert (team.ASSEMBLE, "__end__") in outgoing
    # A failed diagnosis still ends the run rather than entering a worker.
    assert (team.DIAGNOSE, "__end__") in outgoing


def test_the_workers_are_siblings_not_a_chain() -> None:
    """Neither worker waits for the other.

    A chain would still satisfy "three agents" while serializing two full
    model calls for no reason — and would make a slow hint agent look
    like a slow variant agent. This is the assertion that keeps them
    parallel.
    """
    outgoing = {
        (edge.source, edge.target)
        for edge in build_team_graph(FakeRetriever()).get_graph().edges
    }

    assert (team.HINT, team.VARIANT) not in outgoing
    assert (team.VARIANT, team.HINT) not in outgoing


def test_the_diagnosis_subgraph_is_the_s4_graph() -> None:
    """The four diagnosis steps exist once, in one graph, for both entry
    points. Composing rather than re-implementing is the whole reason
    this module can call itself additive.
    """
    assert type(build_team_graph(FakeRetriever())).__name__ == "CompiledStateGraph"

    assert type(build_analysis_graph(FakeRetriever())).__name__ == "CompiledStateGraph"


def test_the_team_state_is_a_superset_of_the_analysis_state() -> None:
    """The subgraph-as-node contract, asserted rather than assumed.

    Every channel the diagnosis subgraph writes must exist in the parent,
    or LangGraph fails at *runtime* with an error naming a channel. A key
    added to ``AnalysisState`` and forgotten here would therefore show up
    as a broken graph rather than as a failing test — unless this test
    exists, which is why it does.
    """
    missing = set(AnalysisState.__annotations__) - set(TeamState.__annotations__)

    assert not missing, f"TeamState is missing channels the subgraph writes: {missing}"


# --------------------------------------------------- the full collaboration


def test_a_full_run_produces_all_three_agents_output() -> None:
    outcome, llm = _full_run()

    assert outcome.ok
    assert outcome.complete
    assert outcome.diagnosis is not None
    assert outcome.hint is not None
    assert outcome.variant is not None
    # One diagnosis call plus one per worker.
    assert outcome.attempts == 3
    assert len(llm.calls) == 3
    assert set(outcome.produced) == {team.HINT, team.VARIANT}


def test_the_workers_run_only_after_a_successful_diagnosis() -> None:
    """No diagnosis, no prompts.

    Neither worker's prompt can be rendered without a label and an
    ``error_detail``, so a failed diagnosis must end the run rather than
    send two hollow requests to a paid endpoint.
    """
    with _llm(LLMConnectionError("timed out"), HINT_OK, VARIANT_OK) as llm:
        outcome = _run(FakeRetriever())

    assert not outcome.ok
    assert outcome.hint is None
    assert outcome.variant is None
    assert outcome.produced == []
    assert len(llm.calls) == 1, "only the diagnosis should have been attempted"
    assert team.HINT not in outcome.workers
    assert team.VARIANT not in outcome.workers


def test_the_diagnosis_self_check_still_runs_inside_the_team() -> None:
    """The subgraph is not bypassed: its retry branch works the same way
    inside the team as it does standalone, and its criticism reaches the
    outcome.
    """
    with _llm([ANALYSIS_THIN, ANALYSIS_OK], HINT_OK, VARIANT_OK) as llm:
        outcome = _run(FakeRetriever())

    assert outcome.ok
    assert outcome.complete
    assert outcome.notes, "the subgraph's self-check complaint must survive"
    # Two diagnosis attempts plus one per worker.
    assert outcome.attempts == 4
    assert len(llm.by_role["analysis"]) == 2


def test_the_hint_agent_is_given_the_diagnosis() -> None:
    """The collaboration: the hint agent does not re-derive the bug, it
    builds on what the diagnosis found.
    """
    _, llm = _full_run()
    prompt = llm.by_role[team.HINT][0]

    assert "overflow" in prompt
    assert DETAIL_MARKER in prompt
    assert STATEMENT in prompt


def test_the_variant_agent_is_given_the_diagnosis() -> None:
    _, llm = _full_run()
    prompt = llm.by_role[team.VARIANT][0]

    assert "overflow" in prompt
    assert DETAIL_MARKER in prompt
    assert STATEMENT in prompt


def test_the_workers_are_not_given_the_fix() -> None:
    """The pedagogical boundary.

    The hint ladder exists so the student finds the fix; handing the hint
    agent the diagnosis's ``suggested_fix`` is exactly how a "hint"
    degrades into a paraphrase of the answer. The marker makes the
    assertion about *what was sent*, not about the wording of a prompt.
    """
    _, llm = _full_run()

    for role in (team.HINT, team.VARIANT):
        prompt = llm.by_role[role][0]
        assert FIX_MARKER not in prompt, f"the {role} agent was shown the fix"


def test_the_workers_reuse_the_diagnosis_retrieval_instead_of_searching_again() -> None:
    """One search per submission.

    The query has not changed between the three agents, so a second search
    would return the same cards and add an embedding round trip to a path
    a student is waiting on.
    """
    retriever = FakeRetriever(cards=(CARD,))

    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK) as llm:
        outcome = _run(retriever)

    assert len(retriever.calls) == 1
    assert outcome.context
    for role in (team.HINT, team.VARIANT):
        assert CARD.title in llm.by_role[role][0]


def test_an_empty_retrieval_is_stated_rather_than_left_blank() -> None:
    """A blank block reads to the model as "the knowledge base says
    nothing", which is a claim retrieval never made — the same reasoning
    ``analyzer`` applies to a missing statement.
    """
    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK) as llm:
        _run(FakeRetriever(cards=()))

    for role in (team.HINT, team.VARIANT):
        assert team.NO_KNOWLEDGE_NOTE in llm.by_role[role][0]


def test_a_missing_statement_is_stated_rather_than_left_blank() -> None:
    """An ad-hoc run against inline test cases carries no statement."""
    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK) as llm:
        _run(FakeRetriever(), statement="")

    for role in (team.HINT, team.VARIANT):
        assert analyzer.NO_STATEMENT_NOTE in llm.by_role[role][0]


def test_the_rendered_worker_prompts_leave_no_placeholders() -> None:
    """The general assertion that actually protects the model call: a
    literal ``{{error_type}}`` reaching the model would produce a
    confidently wrong hint rather than an error.
    """
    _, llm = _full_run()

    for role in (team.HINT, team.VARIANT):
        assert "{{" not in llm.by_role[role][0]


# --------------------------------------------------------- degradation


def test_a_failing_hint_agent_keeps_the_diagnosis_and_the_variant() -> None:
    """Isolation, not repetition, is what makes the team robust: one
    worker failing cannot take the other down with it.
    """
    with _llm(ANALYSIS_OK, LLMConnectionError("timed out"), VARIANT_OK):
        outcome = _run(FakeRetriever(cards=(CARD,)))

    assert outcome.ok
    assert not outcome.complete
    assert outcome.hint is None
    assert outcome.hint_error is not None
    assert outcome.variant is not None
    assert outcome.produced == [team.VARIANT]
    assert team.HINT in outcome.workers, "it ran — it just came back empty"


def test_a_failing_variant_agent_keeps_the_diagnosis_and_the_hint() -> None:
    with _llm(ANALYSIS_OK, HINT_OK, LLMConnectionError("timed out")):
        outcome = _run(FakeRetriever(cards=(CARD,)))

    assert outcome.ok
    assert not outcome.complete
    assert outcome.variant is None
    assert outcome.variant_error is not None
    assert outcome.hint is not None
    assert outcome.produced == [team.HINT]


def test_a_worker_retries_an_unparseable_reply_once() -> None:
    """The first real-model run of the team lost the variant section to a
    malformed reply, which is why a worker retries a *parse* failure.

    The retry carries the offending output and the parser's complaint
    back, so the second attempt is a correction rather than a second
    opinion — the same mechanism ``analyzer`` uses.
    """
    with _llm(ANALYSIS_OK, ["这不是 JSON。", HINT_OK], VARIANT_OK) as llm:
        outcome = _run(FakeRetriever(cards=(CARD,)))

    assert outcome.hint is not None
    assert outcome.hint_error is None
    assert outcome.complete
    # One diagnosis call, two hint calls, one variant call.
    assert outcome.attempts == 4
    assert len(llm.by_role[team.HINT]) == 2


def test_the_worker_retry_shows_the_model_its_own_output() -> None:
    """A retry that re-sends the identical prompt is a second draw, not a
    correction. This is the assertion that keeps it from degenerating
    into "ask again and hope".
    """
    with _llm(ANALYSIS_OK, ["这不是 JSON。", HINT_OK], VARIANT_OK) as llm:
        _run(FakeRetriever())

    first, second = llm.by_role[team.HINT]

    assert "上一次的输出无法被解析" not in first
    assert "上一次的输出无法被解析" in second
    assert "这不是 JSON。" in second, "the offending reply must be quoted back"


def test_a_worker_gives_up_after_one_retry() -> None:
    """Two attempts, not three: each one is a paid call in front of a
    student waiting for ``judge_done``.
    """
    with _llm(ANALYSIS_OK, ["不是 JSON。", "还是不是 JSON。"], VARIANT_OK) as llm:
        outcome = _run(FakeRetriever())

    assert outcome.hint is None
    assert outcome.hint_error is not None
    assert len(llm.by_role[team.HINT]) == 2
    assert outcome.attempts == 4


def test_a_worker_does_not_retry_a_gateway_failure() -> None:
    """Re-sending would fail identically and be paid for twice — the same
    call ``analyzer`` makes.
    """
    with _llm(ANALYSIS_OK, LLMConnectionError("timed out"), VARIANT_OK) as llm:
        outcome = _run(FakeRetriever())

    assert outcome.hint is None
    assert outcome.hint_error is not None
    assert len(llm.by_role[team.HINT]) == 1
    assert outcome.attempts == 3


def test_an_unparseable_worker_reply_is_recorded_not_raised() -> None:
    """A worker that cannot answer is a missing section, not a broken
    submission: the diagnosis and the other worker still come back.
    """
    with _llm(ANALYSIS_OK, ["这不是 JSON。", "还是不是 JSON。"], VARIANT_OK):
        outcome = _run(FakeRetriever(cards=(CARD,)))

    assert outcome.ok
    assert outcome.hint is None
    assert outcome.hint_error is not None
    assert outcome.variant is not None
    assert outcome.produced == [team.VARIANT]


def test_both_workers_failing_still_returns_the_diagnosis() -> None:
    with _llm(ANALYSIS_OK, LLMConnectionError("down"), LLMConnectionError("down")):
        outcome = _run(FakeRetriever())

    assert outcome.ok
    assert not outcome.complete
    assert outcome.produced == []
    assert outcome.hint_error and outcome.variant_error


def test_blank_code_costs_no_model_call() -> None:
    """A blank submission is not analysable, and ``analyzer`` says so
    before spending a call. The team must preserve that.
    """
    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK) as llm:
        outcome = run_team(
            "", "", "", "", statement=STATEMENT, retriever=FakeRetriever()
        )

    assert not outcome.ok
    assert outcome.attempts == 0
    assert llm.calls == []


def test_a_raising_retriever_still_produces_a_full_team_run() -> None:
    """The knowledge base is an enrichment at every level of the stack."""
    with _llm(ANALYSIS_OK, HINT_OK, VARIANT_OK):
        outcome = _run(FakeRetriever(error=RuntimeError("milvus went away")))

    assert outcome.complete
    assert outcome.context == ""


def test_a_thin_but_valid_ladder_is_accepted() -> None:
    """The documented boundary of the worker agents.

    The diagnosis graph has a self-check that sends a degenerate answer
    back around; the workers do not, by design — see the module
    docstring. A ladder that is structurally sound is therefore accepted
    however terse its rungs are, and this test records that as a decision
    rather than leaving it to be discovered as a surprise.
    """
    terse = json.dumps(
        {
            "levels": [
                {"level": 1, "text": "看乘法"},
                {"level": 2, "text": "看第四行"},
                {"level": 3, "text": "转 64 位再试"},
            ],
            "self_check": ["边界试了吗", "范围对吗"],
        },
        ensure_ascii=False,
    )

    with _llm(ANALYSIS_OK, terse, VARIANT_OK):
        outcome = _run(FakeRetriever())

    assert outcome.complete
    assert outcome.hint is not None


# --------------------------------------------------------------- wiring


def test_the_outcome_records_which_agents_ran() -> None:
    """``workers`` is the set of *agents* that executed. The diagnosis is
    a subgraph and the join is a join, so neither is in here; the run of
    the former shows up as ``ok`` and the latter as ``produced``.
    """
    outcome, _ = _full_run()

    assert set(outcome.workers) == {team.HINT, team.VARIANT}


def test_a_worker_node_entered_without_a_diagnosis_is_harmless() -> None:
    """Unreachable through the router, but a node function is a public
    callable. An ``AttributeError`` from inside a graph run is far harder
    to read than an empty worker record.
    """
    assert team._hint({}) == {"workers": [team.HINT]}
    assert team._variant({}) == {"workers": [team.VARIANT]}


def test_a_negative_retry_budget_is_still_rejected() -> None:
    """The subgraph's own guard is not swallowed by the team wrapping it."""
    with pytest.raises(ValueError, match="max_retries"):
        build_team_graph(FakeRetriever(), max_retries=-1)


# ------------------------------------------------- the prompts (red line)


def test_the_new_templates_declare_exactly_what_the_team_supplies() -> None:
    """AGENTS.md §二.1: prompts live in ``sidecar/app/prompts/`` as files.

    Pinned deliberately: this is the contract between each template and
    its call in ``team.py``. Add a placeholder and this fails until the
    caller is updated — which is the point, because ``render`` would
    otherwise fail at runtime on the student-facing path.
    """
    hint = loader.extract_variables(loader.load_prompt(team.HINT_PROMPT))
    assert hint == {
        "verdict",
        "stdin",
        "expected_output",
        "actual_output",
        "statement",
        "code",
        "error_type",
        "error_detail",
        "knowledge_context",
    }

    variant = loader.extract_variables(loader.load_prompt(team.VARIANT_PROMPT))
    assert variant == {
        "statement",
        "code",
        "error_type",
        "error_detail",
        "knowledge_context",
    }


def test_the_correction_template_declares_its_variables() -> None:
    """The retry message is a prompt too, so it lives in a template file
    (``AGENTS.md`` §二.1) and gets the same drift guard.
    """
    correction = loader.extract_variables(loader.load_prompt(team.CORRECTION_PROMPT))

    assert correction == {"problem", "previous"}
