"""Tests for tools/load_problems.py — the catalogue verification gate.

The CLI has two jobs and they are worth testing separately:

* **verify** decides whether a problem file is sound, by running the
  reference solution against every case. Getting this wrong is the one
  failure that matters, because it is the only thing standing between a
  corpus with a wrong expected output and the database. The tests below
  cover both directions: a good file passes, and a file whose expected
  output disagrees with the reference solution is rejected with the
  offending case named.
* **main** applies the gate to the whole directory and writes nothing
  unless every file passed. That all-or-nothing rule is the reason a
  partial import can never happen.

Compiling is slow, so the tests that need a real compile share one
module-scoped fixture per distinct program.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))

from app.problems_corpus import CatalogueProblem, CatalogueTestCase  # noqa: E402

# tools/ is not a package, so the module is loaded by path. This mirrors
# how the script is actually run (`python tools/load_problems.py`).
_spec = importlib.util.spec_from_file_location(
    "load_problems", ROOT / "tools" / "load_problems.py"
)
assert _spec is not None and _spec.loader is not None
load_problems = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load_problems)


# A trivial program: reads one int, prints it twice the value.
_ECHO_DOUBLE = """\
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n * 2 << std::endl;
    return 0;
}
"""


def _problem(
    *,
    reference: str = _ECHO_DOUBLE,
    cases: list[CatalogueTestCase] | None = None,
    **overrides,
) -> CatalogueProblem:
    """A shape-valid problem wrapping *reference*, unless overridden."""
    base = {
        "problem_id": "p",
        "title": "t",
        "knowledge_tags": ["tag"],
        "difficulty": "easy",
        "description": "d",
        "test_cases": cases
        or [
            CatalogueTestCase("1\n", "2\n", is_sample=True),
            CatalogueTestCase("2\n", "4\n", is_sample=True),
            CatalogueTestCase("3\n", "6\n"),
            CatalogueTestCase("4\n", "8\n"),
            CatalogueTestCase("5\n", "10\n"),
        ],
        "correct_reference": reference,
    }
    base.update(overrides)
    return CatalogueProblem(**base)  # type: ignore[arg-type]


# --- verify: the happy path -------------------------------------------


def test_a_correct_reference_verifies() -> None:
    assert load_problems.verify(_problem()) == []


def test_verify_covers_hidden_cases_too() -> None:
    """A reference that fails only a hidden case must still be rejected.

    This is the case a samples-only check would wave through, and it is
    the one that matters: a hidden case is never shown to the user, so
    nothing else in the system would ever notice it was wrong.
    """
    problem = _problem(
        cases=[
            CatalogueTestCase("1\n", "2\n", is_sample=True),
            CatalogueTestCase("2\n", "4\n", is_sample=True),
            CatalogueTestCase("3\n", "6\n"),
            CatalogueTestCase("4\n", "8\n"),
            # The reference outputs 10 here, so the file is wrong.
            CatalogueTestCase("5\n", "999\n"),
        ]
    )

    problems = load_problems.verify(problem)

    assert problems
    assert any("not AC" in p for p in problems)
    assert any("999" in p for p in problems), "the bad case is not named"


def test_a_failing_sample_is_reported_as_a_sample() -> None:
    problem = _problem(
        cases=[
            CatalogueTestCase("1\n", "999\n", is_sample=True),
            CatalogueTestCase("2\n", "4\n", is_sample=True),
            CatalogueTestCase("3\n", "6\n"),
            CatalogueTestCase("4\n", "8\n"),
            CatalogueTestCase("5\n", "10\n"),
        ]
    )

    problems = load_problems.verify(problem)

    assert any("(sample)" in p for p in problems), problems


def test_a_failing_hidden_case_is_reported_as_hidden() -> None:
    problem = _problem(
        cases=[
            CatalogueTestCase("1\n", "2\n", is_sample=True),
            CatalogueTestCase("2\n", "4\n", is_sample=True),
            CatalogueTestCase("3\n", "6\n"),
            CatalogueTestCase("4\n", "8\n"),
            CatalogueTestCase("5\n", "999\n"),
        ]
    )

    problems = load_problems.verify(problem)

    assert any("(hidden)" in p for p in problems), problems


# --- verify: rejection -------------------------------------------------


def test_a_reference_that_does_not_compile_is_rejected() -> None:
    problem = _problem(reference="int main( { this is not C++")

    problems = load_problems.verify(problem)

    assert any("does not compile" in p for p in problems), problems


def test_a_wrong_answer_reference_is_rejected() -> None:
    problem = _problem(
        reference="""\
