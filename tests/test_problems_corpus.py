"""Tests for the problem catalogue schema and loader (PU-14).

The corpus itself is verified by ``tools/load_problems.py``, which runs
every reference solution against every case — that is the gate that
proves the twenty problems are internally consistent, and it is covered
by ``test_problems_loader_cli.py``.

What this module tests is the *schema*: that a malformed file is
rejected with a useful message, that the fail-closed default on
``is_sample`` really is closed, and that ``samples`` is derived from the
flagged cases rather than declared twice. Those are the properties that
stop a bad file from reaching the database at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.eval_cases import ROOT_CAUSE_LABELS
from app.problems_corpus import (
    DIFFICULTIES,
    NOTE_LABEL_ALIASES,
    CatalogueProblem,
    CatalogueTestCase,
    canonical_labels_for,
    difficulty_distribution,
    extract_note_labels,
    load_catalogue,
    tag_distribution,
)

VALID_YAML = """\
id: test-problem
title: 测试题
knowledge_tags:
  - 基础语法
difficulty: easy
description: |
  输入一个整数，输出它本身。
test_cases:
  - input: "1\\n"
    expected_output: "1\\n"
    is_sample: true
  - input: "2\\n"
    expected_output: "2\\n"
    is_sample: true
  - input: "3\\n"
    expected_output: "3\\n"
  - input: "4\\n"
    expected_output: "4\\n"
  - input: "5\\n"
    expected_output: "5\\n"
correct_reference: |
  #include <iostream>
  int main() { int n; std::cin >> n; std::cout << n << std::endl; }
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _problem(**overrides) -> CatalogueProblem:
    """A problem that passes ``validate_shape`` unless overridden."""
    base = {
        "problem_id": "p",
        "title": "t",
        "knowledge_tags": ["tag"],
        "difficulty": "easy",
        "description": "d",
        "test_cases": [
            CatalogueTestCase("1\n", "1\n", is_sample=True),
            CatalogueTestCase("2\n", "2\n", is_sample=True),
            CatalogueTestCase("3\n", "3\n"),
            CatalogueTestCase("4\n", "4\n"),
            CatalogueTestCase("5\n", "5\n"),
        ],
        "correct_reference": "int main() {}",
    }
    base.update(overrides)
    return CatalogueProblem(**base)


# --- Loading -----------------------------------------------------------


def test_loads_a_valid_file(tmp_path: Path) -> None:
    _write(tmp_path, "p.yaml", VALID_YAML)

    problems = load_catalogue(tmp_path)

    assert len(problems) == 1
    problem = problems[0]
    assert problem.problem_id == "test-problem"
    assert problem.title == "测试题"
    assert problem.difficulty == "easy"
    assert len(problem.test_cases) == 5


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_catalogue(tmp_path / "nope")


def test_empty_directory_returns_nothing(tmp_path: Path) -> None:
    """An empty corpus is not an error here — the CLI reports it."""
    assert load_catalogue(tmp_path) == []


def test_malformed_yaml_names_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "broken.yaml", "id: [unclosed\n")

    with pytest.raises(ValueError, match="broken.yaml"):
        load_catalogue(tmp_path)


def test_non_mapping_top_level_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "list.yaml", "- just\n- a list\n")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_catalogue(tmp_path)


def test_duplicate_problem_ids_are_rejected(tmp_path: Path) -> None:
    """Two files claiming the same id is a copy-paste error, not a merge."""
    _write(tmp_path, "a.yaml", VALID_YAML)
    _write(tmp_path, "b.yaml", VALID_YAML.replace("test-problem", "test-problem"))

    with pytest.raises(ValueError, match="duplicate problem id"):
        load_catalogue(tmp_path)


def test_problem_id_falls_back_to_the_filename(tmp_path: Path) -> None:
    _write(tmp_path, "no-id.yaml", VALID_YAML.replace("id: test-problem\n", ""))

    problems = load_catalogue(tmp_path)

    assert problems[0].problem_id == "no-id"


def test_files_are_loaded_in_sorted_order(tmp_path: Path) -> None:
    """Deterministic order, so the import produces reproducible ids."""
    _write(tmp_path, "b.yaml", VALID_YAML.replace("test-problem", "bbb"))
    _write(tmp_path, "a.yaml", VALID_YAML.replace("test-problem", "aaa"))

    ids = [p.problem_id for p in load_catalogue(tmp_path)]

    assert ids == ["aaa", "bbb"]


def test_numeric_fields_survive_yaml_typing(tmp_path: Path) -> None:
    """``time_limit: 2`` parses as int; the dataclass must coerce it."""
    text = VALID_YAML + "time_limit: 2\nmemory_limit: 512\n"
    _write(tmp_path, "p.yaml", text)

    problem = load_catalogue(tmp_path)[0]

    assert problem.time_limit == 2.0
    assert isinstance(problem.time_limit, float)
    assert problem.memory_limit == 512


# --- The fail-closed default -------------------------------------------


