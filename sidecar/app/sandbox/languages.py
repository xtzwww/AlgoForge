"""Language descriptors for the multi-language sandbox (PU-15).

Before this module the sandbox was C++-only by construction: the compiler
path, the flags and even the source filename (``main.cpp``) were baked
into ``compiler.py``. Adding a second language that way means a third
``if language == ...`` branch every time a path is needed.

Instead each language is described by data — a :class:`LanguageSpec` —
and the build/run steps are *derived* from that description. The three
differences that actually matter are:

===================  ==============  ===================  ===============
                     C++             Python               Java
===================  ==============  ===================  ===============
build step           ``g++ -o``      ``py_compile``       ``javac -d``
run step             the binary      the interpreter      ``java -cp``
source filename      any             any                  **Main.java**
===================  ==============  ===================  ===============

Java is the reason ``source_filename`` exists at all: ``javac`` requires
the file to be named after its public class, so writing ``main.java``
would fail to compile no matter what flags are passed. That is a
structural difference, not a command-line one, which is why it lives in
the descriptor rather than in a branch.

Deliberately **not** a class hierarchy. Three languages whose differences
are entirely expressible as data do not need three subclasses; that would
be ceremony, not abstraction. Subclassing becomes justified when a
language needs genuinely different *behaviour* (e.g. an interactive
driver), and that is not the case here.

PU-16 added three descriptor fields that describe the *process
environment* rather than the command line. They live here because they
are per-language facts, and the alternative — testing ``spec.id`` inside
the sandbox modules — reintroduces exactly the branching this layer was
built to remove:

* :attr:`LanguageSpec.toolchain_bin_dir` — the directory that has to be on
  ``PATH`` for the toolchain to work at all. MinGW needs it so compiled
  binaries find ``libstdc++``/``libgcc``; a JDK whose ``bin`` is not on
  ``PATH`` needs it so a bare ``java`` resolves.
* :attr:`LanguageSpec.build_artefact` — the build product the run step
  consumes, *when* that product is a single nameable file.
* :attr:`LanguageSpec.rss_allowance_mb` — how much resident memory a
  runtime gets on top of the stated limit before the sandbox's OS-level
  check fires. Only the JVM needs any, and for a structural reason: it
  reserves memory before running a byte of user code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.config import settings

# --------------------------------------------------------------- exceptions


class UnknownLanguageError(LookupError):
    """Raised when a language id has no registered descriptor.

    A plain ``KeyError`` from a dict lookup produces the famously unhelpful
    message ``'cppp'`` — no hint that it was a language id, no list of what
    is valid. Since this id arrives from the network (the judge and
    run-code request bodies), the error has to be self-explanatory enough
    to log or surface to a user.

    Subclasses ``LookupError`` (not ``KeyError``) because callers should
    handle this explicitly rather than accidentally swallow it as a
    generic dict miss.
    """

    def __init__(self, language_id: object, known: list[str]) -> None:
        self.language_id = language_id
        self.known = known
        super().__init__(
            f"unknown language id {language_id!r}; "
            f"expected one of: {', '.join(known)}"
        )


# ---------------------------------------------------------------- descriptor


@dataclass(frozen=True)
class LanguageSpec:
    """Everything the sandbox needs to know about one language.

    The command fields are callables rather than literal argv lists for
    two reasons:

    1. Java's ``java -cp <dir> Main`` has to derive ``<dir>`` from the
       paths it is given, which a fixed list cannot express.
    2. They must read :mod:`app.config` when *called*, not when the
       descriptor is built. Freezing the configured toolchain path at
       import time would make the settings untestable (patching
       ``settings.cpp_compiler_path`` would have no effect) and would
       ignore any runtime reconfiguration.

    Attributes:
        id: Canonical identifier used on the wire: ``"cpp"``, ``"python"``
            or ``"java"``.
        display_name: Human-facing name for the UI.
        source_filename: Name the source file **must** be written as
            inside the working directory. ``"Main.java"`` for Java because
            ``javac`` requires it to match the public class name.
        monaco_language: Monaco's language id, so the editor and the
            sandbox cannot disagree about what "cpp" means.
        default_template: Starter code inserted into an empty editor.
            Must be valid for the toolchain actually configured — the Java
            template is written in Java 8 syntax, since the only JDK on
            this machine is 1.8.0_291.
        needs_compile: Whether a separate build step gates execution.
            True for all three languages, including Python: Python has no
            build phase, but it is still pushed through ``py_compile`` so
            a syntax error surfaces as CE rather than as a confusing
            runtime error (see PHASE-UX decision 4).
        compile_cmd: ``(source_path, work_dir) -> argv`` performing the
            build. ``None`` only for a hypothetical language with no build
            step at all; none of the three registered here use that.
        run_cmd: ``(work_dir) -> argv`` to execute the built program. Takes
            the directory rather than the executable so that each spec can
            derive its own artefact (``main.exe`` / ``main.py`` /
            ``Main.class``) instead of the caller assuming one shape.
        version_cmd: ``() -> argv`` used by the environment self-check.
            A callable for the same settings-timing reason as above.
        toolchain_bin_dir: ``() -> dir`` giving the directory to prepend to
            ``PATH`` when building or running this language. Empty string
            means "nothing to prepend" (the toolchain is already reachable
            by absolute path). Lazy for the same settings-timing reason as
            ``compile_cmd``.
        build_artefact: ``(work_dir) -> path | None`` naming the single
            file the run step consumes, or ``None`` when there is no such
            file. C++ produces one binary; a Python submission is executed
            from its source (``py_compile``'s cache is never run) and Java
            compiles to a class *tree* read via ``-cp``, so both report
            ``None``. Informational only — what actually gets executed is
            decided by :attr:`run_cmd`, which takes the directory.
        rss_allowance_mb: ``() -> MB`` of resident memory granted on top of
            the request's ``mem_limit_mb`` before the OS-level check fires.
            0 for languages whose RSS tracks their own allocation.
        source_suffix: Convenience for callers that need the extension
            alone (e.g. temp-file naming or Monaco language inference).
    """

    id: str
    display_name: str
    source_filename: str
    monaco_language: str
    default_template: str
    needs_compile: bool
    compile_cmd: Optional[Callable[[Path, Path], list[str]]]
    run_cmd: Callable[[Path], list[str]]
    version_cmd: Callable[[], list[str]]
    toolchain_bin_dir: Callable[[], str]
    build_artefact: Callable[[Path], Optional[Path]]
    rss_allowance_mb: Callable[[], int]

    @property
    def source_suffix(self) -> str:
        """File extension of :attr:`source_filename`, including the dot."""
        return Path(self.source_filename).suffix

    def source_path(self, work_dir: Path) -> Path:
        """Path the source must be written to inside *work_dir*."""
        return Path(work_dir) / self.source_filename


# --------------------------------------------------------------- templates

_CPP_TEMPLATE = """\
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    // your code here

    return 0;
}
"""

_PYTHON_TEMPLATE = '''\
import sys


def main() -> None:
    data = sys.stdin.read().split()
    # your code here


if __name__ == "__main__":
    main()
'''

# Java 8 syntax only: no `var`, no switch expressions, no text blocks.
# The configured JDK here is 1.8.0_291, and a template that does not
# compile is worse than no template.
_JAVA_TEMPLATE = """\
import java.io.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = br.readLine()) != null) {
            sb.append(line).append(' ');
        }

        StringTokenizer st = new StringTokenizer(sb.toString());
        if (!st.hasMoreTokens()) {
            return;
        }
        // long a = Long.parseLong(st.nextToken());

        PrintWriter out = new PrintWriter(
            new BufferedWriter(new OutputStreamWriter(System.out)));
        // your code here
        out.flush();
    }
}
"""


# ------------------------------------------------------------- spec builders


def _compile_cpp(source: Path, work_dir: Path) -> list[str]:
    """``g++ main.cpp -o main.exe -std=c++17``.

    The ``-o`` target lives in *work_dir* so the sandbox owns every
    artefact it creates. The extension is platform-dependent: MinGW
    produces ``.exe``, POSIX does not.
    """
    exe = _cpp_executable(work_dir)
    return [
        settings.cpp_compiler_path,
        str(source),
        "-o",
        str(exe),
        f"-std={settings.cpp_std}",
    ]


def _cpp_executable(work_dir: Path) -> Path:
    """Location of the binary :func:`_compile_cpp` produces."""
    suffix = ".exe" if os.name == "nt" else ""
    return Path(work_dir) / f"main{suffix}"


def _run_cpp(work_dir: Path) -> list[str]:
    return [str(_cpp_executable(work_dir))]


def _compile_python(source: Path, _work_dir: Path) -> list[str]:
    """``python -m py_compile main.py`` — a syntax check, not a build.

    Python has no compile phase, but without this a syntax error would be
    reported as a runtime failure, so the CE verdict would never be
    reachable for Python submissions. ``py_compile`` writes ``__pycache__``
    next to the source; since the source lives inside the per-run work
    directory, that cache is cleaned up with it.
    """
    return [settings.resolved_python_interpreter, "-m", "py_compile", str(source)]


def _run_python(work_dir: Path) -> list[str]:
    return [
        settings.resolved_python_interpreter,
        str(Path(work_dir) / PYTHON_SPEC.source_filename),
    ]


def _compile_java(source: Path, work_dir: Path) -> list[str]:
    """``javac -d <work_dir> Main.java``.

    ``-d`` is explicit rather than omitted: without it ``javac`` drops the
    ``.class`` files beside the source, and the run step would then depend
    on an implicit layout. Pinning the output directory keeps compile and
    run in agreement through one variable.
    """
    return [settings.java_compiler_path, "-d", str(work_dir), str(source)]


def _run_java(work_dir: Path) -> list[str]:
    """``java -Xmx<heap>m -cp <work_dir> Main``.

    The heap cap matters for MLE: the runner measures resident memory of
    the process it spawned, but a JVM reserves tens of megabytes before the
    program does anything, so the OS-level ceiling is a poor signal. A
    bounded heap makes the JVM itself fail with ``OutOfMemoryError`` at a
    predictable point instead.
    """
    argv = [
        settings.java_runtime_path,
        f"-Xmx{settings.java_max_heap_mb}m",
        "-cp",
        str(work_dir),
        "Main",
    ]
    return argv


def _version_cpp() -> list[str]:
    return [settings.cpp_compiler_path, "--version"]


def _version_python() -> list[str]:
    return [settings.resolved_python_interpreter, "--version"]


def _version_java() -> list[str]:
    # javac is the right probe target: a JRE has `java` but no `javac`,
    # and a JRE cannot compile anything, so it cannot judge Java at all.
    return [settings.java_compiler_path, "-version"]


# ----------------------------------------------------- environment builders


def _bin_dir_of(command: str) -> str:
    """Parent directory of *command*, or ``''`` when it is a bare name.

    A bare name is resolved through ``PATH`` by the OS, so there is no
    directory the sandbox could usefully prepend.
    """
    if not command or (os.sep not in command and "/" not in command):
        return ""
    return str(Path(command).parent)


def _bin_dir_cpp() -> str:
    """PATH hint for g++, honouring an explicit ``CPP_COMPILER_BIN_DIR``.

    Delegates to :attr:`Settings.resolved_compiler_bin_dir` rather than
    re-deriving it from the compiler path, because that property exists
    precisely to cover the case where the MinGW runtime DLLs live
    somewhere other than beside ``g++``.
    """
    return settings.resolved_compiler_bin_dir


def _bin_dir_python() -> str:
    """PATH hint for the interpreter, derived from the configured path.

    Usually empty: :attr:`Settings.resolved_python_interpreter` falls back
    to ``sys.executable``, which is absolute.
    """
    return _bin_dir_of(settings.resolved_python_interpreter)


def _bin_dir_java() -> str:
    """PATH hint for the JDK, so a bare ``java``/``javac`` can resolve.

    This is what makes ``JAVA_RUNTIME_PATH=java`` work on a machine whose
    JDK is not on ``PATH`` but whose compiler path is configured.
    """
    return settings.resolved_java_bin_dir


def _artefact_cpp(work_dir: Path) -> Optional[Path]:
    """The binary ``-o`` points g++ at."""
    return _cpp_executable(work_dir)


def _artefact_python(_work_dir: Path) -> Optional[Path]:
    """``None`` — a Python submission is executed from its source file.

    ``py_compile`` does produce ``__pycache__``, but the run step never
    loads it (the source is passed to the interpreter directly), so there
    is no artefact whose path means anything to a caller.
    """
    return None


def _artefact_java(_work_dir: Path) -> Optional[Path]:
    """``None`` — Java compiles to a class *tree*, not one file.

    ``javac -d`` may emit several ``.class`` files for a single source
    file, and the run step consumes them through ``-cp``. Naming one of
    them here would be a convenient lie.
    """
    return None


def _rss_allowance_none() -> int:
    """No headroom: for these languages RSS tracks the program's own use."""
    return 0


