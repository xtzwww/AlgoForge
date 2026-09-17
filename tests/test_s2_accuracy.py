"""S2 accuracy gate — the go/no-go measurement (P2-5).

This is the test AGENTS.md calls "整个项目的生死线". It runs
:func:`analyze_error` over the 20 labelled cases in
``corpus/eval_cases/`` and asserts that exact-match accuracy on
``expected_root_cause`` clears :data:`ACCURACY_GATE`.

Why this is a test rather than a script
---------------------------------------
The gate is a *decision*, not a report: if it fails, the remediation
path branches (调 prompt / 换模型 / 提前进 P2-13 加 RAG). Making it a
pytest assertion means the number is re-measured on every run, and the
failure message carries the breakdown needed to pick the branch.

It is expensive and non-deterministic at the edges
--------------------------------------------------
Every case costs at least one paid API call (two when the first reply
fails validation), and the score depends on a remote model. Two
consequences:

* marked ``eval_set``, so it is deselected with ``-m "not eval_set"``
  alongside the other C++-compiling behavioural tests;
* skipped — never failed, never silently passed — when no provider is
  configured. This machine has an empty ``DEEPSEEK_API_KEY`` and no
  Ollama, so the honest local outcome is a skip. A skip here means
  **the gate has not been measured**, which is materially different from
  a pass and must be carried as an unclosed item.

Why each case is judged before it is analysed
---------------------------------------------
The case's ``test_cases`` are run through the real judge to find an
input the submission actually gets wrong. Handing the model a passing
case's I/O would produce a confident analysis of something that is not
broken, and the resulting accuracy number would say more about the
harness than about the Agent.

The judge run is also the only source of ``actual_output``: the case
files record the *expected* output but not what the buggy program
printed, so running it is the only way to fill that field in. That makes
this harness depend on a working compiler as well as on a network — see
:data:`MAX_PROMPT_CHARS` for the one place the resulting payload needs
trimming.

If no supplied test case fails, the case falls back to its first input
and is flagged (``fallback``). All 20 cases in the current set expose a
genuine failure, so the flag should stay empty; a drift upward would
mean the set's ``test_cases`` no longer demonstrate the bugs their
labels claim, which is a defect in the set rather than in the model.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pytest

# Re-exported from the set's own test module so the gate has exactly one
# definition. Declaring 0.60 a second time here would let the two drift,
# and the gate is the one number in this project that must not drift.
from test_eval_set import ACCURACY_GATE, EXPECTED_CASE_COUNT

from app.agent.analyzer import AnalysisOutcome, analyze_error
from app.eval_cases import EvalCase, load_eval_cases
from app.llm.gateway import LLMConfigError
from app.sandbox.judge import JudgeStatus, judge
from app.sandbox.judge import TestCase as JudgeTestCase


#: Which analysis path this harness measures.
#:
#: P2-8 wired the *product's* recording path to P2-7's tool loop, so the
#: analysis the user actually gets is now the tool-using one — while this
#: harness, left alone, would keep measuring the text-only call it was
#: written for. A gate whose number describes a path that no longer ships
#: is worse than no gate: it reads as evidence for a decision it no longer
#: covers.
#:
#: Set ``S2_USE_TOOLS=1`` to measure the shipped path. The default stays
#: off so the P2-5 figure already recorded remains reproducible, and
#: ``describe()`` states which path produced the number either way.
def _tool_switch_enabled(raw: str) -> bool:
    """Read ``S2_USE_TOOLS`` leniently but unambiguously.

    Anything falsy-looking means "text-only", and anything else means
    "tools" — so ``1``, ``true``, ``yes`` and ``on`` all work. A stricter
    reading would reject a plausible spelling, and the failure mode is
    silent: a run meant to measure the shipped path would quietly measure
    the old one and print a number that looks perfectly fine.
    """
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


_USE_TOOLS = _tool_switch_enabled(os.environ.get("S2_USE_TOOLS", ""))

#: Human-readable name of the measured path, for the report.
MEASURED_PATH = "tool-using (P2-8)" if _USE_TOOLS else "text-only (P2-5)"

# Cap on how much of an input/output is pasted into the prompt.
#
# ``s2-011`` is a TLE case whose input is 400,018 characters — 99.95% of
# the whole set's payload. Sent verbatim it would spend the entire request
# budget on one of twenty cases and would very likely overflow the model's
# context window, turning a solvable case into a guaranteed miss for a
# reason that has nothing to do with the Agent. So long values are clipped.
#
# The *head* is kept, not the tail: what the model needs from a huge input
# is its shape (a count, then a long array) in order to reason about
# complexity. The tail of a repeated array carries no extra information.
MAX_PROMPT_CHARS = 2000


def _clip(text: str, limit: int = MAX_PROMPT_CHARS) -> tuple[str, bool]:
    """Shorten *text* for the prompt, marking any truncation.

    Returns:
        ``(text, was_clipped)``. The caller records the flag so that a
        case whose label depends on the omitted tail is diagnosable
        rather than looking like a plain model miss.
    """
    if len(text) <= limit:
        return text, False
    return (
        f"{text[:limit]}\n…（此处只显示前 {limit} 个字符，原文共 {len(text)} 个字符）",
        True,
    )


@dataclass
class CaseOutcome:
    """One case's journey through the harness, including its cost."""

    case: EvalCase
    predicted: Optional[str] = None
    # True when the submission was AC on every supplied test case, so the
    # analysis was run against an input the program handles correctly.
    fallback: bool = False
    # True when the input or output had to be clipped for the prompt.
    clipped: bool = False
    # Set when no analysis was produced at all (gateway unreachable,
    # both replies unparseable). Distinct from a wrong label: one is an
    # agent failure, the other is not the model's fault.
    error: Optional[str] = None
    attempts: int = 0
    # How many tools the Agent called for this case. Non-zero is the
    # evidence that the tool-using path really was exercised, which is the
    # difference between "the gate passed with tools" and "the gate passed
    # because nothing changed".
    tools_used: int = 0

    @property
    def expected(self) -> str:
        return self.case.expected_root_cause

    @property
    def scored(self) -> bool:
        """Whether this case contributes to the accuracy fraction.

        An unanalysable case does count — as a miss. Dropping it would
        let a broken provider raise the score, which is the opposite of
        what a go/no-go gate is for.

        Every case in the set is scored, including the single TLE case:
        its label is ``wrong_complexity``, which the taxonomy has a slot
        for, and the judge does expose a failing input for it (the huge
        one that times the program out). Excluding it would silently drop
        a label from the measurement.
        """
        return self.predicted is not None or self.error is not None

    @property
    def correct(self) -> bool:
        return self.predicted is not None and self.predicted == self.expected


