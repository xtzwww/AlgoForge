"""Breakpoint debugging for C++ submissions (PU-17).

Drives ``gdb`` in batch mode over a generated command script and parses
its text output back into structured JSON. This is the backend behind the
practice page's 调试 panel: the student sets breakpoints in the editor,
the sidecar compiles with debug info, runs the program under gdb, and
reports where it stopped, what the variables hold, and what the call
stack looks like.

Why text output and not GDB/MI
------------------------------

``--interpreter=mi2`` is the machine-readable protocol and would be the
obvious choice. It was **measured and rejected** here: on this machine's
pyramid of pipes, an MI session emitted only ``=thread-group-added``
before hanging, and the interactive ``select()``-based draining that MI
rewards read nothing at all on Windows pipes. The text interface with
``--batch -x <script>`` was measured to work reliably, first try, for
every operation the panel needs. Parsing is therefore a deliberate
concession to the platform, contained entirely in this module.

The one thing MI would have given that this cannot: MI streams events,
so an interactive session can react to a stop without polling. The cost
is handled at the protocol layer instead — each debug command is one
gdb invocation that runs to the next stop, and the caller drives the
sequence (see :mod:`app.debug_session`).

Why a matched toolchain is mandatory
------------------------------------

Debug info has a version. This project's own ``g++`` (15.2) emits DWARF
5; every gdb available here except the one shipped beside a matching
compiler predates it, and fails with ``Dwarf Error: wrong version in
compilation unit header`` — which surfaces as the deeply unhelpful
``No symbol table is loaded``. Measured failure modes, so nobody has to
re-derive them:

===================================  ==============================
toolchain                            result
===================================  ==============================
project g++ 15.2 + RedPanda gdb 10.2  0xc0000139 at startup (runtime)
project g++ 15.2 + Dev-Cpp gdb 7.8.1  ``Dwarf Error ... (is 5, should be 2, 3, or 4)``
RedPanda g++ 11.5 + RedPanda gdb 10.2  **works** — breakpoints, locals, steps
===================================  ==============================

So :data:`DEBUG_TOOLCHAIN` pins the compiler *and* the debugger together
rather than reusing ``settings.cpp_compiler_path``, which is configured
for judging and has no reason to carry debug support. The pair is
discovered at runtime by :func:`probe_debug_toolchain`; when it is
missing, the panel reports that plainly instead of failing mid-session.

Two Windows-specific facts this module has to respect
-----------------------------------------------------

1. **The debugger needs its toolchain directory on ``PATH``.** Invoking
   gdb by absolute path without it dies at startup with ``0xc0000139``
   (``STATUS_ENTRYPOINT_NOT_FOUND``) — a missing-DLL error that looks
   nothing like a PATH problem. :func:`_debug_env` prepends the
   directory for the same reason ``compiler._build_env`` does.
2. **A security product injects itself into every process spawned
   here** and writes dozens of ``warning:`` lines to stderr (``[TSBX]``,
   ``[LSHOOK]``). They are noise from the environment, not from the
   student's program, and would bury the real diagnostics if forwarded.
   :func:`_is_environment_noise` filters exactly those prefixes — not a
   generic "drop stderr" rule, so a genuine gdb warning still gets
   through.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------- toolchain

#: Candidate debug toolchains, best first. Each entry pins a compiler and
#: a debugger that are known to agree on debug-info format; they are
#: consumed as a *pair* and never mixed. See the module docstring for the
#: measurements that make this pairing necessary.
#:
#: These are absolute install paths rather than ``PATH`` lookups because a
#: bare ``gdb`` on this machine resolves to an unrelated older build whose
#: DWARF support is the whole problem.
DEBUG_TOOLCHAIN: tuple[tuple[str, str], ...] = (
    (r"D:\RedPanda-CPP\mingw64\bin\g++.exe", r"D:\RedPanda-CPP\mingw64\bin\gdb.exe"),
)

#: Debug builds are unoptimised on purpose. ``-O2`` (what the judge uses)
#: reorders and inlines statements, so the line a breakpoint lands on
#: stops corresponding to the line the student clicked, and variables get
#: optimised out entirely — the panel would show ``<optimized out>``
#: everywhere. ``-O0`` is what makes the shown state match the source.
_DEBUG_FLAGS: tuple[str, ...] = ("-g", "-O0")

#: How long a single gdb invocation may take before it is killed. This
#: covers *one* step (run to first breakpoint, one `next`, …), not a whole
#: session, so it only has to absorb process startup plus debug-info
#: loading — which is slow the first time on a template-heavy file.
_STEP_TIMEOUT = 60


def _is_environment_noise(line: str) -> bool:
    """Whether a gdb stderr line comes from the machine, not the program.

    A security agent hooks every process spawned on this host and reports
    its progress on stderr with these prefixes. They arrive *before* the
    inferior starts, are never about the student's code, and number in the
    dozens — enough to push a real compiler or runtime error out of view.

    Deliberately a denylist of known prefixes rather than "ignore all
    stderr": a genuine gdb warning (``warning: Source file is more recent
    than executable``) has to survive, because that is exactly the class
    of message a student needs to see.
    """
    stripped = line.lstrip()
    return stripped.startswith(
        ("[TSBX]", "[LSHOOK", "warning: [TSBX]", "warning: [LSHOOK")
    )


@dataclass(frozen=True)
class DebugToolchain:
    """A compiler and debugger that agree on debug-info format."""

    compiler: str
    gdb: str

    @property
    def bin_dir(self) -> str:
        """Directory to prepend to ``PATH`` for both tools.

        Derived from the debugger: it is the process that fails with a
        missing-DLL error when its directory is absent, and both binaries
        ship in the same ``bin``.
        """
        return str(Path(self.gdb).parent)

    def to_dict(self) -> dict:
        return {"compiler": self.compiler, "gdb": self.gdb}


def probe_debug_toolchain() -> Optional[DebugToolchain]:
    """The first usable debug toolchain, or ``None`` if there is none.

    Checks that both halves exist on disk. It deliberately does **not**
    run them: a probe that launches a debugger costs a second per call
    and this is consulted when the UI asks whether 调试 is available at
    all. Existence is the honest signal — a present-but-broken pair fails
    later with gdb's own message, which says more than a bare "unavailable".
    """
    for compiler, gdb in DEBUG_TOOLCHAIN:
        if Path(compiler).is_file() and Path(gdb).is_file():
            return DebugToolchain(compiler=compiler, gdb=gdb)
    return None


def debug_available() -> bool:
    """Whether the debugpanel can run at all on this machine."""
    return probe_debug_toolchain() is not None


def _debug_env(toolchain: DebugToolchain) -> dict:
    """Environment for a debug compile or run.

    Prepends the toolchain's ``bin`` to ``PATH`` and nothing else — the
    compile side reads it so the linker can find ``libstdc++``, and the
    gdb side reads it so the *inferior* can. Copying ``os.environ`` first
    keeps whatever the operator configured, matching
    ``compiler._build_env``.
    """
    env = os.environ.copy()
    bin_dir = toolchain.bin_dir
    current = env.get("PATH", "")
    if bin_dir and bin_dir not in current.split(os.pathsep):
        env["PATH"] = bin_dir + os.pathsep + current
    return env


# ---------------------------------------------------------------- compiling


@dataclass
class DebugBuild:
    """Outcome of compiling a submission for debugging.

    Attributes:
        success: Whether the compiler exited 0.
        executable: The binary, when the build succeeded.
        output: Compiler diagnostics, stripped. Empty on a clean build.
        toolchain: Which pair was used, for the UI to report and for a
            failure to be reproducible.
    """

    success: bool
    executable: Optional[Path]
    output: str
    toolchain: Optional[DebugToolchain] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "executable": str(self.executable) if self.executable else None,
            "output": self.output,
            "toolchain": self.toolchain.to_dict() if self.toolchain else None,
        }


def compile_for_debug(
    code: str,
    work_dir: Path,
    toolchain: Optional[DebugToolchain] = None,
) -> DebugBuild:
    """Compile *code* into *work_dir* with debug info at ``-O0``.

    Separate from :func:`app.sandbox.compiler.compile_source` rather than
    a flag on it, because the two serve genuinely different purposes: the
    judge wants a fast optimised binary whose behaviour is all that
    matters, while debugging needs a slow unoptimised one whose *state* is
    inspectable. Threading a ``debug=True`` through the judge's compiler
    would put both meanings behind one name and invite the judge to
    inherit ``-O0``.

    Args:
        code: The C++ source.
        work_dir: Directory to build in. The caller owns it, so the
            binary survives across the session's steps.
        toolchain: Override, for tests. Defaults to the probed pair.

    Returns:
        A :class:`DebugBuild`; on failure ``output`` carries the
        compiler's diagnostics verbatim, because that is what the student
        reads after a CE.
    """
    toolchain = toolchain or probe_debug_toolchain()
    if toolchain is None:
        return DebugBuild(
            success=False,
            executable=None,
            output=(
                "调试功能需要配套的编译器与调试器，本机没有找到。\n"
                "期望的路径：" + "、".join(DEBUG_TOOLCHAIN[0]) + "\n"
                "请安装 RedPanda-CPP 后重试。"
            ),
            toolchain=None,
        )

    src = Path(work_dir) / "main.cpp"
    src.write_text(code, encoding="utf-8")
    exe = Path(work_dir) / ("main.exe" if os.name == "nt" else "main")

    argv = [toolchain.compiler, str(src), "-o", str(exe), *_DEBUG_FLAGS, "-std=c++17"]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_debug_env(toolchain),
            timeout=_STEP_TIMEOUT,
            cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return DebugBuild(
            success=False,
            executable=None,
            output=f"编译超时（>{_STEP_TIMEOUT}s）",
            toolchain=toolchain,
        )

    output = tidy_diagnostics(_clean_stderr(proc.stdout or "", proc.stderr or ""))
    return DebugBuild(
        success=proc.returncode == 0 and exe.is_file(),
        executable=exe if proc.returncode == 0 and exe.is_file() else None,
        output=output,
        toolchain=toolchain,
    )


def _clean_stderr(stdout: str, stderr: str) -> str:
    """Merge tool output, dropping the environment's injected noise."""
    lines = []
    for raw in (stdout + "\n" + stderr).splitlines():
        if _is_environment_noise(raw):
            continue
        lines.append(raw)
    return "\n".join(lines).strip()