def _rss_allowance_java() -> int:
    """Headroom covering the JVM's non-heap footprint.

    The runner decides MLE by polling the process's resident memory, but
    for a JVM that number is dominated by things the submission does not
    control: metaspace, the code cache, GC structures, thread stacks. Even
    an empty ``main`` sits well above zero, so polling a JVM against the
    same ceiling used for C++ produces false MLE verdicts on correct code.

    The real memory bound for Java is the ``-Xmx`` already baked into
    :func:`_run_java`. This allowance only stops the OS-level check from
    firing first — it is the "relax Java's memory verdict" half of the
    PHASE-UX decision, paired with ``-Xmx`` on the other half.

    Known limitation: a Java submission that exhausts its heap dies with
    ``OutOfMemoryError`` and is therefore classified RE, not MLE. Making
    that an MLE verdict would mean reading JVM stderr for a language-
    specific string inside the generic classifier, which is a worse trade
    than an honest RE with the cause visible in ``stderr``.
    """
    return settings.java_rss_allowance_mb


# ------------------------------------------------------------ the registry

PYTHON_SPEC = LanguageSpec(
    id="python",
    display_name="Python",
    source_filename="main.py",
    monaco_language="python",
    default_template=_PYTHON_TEMPLATE,
    needs_compile=True,  # via py_compile; see the field docs
    compile_cmd=_compile_python,
    run_cmd=_run_python,
    version_cmd=_version_python,
    toolchain_bin_dir=_bin_dir_python,
    build_artefact=_artefact_python,
    rss_allowance_mb=_rss_allowance_none,
)