@dataclass
class AccuracyReport:
    """Aggregate accuracy plus the breakdowns the remediation path needs."""

    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def scored(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.scored]

    @property
    def hits(self) -> list[CaseOutcome]:
        return [o for o in self.scored if o.correct]

    @property
    def accuracy(self) -> float:
        return len(self.hits) / len(self.scored) if self.scored else 0.0

    @property
    def fallbacks(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.fallback]

    @property
    def clipped(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.clipped]

    @property
    def calls(self) -> int:
        """Total gateway calls — the run's cost."""
        return sum(o.attempts for o in self.outcomes)

    @property
    def retries(self) -> int:
        """Extra calls beyond the first per case.

        Counted per case rather than as ``calls - len(outcomes)`` so a
        case that was never attempted (blank code, which short-circuits
        inside ``analyze_error``) cannot push the figure negative.
        """
        return sum(max(0, o.attempts - 1) for o in self.outcomes)

    @property
    def tools_used(self) -> int:
        """Total tools the Agent called across the set.

        Reported because it is the evidence that a ``tool-using`` run
        really did use tools: a run where this is zero measured the
        text-only path no matter what the mode said.
        """
        return sum(o.tools_used for o in self.outcomes)

    def by_label(self) -> Counter:
        """``label -> (hits, total)`` so a systematic miss is visible."""
        table: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for outcome in self.scored:
            table[outcome.expected][1] += 1
            if outcome.correct:
                table[outcome.expected][0] += 1
        return Counter({k: v[0] for k, v in table.items()})

    def label_totals(self) -> Counter:
        return Counter(o.expected for o in self.scored)

    def by_difficulty(self) -> Counter:
        table: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for outcome in self.scored:
            table[outcome.case.difficulty][1] += 1
            if outcome.correct:
                table[outcome.case.difficulty][0] += 1
        return Counter({k: v[0] for k, v in table.items()})

    def difficulty_totals(self) -> Counter:
        return Counter(o.case.difficulty for o in self.scored)

    def describe(self) -> str:
        """A human-readable report for the assertion message.

        Deliberately verbose: the spec's next step depends on *which*
        labels are missed, so a bare percentage would not be actionable.
        """
        lines = [
            f"accuracy {len(self.hits)}/{len(self.scored)} "
            f"= {self.accuracy:.0%} (gate {ACCURACY_GATE:.0%})",
            f"measured path: {MEASURED_PATH}",
            f"gateway calls: {self.calls} ({self.retries} retries)",
            f"tool calls: {self.tools_used}",
        ]

        if self.fallbacks:
            lines.append(
                f"fallback analyses: {len(self.fallbacks)} "
                f"(no supplied test case failed — "
                f"{', '.join(o.case.case_id for o in self.fallbacks)})"
            )
        if self.clipped:
            lines.append(
                f"clipped prompts: {', '.join(o.case.case_id for o in self.clipped)}"
            )

        lines.append("per label (hit/total):")
        hits, totals = self.by_label(), self.label_totals()
        for label in sorted(totals):
            lines.append(f"  {label:<22} {hits[label]}/{totals[label]}")

        lines.append("per difficulty (hit/total):")
        dh, dt = self.by_difficulty(), self.difficulty_totals()
        for level in sorted(dt):
            lines.append(f"  {level:<22} {dh[level]}/{dt[level]}")

        misses = [o for o in self.scored if not o.correct]
        if misses:
            lines.append("misses:")
            for outcome in misses:
                got = outcome.predicted or f"<no analysis: {outcome.error}>"
                lines.append(
                    f"  {outcome.case.case_id}: "
                    f"expected {outcome.expected!r}, got {got!r}"
                )

        return "\n".join(lines)