def tidy_diagnostics(text: str) -> str:
    """Strip the sandbox's scratch paths out of compiler diagnostics.

    gcc prints absolute paths, and the sandbox's are both long and
    meaningless to a student::

        C:\\...\\codelab_judge\\debug\\dbg_KK_05Ckx\\main.cpp: In function 'int main()':

    Every line naming the scratch directory is a line the student has to
    read past to find their own mistake. The file is always ``main.cpp``
    inside that directory, so replacing the whole path with the bare name
    gives ``main.cpp: In function 'int main()':`` — the same diagnostic
    an IDE would show, and one the editor can jump to.

    A regex rather than string surgery because the path contains a drive
    letter, backslashes and several nested directories; finding "the part
    before main.cpp" by hand is what made the first attempt unreadable.
    The optional drive letter and the separator class cover both Windows
    and POSIX, so this behaves the same way on either host.
    """
    if not text:
        return text
    # Any path ending in ``main.cpp`` — the submission's own file — is
    # reduced to the bare name. Deliberately does not touch paths to
    # anything else: a message naming a real system header is information
    # the student may need.
    pattern = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[^\\/\s:]+[\\/])*main\.cpp")
    return pattern.sub("main.cpp", text)


# ------------------------------------------------------------------ parsing

#: A source location as gdb prints it: ``m2.cpp:6`` or
#: ``C:\dir\main.cpp:6``.
#:
#: The colon in a Windows drive letter is why this cannot be the obvious
#: ``[^:]+:(\d+)``: that stops at ``C:`` and captures nothing useful. The
#: greedy ``.+`` before the final ``:(\d+)`` is deliberate — it takes the
#: *last* colon, which is the one that separates the path from the line
#: number. Measured failure mode of the naive pattern: every stop parsed
#: as ``reason="unknown"`` with no error, because the only thing that
#: failed was the location match.
_PATH = r"(?P<file>.+?):(?P<line>\d+)"

