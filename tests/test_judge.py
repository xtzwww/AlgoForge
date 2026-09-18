"""Tests for app.sandbox.judge (P1-13).

Verifies the output-comparison logic required by the slice:
- Exact match → AC.
- Whitespace-only differences (trailing spaces, trailing blank lines,
  CRLF vs LF) → AC (forgiving comparison).
- Mismatched content → WA.

Also covers :func:`normalize_output` directly, since the comparison
function is built on top of it.
"""

from __future__ import annotations

from app.sandbox.judge import JudgeStatus, judge_output, normalize_output

# --- normalize_output --------------------------------------------------

def test_normalize_strips_trailing_whitespace_per_line() -> None:
    """Trailing spaces on each line are removed."""
    assert normalize_output("42   \n13\t") == "42\n13"


def test_normalize_drops_trailing_blank_lines() -> None:
    """Trailing empty lines are removed; internal blanks are kept."""
    assert normalize_output("hello\n\n\n") == "hello"
    assert normalize_output("a\n\nb\n") == "a\n\nb"


def test_normalize_collapses_crlf() -> None:
    """Windows CRLF line endings are normalized to LF."""
    assert normalize_output("42\r\n13\r\n") == "42\n13"


def test_normalize_empty_input() -> None:
    """Empty input normalizes to an empty string."""
    assert normalize_output("") == ""
    assert normalize_output("\n\n\n") == ""


# --- judge_output: exact match ----------------------------------------

def test_judge_output_exact_match() -> None:
    """Identical strings with no padding → AC."""
    assert judge_output("42\n", "42\n") is JudgeStatus.AC


def test_judge_output_multiline_exact_match() -> None:
    """Multi-line identical output → AC."""
    actual = "1 2 3\n4 5 6\n7 8 9\n"
    expected = "1 2 3\n4 5 6\n7 8 9\n"
    assert judge_output(actual, expected) is JudgeStatus.AC


# --- judge_output: forgiving whitespace --------------------------------

def test_judge_output_trailing_spaces_tolerated() -> None:
    """Trailing spaces on lines should not cause WA."""
    actual = "42   \n13   \n"
    expected = "42\n13\n"
    assert judge_output(actual, expected) is JudgeStatus.AC


def test_judge_output_trailing_blank_lines_tolerated() -> None:
    """Trailing blank lines should not cause WA."""
    actual = "42\n\n\n"
    expected = "42\n"
    assert judge_output(actual, expected) is JudgeStatus.AC


def test_judge_output_crlf_tolerated() -> None:
    """CRLF in actual vs LF in expected should not cause WA."""
    actual = "42\r\n13\r\n"
    expected = "42\n13\n"
    assert judge_output(actual, expected) is JudgeStatus.AC


def test_judge_output_no_trailing_newline_tolerated() -> None:
    """Missing trailing newline in actual should not cause WA."""
    actual = "42"
    expected = "42\n"
    assert judge_output(actual, expected) is JudgeStatus.AC


# --- judge_output: mismatch -------------------------------------------

def test_judge_output_content_mismatch() -> None:
    """Different content → WA."""
    actual = "42\n"
    expected = "13\n"
    assert judge_output(actual, expected) is JudgeStatus.WA


def test_judge_output_missing_line() -> None:
    """Missing a line → WA (not a whitespace-only difference)."""
    actual = "1 2 3\n"
    expected = "1 2 3\n4 5 6\n"
    assert judge_output(actual, expected) is JudgeStatus.WA


def test_judge_output_extra_line() -> None:
    """Extra line in actual → WA."""
    actual = "1 2 3\n4 5 6\n"
    expected = "1 2 3\n"
    assert judge_output(actual, expected) is JudgeStatus.WA


def test_judge_output_internal_blank_line_matters() -> None:
    """A blank line in the middle is significant, not tolerated."""
    # actual has a blank line between values; expected doesn't.
    actual = "42\n\n13\n"
    expected = "42\n13\n"
    assert judge_output(actual, expected) is JudgeStatus.WA


def test_judge_output_empty_vs_nonempty() -> None:
    """Empty actual vs non-empty expected → WA."""
    assert judge_output("", "42\n") is JudgeStatus.WA


def test_judge_output_both_empty() -> None:
    """Both empty → AC (the program produced nothing, as expected)."""
    assert judge_output("", "") is JudgeStatus.AC
