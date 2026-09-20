"""Code compilation sandbox.

Builds a source file with its language's toolchain and, in the
``compile_and_run`` entry point, executes the result once with a timeout.

PU-16 rewrote this module around ``app.sandbox.languages``. Before that,
the compiler path, the flags and even the source filename (``main.cpp``)
were baked in here, so a second language meant a new ``if language ==``
branch at every point a path was needed. Now the mechanics — write the
source, run the argv the descriptor generates, report the outcome — are
all that remains here, and the per-language knowledge lives in a
:class:`~app.sandbox.languages.LanguageSpec`.

Two entry points, deliberately kept separate since P1-11:
``compile_source`` builds a source file the caller already wrote, while
``compile_and_run`` owns a scratch directory and does both steps for
callers that just want to see the output. ``judge`` uses the first, so
that one build can be reused across many test cases.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import settings
from app.sandbox.languages import (
    CPP_SPEC,
    LanguageSpec,
    all_languages,
    get_language,
)

# Compile timeout (seconds). Compilation rarely takes long; a generous
# ceiling protects against pathological template-heavy submissions.
# Shared by both entry points so they cannot drift apart.
_COMPILE_TIMEOUT = 30


@dataclass
class CompileResult:
    """Result of building a single source file.

    Attributes:
        success: True if the toolchain returned exit code 0.
        executable_path: The built artefact on success, or None. None also
            on failure, and for languages whose build product is not a
            single file — a Python submission runs from its source and
            Java compiles to a class tree, so neither has one (see
            ``LanguageSpec.build_artefact``). Callers that need to *run*
            the result should go through the descriptor's ``run_cmd``
            rather than assuming this is populated.
        output: Combined stdout + stderr from the toolchain, stripped.
            Empty when there is nothing to report.
        exit_code: Toolchain process exit code. -1 indicates a timeout
            or other failure to launch it.
    """

    success: bool
    executable_path: Path | None
    output: str
    exit_code: int

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "executable_path": (
                str(self.executable_path) if self.executable_path else None
            ),
            "output": self.output,
            "exit_code": self.exit_code,
        }


@dataclass
class RunResult:
    """Result of building and running a piece of code."""

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


def sandbox_tmp_base() -> Path:
    """Directory the sandbox creates its scratch directories under.

    On Windows this is ``C:\\Windows\\Temp`` rather than the OS default,
    because MinGW's linker cannot open output files on non-ASCII paths and
    this machine's user profile contains CJK characters ("雪团子").
    ``conftest.py`` places test sources in the same tree for that reason,
    and ``env_probe`` reports on this very directory in
    ``/api/v1/env-check`` — so a read-only temp dir becomes an actionable
    startup message instead of an opaque linker failure on the first
    submission.

    Single source of truth: ``judge``, ``compile_and_run`` and the
    environment probe all resolve the location through here, so they
    cannot disagree about where the sandbox is allowed to write. (They did
    before PU-16: ``judge`` and ``compile_and_run`` used different
    locations, and the second one was the non-ASCII default — every C++
    compile it attempted failed.)
    """
    if os.name == "nt":
        return Path(r"C:\Windows\Temp") / "algoforge_judge"
    # tempfile.gettempdir() honours TMPDIR/TEMP and picks a sane fallback,
    # so this does not need to re-implement that lookup.
    return Path(tempfile.gettempdir()) / "algoforge_judge"


def sandbox_tmpdir() -> tempfile.TemporaryDirectory:
    """Create the scratch directory used by one build/run cycle.

    The location comes from :func:`sandbox_tmp_base`; the other deliberate
    choice here is ``ignore_cleanup_errors=True``, which is a fix rather
    than a convenience. The built binary lives in this directory and is
    killed on TLE/MLE, and on Windows a killed process can still hold a
    handle to its own executable for a short while. ``shutil.rmtree`` then
    fails with ``PermissionError`` while unwinding the context manager —
    so the exception escaped the calling function entirely. That surfaced
    as an intermittent failure of the MLE test which only appeared under
    full-suite load, and in production it would turn an ordinary
    submission into a 500 because the sandbox could not delete its own
    scratch files. A stray directory in the temp folder is the much lesser
    problem.
    """
    base = sandbox_tmp_base()
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(
        prefix="judge_",
        dir=str(base),
        ignore_cleanup_errors=True,
    )


def _path_prefix_dirs(spec: Optional[LanguageSpec]) -> list[str]:
    """Toolchain bin directories to prepend to ``PATH``, deduplicated.

    With *spec* set, only that language's directory; with ``None`` (the
    caller does not know the language) every registered one, which is what
    ``run_executable`` needs when handed a bare executable path.
    """
    specs = all_languages() if spec is None else [spec]

    dirs: list[str] = []
    for candidate in specs:
        bin_dir = candidate.toolchain_bin_dir()
        if bin_dir and bin_dir not in dirs:
            dirs.append(bin_dir)
    return dirs


def _build_env(spec: Optional[LanguageSpec] = None) -> dict:
    """Return the process environment used to build and run *spec*'s code.

    Three things go into it:

    1. The toolchain's ``bin`` directory, prepended to ``PATH``. MinGW
       needs this so a compiled binary can find ``libstdc++``/``libgcc``;
       a JDK outside ``PATH`` needs it so a bare ``java`` resolves.
    2. ``PYTHONIOENCODING=utf-8``. Python's stdio otherwise inherits the
       locale encoding — GBK on this machine's Chinese Windows — so a
       submission printing Chinese would emit GBK bytes that the runner
       decodes as UTF-8, producing mojibake and turning a correct answer
       into a WA. The variable is inert for every other toolchain: only
       the Python interpreter reads it. Java has the same class of problem
       but cannot be reached from the environment, so its answer is a pair
       of ``-D`` flags on the descriptor's commands instead (see
       ``languages._JAVA_LOCALE_FLAGS``) — and note those deliberately pin
       the *locale*, not the charset.
    3. Everything already in ``os.environ``, so the toolchain keeps any
       environment the operator configured.

    Args:
        spec: The language being built or run. ``None`` means "unknown"
            and prepends every registered toolchain's ``bin`` directory.
    """
    env = os.environ.copy()

    for bin_dir in _path_prefix_dirs(spec):
        current = env.get("PATH", "")
        if bin_dir not in current.split(os.pathsep):
            env["PATH"] = bin_dir + os.pathsep + current

    env["PYTHONIOENCODING"] = "utf-8"
    return env


def compile_source(
    source_path: str | Path,
    language: Optional[str] = None,
) -> CompileResult:
    """Build an already-written source file with its language's toolchain.

    Args:
        source_path: Path to the source file. Its filename must **already**
            match the language's required name (``LanguageSpec
            .source_filename``) — ``Main.java`` for Java, because ``javac``
            refuses a file that is not named after its public class. This
            function builds what it is given; it does not rename it.
        language: Language id such as ``"cpp"``. ``None`` or blank selects
            the default (see ``DEFAULT_LANGUAGE``).

    Returns:
        A :class:`CompileResult`. On success ``executable_path`` names the
        artefact when the language produces exactly one (C++), and is
        ``None`` otherwise.

    Raises:
        UnknownLanguageError: *language* is not registered.

    The toolchain works in the source file's own directory: every command
    a descriptor generates writes its products there (``-o`` target,
    ``-d`` output directory), so a caller wanting isolated artefacts only
    has to put the source in an isolated directory.
    """
    spec = get_language(language)
    src = Path(source_path)
    work_dir = src.parent

    if not spec.needs_compile:
        # Reserved for a language with no build phase at all. No registered
        # spec takes this branch — Python is pushed through py_compile so a
        # syntax error surfaces as CE rather than as a confusing runtime
        # failure — but the descriptor allows it, and returning success here
        # is more honest than asserting that every language has a compiler.
        return CompileResult(success=True, executable_path=None, output="", exit_code=0)

    # `compile_cmd` is None only for a language that needs no build step,
    # which the branch above has already handled.
    assert spec.compile_cmd is not None

    try:
        proc = subprocess.run(
            spec.compile_cmd(src, work_dir),
            capture_output=True,
            text=True,
            # Every toolchain here is expected to emit ASCII or UTF-8: g++
            # and CPython diagnostics were measured to be pure ASCII, and
            # javac's locale is pinned to en_US by its descriptor (see
            # ``languages._JAVA_LOCALE_FLAGS``) precisely so its diagnostics
            # are too. Decoding permissively is the last-resort net for the
            # cases that remain — a CJK character in a path, or a toolchain
            # the descriptor does not cover — so we never end up with None
            # stdout/stderr.
            encoding="utf-8",
            errors="replace",
            env=_build_env(spec),
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
        executable_path=spec.build_artefact(work_dir) if success else None,
        output=output,
        exit_code=proc.returncode,
    )


def compile_cpp(source_path: str | Path) -> CompileResult:
    """Build a C++ source file. Thin wrapper over :func:`compile_source`.

    Kept under its P1-11 name because compile-once/run-many callers know it
    (``judge``, ``test_compiler``, ``test_runner``). Behaviour is now the C++
    descriptor's: the binary is ``main.exe`` beside the source on Windows
    (``main`` on POSIX).

    Args:
        source_path: Path to the ``.cpp`` source file.

    Returns:
        A :class:`CompileResult`. On success ``executable_path`` points at
        the produced binary; on failure it is None and ``output`` carries
        the compiler's diagnostics.

    Note:
        Unlike this function's pre-PU-16 implementation, the binary is no
        longer named after the source file. Compiling two different sources
        into the *same* directory therefore overwrites the first binary.
        Callers that build more than one file should give each its own
        directory — ``judge`` and ``compile_and_run`` both do.
    """
    return compile_source(source_path, CPP_SPEC.id)


def compile_and_run(
    code: str,
    stdin_input: Optional[str] = None,
    language: Optional[str] = None,
) -> RunResult:
    """Build *code* with its language's toolchain and run the result.

    Args:
        code: Source code to build.
        stdin_input: Optional text to feed to the program's stdin.
        language: Language id. ``None`` or blank selects the default
            (C++, see ``DEFAULT_LANGUAGE``), which is what P1-8's callers
            relied on.

    Returns:
        A :class:`RunResult` with stdout/stderr/exit_code. Build failures
        are reported through ``compile_output`` with ``success=False``.

    Raises:
        UnknownLanguageError: *language* is not registered.

    This is the "just run it once" entry point behind
    ``POST /api/v1/run-code``. No memory ceiling is applied — bounded
    execution with an MLE verdict is ``run_executable``'s job, and
    ``judge`` is what combines the two.
    """
    spec = get_language(language)

    with sandbox_tmpdir() as tmpdir:
        tmp = Path(tmpdir)
        # The filename comes from the descriptor, not from a literal: Java
        # cannot be compiled from `main.java` no matter the flags.
        src_path = spec.source_path(tmp)
        src_path.write_text(code, encoding="utf-8")

        compile_result = compile_source(src_path, spec.id)
        if not compile_result.success:
            return RunResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=compile_result.exit_code,
                compile_output=compile_result.output,
            )

        try:
            run_proc = subprocess.run(
                spec.run_cmd(tmp),
                input=stdin_input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_build_env(spec),
                timeout=settings.sandbox_timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=True,  # the build succeeded; the program did not finish
                stdout="",
                stderr=f"Time Limit Exceeded (>{settings.sandbox_timeout}s)",
                exit_code=-1,
                compile_output=compile_result.output,
            )

        return RunResult(
            success=run_proc.returncode == 0,
            stdout=run_proc.stdout,
            stderr=run_proc.stderr,
            exit_code=run_proc.returncode,
            compile_output=compile_result.output,
        )
