"""S4/S7 wiring: the judging path picks an orchestrator (P2-3 · 阶段 8).

``app.agent.pipeline`` exists to close the gap README §四 describes as
"已实现并测试，但未接入主路径". These tests pin the two properties that
make it safe to ship:

1. **The switch is off by default, and "off" means the old code path.**
   Not "an equivalent code path" — ``analyzer.analyze_error`` itself, with
   every argument forwarded unchanged. A default that quietly rerouted
   submissions would change what each one costs and how a failure
   degrades, and AGENTS.md §三 makes S2's accuracy figure a go/no-go line
   that only ``pytest -m eval_set`` (paid, and therefore never run here)
   can measure.
2. **The graph branch degrades rather than raises.** ``records.
   analyse_submission`` — unlike ``record_submission`` — does not swallow
   exceptions, so a raise out of ``graph.invoke`` would turn a
   successfully judged submission into a 500.

The end-to-end "direct is unchanged" evidence lives in
``tests/test_agent_analysis_recording.py``: those tests stub
``tool_caller.complete`` and now travel through this module's ``direct``
branch, so they cover the wiring without having been edited for it.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.agent import graph as graph_module
from app.agent import pipeline
from app.agent import team as team_module
from app.agent.analyzer import AnalysisOutcome
from app.agent.graph import GraphOutcome
from app.agent.schemas import (
    HintLadderResult,
    HintLevel,
    VariantResult,
    parse_error_analysis,
)
from app.agent.team import TeamOutcome
from app.config import settings

VALID_ANALYSIS = (
    '{"error_type":"overflow","error_detail":"a * b 在 int 内先溢出",'
    '"hint":"看看乘法的操作数是什么类型","suggested_fix":"让运算前就是 64 位"}'
)

#: Positional arguments ``analyse`` needs, so each test can vary only what
#: it is about.
ARGS = ("int a, b;", "3 7", "21", "0")


def _graph_outcome(**overrides: Any) -> GraphOutcome:
    """A successful graph run, with any field overridable per test."""
    fields: dict[str, Any] = {
        "analysis": parse_error_analysis(VALID_ANALYSIS),
        "error": None,
        "attempts": 2,
        "retries": 1,
        "retrieved": [],
        "context": "【卡片】整数溢出",
        "notes": [],
        "tool_calls": [],
    }
    fields.update(overrides)
    return GraphOutcome(**fields)


class _Recorder:
    """Stand-in for a branch's implementation, capturing how it was called."""

    def __init__(self, returns: Any = None, raises: Exception | None = None):
        self.returns = returns
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        return self.returns

    @property
    def call(self) -> dict[str, Any]:
        """The single call, asserted to be single."""
        assert len(self.calls) == 1, f"expected one call, got {len(self.calls)}"
        return self.calls[0]


# ----------------------------------------------------- which branch runs


def test_the_switch_ships_off() -> None:
    """``direct`` is the default — the graph is opt-in, not opt-out.

    Pinned as a test rather than left to the field default because this is
    the one property the whole change rests on. If someone flips the
    default, they must do it knowing they also owe a ``-m eval_set`` run.
    """
    assert settings.analysis_pipeline == pipeline.DIRECT
    assert pipeline.resolved_pipeline() == pipeline.DIRECT