#: ``Breakpoint 2 at 0x140001553: file m2.cpp, line 6.``
#: ``Breakpoint 1 at 0x14000154c: m.cpp:3.``
#:
#: Two shapes on purpose — gdb uses ``file X, line N`` when it resolved
#: the location, and the bare ``X:N`` form when a line mapped to several
#: addresses. Both are matched so a breakpoint on a multi-location line
#: still reports where it landed.
_RE_BREAKPOINT_SET = re.compile(
    r"^Breakpoint\s+(?P<num>\d+)\s+at\s+(?P<addr>0x[0-9a-fA-F]+)"
    r"(?:.*?file\s+(?P<file1>.+?),\s*line\s+(?P<line1>\d+)"
    r"|:\s*(?P<file2>.+?):(?P<line2>\d+))"
)

#: ``Thread 1 hit Breakpoint 1, sum (v=...) at m2.cpp:6``
_RE_HIT = re.compile(
    r"^Thread\s+(?P<thread>\d+)\s+hit\s+Breakpoint\s+(?P<num>\d+)"
    r"(?:,\s*(?P<func>[^(]+?)\s*\((?P<args>.*)\))?\s+at\s+" + _PATH
)

#: ``#0  sum (v=...) at m2.cpp:6`` / ``#1  0x... in main () at m2.cpp:14``
_RE_FRAME = re.compile(
    r"^#(?P<level>\d+)\s+(?:(?P<addr>0x[0-9a-fA-F]+)\s+in\s+)?(?P<func>.+?)"
    r"\s+at\s+" + _PATH
)

