"""End-to-end tests for app.sandbox.judge.judge (P1-14).

Exercises the full compile → run → compare pipeline required by the
slice definition. Each test feeds real C++ code through ``judge`` and
asserts the overall verdict plus per-case details:

- AC: a correct solution to a simple problem.
- WA: an incorrect solution (wrong operator).
- TLE: an infinite loop, killed by the time limit.
- CE: a syntax error, short-circuited before any run.
- Mixed: a multi-case submission where one case ACs and another WAs.
- MLE: a program that allocates past the memory ceiling.

The ``ascii_tmp_path`` fixture from the root conftest is not needed
here because ``judge`` manages its own temp directory (under
``C:\\Windows\\Temp`` on Windows to avoid the non-ASCII-profile issue).
"""

from __future__ import annotations

from app.sandbox.judge import JudgeResult, JudgeStatus, TestCase, judge

# --- C++ fixtures ------------------------------------------------------

# Reads two ints from stdin, prints their sum. Correct for the test
# cases below.
_SUM_AC_CODE = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""

# Same shape but uses '*' instead of '+' — produces wrong answers.
_SUM_WA_CODE = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a * b << std::endl;
    return 0;
}
"""

# Infinite loop — must trip the time limit.
_TLE_CODE = """\
int main() {
    while (true) {}
    return 0;
}
"""

# Missing semicolon — guaranteed compile error.
_CE_CODE = """\
#include <iostream>
int main() {
    std::cout << "missing semicolon" << std::endl
    return 0;
}
"""

# Allocates and touches 256 MB to exceed a 64 MB ceiling.
_MLE_CODE = """\
#include <cstdlib>
int main() {
    const size_t size = 256 * 1024 * 1024;
    char* p = (char*)malloc(size);
    if (p) {
        for (size_t i = 0; i < size; i += 4096) {
            p[i] = 1;
        }
    }
    return 0;
}
"""


# --- Tests -------------------------------------------------------------

def test_judge_ac() -> None:
    """A correct solution against multiple cases → overall AC."""
    code = _SUM_AC_CODE
    cases = [
        TestCase(input="1 2\n", expected_output="3\n"),
        TestCase(input="10 20\n", expected_output="30\n"),
        TestCase(input="-5 5\n", expected_output="0\n"),
    ]

    result = judge(code, cases, time_limit=5.0, mem_limit_mb=256)

    assert isinstance(result, JudgeResult)
    assert result.status is JudgeStatus.AC
    assert result.passed_count == 3
    assert result.total_count == 3
    # Compile output should be empty (or near-empty) on success.
    assert result.compile_output == ""
    # Every per-case verdict must be AC.
    for tc_result in result.test_cases:
        assert tc_result.status is JudgeStatus.AC


def test_judge_wa() -> None:
    """A wrong-operator solution → overall WA, all cases WA."""
    code = _SUM_WA_CODE
    cases = [
        TestCase(input="1 2\n", expected_output="3\n"),  # actual: 2
        TestCase(input="3 4\n", expected_output="7\n"),  # actual: 12
    ]

    result = judge(code, cases, time_limit=5.0, mem_limit_mb=256)

    assert result.status is JudgeStatus.WA
    assert result.passed_count == 0
    assert result.total_count == 2
    for tc_result in result.test_cases:
        assert tc_result.status is JudgeStatus.WA
    # Sanity: the actual outputs should be the products, not the sums.
    assert "2" in result.test_cases[0].actual_output
    assert "12" in result.test_cases[1].actual_output


def test_judge_tle() -> None:
    """An infinite loop within a 1s time limit → overall TLE."""
    code = _TLE_CODE
    cases = [TestCase(input="", expected_output="anything\n")]

    result = judge(code, cases, time_limit=1.0, mem_limit_mb=256)

    assert result.status is JudgeStatus.TLE
    assert result.total_count == 1
    assert result.test_cases[0].status is JudgeStatus.TLE


def test_judge_ce() -> None:
    """A syntax error → overall CE, no cases run, diagnostics captured."""
    code = _CE_CODE
    cases = [
        TestCase(input="", expected_output=""),
        TestCase(input="1\n", expected_output="1\n"),
    ]

    result = judge(code, cases, time_limit=5.0, mem_limit_mb=256)

    assert result.status is JudgeStatus.CE
    # No cases should be run when compilation fails.
    assert result.total_count == 0
    assert result.test_cases == []
    # The compiler diagnostics must be surfaced.
    assert result.compile_output, "Expected non-empty compile_output on CE"
    assert "error" in result.compile_output.lower()


def test_judge_mixed_ac_and_wa() -> None:
    """One AC case + one WA case → overall WA, 1 passed / 2 total.

    This verifies the aggregation priority (WA > AC) and that the
    per-case breakdown correctly reports one of each.
    """
    code = _SUM_AC_CODE
    # First case matches; second case expects a wrong sum.
    cases = [
        TestCase(input="1 2\n", expected_output="3\n"),    # AC
        TestCase(input="3 4\n", expected_output="100\n"),  # WA (actual 7)
    ]

    result = judge(code, cases, time_limit=5.0, mem_limit_mb=256)

    assert result.status is JudgeStatus.WA
    assert result.passed_count == 1
    assert result.total_count == 2
    assert result.test_cases[0].status is JudgeStatus.AC
    assert result.test_cases[1].status is JudgeStatus.WA


def test_judge_mle() -> None:
    """A program that allocates past the ceiling → overall MLE."""
    code = _MLE_CODE
    cases = [TestCase(input="", expected_output="")]

    result = judge(code, cases, time_limit=10.0, mem_limit_mb=64)

    assert result.status is JudgeStatus.MLE
    assert result.total_count == 1
    assert result.test_cases[0].status is JudgeStatus.MLE


def test_judge_to_dict_round_trip() -> None:
    """to_dict() produces a JSON-safe structure with all expected keys."""
    code = _SUM_AC_CODE
    cases = [TestCase(input="1 2\n", expected_output="3\n")]
    result = judge(code, cases, time_limit=5.0, mem_limit_mb=256)
    d = result.to_dict()

    assert d["status"] == "AC"
    assert d["passed"] == 1
    assert d["total"] == 1
    assert isinstance(d["test_cases"], list)
    assert d["test_cases"][0]["status"] == "AC"
    assert d["test_cases"][0]["input"] == "1 2\n"
    assert d["test_cases"][0]["expected_output"] == "3\n"
    assert "actual_output" in d["test_cases"][0]
    assert "exit_code" in d["test_cases"][0]