#include <iostream>
int main() { std::cout << 0 << std::endl; return 0; }
""",
    )

    problems = load_problems.verify(problem)

    assert any("not AC" in p for p in problems), problems


def test_a_shape_problem_short_circuits_before_compiling() -> None:
    """A malformed file is reported structurally, not as a judge failure.

    Compiling first would replace "needs at least 3 hidden cases" with
    "the reference got WA", which sends the author looking in the wrong
    place.
    """
    problem = _problem(
        correct_reference="",
        cases=[CatalogueTestCase("1\n", "2\n", is_sample=True)],
    )

    problems = load_problems.verify(problem)

    assert any("correct_reference is empty" in p for p in problems)
    assert not any("not AC" in p for p in problems), "it compiled anyway"


def test_an_empty_reference_with_good_cases_is_still_rejected() -> None:
    problems = load_problems.verify(_problem(reference=""))

    assert any("correct_reference is empty" in p for p in problems)


# --- main: the all-or-nothing gate ------------------------------------


def _write_corpus(tmp_path: Path, **files: str) -> Path:
    for name, text in files.items():
        (tmp_path / f"{name}.yaml").write_text(text, encoding="utf-8")
    return tmp_path


_GOOD_YAML = """\
id: good
title: 好题
knowledge_tags: [基础语法]
difficulty: easy
description: 读入一个整数，输出它的两倍。
test_cases:
  - input: "1\\n"
    expected_output: "2\\n"
    is_sample: true
  - input: "2\\n"
    expected_output: "4\\n"
    is_sample: true
  - input: "3\\n"
    expected_output: "6\\n"
  - input: "4\\n"
    expected_output: "8\\n"
  - input: "5\\n"
    expected_output: "10\\n"
correct_reference: |
  #include <iostream>
  int main() { int n; std::cin >> n; std::cout << n * 2 << std::endl; return 0; }
"""


def test_main_succeeds_on_a_good_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_corpus(tmp_path, good=_GOOD_YAML)

    code = load_problems.main(["--dry-run", "--dir", str(tmp_path)])

    assert code == 0
    assert "all 1 problems verified" in capsys.readouterr().out


def test_main_fails_and_reports_a_bad_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_corpus(tmp_path, bad=_GOOD_YAML.replace('"10\\n"', '"999\\n"'))

    code = load_problems.main(["--dry-run", "--dir", str(tmp_path)])

    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "nothing was written" in out


def test_main_reports_every_file_not_just_the_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One run should surface all the broken files."""
    _write_corpus(
        tmp_path,
        bad_a=_GOOD_YAML.replace('"10\\n"', '"999\\n"').replace(
            "id: good", "id: bad-a"
        ),
        bad_b=_GOOD_YAML.replace('"2\\n"', '"777\\n"').replace(
            "id: good", "id: bad-b"
        ),
    )

    code = load_problems.main(["--dry-run", "--dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "bad-a" in out
    assert "bad-b" in out


def test_main_fails_on_a_missing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = load_problems.main(["--dry-run", "--dir", str(tmp_path / "nope")])

    assert code == 1
    assert "error:" in capsys.readouterr().out


def test_main_fails_on_an_empty_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = load_problems.main(["--dry-run", "--dir", str(tmp_path)])

    assert code == 1
    assert "no problems found" in capsys.readouterr().out


def test_dry_run_reports_the_distributions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_corpus(tmp_path, good=_GOOD_YAML)

    load_problems.main(["--dry-run", "--dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert "difficulty:" in out
    assert "tags (" in out
    assert "skipping the database import" in out


# --- The real corpus ---------------------------------------------------


def test_the_real_corpus_verifies() -> None:
    """**PU-14's strongest check.** Every reference solution must AC.

    Runs all twenty problems through the judge, which is why it is the
    slowest test in the suite. It is also the only thing that proves the
    shipped expected outputs are right — three of them were wrong on the
    first draft and were caught by exactly this.
    """
    from app.problems_corpus import load_catalogue

    failures = {
        p.problem_id: problems
        for p in load_catalogue()
        if (problems := load_problems.verify(p))
    }

    assert not failures, f"corpus failed verification: {failures}"