def test_direct_is_also_what_an_unset_value_means(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blank ``ANALYSIS_PIPELINE`` is "not configured", not a typo."""
    monkeypatch.setattr(settings, "analysis_pipeline", "")

    with caplog.at_level(logging.WARNING, logger="app.agent.pipeline"):
        assert pipeline.resolved_pipeline() == pipeline.DIRECT

    assert caplog.records == []


@pytest.mark.parametrize("written", ["graph", "GRAPH", " Graph ", "gRaPh"])
def test_graph_is_selected_however_it_is_spelled(
    monkeypatch: pytest.MonkeyPatch,
    written: str,
) -> None:
    """Case and stray whitespace are the operator's, not a config error."""
    monkeypatch.setattr(settings, "analysis_pipeline", written)

    assert pipeline.resolved_pipeline() == pipeline.GRAPH


def test_an_unknown_value_falls_back_to_direct_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A misspelling must not silently pick an orchestrator.

    It also must not raise: this runs inside the judging path, where the
    contract is that a missing analysis is an enrichment gap and never a
    failed submission.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", "langgraph")

    with caplog.at_level(logging.WARNING, logger="app.agent.pipeline"):
        assert pipeline.resolved_pipeline() == pipeline.DIRECT

    assert any("langgraph" in record.getMessage() for record in caplog.records)


def test_the_default_setting_reaches_analyze_error_not_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default branch is the S2 call, asserted end to end.

    ``resolved_pipeline`` returning ``direct`` is necessary but not
    sufficient — this pins that ``analyse`` actually acts on it.
    """
    direct = _Recorder(returns=AnalysisOutcome(attempts=1))
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr(pipeline, "analyze_error", direct)
    monkeypatch.setattr(pipeline, "_via_graph", graph)

    pipeline.analyse(*ARGS)

    assert len(direct.calls) == 1
    assert graph.calls == []


# ------------------------------------------------ the direct branch (S2)


def test_direct_forwards_every_argument_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No argument may be dropped on the way to ``analyze_error``.

    ``max_attempts`` and ``max_steps`` especially: both are latency
    budgets on the student-facing path, and a silently-defaulted value
    would be a behaviour change hiding inside a "wiring only" commit.
    """
    direct = _Recorder(returns=AnalysisOutcome(attempts=1))
    monkeypatch.setattr(pipeline, "analyze_error", direct)
    sentinel = object()

    outcome = pipeline.analyse(
        *ARGS,
        statement="读入两个整数，输出乘积。",
        verdict="TLE",
        extra_context="【卡片】整数溢出",
        max_attempts=1,
        use_tools=True,
        on_event=sentinel,
        max_steps=5,
    )

    assert direct.call["args"] == ARGS
    assert direct.call["kwargs"] == {
        "statement": "读入两个整数，输出乘积。",
        "verdict": "TLE",
        "extra_context": "【卡片】整数溢出",
        "max_attempts": 1,
        "use_tools": True,
        "on_event": sentinel,
        "max_steps": 5,
    }
    assert outcome.ok is False


def test_direct_returns_whatever_analyze_error_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default branch is a pass-through, not a re-wrap.

    Rebuilding the outcome would quietly drop fields the analyzer added —
    ``rejected`` and ``tool_calls`` are the ones a caller can observe.
    """
    expected = AnalysisOutcome(
        result=parse_error_analysis(VALID_ANALYSIS),
        attempts=2,
        rejected=["oops"],
    )
    monkeypatch.setattr(pipeline, "analyze_error", _Recorder(returns=expected))

    assert pipeline.analyse(*ARGS) is expected


# ----------------------------------------------- the graph branch (S4)


def test_the_graph_branch_runs_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``graph`` configured, ``_via_graph`` is the one that runs."""
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    direct = _Recorder(returns=AnalysisOutcome(attempts=1))
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr(pipeline, "analyze_error", direct)
    monkeypatch.setattr(pipeline, "_via_graph", graph)

    pipeline.analyse(*ARGS)

    assert len(graph.calls) == 1
    assert direct.calls == []


def test_graph_outcome_is_flattened_into_an_analysis_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers must not have to branch on which orchestrator ran.

    ``GraphOutcome`` is shaped like ``AnalysisOutcome`` on purpose, so the
    fields the recording path reads — ``ok``, ``result``, ``error``,
    ``tool_names`` — have to survive the flattening with the same names.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    monkeypatch.setattr(
        "app.agent.graph.run_analysis_graph",
        _Recorder(returns=_graph_outcome(attempts=3, error=None)),
    )

    outcome = pipeline.analyse(*ARGS)

    assert isinstance(outcome, AnalysisOutcome)
    assert outcome.ok is True
    assert outcome.result is not None
    assert outcome.result.error_type == "overflow"
    assert outcome.attempts == 3


def test_a_failed_graph_run_keeps_the_error_and_the_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A graph that gave up is reported the way a gateway outage is.

    Not as an exception, and not as a silent ``None``: the entry has to be
    able to say "analysis unavailable" rather than look untouched.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    monkeypatch.setattr(
        "app.agent.graph.run_analysis_graph",
        _Recorder(returns=_graph_outcome(analysis=None, error="LLMConnectionError: x")),
    )

    outcome = pipeline.analyse(*ARGS)

    assert outcome.ok is False
    assert outcome.error == "LLMConnectionError: x"


def test_graph_receives_the_tags_and_the_event_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrieval filters and P2-8's stream both survive the wiring.

    ``on_event`` matters most: it is the only reason the graph needed
    ``run_analysis_graph`` extended, and without it switching orchestrators
    would silently drop every ``agent_tool`` event the UI renders.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr("app.agent.graph.run_analysis_graph", graph)
    sink = object()

    pipeline.analyse(
        *ARGS,
        statement="读入两个整数，输出乘积。",
        verdict="TLE",
        use_tools=True,
        on_event=sink,
        tags=["数学", "溢出"],
    )

    assert graph.call["args"] == ARGS
    assert graph.call["kwargs"] == {
        "statement": "读入两个整数，输出乘积。",
        "verdict": "TLE",
        "tags": ["数学", "溢出"],
        "use_tools": True,
        "on_event": sink,
        "submission_id": None,
        "on_step": None,
    }


def test_the_step_sink_reaches_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S8 streaming on the graph path: the node-progress sink is another
    thing that only exists for one orchestrator, and so another thing the
    wiring can quietly drop.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr("app.agent.graph.run_analysis_graph", graph)
    sink = object()

    pipeline.analyse(*ARGS, on_step=sink)

    assert graph.call["kwargs"]["on_step"] is sink


def test_the_graph_branch_does_not_forward_extra_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph retrieves its own context; passing both would double it.

    Asserted rather than left to the reader because "accept the argument
    and ignore it" is exactly the kind of thing a later refactor
    "helpfully" fixes into a duplicate block in the prompt.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr("app.agent.graph.run_analysis_graph", graph)

    pipeline.analyse(*ARGS, extra_context="【卡片】整数溢出")

    assert "extra_context" not in graph.call["kwargs"]


def test_a_raising_graph_is_an_unavailable_analysis_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph is new code driving a third-party runtime.

    ``analyze_error`` promises not to raise for provider problems, and the
    graph makes the same promise — but a bug in either is still possible,
    and ``analyse_submission`` does not swallow exceptions. So the one
    thing this branch must never do is let a raise escape.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    monkeypatch.setattr(
        "app.agent.graph.run_analysis_graph",
        _Recorder(raises=RuntimeError("graph blew up")),
    )

    outcome = pipeline.analyse(*ARGS)

    assert outcome.ok is False
    assert "RuntimeError" in (outcome.error or "")
    assert "graph blew up" in (outcome.error or "")


def test_self_check_notes_are_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the graph knows and a single call does not must not vanish.

    The mistake-book schema has nowhere to store the self-check's
    criticism, so the log is the only place a reviewer can see that the
    retry branch fired at all.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    monkeypatch.setattr(
        "app.agent.graph.run_analysis_graph",
        _Recorder(returns=_graph_outcome(notes=["上一次的分析过于简略：error_detail"])),
    )

    with caplog.at_level(logging.INFO, logger="app.agent.pipeline"):
        pipeline.analyse(*ARGS)

    assert any("过于简略" in record.getMessage() for record in caplog.records)


# ------------------------------------------------------- the real graph


def test_the_real_graph_branch_produces_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run through the actual compiled graph, not a stand-in.

    The fake ``run_analysis_graph`` above pins this module's half of the
    contract; this pins that the two halves fit. The model call is stubbed
    at ``analyzer.chat_completion`` — the seam ``graph``'s ``analyse`` node
    goes through — and the retriever degrades to no-op, which is the
    documented behaviour when no embedder is configured.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.GRAPH)
    monkeypatch.setattr(
        "app.agent.analyzer.chat_completion", lambda *a, **k: VALID_ANALYSIS
    )

    outcome = pipeline.analyse(*ARGS, use_tools=False)

    assert outcome.ok is True
    assert outcome.result is not None
    assert outcome.result.error_type == "overflow"