# --- provider availability --------------------------------------------


def _provider_status() -> tuple[bool, str]:
    """Whether an analysis can be attempted, plus the reason either way.

    Mirrors the gateway's own resolution rules (P2-0): an unknown
    provider name, or a named provider whose credential is blank, means
    no call can be made. The message is built here rather than at the
    skip site because a skip that does not say *what to set* is a skip
    nobody acts on.

    The list of valid names is read from the gateway rather than
    restated here. It was hardcoded once and went stale the moment a
    third provider was added — and the failure mode of a stale copy is
    the worst kind: the gate reports "skipped", which reads as "not
    measured yet" when the truth was "measured, and the harness was
    wrong about what the gateway supports".
    """
    from app.config import settings
    from app.llm import gateway

    provider = (settings.llm_provider or "").strip().lower()
    known = gateway.available_providers()

    if provider == "deepseek":
        if settings.deepseek_api_key.strip():
            return True, ""
        return False, (
            "LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is empty. The S2 "
            "accuracy gate has therefore NOT been measured — a skip here "
            "is not a pass. Set DEEPSEEK_API_KEY in sidecar/.env to run it."
        )

    if provider == "ollama":
        # Reachability is the gateway's problem; being configured is
        # enough to let the run proceed and report a real failure.
        if settings.ollama_base_url.strip():
            return True, ""
        return False, "LLM_PROVIDER=ollama but OLLAMA_BASE_URL is empty."

    if provider in ("openai_compatible", "openai-compatible"):
        # The two settings the gateway itself requires (P2-0). The key is
        # optional there and so it is optional here.
        if not settings.openai_compatible_base_url.strip():
            return False, (
                "LLM_PROVIDER=openai_compatible but "
                "OPENAI_COMPATIBLE_BASE_URL is empty. Set it in sidecar/.env."
            )
        if not settings.openai_compatible_model.strip():
            return False, (
                "LLM_PROVIDER=openai_compatible but "
                "OPENAI_COMPATIBLE_MODEL is empty. Set it in sidecar/.env."
            )
        return True, ""

    return False, (
        f"LLM_PROVIDER={provider!r} is not a provider the gateway accepts "
        f"(expected one of: {', '.join(known)}), so no accuracy can be "
        f"measured."
    )


def _provider_ready() -> bool:
    return _provider_status()[0]


_PROVIDER_READY, _SKIP_REASON = _provider_status()