CPP_SPEC = LanguageSpec(
    id="cpp",
    display_name="C++",
    source_filename="main.cpp",
    monaco_language="cpp",
    default_template=_CPP_TEMPLATE,
    needs_compile=True,
    compile_cmd=_compile_cpp,
    run_cmd=_run_cpp,
    version_cmd=_version_cpp,
    toolchain_bin_dir=_bin_dir_cpp,
    build_artefact=_artefact_cpp,
    rss_allowance_mb=_rss_allowance_none,
)

JAVA_SPEC = LanguageSpec(
    id="java",
    display_name="Java",
    source_filename="Main.java",
    monaco_language="java",
    default_template=_JAVA_TEMPLATE,
    needs_compile=True,
    compile_cmd=_compile_java,
    run_cmd=_run_java,
    version_cmd=_version_java,
    toolchain_bin_dir=_bin_dir_java,
    build_artefact=_artefact_java,
    rss_allowance_mb=_rss_allowance_java,
)

#: Language used when a request does not specify one. Kept as C++ to match
#: the pre-PU-16 behaviour of every existing caller and test.
DEFAULT_LANGUAGE = CPP_SPEC.id

# Insertion order is the display order the UI should use; C++ first
# because it is the default.
LANGUAGES: dict[str, LanguageSpec] = {
    CPP_SPEC.id: CPP_SPEC,
    PYTHON_SPEC.id: PYTHON_SPEC,
    JAVA_SPEC.id: JAVA_SPEC,
}


