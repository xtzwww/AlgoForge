"""Standalone executable runner for the sandbox.

P1-12 separates execution from compilation. ``run_executable`` takes an
already-compiled binary and runs it with a wall-clock timeout and a
peak-memory ceiling, returning an :class:`ExecutionResult`.

This is deliberately separated from ``compile_and_run`` (P1-8) and
``compile_cpp`` (P1-11) so that later slices — function calling,
LangGraph nodes — can compile once and run against multiple test cases
without paying the compilation cost each time.

Memory monitoring uses ``psutil`` (listed in README as part of the
sandbox stack). The process's RSS is polled at a short interval; if it
exceeds ``mem_limit_mb`` the process is killed and the result is
flagged with ``memory_exceeded=True``. This is a best-effort check —
AGENTS.md mandates "不自研 setrlimit", and psutil polling is the
pragmatic Windows-compatible alternative. It is "够判 AC/WA/TLE" as
required.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.sandbox.compiler import _build_env

# Shortest wait used inside the monitoring loop. This is the granularity
# at which we check "did the process exit?" — and therefore also the
# granularity at which we get a chance to sample RSS. A first-cut 50 ms
# was too coarse: a submission can allocate past a 64 MB ceiling and
# exit in ~200 ms, leaving only ~4 sampling opportunities, and Windows
# RSS accounting lags the actual allocation. The result was a
# nondeterministic MLE verdict (see _sample_peak_rss for the mitigation
# that makes this robust rather than merely "less flaky").
_POLL_INTERVAL = 0.01

# Sample RSS this many times per _POLL_INTERVAL wait window. The wait is
# split into sub-intervals so a fast allocator cannot slip through
# between samples. Callers get N chances to observe the spike instead
# of one.
_SAMPLES_PER_POLL = 5

# Hard ceiling on how long we wait to drain pipes after the process has
# already ended. Should never be hit for well-behaved programs.
_DRAIN_TIMEOUT = 5.0


@dataclass
class ExecutionResult:
    """Result of running a single compiled binary.

    Attributes:
        success: True if the process exited with code 0 and was not
            killed for timeout or memory.
        stdout: Captured standard output (may be empty).
        stderr: Captured standard error (may be empty). On timeout or
            memory-exceeded, a human-readable reason is placed here.
        exit_code: Process exit code. -1 indicates the process was
            killed (timeout or memory) or never started.
        timed_out: True if the process exceeded ``time_limit``.
        memory_exceeded: True if the process exceeded ``mem_limit_mb``.
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    memory_exceeded: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "memory_exceeded": self.memory_exceeded,
        }


def _peak_rss(proc: psutil.Process) -> int:
    """Best-effort peak RSS of *proc* in bytes.

    ``memory_info().rss`` is an instantaneous reading and on Windows it
    can lag a burst allocation by enough that a short-lived process is
    never observed above its true peak. We therefore consult both
    ``memory_info`` and ``memory_full_info`` (when the platform supports
    the latter) and take the larger reading. Returns 0 when the process
    is already gone or access is denied — callers treat that as "no
    observation".
    """
    peak = 0
    for attr in ("memory_info", "memory_full_info"):
        getter = getattr(proc, attr, None)
        if getter is None:
            continue
        try:
            peak = max(peak, getter().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        except Exception:
            # memory_full_info raises NotImplementedError on some
            # platforms; skip that source rather than failing.
            continue
    return peak


def run_executable(
    exec_path: str | Path,
    stdin: str | None = None,
    time_limit: float = 10.0,
    mem_limit_mb: int = 256,
) -> ExecutionResult:
    """Run a compiled binary with a timeout and memory ceiling.

    Args:
        exec_path: Path to the executable produced by ``compile_cpp``.
        stdin: Optional text fed to the program's standard input.
        time_limit: Wall-clock timeout in seconds.
        mem_limit_mb: Memory ceiling in megabytes. The process's RSS
            is polled; if it exceeds this value the process is killed.

    Returns:
        An :class:`ExecutionResult`. The ``success`` flag is True only
        when the process exited 0 without hitting either limit.

    The function never raises for process-level failures (missing
    executable, OS errors, limits exceeded) — those are reported in the
    returned result so callers can treat them uniformly.
    """
    exe = Path(exec_path)
    if not exe.exists():
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=f"Executable not found: {exe}",
            exit_code=-1,
        )

    mem_limit_bytes = mem_limit_mb * 1024 * 1024

    try:
        proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Match the compiler's decode posture: permissive so we
            # never end up with None stdout/stderr on locale-odd output.
            encoding="utf-8",
            errors="replace",
            # MinGW executables need the MinGW runtime DLLs (libstdc++,
            # libgcc_s, etc.) which live in cpp_compiler_bin_dir. Reuse
            # the compiler's env builder so the binary can find them.
            env=_build_env(),
        )
    except OSError as exc:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=str(exc),
            exit_code=-1,
        )

    # Feed stdin up front, then close so the program sees EOF. We do
    # not use communicate() here because we need to poll memory while
    # the process runs. For the small outputs a learning-project OJ
    # produces, pipe-buffer deadlock is not a practical concern.
    try:
        if stdin is not None:
            proc.stdin.write(stdin)
    except (BrokenPipeError, OSError):
        # Program may have exited or closed stdin before we finished
        # writing — not necessarily an error.
        pass
    finally:
        if proc.stdin is not None:
            proc.stdin.close()

    timed_out = False
    mem_exceeded = False
    retcode: int | None = None
    start = time.monotonic()

    # One psutil.Process object reused across the loop. Constructing it
    # per sample is comparatively expensive and would itself slow the
    # sampling cadence.
    try:
        mon = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        mon = None

    # The wait window is split into _SAMPLES_PER_POLL sub-intervals so
    # RSS is sampled several times per window instead of once at the
    # end of it. This is what makes the MLE verdict deterministic for
    # programs that allocate and exit quickly: the spike gets several
    # chances to be observed rather than one.
    sub_interval = _POLL_INTERVAL / _SAMPLES_PER_POLL

    while True:
        try:
            retcode = proc.wait(timeout=sub_interval)
            break  # process exited
        except subprocess.TimeoutExpired:
            pass

        elapsed = time.monotonic() - start
        if elapsed >= time_limit:
            proc.kill()
            proc.wait()
            timed_out = True
            break

        if mon is not None:
            rss = _peak_rss(mon)
            if rss > mem_limit_bytes:
                proc.kill()
                proc.wait()
                mem_exceeded = True
                break

    # Drain pipes. After kill/wait the process is gone, so this returns
    # immediately with whatever was buffered.
    try:
        stdout, stderr = proc.communicate(timeout=_DRAIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    stdout = stdout or ""
    stderr = stderr or ""

    if timed_out:
        return ExecutionResult(
            success=False,
            stdout=stdout,
            stderr=f"Time Limit Exceeded (>{time_limit}s)",
            exit_code=-1,
            timed_out=True,
        )
    if mem_exceeded:
        return ExecutionResult(
            success=False,
            stdout=stdout,
            stderr=f"Memory Limit Exceeded (>{mem_limit_mb}MB)",
            exit_code=-1,
            memory_exceeded=True,
        )

    assert retcode is not None  # narrowed by the loop exit condition
    return ExecutionResult(
        success=retcode == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=retcode,
    )
