"""Environment probing for the ``/api/v1/env-check`` endpoint (PU-3).

The desktop app calls this once at startup to learn which language
toolchains are actually usable on this machine, so the UI can disable
options it cannot honour instead of failing at judge time.

Why this module lives in ``sandbox/`` rather than ``api/``:
AGENTS.md section 4 says ``api/`` must not hold business logic — routes
should stay thin and delegate. Probing toolchains is sandbox concern.

PU-15: probing is now driven by the ``LanguageSpec`` descriptors in
``sandbox/languages.py``. Each probe runs exactly the version command the
sandbox itself would run, resolved from the same configuration, so the
self-check cannot drift away from what judging actually does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text

from app.config import settings
from app.db import _resolve_sync_url
from app.sandbox.compiler import sandbox_tmp_base as _sandbox_tmp_base
from app.sandbox.languages import (
    CPP_SPEC,
    JAVA_SPEC,
    PYTHON_SPEC,
    LanguageSpec,
    all_languages,
)

# Version probes are short-lived; a hung compiler should not stall startup.
_PROBE_TIMEOUT = 5

# Database probes get a tight bound: env-check runs on every launch and
# must not hang for the OS-level TCP timeout when Postgres is down.
_DB_CONNECT_TIMEOUT = 3


@dataclass
class ProbeResult:
    """Outcome of probing one capability.

    ``available`` is the only field callers should branch on. ``detail``
    carries a human-readable reason when the probe failed, so the UI can
    explain *why* (missing on PATH vs. present but broken).
    """

    id: str
    display_name: str
    available: bool
    version: Optional[str] = None
    path: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "available": self.available,
            "version": self.version,
            "path": self.path,
            "detail": self.detail,
        }


def _run_version_command(cmd: list[str]) -> tuple[bool, Optional[str], Optional[str]]:
    """Run *cmd* and return ``(ok, first_output_line, error_detail)``.

    Compilers are inconsistent about which stream they use: ``g++`` and
    ``python`` write ``--version`` to stdout, while ``javac``/``java``
    write it to stderr. Both are captured, so the caller does not need
    to care.

    ``errors="replace"`` guards against a GBK-locale Windows toolchain
    emitting undecodable bytes — the probe must never raise.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT,
        )
    except FileNotFoundError:
        return False, None, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return False, None, f"timed out after {_PROBE_TIMEOUT}s"
    except OSError as exc:  # e.g. permission denied
        return False, None, f"failed to launch: {exc}"
    except BaseException as exc:  # noqa: BLE001
        # Same reasoning as probe_sandbox_tmp: a hook or signal that raises a
        # BaseException while spawning a compiler must degrade to "this one
        # language is unavailable", not crash /env-check and make the whole
        # engine look dead. One broken toolchain is not a broken app.
        return False, None, f"probe interrupted: {type(exc).__name__}: {exc}"

    combined = ((proc.stdout or "") + (proc.stderr or "")).strip()
    first_line = combined.splitlines()[0].strip() if combined else None

    if proc.returncode != 0 and not first_line:
        return False, None, f"exit code {proc.returncode}"
    return True, first_line, None


def _resolve_executable(command: str) -> Optional[str]:
    """Best-effort absolute path for *command* (which may already be one)."""
    if os.sep in command or "/" in command:
        return command if Path(command).exists() else None
    return shutil.which(command)


# Language-specific guidance appended when a toolchain cannot be run.
# A bare "not found" tells the user nothing they can act on; naming the
# setting to change turns a red badge into a fix.
_INSTALL_HINTS = {
    "cpp": "Install a C++ toolchain (MinGW-w64) and point CPP_COMPILER_PATH at it.",
    "python": "Point PYTHON_INTERPRETER_PATH at a working interpreter.",
    "java": (
        "A JDK is required — a JRE cannot compile. Install a JDK and point "
        "JAVA_COMPILER_PATH at its bin\\javac."
    ),
}