# ------------------------------------------------- the records seam


def test_records_analysis_path_goes_through_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``records._analyse_and_annotate`` must call the pipeline, not the analyzer.

    This is the wiring itself. Without it, everything above could be
    correct and the judging path would still be calling ``analyze_error``
    directly — which is precisely the state README §四 complains about.

    The failing-outcome branch is used on purpose: it returns before
    ``_find_recurrence`` touches a database, so this stays a unit test.
    """
    from app.models.mistake_book import MistakeBookEntry
    from app.records import SubmissionContext, _analyse_and_annotate
    from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult

    calls: list[dict[str, Any]] = []

    def fake_analyse(*args: Any, **kwargs: Any) -> AnalysisOutcome:
        calls.append(kwargs)
        return AnalysisOutcome(error="stubbed out", attempts=1)

    monkeypatch.setattr(pipeline, "analyse", fake_analyse)

    result = JudgeResult(
        status=JudgeStatus.WA,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.WA,
                input="3 7",
                expected_output="21",
                actual_output="0",
            )
        ],
    )
    context = SubmissionContext(code="int a, b;", language="cpp", problem_id=1)
    entry = MistakeBookEntry()

    payload = _analyse_and_annotate(entry, result, context, "读入两个整数。")

    assert len(calls) == 1
    assert calls[0]["code"] == "int a, b;"
    assert calls[0]["stdin"] == "3 7"
    assert calls[0]["expected"] == "21"
    assert calls[0]["actual"] == "0"
    assert calls[0]["statement"] == "读入两个整数。"
    assert calls[0]["verdict"] == "WA"
    # P2-8's tool path is the product's, and the wiring must not have
    # dropped it on the way through.
    assert calls[0]["use_tools"] is True
    assert payload is not None and payload["ok"] is False


def test_the_graph_entry_point_is_not_bound_at_module_scope() -> None:
    """The ``direct`` path must not pay for the graph's imports.

    ``records.py`` imports this module lazily precisely so the judge routes
    stay light, and that only buys anything if ``pipeline`` defers the
    ``langgraph`` import into the branch that needs it. Pinned on the
    namespace rather than by reloading with ``langgraph`` blocked, because
    reloading ``sys.modules`` mid-suite is a bigger hazard than the thing
    it would be checking. If a refactor ever binds this at module level on
    purpose, this test is the note saying the laziness was deliberate.
    """
    assert "run_analysis_graph" not in vars(pipeline)


# ------------------------------------------------------------- S7: team


def _team_outcome(**overrides: Any) -> TeamOutcome:
    """A successful team run, with any field overridable per test."""
    fields: dict[str, Any] = {
        "diagnosis": parse_error_analysis(VALID_ANALYSIS),
        "hint": None,
        "variant": None,
        "error": None,
        "hint_error": None,
        "variant_error": None,
        "retrieved": [],
        "context": "",
        "notes": [],
        "attempts": 3,
        "workers": ["diagnose", "hint", "variant"],
        "produced": ["diagnose", "hint", "variant"],
    }
    fields.update(overrides)
    return TeamOutcome(**fields)


@pytest.mark.parametrize("written", ["team", "TEAM", " team "])
def test_team_is_selected_however_it_is_spelled(
    written: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "analysis_pipeline", written)
    assert pipeline.resolved_pipeline() == pipeline.TEAM


def test_team_branch_runs_run_team(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ANALYSIS_PIPELINE=team`` actually reaches the three agents.

    Patched at ``team.run_team``, not at ``pipeline._via_team``: replacing
    the branch would skip the very conversion this is about (TeamOutcome
    in, AnalysisOutcome out) and the test would pass on a branch that
    returns the wrong type.
    """
    monkeypatch.setattr(settings, "analysis_pipeline", "team")
    team = _Recorder(returns=_team_outcome())
    graph = _Recorder(returns=_graph_outcome())
    monkeypatch.setattr(team_module, "run_team", team)
    monkeypatch.setattr(graph_module, "run_analysis_graph", graph)

    out = pipeline.analyse(*ARGS, statement="读入两个整数。")

    assert isinstance(out, AnalysisOutcome)
    assert out.ok
    assert out.result is not None and out.result.error_type == "overflow"
    assert team.calls and graph.calls == []
    assert team.call["kwargs"]["statement"] == "读入两个整数。"


