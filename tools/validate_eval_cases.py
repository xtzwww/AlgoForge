"""Validate the S2 evaluation set against the real judge.

For every case in corpus/eval_cases/ this script:

1. Judges ``correct_code`` against the case's test cases.
   It MUST be AC — otherwise the test cases themselves are wrong, and any
   accuracy measured against them would be meaningless.
2. Judges ``wrong_code`` and checks the observed verdict matches
   ``observable_verdict`` (WA / TLE / ...). It must NOT be AC — otherwise
   the submission isn't actually buggy and the "expected root cause"
   label has no observable basis.

This is the gate that makes the P2-5 accuracy number trustworthy. Run it
after editing any case:

    python tools/validate_eval_cases.py

Exit code is non-zero if any case fails validation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))

from app.eval_cases import EvalCase, load_eval_cases  # noqa: E402
from app.sandbox.judge import JudgeStatus, TestCase as JudgeTestCase, judge  # noqa: E402


def _to_judge_cases(case: EvalCase) -> list[JudgeTestCase]:
    return [
        JudgeTestCase(input=tc.input, expected_output=tc.expected_output)
        for tc in case.test_cases
    ]


def validate(case: EvalCase) -> list[str]:
    """Return a list of problems; empty means the case is sound."""
    problems: list[str] = []
    jcases = _to_judge_cases(case)

    if not case.correct_code.strip():
        problems.append("missing correct_code (cannot verify test cases)")
    else:
        ref = judge(case.correct_code, jcases, time_limit=10.0, mem_limit_mb=256)
        if ref.status is not JudgeStatus.AC:
            detail = "; ".join(
                f"case {i}: got {tc.status.value} "
                f"(expected {tc.expected_output!r}, actual {tc.actual_output!r})"
                for i, tc in enumerate(ref.test_cases)
                if tc.status is not JudgeStatus.AC
            )
            problems.append(
                f"correct_code is {ref.status.value}, not AC — test cases are "
                f"invalid. {detail or ref.compile_output[:200]}"
            )

    wrong = judge(case.wrong_code, jcases, time_limit=10.0, mem_limit_mb=256)
    if wrong.status is JudgeStatus.AC:
        problems.append(
            "wrong_code is AC — the submission is not actually buggy, so the "
            "'expected_root_cause' label is unsupported"
        )
    elif wrong.status.value != case.observable_verdict:
        problems.append(
            f"wrong_code produced {wrong.status.value}, expected "
            f"{case.observable_verdict}"
        )

    return problems


def main() -> int:
    cases = load_eval_cases()
    print(f"validating {len(cases)} cases\n")

    failures = 0
    for case in cases:
        problems = validate(case)
        label = f"{case.case_id} [{case.expected_root_cause}]"
        if problems:
            failures += 1
            print(f"FAIL  {label}")
            for p in problems:
                print(f"        - {p}")
        else:
            print(f"ok    {label}")

    print()
    if failures:
        print(f"{failures}/{len(cases)} case(s) FAILED validation")
        return 1
    print(f"all {len(cases)} cases validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