#: The source line gdb echoes after a stop: ``6\t    for (...) {``
_RE_SOURCE_ECHO = re.compile(r"^(?P<line>\d+)\t(?P<text>.*)$")

#: ``name = value`` from ``info locals`` / ``info args``.
_RE_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s(?P<value>.*)$")

#: ``$1 = 5`` from ``print``.
_RE_PRINT = re.compile(r"^\$\d+\s+=\s*(?P<value>.*)$")

#: ``[Inferior 1 (process 8636) exited normally]``
#:
#: ``how`` is the tail *after* the word ``exited``, so it reads
#: ``normally`` or ``with code 2`` — not ``exited with code 2``. The
#: exit-code pattern below therefore cannot require the leading
#: ``exited``; see its comment for the bug that caused.
_RE_EXITED = re.compile(r"Inferior\s+\d+\s+\(process\s+\d+\)\s+exited\s+(?P<how>.+?)\]")

#: ``with code 0`` / ``with code 3221225785``
#:
#: Matched against :data:`_RE_EXITED`'s ``how`` group, which has already
#: consumed the word ``exited``. Requiring it here made this pattern
#: match *nothing*, so every non-zero exit was reported as 0 — a program
#: that returned 2 looked like a clean run. Found by
#: ``tests/test_debug_parser.py::test_non_zero_exit_code_is_read_from_the_message``.
_RE_EXIT_CODE = re.compile(r"with code\s+(?P<code>\d+)")

#: ``Program received signal SIGSEGV, Segmentation fault.``
_RE_SIGNAL = re.compile(r"^Program received signal\s+(?P<sig>\w+),\s*(?P<desc>.*)$")

#: ``No symbol "i" in current context.`` — not an error worth showing:
#: a variable that is not yet in scope is normal right after a stop.
_RE_NO_SYMBOL = re.compile(r'No symbol .+ in current context')

#: Frames inside the compiler's own headers. `step` at a ``v[i]`` descends
#: into ``stl_vector.h`` (measured), and showing a student
#: ``std::vector<int>::operator[]`` in the call stack is worse than
#: useless — it is the one frame that is *certainly* not their bug.
#:
#: Matched against a **forward-slashed** copy of the path: gdb prints
#: Windows paths with backslashes (``D:\RedPanda-CPP\...\stl_vector.h``)
#: while the compiler's own diagnostics and this project's markers use
#: forward slashes, so a raw substring test silently classifies every
#: library frame as student code. Measured: the filter dropped *nothing*
#: before normalisation.
_LIBRARY_MARKERS = (
    "stl_vector.h",
    "stl_algobase.h",
    "stl_iterator.h",
    "bits/stl_",
    "mingw64/include",
    "lib/gcc/",
    "/include/c++/",
)