def test_team_carries_only_the_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hint ladder and the variant are logged, not silently dropped.

    There is no column for either, so an implementation that quietly threw
    them away would look identical to this one from the outside. Asserting
    the log line is what makes "we know they ran and we chose not to store
    them" different from "we forgot".
    """
    monkeypatch.setattr(settings, "analysis_pipeline", "team")
    monkeypatch.setattr(
        team_module,
        "run_team",
        _Recorder(
            returns=_team_outcome(
                attempts=3,
                hint=HintLadderResult(
                    levels=[
                        HintLevel(level=1, text="看看 a * b 的类型"),
                        HintLevel(level=2, text="int 装得下吗"),
                        HintLevel(level=3, text="先把一边转 long long"),
                    ],
                    self_check=["三级都提到了类型", "最后一级才给改法"],
                ),
                variant=VariantResult(
                    statement="换成矩阵乘法，同样会溢出吗？",
                    sample_input="2 3",
                    sample_output="6",
                    knowledge_points=["整数溢出"],
                    difference="操作数来自矩阵规模，不是直接输入",
                ),
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger="app.agent.pipeline"):
        out = pipeline.analyse(*ARGS)

    assert out.ok
    assert out.attempts == 3
    assert any("not persisted" in record.getMessage() for record in caplog.records)


def test_team_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raise from the team must not turn a judged submission into a 500."""
    monkeypatch.setattr(settings, "analysis_pipeline", "team")
    monkeypatch.setattr(
        team_module, "run_team", _Recorder(raises=RuntimeError("boom"))
    )

    out = pipeline.analyse(*ARGS)

    assert isinstance(out, AnalysisOutcome)
    assert not out.ok
    assert "RuntimeError" in (out.error or "")