requires_provider = pytest.mark.skipif(not _PROVIDER_READY, reason=_SKIP_REASON)


# --- harness -----------------------------------------------------------


def _judge_cases(case: EvalCase) -> list[JudgeTestCase]:
    return [
        JudgeTestCase(input=tc.input, expected_output=tc.expected_output)
        for tc in case.test_cases
    ]


def _failing_case(case: EvalCase) -> tuple[str, str, str, bool]:
    """Pick the input/expected/actual triple to analyse.

    Returns:
        ``(stdin, expected, actual, fallback)``. ``fallback`` is True when
        no supplied test case failed, in which case the first case's I/O
        is used so the analysis still has concrete facts to work from.
    """
    result = judge(case.wrong_code, _judge_cases(case))

    for test_case in result.test_cases:
        if test_case.status is not JudgeStatus.AC:
            return (
                test_case.input,
                test_case.expected_output,
                test_case.actual_output,
                False,
            )

    first = case.test_cases[0]
    return first.input, first.expected_output, "", True


def _score(case: EvalCase) -> CaseOutcome:
    stdin, expected, actual, fallback = _failing_case(case)

    # Clip before the call, not inside it: the limit is a property of this
    # harness's budget, and ``analyze_error`` is production code that has
    # no business knowing about it.
    stdin, c1 = _clip(stdin)
    expected, c2 = _clip(expected)
    actual, c3 = _clip(actual)

    outcome = analyze_error(
        case.wrong_code,
        stdin,
        expected,
        actual,
        # The case's own verdict: a TLE case must reach the prompt as
        # TLE, otherwise ``wrong_complexity`` is unreachable by
        # construction and its cases are lost for the wrong reason.
        statement=case.statement,
        verdict=case.observable_verdict,
        use_tools=_USE_TOOLS,
    )
    assert isinstance(outcome, AnalysisOutcome)

    return CaseOutcome(
        case=case,
        predicted=(
            str(outcome.result.error_type) if outcome.result is not None else None
        ),
        fallback=fallback,
        clipped=c1 or c2 or c3,
        error=outcome.error,
        attempts=outcome.attempts,
        tools_used=len(outcome.tool_calls),
    )


@pytest.fixture(scope="module")
def report() -> AccuracyReport:
    """Run the whole eval set once; every assertion reads this."""
    return AccuracyReport(outcomes=[_score(case) for case in load_eval_cases()])


# --- the gate ----------------------------------------------------------


@pytest.mark.eval_set
@requires_provider
def test_s2_accuracy_clears_the_gate(report: AccuracyReport) -> None:
    """P2-5's go/no-go: ≥60% exact-match on the 20 labelled cases.

    A failure here is the signal AGENTS.md describes as the project's
    life-or-death line. Before concluding "the model is not good enough",
    read the per-label breakdown in the message: misses concentrated on
    one label mean the prompt's disambiguation rules need work (P2-1),
    misses spread evenly mean the taxonomy is too fine for a single
    call, and a large ``<no analysis>`` count means the provider, not
    the Agent, is the problem.
    """
    assert report.scored, (
        "no case was scored — every analysis failed before producing a "
        "label, which points at the provider rather than the Agent"
    )
    assert report.accuracy >= ACCURACY_GATE, report.describe()


# --- properties of the run itself --------------------------------------
#
# These do not depend on the model being *right*, only on the harness
# having exercised the whole set. They keep the gate honest: a run that
# silently scored 3 of 20 cases must not be able to report 100%.


@pytest.mark.eval_set
@requires_provider
def test_the_whole_set_is_measured(report: AccuracyReport) -> None:
    """Every case reaches the denominator.

    A shrinking denominator is how an accuracy number quietly becomes
    meaningless — 3 right out of 3 reads as 100% — so it is asserted
    rather than assumed.
    """
    assert len(report.outcomes) == EXPECTED_CASE_COUNT
    assert len(report.scored) == EXPECTED_CASE_COUNT, (
        f"{len(report.scored)} of {EXPECTED_CASE_COUNT} cases contributed "
        "to the score; the gate must be measured on the whole set"
    )


