"""Tests for the S2 evaluation set (P2-5).

Two layers of assurance:

1. **Structural** — the set loads, has 20 cases, uses only canonical
   root-cause labels, and is reasonably balanced. These run in ~1s and
   catch mistakes when someone edits a case file.

2. **Behavioural** — every case is run through the real judge: the
   reference solution must AC and the buggy submission must produce the
   labelled failure verdict. This is what makes an accuracy number
   measured on this set meaningful; without it a case could carry a
   "root cause" label that no observable evidence supports.

The behavioural tests compile C++ for every case, so they are several
minutes of wall clock. They are marked ``eval_set`` and run by default;
deselect with ``-m "not eval_set"`` for a fast inner loop.
"""

from __future__ import annotations

import pytest

from app.eval_cases import (
    ROOT_CAUSE_LABELS,
    load_eval_cases,
    root_cause_distribution,
)
from app.sandbox.judge import JudgeStatus, judge
from app.sandbox.judge import TestCase as JudgeTestCase

EXPECTED_CASE_COUNT = 20

# Accuracy gate from P2-5 / AGENTS.md: the project is a no-go if the
# Agent cannot reach this on the evaluation set.
ACCURACY_GATE = 0.60


@pytest.fixture(scope="module")
def cases():
    return load_eval_cases()


# --- Structural -------------------------------------------------------


def test_eval_set_has_twenty_cases(cases) -> None:
    """P2-5 specifies exactly 20 annotated real-problem cases."""
    assert len(cases) == EXPECTED_CASE_COUNT


def test_case_ids_are_unique(cases) -> None:
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_all_labels_are_canonical(cases) -> None:
    """Every expected_root_cause must be in the closed taxonomy.

    Accuracy is only well-defined against a closed label set, so a stray
    free-text label would silently corrupt the metric.
    """
    for case in cases:
        assert case.expected_root_cause in ROOT_CAUSE_LABELS, case.case_id


def test_every_case_has_required_metadata(cases) -> None:
    """Statement, tags, notes and a reference solution are all required."""
    for case in cases:
        assert case.title, f"{case.case_id}: missing title"
        assert case.statement.strip(), f"{case.case_id}: missing statement"
        assert case.knowledge_tags, f"{case.case_id}: missing knowledge_tags"
        assert case.notes.strip(), f"{case.case_id}: missing notes"
        assert case.correct_code.strip(), f"{case.case_id}: missing correct_code"
        assert case.test_cases, f"{case.case_id}: missing test_cases"


def test_label_distribution_is_balanced(cases) -> None:
    """No single label may dominate the set.

    If, say, 15 of 20 cases were ``off_by_one``, an Agent that always
    answered ``off_by_one`` would score 75% and pass the 60% gate while
    knowing nothing. Capping any single label at 1/4 of the set keeps
    the metric honest.
    """
    dist = root_cause_distribution(cases)
    cap = EXPECTED_CASE_COUNT // 4
    for label, count in dist.items():
        assert count <= cap, f"label {label!r} appears {count} times (cap {cap})"


def test_taxonomy_coverage(cases) -> None:
    """The set should exercise a substantial share of the taxonomy."""
    covered = {c.expected_root_cause for c in cases}
    assert len(covered) >= 8, f"only {len(covered)} labels covered"


def test_diffulty_spread(cases) -> None:
    """The set should not be all one difficulty."""
    levels = {c.difficulty for c in cases}
    assert len(levels) >= 2
    # Every case has a statement that actually describes a problem.
    for case in cases:
        assert len(case.statement.strip()) > 15, case.case_id


# --- Behavioural (compile + run) --------------------------------------


def _judge_cases(case) -> list[JudgeTestCase]:
    return [
        JudgeTestCase(input=tc.input, expected_output=tc.expected_output)
        for tc in case.test_cases
    ]


@pytest.mark.eval_set
def test_reference_solutions_are_accepted(cases) -> None:
    """Each case's correct_code must AC every test case.

    A reference that fails means the test cases encode the wrong
    expected output, which would make the whole case unusable as a
    labelled sample.
    """
    broken: list[str] = []
    for case in cases:
        result = judge(case.correct_code, _judge_cases(case))
        if result.status is not JudgeStatus.AC:
            detail = ", ".join(
                f"case{i}={tc.status.value}"
                for i, tc in enumerate(result.test_cases)
                if tc.status is not JudgeStatus.AC
            )
            broken.append(f"{case.case_id}({result.status.value}: {detail})")
    assert not broken, f"reference solutions not AC: {broken}"


@pytest.mark.eval_set
def test_buggy_submissions_fail_as_labelled(cases) -> None:
    """Each wrong_code must fail with the verdict the case declares.

    This is the evidence backing ``expected_root_cause``: if the
    submission were AC there would be no bug to explain.
    """
    problems: list[str] = []
    for case in cases:
        result = judge(case.wrong_code, _judge_cases(case))
        if result.status is JudgeStatus.AC:
            problems.append(f"{case.case_id}=AC (expected a failure)")
        elif result.status.value != case.observable_verdict:
            problems.append(
                f"{case.case_id}={result.status.value} "
                f"(expected {case.observable_verdict})"
            )
    assert not problems, f"buggy submissions behaved unexpectedly: {problems}"


@pytest.mark.eval_set
def test_cases_are_discriminating(cases) -> None:
    """At least one test case must separate wrong_code from correct_code.

    A case whose buggy and correct solutions produce identical verdicts
    provides no signal to the error-analysis Agent.
    """
    not_discriminating: list[str] = []
    for case in cases:
        jc = _judge_cases(case)
        if judge(case.wrong_code, jc).status is judge(case.correct_code, jc).status:
            not_discriminating.append(case.case_id)
    assert not not_discriminating, (
        f"cases where buggy and correct behave identically: {not_discriminating}"
    )