def _probe_language(spec: LanguageSpec) -> ProbeResult:
    """Probe one language by running the version command from its spec.

    Driving this from the descriptor rather than from a hardcoded command
    is what makes a *configured* toolchain discoverable: the probe runs
    exactly the tool the sandbox would run, resolved from the same setting.
    Before PU-15 the Java probe only searched PATH, so a machine with a
    perfectly good JDK in a non-standard location reported Java as
    unusable even when the sandbox could have used it.
    """
    argv = spec.version_cmd()
    tool = argv[0]

    resolved = _resolve_executable(tool)
    if resolved is None:
        return ProbeResult(
            id=spec.id,
            display_name=spec.display_name,
            available=False,
            version=None,
            path=None,
            detail=f"{tool} not found. {_INSTALL_HINTS[spec.id]}",
        )

    ok, version, error = _run_version_command(argv)
    return ProbeResult(
        id=spec.id,
        display_name=spec.display_name,
        available=ok,
        version=version if ok else None,
        path=resolved,
        detail=None if ok else f"{error or 'not runnable'}. {_INSTALL_HINTS[spec.id]}",
    )


def probe_cpp() -> ProbeResult:
    """Probe the configured C++ compiler."""
    return _probe_language(CPP_SPEC)


def probe_python() -> ProbeResult:
    """Probe the interpreter used to run Python submissions.

    Defaults to the interpreter running the sidecar itself. That is the
    one interpreter guaranteed to exist in this deployment (the venv the
    sidecar was started from), so it is the honest default rather than a
    guess at some system Python. ``PYTHON_INTERPRETER_PATH`` overrides it
    when submissions must run on a different interpreter.
    """
    return _probe_language(PYTHON_SPEC)


def probe_java() -> ProbeResult:
    """Probe the configured JDK (``javac`` — compiling needs a full JDK).

    ``javac`` is the right probe target, not ``java``: every JRE ships a
    ``java`` launcher and still cannot compile a single submission.
    """
    return _probe_language(JAVA_SPEC)


def sandbox_tmp_base() -> Path:
    """Directory that sandbox temporary files are created under.

    Re-exported from :mod:`app.sandbox.compiler`, which owns the policy
    because that is where the sandbox's own scratch directories are made.
    PU-16 folded the two copies together as the NOTE left here by PU-3
    asked: ``judge`` and ``compile_and_run`` had been resolving the
    location independently, and ``compile_and_run`` had been picking the
    OS default — the non-ASCII path that breaks MinGW, so every C++
    compile it attempted failed. There is now exactly one computation, and
    this probe therefore reports on the directory the sandbox really uses
    rather than a lookalike.

    The name is kept because this module's public surface is what
    ``/api/v1/env-check`` and its tests refer to.
    """
    return _sandbox_tmp_base()


def probe_sandbox_tmp() -> ProbeResult:
    """Verify the sandbox temp directory can actually be written to.

    A read-only or missing temp dir is a silent judge-breaker: every
    submission would fail at compile time with an opaque linker error.
    Checking up front turns that into an actionable startup message.

    **Why the cleanup is separated from the write.** The old version did
    write-then-``unlink`` inside one ``try``. The write is what this probe
    is actually about; the ``unlink`` is bookkeeping. When something in
    the environment hijacks deletion -- a delete-guard hook, an A/V
    quarantine -- that hook raises ``SystemExit``, which is a
    ``BaseException`` and therefore slips straight through ``except
    OSError`` and out of the endpoint as a 500. The whole app then looks
    down because a *cleanup* step failed, which is both wrong and
    spectacularly unhelpful to diagnose.

    Splitting them means: the write decides availability, and a failed
    cleanup is reported in ``detail`` without changing the verdict.
    """
    base = sandbox_tmp_base()
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe_file = base / ".algoforge_write_probe"
        probe_file.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return ProbeResult(
            id="sandbox_tmp",
            display_name="Sandbox temp dir",
            available=False,
            version=None,
            path=str(base),
            detail=f"not writable: {exc}",
        )

    # `BaseException` on purpose. A delete hook that calls `sys.exit()` or
    # raises `KeyboardInterrupt` must not be able to take down the probe:
    # the directory *was* written successfully, so the sandbox is usable
    # and the leftover file is harmless (the judge clears its scratch dir
    # between runs).
    leftover: Optional[str] = None
    try:
        probe_file.unlink()
    except BaseException as exc:  # noqa: BLE001 - see the docstring
        leftover = f"could not remove probe file: {type(exc).__name__}: {exc}"

    return ProbeResult(
        id="sandbox_tmp",
        display_name="Sandbox temp dir",
        available=True,
        version=None,
        path=str(base),
        detail=leftover,
    )