def test_a_case_without_the_flag_is_hidden() -> None:
    """**The security-relevant default.** Unmarked means hidden."""
    case = CatalogueTestCase(input="1\n", expected_output="1\n")

    assert case.is_sample is False


def test_unmarked_cases_stay_hidden_when_loaded(tmp_path: Path) -> None:
    text = """\
id: p
title: t
knowledge_tags: [tag]
difficulty: easy
description: d
test_cases:
  - input: "1\\n"
    expected_output: "1\\n"
    is_sample: true
  - input: "2\\n"
    expected_output: "2\\n"
    is_sample: true
  - input: "SECRET\\n"
    expected_output: "SECRET-OUT\\n"
correct_reference: x
"""
    _write(tmp_path, "p.yaml", text)

    problem = load_catalogue(tmp_path)[0]

    assert [tc.input for tc in problem.hidden_cases] == ["SECRET\n"]
    assert all("SECRET" not in s["input"] for s in problem.samples)


# --- samples derivation ------------------------------------------------


def test_samples_are_derived_from_flagged_cases() -> None:
    """One source of truth: samples mirror the flagged cases."""
    problem = _problem()

    assert [s["input"] for s in problem.samples] == ["1", "2"]


def test_samples_strip_the_trailing_newline() -> None:
    """Samples are display text; a trailing newline would render oddly."""
    assert all(not s["input"].endswith("\n") for s in _problem().samples)
    assert all(not s["output"].endswith("\n") for s in _problem().samples)


def test_samples_keep_the_hidden_cases_out() -> None:
    problem = _problem()

    assert len(problem.samples) == 2
    assert len(problem.hidden_cases) == 3


def test_no_samples_means_an_empty_list() -> None:
    """Not None — callers should not have to null-check."""
    problem = _problem(
        test_cases=[CatalogueTestCase(f"{i}\n", f"{i}\n") for i in range(5)]
    )

    assert problem.samples == []


# --- validate_shape ----------------------------------------------------


def test_a_well_formed_problem_has_no_complaints() -> None:
    assert _problem().validate_shape() == []


@pytest.mark.parametrize(
    "field, expected",
    [
        ("title", "title is empty"),
        ("description", "description is empty"),
        ("correct_reference", "correct_reference is empty"),
    ],
)
def test_missing_required_text_is_reported(field: str, expected: str) -> None:
    problems = _problem(**{field: "   "}).validate_shape()

    assert any(expected in p for p in problems)


def test_empty_knowledge_tags_are_reported() -> None:
    problems = _problem(knowledge_tags=[]).validate_shape()

    assert any("knowledge_tags" in p for p in problems)


def test_no_test_cases_is_reported() -> None:
    problems = _problem(test_cases=[]).validate_shape()

    assert any("no test cases" in p for p in problems)


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_each_allowed_difficulty_passes(difficulty: str) -> None:
    assert _problem(difficulty=difficulty).validate_shape() == []


def test_an_unknown_difficulty_is_reported() -> None:
    problems = _problem(difficulty="impossible").validate_shape()

    assert any("difficulty must be one of" in p for p in problems)


def test_too_few_samples_is_reported() -> None:
    """One sample does not show the user enough of the format."""
    problem = _problem(
        test_cases=[
            CatalogueTestCase("1\n", "1\n", is_sample=True),
            *[CatalogueTestCase(f"{i}\n", f"{i}\n") for i in range(2, 5)],
        ]
    )

    assert any("at least 2 sample cases" in p for p in problem.validate_shape())


def test_too_few_hidden_cases_is_reported() -> None:
    """Two hidden cases are not enough to catch a sample-only solution."""
    problem = _problem(
        test_cases=[
            CatalogueTestCase("1\n", "1\n", is_sample=True),
            CatalogueTestCase("2\n", "2\n", is_sample=True),
            CatalogueTestCase("3\n", "3\n"),
            CatalogueTestCase("4\n", "4\n"),
        ]
    )

    assert any("at least 3 hidden cases" in p for p in problem.validate_shape())


def test_the_required_minimum_is_accepted() -> None:
    """Exactly 2 samples + 3 hidden is the documented minimum."""
    problem = _problem(
        test_cases=[
            CatalogueTestCase("1\n", "1\n", is_sample=True),
            CatalogueTestCase("2\n", "2\n", is_sample=True),
            CatalogueTestCase("3\n", "3\n"),
            CatalogueTestCase("4\n", "4\n"),
            CatalogueTestCase("5\n", "5\n"),
        ]
    )

    assert problem.validate_shape() == []


def test_an_entirely_empty_case_is_reported() -> None:
    problem = _problem(
        test_cases=[
            CatalogueTestCase("1\n", "1\n", is_sample=True),
            CatalogueTestCase("2\n", "2\n", is_sample=True),
            CatalogueTestCase("", ""),
            CatalogueTestCase("4\n", "4\n"),
            CatalogueTestCase("5\n", "5\n"),
        ]
    )

    assert any("case 3 is empty" in p for p in problem.validate_shape())