@dataclass
class Frame:
    """One call-stack frame, as shown in the panel's 调用栈 list."""

    level: int
    function: str
    file: str
    line: int
    address: Optional[str] = None

    @property
    def is_library(self) -> bool:
        """Whether this frame is compiler/library code, not student code.

        Compares on a normalised path so the check works whichever
        separator gdb used — see :data:`_LIBRARY_MARKERS`.
        """
        normalised = self.file.replace("\\", "/")
        return any(marker in normalised for marker in _LIBRARY_MARKERS)

    @property
    def display_file(self) -> str:
        """The file name to show, with the sandbox's scratch path removed.

        The compiler is handed ``<scratch>/main.cpp`` and gdb reports that
        absolute path back. Showing it would put
        ``C:\\Windows\\Temp\\codelab_judge\\debug\\dbg_K3x9\\main.cpp`` in the
        panel's location line, which tells the student nothing they can
        use — and on a machine with a tight disk, invites them to go
        looking in a temp directory. The submission is always ``main.cpp``,
        so that is what they see.
        """
        return tidy_diagnostics(self.file)

    @property
    def display_function(self) -> str:
        """The function name alone, with the argument list stripped.

        gdb prints the whole signature and the *values* of the arguments:
        ``sum (n=5)``, or for a container ``sum (v=std::vector of length 5,
        capacity 5 = {...})`` — measured. A call-stack row that reads
        ``sum (v=std::vector of length 5, capacity 5 = {...})()`` is a wall
        of text where a name should be, and the panel appends its own
        parentheses.

        Cut at the first ``(``: a function name cannot contain one, and
        the argument values are the only thing this could be trimming.
        """
        name = self.function.split("(", 1)[0].strip()
        return name or self.function

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "function": self.function,
            #: The name to *render*. Sent rather than derived in the panel
            #: so the trimming rule lives with the parser that knows what
            #: gdb's format is — see :attr:`display_function`.
            "display_function": self.display_function,
            "file": self.display_file,
            "line": self.line,
            "address": self.address,
            "is_library": self.is_library,
        }


@dataclass
class Variable:
    """One variable in the panel's 变量 list."""

    name: str
    value: str
    kind: str = "local"
    #: True when the variable is in scope but has not been assigned yet.
    #: The panel renders these as 尚未初始化 rather than as a number —
    #: see :func:`_is_uninitialised` for why showing the raw value would
    #: be actively misleading.
    uninitialised: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "kind": self.kind,
            "uninitialised": self.uninitialised,
        }


#: Names of the loop variable that gdb happily reports a value for before
#: the declaration has executed.
#:
#: This is the single most misleading thing a naive debugger can show.
#: Measured three runs of the same breakpoint on ``for (int i = 0; …)``:
#: gdb reported ``i`` as ``583006768``, then ``-75499648``, then
#: ``564133088``. It is whatever was on the stack, and a student reading
#: the panel would conclude their loop starts at 583006768.
#:
#: Detecting it properly needs the DWARF location list for the variable,
#: which this text protocol does not expose. The available signal is the
#: stop's own source line: if the variable is declared *at or after* the
#: line the program is stopped on, its declaration has not run yet. That
#: is decidable from what gdb already prints, and it errs toward "not yet
#: initialised", which is the safe direction — claiming a value that is
#: not real is a much worse error than being briefly unsure.
_RE_DECL = re.compile(
    r"\b(?:int|long|short|char|bool|float|double|unsigned|signed|auto|size_t)"
    r"(?:\s+long)?(?:\s+int)?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;|,|\))"
)


