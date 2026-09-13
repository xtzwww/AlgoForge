"""C++ code compilation and execution sandbox.

Compiles user-submitted C++ code with g++ and runs the resulting binary
with a timeout. Temporary files are cleaned up automatically.

P1-8 introduced ``compile_and_run`` (compile + run in one call).
P1-11 adds the standalone ``compile_cpp`` for reuse by later slices
(function calling, LangGraph nodes) that need to compile once and run
multiple times.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


# Compile timeout (seconds). Compilation rarely takes long; a generous
# ceiling protects against pathological template-heavy submissions.
_COMPILE_TIMEOUT = 30


@dataclass
class CompileResult:
    """Result of compiling a single C++ source file.

    Attributes:
        success: True if the compiler returned exit code 0.
        executable_path: Path to the produced binary on success;
            None on failure (or when compilation timed out).
        output: Combined stdout + stderr from the compiler, stripped.
            Empty when there is nothing to report.
        exit_code: Compiler process exit code. -1 indicates a timeout
            or other failure to launch the compiler.
    """

    success: bool
    executable_path: Path | None
    output: str
    exit_code: int

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "executable_path": str(self.executable_path) if self.executable_path else None,
            "output": self.output,
            "exit_code": self.exit_code,
        }


@dataclass
class RunResult:
    """Result of compiling and running C++ code."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    compile_output: str

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "compile_output": self.compile_output,
        }


def _build_env() -> dict:
    """Return an environment dict with the compiler's bin dir on PATH.

    The bin dir is derived from ``cpp_compiler_path`` when not set
    explicitly (see ``Settings.resolved_compiler_bin_dir``) so compiled
    binaries can locate the MinGW runtime DLLs. When the compiler is a
    bare command resolved via PATH there is nothing to prepend.
    """
    env = os.environ.copy()
    bin_dir = settings.resolved_compiler_bin_dir
    if bin_dir:
        current_path = env.get("PATH", "")
        if bin_dir not in current_path:
            env["PATH"] = bin_dir + os.pathsep + current_path
    return env


def compile_cpp(source_path: str | Path) -> CompileResult:
    """Compile a single C++ source file with g++.

    The output binary is placed next to the source file with the
    platform-appropriate suffix (``.exe`` on Windows, no suffix on
    POSIX). The caller owns the binary's lifecycle (deletion, reuse).

    Args:
        source_path: Path to the ``.cpp`` source file.

    Returns:
        A :class:`CompileResult`. On success ``executable_path`` points
        to the produced binary; on failure ``executable_path`` is None
        and ``output`` carries the compiler's diagnostics.

    Compilation is bounded by :data:`_COMPILE_TIMEOUT` seconds. A
    timeout yields ``success=False`` with ``exit_code=-1``.
    """
    src = Path(source_path)
    # Match the existing compile_and_run behavior: Windows uses .exe,
    # other platforms use no suffix. with_suffix(".exe") on POSIX would
    # produce "main.exe", which is still runnable, but to stay consistent
    # with platform conventions we strip the suffix there.
    if os.name == "nt":
        exe_path = src.with_suffix(".exe")
    else:
        exe_path = src.with_suffix("")

    compile_cmd = [
        settings.cpp_compiler_path,
        str(src),
        "-o",
        str(exe_path),
        f"-std={settings.cpp_std}",
    ]

    try:
        proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            # g++ on a Chinese-locale Windows emits GBK diagnostics;
            # decode permissively so we never end up with None stdout/stderr.
            encoding="utf-8",
            errors="replace",
            env=_build_env(),
            timeout=_COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            success=False,
            executable_path=None,
            output=f"Compilation timed out (>{_COMPILE_TIMEOUT}s)",
            exit_code=-1,
        )

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    success = proc.returncode == 0
    return CompileResult(
        success=success,
        executable_path=exe_path if success else None,
        output=output,
        exit_code=proc.returncode,
    )


def compile_and_run(code: str, stdin_input: str | None = None) -> RunResult:
    """Compile *code* with g++ and run the binary.

    Args:
        code: C++ source code to compile.
        stdin_input: Optional text to feed to the program's stdin.

    Returns a :class:`RunResult` with stdout/stderr/exit_code.
    Compilation errors are reported via ``compile_output`` and
    ``success=False``.
    """
    with tempfile.TemporaryDirectory(prefix="algoforge_sandbox_") as tmpdir:
        tmp = Path(tmpdir)
        src_path = tmp / "main.cpp"
        exe_path = tmp / "main.exe"

        # Write source code
        src_path.write_text(code, encoding="utf-8")

        # --- Compile ---
        compile_cmd = [
            settings.cpp_compiler_path,
            str(src_path),
            "-o",
            str(exe_path),
            f"-std={settings.cpp_std}",
        ]
        try:
            compile_proc = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
                env=_build_env(),
                timeout=30,  # compilation timeout
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                compile_output="Compilation timed out (>30s)",
            )

        compile_output = (compile_proc.stdout + compile_proc.stderr).strip()

        if compile_proc.returncode != 0:
            return RunResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=compile_proc.returncode,
                compile_output=compile_output,
            )

        # --- Run ---
        try:
            run_proc = subprocess.run(
                [str(exe_path)],
                input=stdin_input,
                capture_output=True,
                text=True,
                env=_build_env(),
                timeout=settings.sandbox_timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=True,  # compilation succeeded
                stdout="",
                stderr=f"Time Limit Exceeded (>{settings.sandbox_timeout}s)",
                exit_code=-1,
                compile_output=compile_output,
            )

        return RunResult(
            success=run_proc.returncode == 0,
            stdout=run_proc.stdout,
            stderr=run_proc.stderr,
            exit_code=run_proc.returncode,
            compile_output=compile_output,
        )
