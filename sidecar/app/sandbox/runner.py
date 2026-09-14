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
exceeds ``mem_limit_mb`` — plus the language's ``rss_allowance_mb``, see
``LanguageSpec`` — the process is killed and the result is flagged with
``memory_exceeded=True``. This is a best-effort check — AGENTS.md
mandates "不自研 setrlimit", and psutil polling is the pragmatic
Windows-compatible alternative. It is "够判 AC/WA/TLE" as required. A
language whose RSS does not track its own allocation (Java) is bounded by
its own toolchain flag instead, with the allowance keeping the OS-level
check from firing first.

PU-16 generalised the first argument from "a binary path" to "a command":
Java runs as ``java -Xmx… -cp <dir> Main`` and Python as
``<interpreter> main.py``, neither of which is a single file. The language
is now an explicit argument too, because it decides which toolchain
directory joins ``PATH`` and how much resident memory to tolerate before
the ceiling fires.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import psutil

from app.sandbox.compiler import _build_env
from app.sandbox.languages import get_language

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
        memory_exceeded: True if the process exceeded the effective RSS
            ceiling — ``mem_limit_mb`` plus the language's
            ``rss_allowance_mb``.
        exec_time: Wall-clock seconds the process ran, as measured
            around the poll loop. This is the *observed* duration, which
            for a timeout is the limit (the process was killed when it
            was reached) rather than however long the kill took.
        memory_used: Peak RSS observed in megabytes, rounded up, or
            ``None`` when no sample was taken. Sampling can fail for a
            process that exits before the first poll or that the OS will
            not let us inspect, and a missing measurement is reported as
            missing rather than as zero — "0 MB" would read as a real
            reading and is worse than no answer.

            Note this is the raw observation, deliberately *not* the
            effective ceiling: a JVM's ``rss_allowance_mb`` is a verdict
            threshold, not part of what the submission allocated, so
            folding it in would misreport every Java run.
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    memory_exceeded: bool = False
    exec_time: float = 0.0
    memory_used: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "memory_exceeded": self.memory_exceeded,
            "exec_time": self.exec_time,
            "memory_used": self.memory_used,
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


def _as_argv(command: str | Path | Sequence[str]) -> list[str]:
    """Normalize the command argument of :func:`run_executable` to a list.

    A single path (or bare command name) is wrapped; a sequence is taken
    as the complete argv. Doing this once here means the rest of the
    function only ever deals with one shape.
    """
    if isinstance(command, (str, Path)):
        return [str(command)]
    return [str(part) for part in command]


def _is_launchable(program: Path) -> bool:
    """Whether *program* looks like something the OS could launch.

    A path (absolute or relative) is checked on disk. A bare command name
    has no file to stat and is resolved through ``PATH`` by the OS, so it
    is looked up with ``shutil.which`` — otherwise a configured
    ``JAVA_RUNTIME_PATH=java`` would be reported as missing purely because
    ``Path("java").exists()`` is False.
    """
    text = str(program)
    if os.sep in text or "/" in text:
        return program.exists()
    return shutil.which(text) is not None