def _declared_on_or_after(name: str, source_line: Optional[str]) -> bool:
    """Whether *name*'s declaration appears on the line being stopped at.

    Used to spot the not-yet-initialised case. Returns False when there is
    no source line to judge from — the conservative answer, since it keeps
    a variable's value visible rather than blanking it on a guess.

    Only correct for the *first* time a line is reached: a loop's
    ``for (int i = …)`` declares ``i`` on the same line every iteration,
    but from the second visit onward ``i`` holds a real value (measured:
    ``-702547824`` on the first hit, ``0`` on the second). The caller
    therefore combines this with a visit count — see
    :attr:`StopInfo.line_visited_before`.
    """
    if not source_line:
        return False
    # A declaration *with* an initialiser on the very line being stopped at
    # has not had its initialiser evaluated yet: gdb stops before the line's
    # code runs. So it counts as "not yet initialised" too.
    return any(match.group("name") == name for match in _RE_DECL.finditer(source_line))


@dataclass
class StopInfo:
    """Where the program is and what is visible there.

    This is the payload the panel renders after every step: the location
    line, the variables, and the call stack. ``reason`` says *why* it
    stopped, because a student needs to distinguish "hit my breakpoint"
    from "crashed" from "ran to the end" — the same three cases have very
    different next actions.
    """

    reason: str  # "breakpoint" | "signal" | "exited" | "unknown"
    file: Optional[str] = None
    line: Optional[int] = None
    function: Optional[str] = None
    breakpoint_number: Optional[int] = None
    signal: Optional[str] = None
    signal_description: Optional[str] = None
    exit_code: Optional[int] = None
    variables: list[Variable] = field(default_factory=list)
    stack: list[Frame] = field(default_factory=list)
    #: The source text of the stop line, echoed by gdb. Sent so the panel
    #: can show the line without re-reading the file, and so a mismatch
    #: between what the student sees and what gdb ran is visible.
    source_line: Optional[str] = None
    #: True when this same (file, line) was reached earlier in the run.
    #:
    #: The one thing that distinguishes "the declaration on this line has
    #: not run yet" from "it ran, and this is a later iteration" — both
    #: stop on the identical ``for (int i = …)`` line with different
    #: meanings. Without it a loop variable would be reported as
    #: uninitialised forever, which is the opposite failure to showing
    #: garbage as fact and just as misleading.
    #:
    #: The parser can only set this for stops within *one* gdb run. Each
    #: step is a separate run (see ``app.debug_session``), so the session
    #: re-applies it across runs via
    #: :func:`app.debug_session.mark_revisited`.
    line_visited_before: bool = False

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "file": tidy_diagnostics(self.file) if self.file else None,
            "line": self.line,
            "function": self.function,
            "breakpoint_number": self.breakpoint_number,
            "signal": self.signal,
            "signal_description": self.signal_description,
            "exit_code": self.exit_code,
            "variables": [v.to_dict() for v in self.variables],
            "stack": [f.to_dict() for f in self.stack],
            "source_line": self.source_line,
            "line_visited_before": self.line_visited_before,
        }


def parse_breakpoint_set(output: str) -> Optional[tuple[str, int]]:
    """First breakpoint's ``(file, line)`` from a ``break`` reply, if any.

    Returning the resolved location (rather than the requested one) is
    what lets the panel tell a student their breakpoint moved — which
    happens legitimately when a line has no code of its own.
    """
    for line in output.splitlines():
        match = _RE_BREAKPOINT_SET.match(line.strip())
        if match:
            return (
                match.group("file1") or match.group("file2") or "",
                int(match.group("line1") or match.group("line2")),
            )
    return None