def get_language(language_id: Optional[str] = None) -> LanguageSpec:
    """Look up a descriptor by id (case-insensitive, surrounding space ignored).

    Args:
        language_id: Canonical id such as ``"cpp"``. ``None`` or an empty
            string selects :data:`DEFAULT_LANGUAGE`, so callers that do
            not care about language need not special-case it.

    Returns:
        The matching :class:`LanguageSpec`.

    Raises:
        UnknownLanguageError: The id is not registered. The message lists
            every valid id, because this id comes from request bodies and
            a bare ``KeyError('cppp')`` is not actionable.

    Note:
        No aliases are accepted (``"c++"``, ``"py"``, ``"python3"`` all
        fail). One canonical spelling per language keeps the wire format
        unambiguous; the UI is expected to send what ``env-check`` returns.
    """
    if language_id is None or not str(language_id).strip():
        return LANGUAGES[DEFAULT_LANGUAGE]

    if not isinstance(language_id, str):
        raise UnknownLanguageError(language_id, list(LANGUAGES))

    key = language_id.strip().lower()
    try:
        return LANGUAGES[key]
    except KeyError:
        raise UnknownLanguageError(language_id, list(LANGUAGES)) from None


def all_languages() -> list[LanguageSpec]:
    """Every registered descriptor, in display order."""
    return list(LANGUAGES.values())


def language_ids() -> list[str]:
    """Every registered id, in display order."""
    return list(LANGUAGES)


__all__ = [
    "DEFAULT_LANGUAGE",
    "JAVA_SPEC",
    "LANGUAGES",
    "CPP_SPEC",
    "PYTHON_SPEC",
    "LanguageSpec",
    "UnknownLanguageError",
    "all_languages",
    "get_language",
    "language_ids",
]
