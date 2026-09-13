"""Judge logic — compile, run, and compare output against test cases.

Judging pipeline (P1-14 refactor):
1. Compile the submitted code **once** with ``compile_cpp`` (P1-11).
2. On compilation failure, return CE immediately without running any
   test cases.
3. For each test case, run the binary with ``run_executable`` (P1-12),
   which enforces a wall-clock timeout and a memory ceiling.
4. Classify each run: TLE / MLE / RE / AC / WA.
5. Aggregate into an overall verdict with priority
   CE > TLE > MLE > RE > WA > AC.

P1-13's ``judge_output`` is reused for the AC/WA comparison.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from app.sandbox.compiler import CompileResult, compile_cpp
from app.sandbox.runner import ExecutionResult, run_executable

# Type of the optional progress callback. The callback receives a dict
# describing the event (see ``judge`` below for the event schema).
# P1-15's WebSocket route feeds this into an asyncio.Queue so the
# client can watch judging progress in real time.
JudgeEventCallback = Callable[[dict], None]


def _emit(on_event: Optional[JudgeEventCallback], msg: dict) -> None:
    """Invoke the callback if one was provided; no-op otherwise."""
    if on_event is not None:
        on_event(msg)


class JudgeStatus(str, Enum):
    """Possible judge verdicts."""

    AC = "AC"  # Accepted
    WA = "WA"  # Wrong Answer
    TLE = "TLE"  # Time Limit Exceeded
    MLE = "MLE"  # Memory Limit Exceeded
    CE = "CE"  # Compilation Error
    RE = "RE"  # Runtime Error (non-zero exit code)


@dataclass
class TestCase:
    """A single test case with stdin input and expected stdout output."""

    input: str
    expected_output: str


@dataclass
class TestCaseResult:
    """The judge verdict for a single test case."""

    status: JudgeStatus
    input: str
    expected_output: str
    actual_output: str
    stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "input": self.input,
            "expected_output": self.expected_output,
            "actual_output": self.actual_output,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }


@dataclass
class JudgeResult:
    """Overall judge result across all test cases."""

    status: JudgeStatus
    compile_output: str = ""
    test_cases: list[TestCaseResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for tc in self.test_cases if tc.status == JudgeStatus.AC)

    @property
    def total_count(self) -> int:
        return len(self.test_cases)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "compile_output": self.compile_output,
            "passed": self.passed_count,
            "total": self.total_count,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
        }


def normalize_output(text: str) -> str:
    """Normalize output for comparison.

    - Strip trailing whitespace from each line.
    - Remove trailing blank lines.
    - Collapse remaining line endings to ``\\n``.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    # Remove trailing empty lines
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _outputs_match(actual: str, expected: str) -> bool:
    """Compare normalized actual output against expected output."""
    return normalize_output(actual) == normalize_output(expected)


def judge_output(actual: str, expected: str) -> JudgeStatus:
    """Compare two outputs and return AC or WA.

    This is the pure comparison entry point required by P1-13. It does
    not compile or run anything — callers feed it the actual stdout
    captured from the binary and the expected stdout from the test case.

    The comparison is forgiving on presentation: trailing whitespace
    per line and trailing blank lines are ignored (see
    :func:`normalize_output`), and ``\\r\\n`` is collapsed to ``\\n``.
    This matches typical OJ conventions where ``"42\\n"`` and
    ``"42 \\r\\n\\n"`` should both be accepted.

    Args:
        actual: The program's stdout.
        expected: The test case's expected stdout.

    Returns:
        :attr:`JudgeStatus.AC` if the normalized outputs are equal,
        :attr:`JudgeStatus.WA` otherwise.
    """
    return JudgeStatus.AC if _outputs_match(actual, expected) else JudgeStatus.WA


# Priority used when aggregating per-case verdicts into an overall
# verdict. Lower index = higher priority (a single TLE case turns the
# overall verdict into TLE even if other cases AC'd).
_STATUS_PRIORITY: list[JudgeStatus] = [
    JudgeStatus.CE,
    JudgeStatus.TLE,
    JudgeStatus.MLE,
    JudgeStatus.RE,
    JudgeStatus.WA,
    JudgeStatus.AC,
]


def _classify_run(
    run: ExecutionResult, expected_output: str
) -> JudgeStatus:
    """Map an :class:`ExecutionResult` to a per-case :class:`JudgeStatus`.

    Order matters: TLE and MLE are detected via flags on the result
    (set by ``run_executable``); RE is a non-zero exit code without
    those flags; otherwise we fall through to output comparison
    (AC / WA).
    """
    if run.timed_out:
        return JudgeStatus.TLE
    if run.memory_exceeded:
        return JudgeStatus.MLE
    if run.exit_code != 0:
        return JudgeStatus.RE
    return judge_output(run.stdout, expected_output)


def _aggregate_status(per_case: list[JudgeStatus]) -> JudgeStatus:
    """Pick the highest-priority status from per-case verdicts.

    Defaults to AC when there are no cases (e.g. compilation failed and
    we return early with an empty case list).
    """
    if not per_case:
        return JudgeStatus.AC
    return min(per_case, key=_STATUS_PRIORITY.index)