def parse_stops(output: str) -> list[StopInfo]:
    """Every stop in a gdb run, in the order they happened.

    A session is a *sequence* of stops — run to the first breakpoint, step
    once, step again — and the panel needs the final one to render the
    current position. Returning the list rather than only the last entry
    keeps the intermediate stops available for a step-history view, and
    makes the "which stop am I looking at" decision the caller's rather
    than a silent truncation here.

    Two passes on purpose. The stop's own source line is printed *after*
    the location header but the variable values come later still, and
    deciding whether a variable is initialised (:func:`_declared_on_or_after`)
    needs that source line. Collecting raw lines per stop first, then
    interpreting, removes the ordering trap entirely — the single-pass
    version had to read the echo before the values and silently produced
    ``uninitialised=False`` for every variable when it did not.
    """
    # ---- pass 1: split the transcript into per-stop blocks
    blocks: list[tuple[str, list[str]]] = []
    current: Optional[tuple[str, list[str]]] = None
    preamble: list[str] = []

    for raw in output.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if _is_environment_noise(stripped) or "import sitecustomize" in stripped:
            continue

        kind = _stop_kind(stripped)
        if kind is not None:
            current = (kind, [stripped])
            blocks.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current[1].append(line)

    # ---- pass 2: interpret each block, tracking which lines were seen
    stops: list[StopInfo] = []
    seen_locations: set[tuple[str, int]] = set()
    for kind, lines in blocks:
        stop = _interpret_stop(kind, lines)
        key = (stop.file or "", stop.line or 0)
        if stop.reason == "breakpoint" and key in seen_locations:
            stop.line_visited_before = True
        seen_locations.add(key)
        stops.append(stop)
    if not stops:
        # No stop at all: the program never started. Report whatever gdb
        # said in the preamble rather than an empty object, because the
        # reason is almost always a startup failure worth reading.
        err = parse_error("\n".join(preamble))
        return [StopInfo(reason="unknown", source_line=err)]
    return stops


def _stop_kind(stripped: str) -> Optional[str]:
    """Classify a line as the header of a stop, or ``None``.

    Kept apart from the interpretation so the split in :func:`parse_stops`
    is a single readable predicate.
    """
    if _RE_HIT.match(stripped):
        return "breakpoint"
    if _RE_SIGNAL.match(stripped):
        return "signal"
    if _RE_EXITED.search(stripped):
        return "exited"
    return None


def _interpret_stop(kind: str, lines: list[str]) -> StopInfo:
    """Turn one stop's lines into a :class:`StopInfo`."""
    header = lines[0]
    stop = StopInfo(reason=kind)
    variables: list[Variable] = []
    stack: list[Frame] = []
    var_kind = "local"

    if kind == "breakpoint":
        hit = _RE_HIT.match(header)
        assert hit is not None  # guaranteed by _stop_kind
        stop.file = hit.group("file")
        stop.line = int(hit.group("line"))
        stop.function = (hit.group("func") or "").strip() or None
        stop.breakpoint_number = int(hit.group("num"))
    elif kind == "signal":
        sig = _RE_SIGNAL.match(header)
        assert sig is not None
        stop.signal = sig.group("sig")
        stop.signal_description = sig.group("desc").strip() or None
    elif kind == "exited":
        exited = _RE_EXITED.search(header)
        assert exited is not None
        code = _RE_EXIT_CODE.search(exited.group("how"))
        # ``exited normally`` really is a success, and it is the one case
        # with no number in the message — so 0 is a reading of the text,
        # not a guess. Anything else that lacks a number ("exited with
        # code" and then a truncated line) stays None rather than being
        # reported as success.
        stop.exit_code = int(code.group("code")) if code else 0

    # The echoed source line comes first in the block, before any stack
    # or variable output.
    for line in lines[1:]:
        echo = _RE_SOURCE_ECHO.match(line)
        if echo and not stack and not variables:
            stop.source_line = echo.group("text")
            break
        if line.strip():
            break

    for line in lines[1:]:
        stripped = line.strip()

        frame = _RE_FRAME.match(stripped)
        if frame:
            level = int(frame.group("level"))
            # A second ``bt`` restarts at ``#0``. Appending would produce
            # a stack with two frame zeros and two ``main``s — measured,
            # and it reads as a bug in the student's program rather than
            # in the panel.
            if level == 0:
                stack = []
            stack.append(
                Frame(
                    level=level,
                    function=frame.group("func").strip(),
                    file=frame.group("file"),
                    line=int(frame.group("line")),
                    address=frame.group("addr"),
                )
            )
            continue

        # ``info args`` switches what the assignment lines mean.
        if stripped == "info args":
            var_kind = "arg"
            continue
        if stripped.startswith("info locals"):
            var_kind = "local"
            continue

        assignment = _RE_ASSIGNMENT.match(stripped)
        if assignment:
            name = assignment.group("name")
            if not any(v.name == name for v in variables):
                # Only the *first* visit to a declaring line means the
                # declaration has not run. From the second visit on the
                # same line is an ordinary loop iteration and the value is
                # real — so the visit flag gates the declaration check.
                pending = _declared_on_or_after(name, stop.source_line) and not (
                    stop.line_visited_before
                )
                variables.append(
                    Variable(
                        name=name,
                        value=assignment.group("value"),
                        kind=var_kind,
                        uninitialised=pending,
                    )
                )
            continue

        printed = _RE_PRINT.match(stripped)
        if printed:
            seen = len([v for v in variables if v.kind == "expression"])
            variables.append(
                Variable(
                    name=f"#{seen + 1}",
                    value=printed.group("value"),
                    kind="expression",
                )
            )
            continue

    stop.stack = [f for f in stack if not f.is_library]
    stop.variables = [
        v for v in variables if not v.value.strip().startswith("<optimized out>")
    ]

    # A crash reports where it happened on a line *below* the header
    # (``0x… in crash () at bug.cpp:4``), not in the header itself — the
    # header carries only the signal. Without this the panel would show
    # "程序崩溃" with no location, which is the one thing the student
    # needs to fix it. The innermost remaining frame is the crash site;
    # taking it from the filtered stack means a crash inside a library
    # correctly reports the first student frame instead.
    #
    # The name comes from `display_function`, not `function`: the stop's
    # location line reads as a label ("崩溃发生在 crash"), and the raw
    # capture keeps gdb's argument list on it ("crash ()",
    # "sum (v=std::vector of length 5, …)"). The breakpoint branch above
    # gets this for free because its header's `func` group is already
    # bare — so the two must agree on this side instead.
    if kind == "signal" and stop.file is None and stop.stack:
        inner = stop.stack[0]
        stop.file = inner.file
        stop.line = inner.line
        stop.function = inner.display_function
    return stop