def test_an_empty_input_with_an_output_is_allowed() -> None:
    """A case may legitimately read nothing — e.g. a fixed-output problem."""
    problem = _problem(
        test_cases=[
            CatalogueTestCase("", "Hello\n", is_sample=True),
            CatalogueTestCase("2\n", "2\n", is_sample=True),
            CatalogueTestCase("3\n", "3\n"),
            CatalogueTestCase("4\n", "4\n"),
            CatalogueTestCase("5\n", "5\n"),
        ]
    )

    assert problem.validate_shape() == []


def test_all_shape_problems_are_reported_at_once() -> None:
    """One run tells the author everything, not just the first thing."""
    problems = _problem(title="", knowledge_tags=[], difficulty="nope").validate_shape()

    assert len(problems) >= 3


# --- note labels -------------------------------------------------------


def test_extracts_labels_from_a_note() -> None:
    notes = "某个错误。\n错因标签：off_by_one / boundary_condition。\n"

    assert extract_note_labels(notes) == ["off_by_one", "boundary_condition"]


def test_a_note_without_labels_yields_nothing() -> None:
    assert extract_note_labels("只是一段说明，没有标签。") == []


def test_an_empty_note_yields_nothing() -> None:
    assert extract_note_labels("") == []


def test_trailing_period_is_not_part_of_the_label() -> None:
    assert extract_note_labels("错因标签：overflow。") == ["overflow"]


def test_labels_may_use_a_fullwidth_colon() -> None:
    assert extract_note_labels("错因标签：overflow") == ["overflow"]


def test_canonical_labels_pass_through() -> None:
    assert canonical_labels_for(["off_by_one"]) == ["off_by_one"]


def test_aliases_resolve_to_the_taxonomy() -> None:
    assert canonical_labels_for(["greedy_wrong"]) == ["wrong_algorithm"]


def test_resolution_is_case_insensitive() -> None:
    assert canonical_labels_for(["TLE"]) == ["wrong_complexity"]


def test_resolution_keeps_order_and_drops_duplicates() -> None:
    """Two aliases pointing at one label collapse to one entry."""
    assert canonical_labels_for(["TLE", "complexity"]) == ["wrong_complexity"]


def test_an_unknown_label_is_kept_not_dropped() -> None:
    """Dropping it would hide a label the taxonomy may need to grow."""
    assert canonical_labels_for(["brand_new_bug"]) == ["brand_new_bug"]


def test_blank_labels_are_skipped() -> None:
    assert canonical_labels_for(["", "  ", "overflow"]) == ["overflow"]


def test_every_alias_targets_a_real_label() -> None:
    """A typo in the alias table would silently mislabel whole problems."""
    unknown = {
        alias: target
        for alias, target in NOTE_LABEL_ALIASES.items()
        if target not in ROOT_CAUSE_LABELS
    }

    assert not unknown, f"aliases point at labels that do not exist: {unknown}"


# --- distributions -----------------------------------------------------


def test_difficulty_distribution_counts_each_level() -> None:
    problems = [
        _problem(difficulty="easy"),
        _problem(difficulty="easy"),
        _problem(difficulty="hard"),
    ]

    assert difficulty_distribution(problems) == {"easy": 2, "hard": 1}


def test_tag_distribution_counts_shared_tags() -> None:
    problems = [
        _problem(knowledge_tags=["数组", "排序"]),
        _problem(knowledge_tags=["数组"]),
    ]

    dist = tag_distribution(problems)

    assert dist["数组"] == 2
    assert dist["排序"] == 1


# --- The real corpus ---------------------------------------------------


def test_the_real_corpus_loads() -> None:
    """The shipped corpus must at least parse and satisfy its own shape."""
    problems = load_catalogue()

    assert len(problems) == 20, f"expected 20 problems, got {len(problems)}"


def test_the_real_corpus_shape_is_sound() -> None:
    problems = load_catalogue()

    complaints = {
        p.problem_id: p.validate_shape()
        for p in problems
        if p.validate_shape()
    }

    assert not complaints, f"malformed problems in the corpus: {complaints}"


def test_the_real_corpus_uses_a_valid_difficulty() -> None:
    for problem in load_catalogue():
        assert problem.difficulty in DIFFICULTIES


def test_the_real_corpus_ids_are_unique() -> None:
    ids = [p.problem_id for p in load_catalogue()]

    assert len(ids) == len(set(ids))


def test_every_real_problem_ships_a_reference_solution() -> None:
    """The reference is what lets the cases be verified at all."""
    for problem in load_catalogue():
        assert problem.correct_reference.strip(), problem.problem_id


def test_every_real_problem_meets_the_case_minimums() -> None:
    for problem in load_catalogue():
        sample_count = sum(1 for tc in problem.test_cases if tc.is_sample)
        hidden_count = len(problem.test_cases) - sample_count
        assert sample_count >= 2, problem.problem_id
        assert hidden_count >= 3, problem.problem_id