@pytest.mark.eval_set
@requires_provider
def test_every_scored_case_produced_a_label(report: AccuracyReport) -> None:
    """An unanalysable case is reported as such, never as a wrong label.

    Both count as misses, but they mean different things: the former is
    an infrastructure result and the latter an Agent result. Collapsing
    them would send the remediation down the wrong branch.
    """
    broken = [
        f"{o.case.case_id}: {o.error}" for o in report.scored if o.error is not None
    ]
    assert not broken, "analysis failed outright for:\n  " + "\n  ".join(broken)


@pytest.mark.eval_set
@requires_provider
def test_fallback_analyses_stay_rare(report: AccuracyReport) -> None:
    """Few cases should need the fallback input.

    A fallback means the case's ``test_cases`` did not expose the bug
    its label claims — a defect in the eval set, not in the Agent. A
    couple are tolerable (a TLE only manifests on a large hidden input),
    but a drift upward would mean the set is no longer evidence.
    """
    cap = EXPECTED_CASE_COUNT // 4
    assert len(report.fallbacks) <= cap, (
        f"{len(report.fallbacks)} cases fell back to their first test case "
        f"(cap {cap}):\n  "
        + "\n  ".join(o.case.case_id for o in report.fallbacks)
    )


# --- cheap guards (no provider needed) ---------------------------------
#
# These run in the default suite. They cannot measure accuracy, but they
# can catch the ways this harness would *lie* about accuracy.


def test_report_scores_an_unanalysable_case_as_a_miss() -> None:
    """A gateway failure must lower the score, not leave the denominator.

    If ``scored`` excluded errored cases, a total provider outage would
    produce ``0/0`` → 0.0 and a half-outage would *raise* the score by
    dropping the cases that were about to be wrong. Both are the wrong
    direction for a gate.
    """
    from app.eval_cases import EvalCase, TestCase

    case = EvalCase(
        case_id="synthetic",
        title="synthetic",
        statement="not a real problem, built by the test",
        knowledge_tags=["t"],
        wrong_code="int main(){}",
        expected_root_cause="off_by_one",
        test_cases=[TestCase(input="1\n", expected_output="1\n")],
    )
    report = AccuracyReport(
        outcomes=[CaseOutcome(case=case, error="LLMConfigError: no key")]
    )

    assert len(report.scored) == 1
    assert report.accuracy == 0.0
    assert report.calls == 0


def test_report_counts_a_correct_label() -> None:
    """The complementary case, so ``accuracy`` is not trivially zero."""
    from app.eval_cases import EvalCase, TestCase

    case = EvalCase(
        case_id="synthetic",
        title="synthetic",
        statement="not a real problem, built by the test",
        knowledge_tags=["t"],
        wrong_code="int main(){}",
        expected_root_cause="off_by_one",
        test_cases=[TestCase(input="1\n", expected_output="1\n")],
    )
    report = AccuracyReport(
        outcomes=[CaseOutcome(case=case, predicted="off_by_one", attempts=1)]
    )

    assert report.accuracy == 1.0
    assert report.calls == 1
    assert "off_by_one" in report.describe()


def test_describe_names_the_missed_labels() -> None:
    """The failure message is the tool for choosing a remediation branch."""
    from app.eval_cases import EvalCase, TestCase

    def synthetic(case_id: str, expected: str) -> EvalCase:
        return EvalCase(
            case_id=case_id,
            title=case_id,
            statement="not a real problem, built by the test",
            knowledge_tags=["t"],
            wrong_code="int main(){}",
            expected_root_cause=expected,
            test_cases=[TestCase(input="1\n", expected_output="1\n")],
        )

    report = AccuracyReport(
        outcomes=[
            CaseOutcome(case=synthetic("a", "overflow"), predicted="overflow"),
            CaseOutcome(
                case=synthetic("b", "off_by_one"),
                predicted="boundary_condition",
            ),
        ]
    )
    text = report.describe()

    assert "1/2" in text
    assert "expected 'off_by_one', got 'boundary_condition'" in text


def test_an_invalid_provider_name_is_a_skip_not_a_crash() -> None:
    """``_provider_status`` must not raise on a misconfigured provider.

    A typo'd ``LLM_PROVIDER`` is a config error the gateway reports
    nicely (P2-0); this harness must degrade to a skip rather than
    turning every run of the suite into an error.
    """
    from unittest.mock import patch

    from app.config import settings

    with patch.object(settings, "llm_provider", "nonexistent-provider"):
        ready, reason = _provider_status()

    assert ready is False
    assert "nonexistent-provider" in reason