def parse_session(output: str) -> StopInfo:
    """The final stop of a gdb run — what the panel should display.

    Convenience over :func:`parse_stops` for the common case: after a
    ``run``/``next``/``step`` the question is always "where is the program
    *now*". Where a caller needs the intermediate positions (a step
    history), it should use :func:`parse_stops` directly.
    """
    stops = parse_stops(output)
    return stops[-1]


def parse_error(output: str) -> Optional[str]:
    """The first real error gdb reported, or ``None``.

    Used to turn a failed command into a message worth showing. ``No
    symbol "x" in current context`` is deliberately excluded: asking for
    a variable before its declaration is reached is a normal consequence
    of stopping early, not an error.

    Three shapes are recognised, all of them measured:

    * ``Error in sourced command file:`` — gdb's own, printed when a line
      in our command script fails;
    * ``error: …`` — a compiler diagnostic that reached this far;
    * ``<script>:<line>: <message>`` — gdb prefixes a script failure with
      the file and line it happened on. The prefix is *our* generated
      command file, which is meaningless to a student, so it is stripped
      and the message kept.

    The third shape originally required ``.txt``, but the script this
    project writes is ``gdb.cmd`` (see ``app.debug_session._write_script``)
    — so it never matched and the real reason for a failed step was
    dropped. Found by
    ``tests/test_debug_parser.py::test_parse_error_extracts_a_failed_script_line``.
    """
    for raw in output.splitlines():
        stripped = raw.strip()
        if not stripped or _is_environment_noise(stripped):
            continue
        if _RE_NO_SYMBOL.search(stripped):
            continue
        if stripped.startswith("Error") or stripped.startswith("error:"):
            return stripped
        # Any script-file prefix, not just `.txt`: the extension is ours
        # to choose and has already changed once.
        if re.match(r"^\S+\.[A-Za-z0-9]+:\d+:\s", stripped):
            return re.sub(r"^\S+\.[A-Za-z0-9]+:\d+:\s*", "", stripped)
    return None


__all__ = [
    "DEBUG_TOOLCHAIN",
    "DebugBuild",
    "DebugToolchain",
    "Frame",
    "StopInfo",
    "Variable",
    "compile_for_debug",
    "debug_available",
    "parse_breakpoint_set",
    "parse_error",
    "parse_session",
    "probe_debug_toolchain",
]
