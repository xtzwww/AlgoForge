"""Tests for app.sandbox.runner.run_executable (P1-12).

Verifies the standalone execution function against three cases
required by the slice definition:
- Normal run: produces correct stdout, exits 0.
- Timeout: an infinite loop is killed and flagged ``timed_out``.
- Memory limit exceeded: a program that allocates and touches a large
  buffer is killed and flagged ``memory_exceeded``.

The ``ascii_tmp_path`` fixture is provided by the root conftest.py.
Each test compiles its own C++ fixture via ``compile_cpp`` (P1-11) and
then runs the resulting binary via ``run_executable`` (P1-12).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.sandbox.compiler import compile_cpp
from app.sandbox.runner import ExecutionResult, run_executable

# --- C++ fixtures -------------------------------------------------------

# Echoes a line of input — used to verify stdin is wired up correctly.
_ECHO_CODE = """\
#include <iostream>
#include <string>
int main() {
    std::string s;
    std::getline(std::cin, s);
    std::cout << "echo:" << s << std::endl;
    return 0;
}
"""

# Infinite loop — must be killed by the time limit.
_INFINITE_LOOP_CODE = """\
int main() {
    while (true) {
    }
    return 0;
}
"""

# Allocates and touches a large buffer to force RSS past a small
# memory ceiling. Touches every page so the OS actually commits the
# memory (lazy allocation would otherwise keep RSS low).
_MEMORY_HOG_CODE = """\
#include <cstdlib>
int main() {
    const size_t size = 256 * 1024 * 1024;  // 256 MB
    char* p = (char*)malloc(size);
    if (p) {
        for (size_t i = 0; i < size; i += 4096) {
            p[i] = 1;
        }
    }
    return 0;
}
"""


@pytest.fixture
def echo_binary(ascii_tmp_path: Path) -> Path:
    src = ascii_tmp_path / "echo.cpp"
    src.write_text(_ECHO_CODE, encoding="utf-8")
    result = compile_cpp(src)
    assert result.success, f"Failed to compile echo fixture: {result.output}"
    assert result.executable_path is not None
    return Path(result.executable_path)


@pytest.fixture
def infinite_loop_binary(ascii_tmp_path: Path) -> Path:
    src = ascii_tmp_path / "loop.cpp"
    src.write_text(_INFINITE_LOOP_CODE, encoding="utf-8")
    result = compile_cpp(src)
    assert result.success, f"Failed to compile loop fixture: {result.output}"
    assert result.executable_path is not None
    return Path(result.executable_path)


@pytest.fixture
def memory_hog_binary(ascii_tmp_path: Path) -> Path:
    src = ascii_tmp_path / "memhog.cpp"
    src.write_text(_MEMORY_HOG_CODE, encoding="utf-8")
    result = compile_cpp(src)
    assert result.success, f"Failed to compile memhog fixture: {result.output}"
    assert result.executable_path is not None
    return Path(result.executable_path)


# Prints far more than an OS pipe buffer holds (~4 KiB). Regression
# fixture for the deadlock described on
# ``test_large_output_is_not_a_timeout``.
_VERBOSE_OUTPUT_BYTES = 20000
_VERBOSE_CODE = """\
#include <iostream>
int main() {
    for (int i = 0; i < 2000; ++i) {
        std::cout << "abcdefghij";
    }
    return 0;
}
"""


@pytest.fixture
def verbose_binary(ascii_tmp_path: Path) -> Path:
    src = ascii_tmp_path / "verbose.cpp"
    src.write_text(_VERBOSE_CODE, encoding="utf-8")
    result = compile_cpp(src)
    assert result.success, f"Failed to compile verbose fixture: {result.output}"
    assert result.executable_path is not None
    return Path(result.executable_path)


# --- Tests -------------------------------------------------------------

def test_normal_run(echo_binary: Path) -> None:
    """A well-behaved program exits 0 and produces the expected stdout."""
    result = run_executable(
        echo_binary,
        stdin="hello algoforge",
        time_limit=5.0,
        mem_limit_mb=256,
    )

    assert isinstance(result, ExecutionResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.memory_exceeded is False
    assert "echo:hello algoforge" in result.stdout


def test_normal_run_to_dict(echo_binary: Path) -> None:
    """to_dict() round-trips a successful result into a JSON-safe dict."""
    result = run_executable(echo_binary, stdin="x", time_limit=5.0, mem_limit_mb=256)
    d = result.to_dict()

    assert d["success"] is True
    assert d["exit_code"] == 0
    assert d["timed_out"] is False
    assert d["memory_exceeded"] is False
    assert "stdout" in d


def test_timeout(infinite_loop_binary: Path) -> None:
    """An infinite loop is killed within the time limit and flagged."""
    result = run_executable(
        infinite_loop_binary,
        stdin=None,
        time_limit=1.0,  # 1 second — short to keep the test fast
        mem_limit_mb=256,
    )

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.timed_out is True
    assert result.memory_exceeded is False
    assert result.exit_code == -1
    assert "Time Limit Exceeded" in result.stderr


def test_large_output_is_not_a_timeout(verbose_binary: Path) -> None:
    """A program that prints more than a pipe buffer must not be killed.

    Regression test for a deadlock found 2026-09-16. ``stdout`` is an OS
    pipe, and a pipe holds only about 4 KiB before it is full (4096 bytes
    on Windows). The poll loop used to wait for the child to exit without
    reading that pipe, so a program printing more than the buffer holds
    blocked inside ``write()`` and never exited — the loop then ran to
    ``time_limit`` and killed it.

    The consequence was not a cosmetic one: the program was reported as
    ``timed_out`` with its output truncated at the buffer boundary, and
    ``judge`` therefore returned **TLE for code that was correct**. This
    test pins both halves — the flag and the full output — because the
    flag alone would still pass if the output were silently clipped.

    The generous ``time_limit`` is deliberate. A short one would make the
    test pass for the wrong reason: the assertion is that the program is
    *not* killed, so the limit must be long enough that reaching it could
    only mean the deadlock came back.
    """
    result = run_executable(
        verbose_binary,
        stdin=None,
        time_limit=10.0,
        mem_limit_mb=256,
    )

    assert result.timed_out is False
    assert result.memory_exceeded is False
    assert result.success is True
    assert result.exit_code == 0
    assert len(result.stdout) == _VERBOSE_OUTPUT_BYTES
    assert result.stdout == "abcdefghij" * 2000


def test_memory_exceeded(memory_hog_binary: Path) -> None:
    """A program that allocates past the ceiling is killed and flagged."""
    result = run_executable(
        memory_hog_binary,
        stdin=None,
        time_limit=10.0,
        mem_limit_mb=64,  # 64 MB ceiling; the program tries to touch 256 MB
    )

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.memory_exceeded is True
    assert result.timed_out is False
    assert result.exit_code == -1
    assert "Memory Limit Exceeded" in result.stderr


def test_nonexistent_executable(ascii_tmp_path: Path) -> None:
    """Running a path that does not exist returns a clean failure."""
    nonexistent = ascii_tmp_path / "does_not_exist.exe"
    result = run_executable(nonexistent, time_limit=5.0, mem_limit_mb=256)

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.exit_code == -1
    assert "not found" in result.stderr.lower()


# --- Measurement fields (PU-13) ----------------------------------------
#
# PU-13 records exec_time and memory_used on every submission, so the
# runner has to report them. These tests pin the properties that make
# the numbers usable: a real duration, a peak rather than a last
# reading, and None — not zero — when nothing could be measured.


def test_normal_run_reports_a_plausible_duration(echo_binary: Path) -> None:
    """``exec_time`` is a small positive number of seconds."""
    result = run_executable(echo_binary, stdin="hi\n", time_limit=10.0)

    assert result.exec_time > 0.0
    # A trivial echo cannot plausibly take ten seconds; a bound this
    # loose still catches a unit error (e.g. milliseconds reported as
    # seconds) without being flaky on a loaded machine.
    assert result.exec_time < 10.0


def test_timeout_reports_the_limit_not_the_kill_overhead(
    infinite_loop_binary: Path,
) -> None:
    """A timed-out run's duration is about the limit, not unbounded.

    Reported as the *observed* wall-clock time rather than the requested
    limit, so it can exceed it slightly — the kill and drain are inside
    the measurement. It must not be wildly larger, though, because then
    it stops describing why the run was killed.
    """
    result = run_executable(infinite_loop_binary, time_limit=1.0)

    assert result.timed_out is True
    assert 1.0 <= result.exec_time < 3.0


def test_normal_run_reports_peak_memory(echo_binary: Path) -> None:
    """``memory_used`` is a positive number of megabytes.

    Not asserted against an exact figure — the value depends on the C++
    runtime and the OS — only that a sample was taken and is in a
    believable range for a process.
    """
    result = run_executable(echo_binary, stdin="hi\n", time_limit=10.0)

    assert result.memory_used is not None
    assert result.memory_used >= 1
    assert result.memory_used < 1024


def test_memory_hog_reports_a_reading_above_the_ceiling(
    memory_hog_binary: Path,
) -> None:
    """An MLE run reports the reading that tripped the limit.

    The peak is kept rather than the last sample before the kill, so the
    number explains the verdict instead of contradicting it.
    """
    result = run_executable(memory_hog_binary, time_limit=10.0, mem_limit_mb=64)

    assert result.memory_exceeded is True
    assert result.memory_used is not None
    # The programme touches 256 MB, so whatever we sampled must be well
    # past the 64 MB ceiling that killed it.
    assert result.memory_used > 64


def test_nonexistent_executable_reports_no_measurements(
    ascii_tmp_path: Path,
) -> None:
    """A program that never started has zero time and no memory reading.

    ``memory_used`` is None rather than 0 here: nothing was measured, and
    a zero would read as "this used no memory", which is a different and
    false claim.
    """
    result = run_executable(
        ascii_tmp_path / "does_not_exist.exe", time_limit=5.0, mem_limit_mb=256
    )

    assert result.exec_time == 0.0
    assert result.memory_used is None


def test_to_dict_includes_the_measurement_fields(echo_binary: Path) -> None:
    """Both fields reach the wire — PU-13 reads them off ``to_dict``."""
    d = run_executable(echo_binary, stdin="hi\n", time_limit=10.0).to_dict()

    assert "exec_time" in d
    assert "memory_used" in d
    assert isinstance(d["exec_time"], float)
    assert isinstance(d["memory_used"], int)