def test_a_configured_compatible_endpoint_counts_as_ready() -> None:
    """The generic provider must not be mistaken for an unknown one.

    Regression guard for 2026-09-16: ``_provider_status`` hardcoded the
    two original names, so a correctly configured ``openai_compatible``
    endpoint produced a *skip* whose reason claimed the gateway would
    reject it. A skip reads as "not measured", which would have hidden
    the very measurement the endpoint was configured for.
    """
    from unittest.mock import patch

    from app.config import settings

    with (
        patch.object(settings, "llm_provider", "openai_compatible"),
        patch.object(settings, "openai_compatible_base_url", "https://host/v1"),
        patch.object(settings, "openai_compatible_model", "some-model"),
    ):
        ready, reason = _provider_status()

    assert ready is True
    assert reason == ""


def test_a_compatible_endpoint_missing_its_base_url_is_a_skip() -> None:
    """An incomplete generic provider says which setting is missing."""
    from unittest.mock import patch

    from app.config import settings

    with (
        patch.object(settings, "llm_provider", "openai_compatible"),
        patch.object(settings, "openai_compatible_base_url", ""),
        patch.object(settings, "openai_compatible_model", "some-model"),
    ):
        ready, reason = _provider_status()

    assert ready is False
    assert "OPENAI_COMPATIBLE_BASE_URL" in reason


def test_the_unknown_provider_message_lists_the_gateways_own_names() -> None:
    """The "expected ..." list is read from the gateway, not restated.

    This is what keeps the message honest as providers are added: a
    hardcoded copy is what went stale when the third one arrived.
    """
    from unittest.mock import patch

    from app.config import settings
    from app.llm import gateway

    with patch.object(settings, "llm_provider", "nonexistent-provider"):
        _, reason = _provider_status()

    for name in gateway.available_providers():
        assert name in reason


def test_the_gate_constant_is_the_one_the_eval_set_owns() -> None:
    """Guards the import: two copies of 0.60 would eventually disagree."""
    assert ACCURACY_GATE == 0.60
    assert 0.0 < ACCURACY_GATE < 1.0


def test_the_tool_switch_accepts_the_obvious_spellings() -> None:
    """``S2_USE_TOOLS`` must not silently mean "off" for a plausible value.

    A silently-ignored switch is the dangerous case: the run reports the
    text-only number while the operator believes it measured the shipped
    tool-using path, and nothing in the output says otherwise.
    """
    for truthy in ("1", "true", "TRUE", " yes ", "on"):
        assert _tool_switch_enabled(truthy) is True, truthy
    for falsy in ("", "  ", "0", "false", "No", "off"):
        assert _tool_switch_enabled(falsy) is False, falsy


def test_clip_leaves_short_values_untouched() -> None:
    """Ordinary inputs pass through byte-for-byte.

    Clipping must not perturb the 19 small cases — a stray marker in
    every prompt would be noise the model has to reason around.
    """
    text, clipped = _clip("3 7\n")
    assert text == "3 7\n"
    assert clipped is False


def test_clip_keeps_the_head_and_states_the_original_size() -> None:
    """A clipped input still shows its shape and admits what was dropped.

    The model is told the true length so it can reason about the input
    being large — which is exactly the signal a TLE case needs.
    """
    original = "1 " * 5000
    text, clipped = _clip(original)

    assert clipped is True
    assert text.startswith("1 1 1")
    assert str(len(original)) in text
    assert len(text) < len(original)


def test_the_real_tle_case_is_the_one_that_gets_clipped() -> None:
    """Documents which eval case needs clipping, and why the limit exists.

    If someone adds a 400 KB input to another case, or lowers the limit,
    this test is where the token-budget consequence becomes visible.
    """
    from app.eval_cases import load_eval_cases

    huge = [
        c.case_id
        for c in load_eval_cases()
        for tc in c.test_cases
        if len(tc.input) > MAX_PROMPT_CHARS
    ]
    assert huge == ["s2-011-pair-sum-quadratic"], (
        "the set of over-long inputs changed; re-check the prompt budget"
    )


def test_llm_config_error_is_the_expected_unconfigured_signal() -> None:
    """Documents which exception means "no provider", not "no answer"."""
    assert issubclass(LLMConfigError, RuntimeError)
