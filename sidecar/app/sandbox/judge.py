"""Judge logic — build, run, and compare output against test cases.

Judging pipeline (P1-14 refactor, generalised to any language by PU-16):
1. Build the submitted code **once** with ``compile_source`` (P1-11,
   formerly ``compile_cpp``).
2. On build failure, return CE immediately without running any test cases.
3. For each test case, run the program with ``run_executable`` (P1-12),
   which enforces a wall-clock timeout and a memory ceiling.
4. Classify each run: TLE / MLE / RE / AC / WA.
5. Aggregate into an overall verdict with priority
   CE > TLE > MLE > RE > WA > AC.

P1-13's ``judge_output`` is reused for the AC/WA comparison.

PU-16 replaced the hardcoded ``main.cpp`` / ``[exe_path]`` pair with the
language descriptor: the source filename and the run command both come
from ``LanguageSpec``, which is what lets Java (whose run step is
``java -cp <dir> Main``) go through the same pipeline as C++.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from app.sandbox.compiler import CompileResult, compile_source, sandbox_tmpdir
from app.sandbox.languages import get_language
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
    """The judge verdict for a single test case.

    ``exec_time`` / ``memory_used`` come straight from the runner's
    measurement of that case and are ``None`` when nothing was sampled.
    PU-13 aggregates them onto the submission row; the judge itself does
    not care about them.
    """

    status: JudgeStatus
    input: str
    expected_output: str
    actual_output: str
    stderr: str = ""
    exit_code: int = 0
    exec_time: Optional[float] = None
    memory_used: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "input": self.input,
            "expected_output": self.expected_output,
            "actual_output": self.actual_output,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "exec_time": self.exec_time,
            "memory_used": self.memory_used,
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


def _judge_tmpdir():
    """Alias for :func:`app.sandbox.compiler.sandbox_tmpdir`.

    PU-16 moved the implementation next to ``compile_and_run`` so both
    share one directory policy — that function had been creating its
    scratch space in the OS default location, which is the non-ASCII path
    that breaks MinGW, so every C++ compile it attempted failed. Two
    copies of this policy would drift; there is now one.

    The name survives because ``tests/test_judge_integration.py``
    exercises this seam directly: the directory's location and its cleanup
    behaviour are both load-bearing for correctness, and neither is
    observable from a verdict alone until something goes wrong in
    production.

    See :func:`app.sandbox.compiler.sandbox_tmpdir` for why the directory
    lives under ``C:\\Windows\\Temp`` and why
    ``ignore_cleanup_errors=True`` is a fix rather than a convenience.
    """
    return sandbox_tmpdir()


def judge(
    code: str,
    test_cases: list[TestCase],
    *,
    time_limit: float = 10.0,
    mem_limit_mb: int = 256,
    language: Optional[str] = None,
    on_event: Optional[JudgeEventCallback] = None,
) -> JudgeResult:
    """Build *code* once and judge it against *test_cases*.

    The program is built a single time (P1-11, now ``compile_source``) and
    reused for every test case via P1-12 ``run_executable``, which is
    where the wall-clock timeout and memory ceilings are enforced.

    Args:
        code: Source code to judge, written in *language*.
        test_cases: List of :class:`TestCase` with stdin + expected stdout.
        time_limit: Per-case wall-clock timeout in seconds.
        mem_limit_mb: Per-case memory ceiling in megabytes. The language's
            own allowance is added on top (see ``run_executable``), so a
            JVM's fixed overhead does not read as an exceeded limit.
        language: Language id such as ``"cpp"``. ``None`` or blank selects
            the default (C++), which is what P1-14's callers relied on.
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

    Raises:
        UnknownLanguageError: *language* is not registered.

    Returns:
        :class:`JudgeResult` with the overall verdict and per-case
        details. Compilation failure short-circuits to a CE result with
        an empty ``test_cases`` list and the compiler diagnostics in
        ``compile_output``.
    """
    spec = get_language(language)

    # The source and the build products live in a temp dir that we own for
    # the duration of judging, so the program is built once and reused
    # across all cases. See _judge_tmpdir() for why it is located where it
    # is and why cleanup failures are tolerated.
    with _judge_tmpdir() as tmpdir:
        tmp = Path(tmpdir)
        # The filename is the descriptor's, not a literal: javac refuses a
        # file that is not named after its public class, so `main.java`
        # could not be compiled no matter what flags were passed.
        src_path = spec.source_path(tmp)
        src_path.write_text(code, encoding="utf-8")

        _emit(on_event, {"type": "compile_start"})
        compile_result: CompileResult = compile_source(src_path, spec.id)
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

        # The run command comes from the descriptor and is derived from the
        # work directory rather than from the build artefact: Java's is
        # `java -cp <dir> Main`, which has no single file to point at, and
        # Python's is the interpreter plus the source.
        run_argv = spec.run_cmd(tmp)

        per_case_statuses: list[JudgeStatus] = []
        case_results: list[TestCaseResult] = []

        for i, tc in enumerate(test_cases):
            _emit(
                on_event,
                {"type": "case_start", "index": i, "input": tc.input},
            )
            run: ExecutionResult = run_executable(
                run_argv,
                stdin=tc.input,
                time_limit=time_limit,
                mem_limit_mb=mem_limit_mb,
                language=spec.id,
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
                    # PU-13: carried through so the submission row can
                    # report what the run actually cost. See the field
                    # docstring for why None is distinct from 0.
                    exec_time=run.exec_time,
                    memory_used=run.memory_used,
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
                    "exec_time": run.exec_time,
                    "memory_used": run.memory_used,
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