def probe_database() -> ProbeResult:
    """Check that the configured database accepts a connection.

    Reports the dialect from the URL and then performs a real
    ``SELECT 1`` with a short connect timeout. ``available=False`` with
    a ``detail`` is the expected result when Postgres is not running —
    that is useful information, not an error to raise.
    """
    url = settings.database_url or ""
    dialect = url.split("://", 1)[0].split("+", 1)[0] if "://" in url else "unknown"

    if not url:
        return ProbeResult(
            id="database",
            display_name="Database",
            available=False,
            version=None,
            path=None,
            detail="DATABASE_URL is empty",
        )

    # Reuse the data layer's URL rewriting so env-check probes exactly
    # the DSN the app itself would use (asyncpg -> psycopg on Postgres).
    # _resolve_sync_url is private to app.db, but duplicating its rewrite
    # table here would be worse than importing it.
    sync_url = _resolve_sync_url(url)
    connect_args: dict = {}
    if dialect == "postgresql":
        connect_args["connect_timeout"] = _DB_CONNECT_TIMEOUT

    try:
        engine = create_engine(sync_url, connect_args=connect_args)
    except Exception as exc:  # bad driver / malformed URL
        return ProbeResult(
            id="database",
            display_name="Database",
            available=False,
            version=dialect,
            path=None,
            detail=f"could not build engine: {exc}",
        )

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return ProbeResult(
            id="database",
            display_name="Database",
            available=False,
            version=dialect,
            path=None,
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    finally:
        engine.dispose()

    return ProbeResult(
        id="database",
        display_name="Database",
        available=True,
        version=dialect,
        path=None,
        detail=None,
    )


def _language_entry(spec: LanguageSpec) -> dict:
    """Probe *spec* and attach the descriptor fields the editor needs.

    PU-17 needs more than "is Java available": the UI has to offer a choice
    of language, which means it needs each language's Monaco id (so the
    editor highlights correctly) and its starter template (so switching
    language fills the buffer with something valid).

    Those two fields are attached here, at report-assembly time, rather
    than being stored on :class:`ProbeResult`. A probe result is strictly
    the outcome of probing; the template is a property of the language, and
    widening the dataclass to carry it would make ``probe_database()``
    return a structure with two permanently-null template fields.

    Carrying the template on the wire rather than hardcoding it in the
    renderer is deliberate: ``LanguageSpec.default_template`` has to stay
    within what the configured toolchain accepts (the Java template is
    Java 8 syntax because the JDK here is 1.8.0_291), and a second copy in
    TypeScript could not be kept honest against that.
    """
    entry = _probe_language(spec).to_dict()
    entry["monaco_language"] = spec.monaco_language
    entry["default_template"] = spec.default_template
    return entry


def probe_all() -> dict:
    """Probe every capability and return a JSON-serialisable report.

    Never raises: each probe degrades into ``available=False`` plus a
    ``detail`` explaining why. A startup check that can itself fail is
    not much of a startup check.

    The language list comes from the registry, so registering a fourth
    language extends this report with no change here.
    """
    languages = [_language_entry(spec) for spec in all_languages()]
    return {
        "languages": languages,
        "database": probe_database().to_dict(),
        "sandbox_tmp": probe_sandbox_tmp().to_dict(),
        # Convenience for the UI: everything required for the core
        # (edit -> judge) loop to work.
        "core_ready": any(entry["available"] for entry in languages),
    }