def _judge_tmpdir() -> tempfile.TemporaryDirectory:
    """Create the scratch directory used by one judging run.

    Two things here are deliberate:

    **The location.** On Windows this is ``C:\\Windows\\Temp`` rather than
    the OS default. The default is derived from the user profile, which on
    this machine contains CJK characters ("雪团子"), and MinGW's linker
    cannot open output files on non-ASCII paths — every compile would fail
    with an opaque linker error.

    **``ignore_cleanup_errors=True``.** Not a convenience: it is a fix.
    The judged binary lives in this directory and is killed on TLE/MLE.
    On Windows a killed process can still hold a handle to its own
    executable for a short while, and ``shutil.rmtree`` then fails with
    ``PermissionError`` while unwinding the context manager — so the
    exception escaped ``judge()`` itself. That surfaced as an intermittent
    failure of the MLE test that only appeared under full-suite load, and
    in production it would turn an ordinary submission into a 500 because
    the judge could not delete its own scratch files. A stray directory in
    the temp folder is the much lesser problem.
    """
    if os.name == "nt":
        base = Path(r"C:\Windows\Temp") / "algoforge_judge"
        base.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(
            prefix="judge_",
            dir=str(base),
            ignore_cleanup_errors=True,
        )
    return tempfile.TemporaryDirectory(
        prefix="algoforge_judge_",
        ignore_cleanup_errors=True,
    )


def judge(
    code: str,
    test_cases: list[TestCase],
    *,
    time_limit: float = 10.0,
    mem_limit_mb: int = 256,
    on_event: Optional[JudgeEventCallback] = None,
) -> JudgeResult:
    """Compile *code* once and judge it against *test_cases*.

    The binary is compiled a single time (P1-11 ``compile_cpp``) and
    reused for every test case via P1-12 ``run_executable``, which is
    where the wall-clock timeout and memory ceilings are enforced.

    Args:
        code: C++ source code to judge.
        test_cases: List of :class:`TestCase` with stdin + expected stdout.
        time_limit: Per-case wall-clock timeout in seconds.
        mem_limit_mb: Per-case memory ceiling in megabytes.
        on_event: Optional progress callback. When provided, the
            following events are emitted (in order):

            * ``{"type": "compile_start"}``
            * ``{"type": "compile_done", "success": bool, "output": str}``
            * (on CE) ``{"type": "judge_done", "status": "CE",
                "compile_output": str, "passed": 0, "total": 0}``
            * For each case ``i`` (0-based):
              - ``{"type": "case_start", "index": int, "input": str}``
              - ``{"type": "case_done", "index": int, "status": str,
                "actual_output": str, "exit_code": int, "timed_out": bool,
                "memory_exceeded": bool}``
            * ``{"type": "judge_done", "status": str, "passed": int,
                "total": int, "compile_output": str}``

            The callback is invoked synchronously from the judging
            thread; callers are responsible for any thread-safe
            hand-off (e.g. P1-15's WS route uses
            ``asyncio.run_coroutine_threadsafe`` to bridge into the
            event loop).

    Returns:
        :class:`JudgeResult` with the overall verdict and per-case
        details. Compilation failure short-circuits to a CE result with
        an empty ``test_cases`` list and the compiler diagnostics in
        ``compile_output``.
    """
    # The source and binary live in a temp dir that we own for the
    # duration of judging. The binary is reused across all cases.
    # See _judge_tmpdir() for why it is located where it is and why
    # cleanup failures are tolerated.
    with _judge_tmpdir() as tmpdir:
        tmp = Path(tmpdir)
        src_path = tmp / "main.cpp"
        src_path.write_text(code, encoding="utf-8")

        _emit(on_event, {"type": "compile_start"})
        compile_result: CompileResult = compile_cpp(src_path)
        _emit(
            on_event,
            {
                "type": "compile_done",
                "success": compile_result.success,
                "output": compile_result.output,
            },
        )
        if not compile_result.success:
            ce_result = JudgeResult(
                status=JudgeStatus.CE,
                compile_output=compile_result.output,
                test_cases=[],
            )
            _emit(
                on_event,
                {
                    "type": "judge_done",
                    "status": ce_result.status.value,
                    "compile_output": ce_result.compile_output,
                    "passed": ce_result.passed_count,
                    "total": ce_result.total_count,
                },
            )
            return ce_result

        assert compile_result.executable_path is not None  # narrowed by success
        exe_path = compile_result.executable_path

        per_case_statuses: list[JudgeStatus] = []
        case_results: list[TestCaseResult] = []

        for i, tc in enumerate(test_cases):
            _emit(
                on_event,
                {"type": "case_start", "index": i, "input": tc.input},
            )
            run: ExecutionResult = run_executable(
                exe_path,
                stdin=tc.input,
                time_limit=time_limit,
                mem_limit_mb=mem_limit_mb,
            )
            status = _classify_run(run, tc.expected_output)
            per_case_statuses.append(status)
            case_results.append(
                TestCaseResult(
                    status=status,
                    input=tc.input,
                    expected_output=tc.expected_output,
                    actual_output=run.stdout,
                    stderr=run.stderr,
                    exit_code=run.exit_code,
                )
            )
            _emit(
                on_event,
                {
                    "type": "case_done",
                    "index": i,
                    "status": status.value,
                    "actual_output": run.stdout,
                    "exit_code": run.exit_code,
                    "timed_out": run.timed_out,
                    "memory_exceeded": run.memory_exceeded,
                },
            )

        final_result = JudgeResult(
            status=_aggregate_status(per_case_statuses),
            compile_output=compile_result.output,
            test_cases=case_results,
        )
        _emit(
            on_event,
            {
                "type": "judge_done",
                "status": final_result.status.value,
                "compile_output": final_result.compile_output,
                "passed": final_result.passed_count,
                "total": final_result.total_count,
            },
        )
        return final_result