def run_executable(
    command: str | Path | Sequence[str],
    stdin: str | None = None,
    time_limit: float = 10.0,
    mem_limit_mb: int = 256,
    *,
    language: str | None = None,
) -> ExecutionResult:
    """Run a built program with a timeout and a memory ceiling.

    Args:
        command: Either the path of the program to run, or the complete
            argv that launches it. The sequence form exists for languages
            whose run command is not a single file — Java needs
            ``java -Xmx… -cp <dir> Main``, Python needs
            ``<interpreter> main.py``. Build it with the descriptor's
            ``run_cmd`` rather than by hand.
        stdin: Optional text fed to the program's standard input.
        time_limit: Wall-clock timeout in seconds.
        mem_limit_mb: Memory ceiling in megabytes. The process's RSS is
            polled; if it exceeds this value plus the language's allowance
            (see below) the process is killed.
        language: Language id. Decides which toolchain directory joins
            ``PATH`` and how much resident memory is tolerated. ``None``
            selects the default (C++), matching the pre-PU-16 behaviour of
            this function, and prepends every registered toolchain's bin
            directory.

    Returns:
        An :class:`ExecutionResult`. The ``success`` flag is True only
        when the process exited 0 without hitting either limit.

    Raises:
        UnknownLanguageError: *language* is not registered.

    The function never raises for process-level failures (missing
    executable, OS errors, limits exceeded) — those are reported in the
    returned result so callers can treat them uniformly.

    Note:
        The effective RSS ceiling is ``mem_limit_mb`` **plus** the
        language's ``rss_allowance_mb``. For a JVM, resident memory is
        dominated by metaspace, the code cache and GC structures rather
        than by the submission's own allocation, so measuring it against a
        C++-sized ceiling yields false MLE verdicts on correct code; Java's
        ``-Xmx`` is the real bound there, and the allowance only keeps the
        OS-level check from winning the race. See
        ``LanguageSpec.rss_allowance_mb``.
    """
    argv = _as_argv(command)
    program = Path(argv[0])
    if not _is_launchable(program):
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=f"Executable not found: {program}",
            exit_code=-1,
        )

    spec = get_language(language)
    # The allowance is added rather than replacing the limit so the
    # request's mem_limit_mb keeps meaning what it says: for languages
    # with no allowance (C++, Python) nothing changes at all.
    effective_mb = mem_limit_mb + spec.rss_allowance_mb()
    mem_limit_bytes = effective_mb * 1024 * 1024

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Match the compiler's decode posture: permissive so we
            # never end up with None stdout/stderr on locale-odd output.
            encoding="utf-8",
            errors="replace",
            # MinGW executables need the MinGW runtime DLLs (libstdc++,
            # libgcc_s, etc.) which live in cpp_compiler_bin_dir; a JDK
            # outside PATH needs its own bin directory. Reuse the
            # compiler's env builder so the language descriptor decides.
            env=_build_env(spec),
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
    # Highest RSS seen across the sampling loop, in bytes. 0 means "no
    # sample was taken" — either the process finished before the first
    # poll or psutil refused to inspect it. That distinction is preserved
    # all the way out as ``memory_used=None`` rather than being flattened
    # to a zero reading.
    peak_rss_bytes = 0

    # One psutil.Process object reused across the loop. Constructing it
    # per sample is comparatively expensive and would itself slow the
    # sampling cadence.
    try:
        mon = psutil.Process(proc.pid)
    except psutil.Error:
        # The process may already be gone (NoSuchProcess) or it may exist
        # in a state we are not allowed to inspect — on Windows the handle
        # can be refused while the process is being torn down, which
        # surfaces as AccessDenied (a PermissionError subclass). Either
        # way the sole consequence is that this run gets no memory
        # sampling; the verdict is produced from the exit status instead.
        #
        # Catching psutil.Error rather than just NoSuchProcess matters:
        # run_executable documents that it never raises for process-level
        # failures, and an escaping AccessDenied would turn an ordinary
        # submission into a 500. It also caused an intermittent test
        # failure whose cause looked unrelated to memory limits.
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
            # Keep the high-water mark for the reported memory_used. The
            # MLE branch below breaks out of the loop on the same value,
            # so a killed run still reports the reading that killed it
            # rather than the last one before the kill.
            peak_rss_bytes = max(peak_rss_bytes, rss)
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

    # Measured here, after the loop, so it covers the whole run including
    # the drain. For a killed run this is the limit rather than the kill
    # overhead, which is what a user comparing against the limit expects.
    elapsed = time.monotonic() - start
    # Rounded up: 0.4 MB of real allocation should not print as "0 MB",
    # and a value that crosses the ceiling should never round below it.
    # ``None`` when nothing was ever sampled — see the field docstring.
    memory_used = (
        max(1, math.ceil(peak_rss_bytes / (1024 * 1024)))
        if peak_rss_bytes
        else None
    )

    if timed_out:
        return ExecutionResult(
            success=False,
            stdout=stdout,
            stderr=f"Time Limit Exceeded (>{time_limit}s)",
            exit_code=-1,
            timed_out=True,
            exec_time=elapsed,
            memory_used=memory_used,
        )
    if mem_exceeded:
        return ExecutionResult(
            success=False,
            stdout=stdout,
            stderr=f"Memory Limit Exceeded (>{effective_mb}MB)",
            exit_code=-1,
            memory_exceeded=True,
            exec_time=elapsed,
            memory_used=memory_used,
        )

    assert retcode is not None  # narrowed by the loop exit condition
    return ExecutionResult(
        success=retcode == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=retcode,
        exec_time=elapsed,
        memory_used=memory_used,
    )
