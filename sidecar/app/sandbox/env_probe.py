"""Environment probing for the ``/api/v1/env-check`` endpoint (PU-3).

The desktop app calls this once at startup to learn which language
toolchains are actually usable on this machine, so the UI can disable
options it cannot honour instead of failing at judge time.

Why this module lives in ``sandbox/`` rather than ``api/``:
AGENTS.md section 4 says ``api/`` must not hold business logic — routes
should stay thin and delegate. Probing toolchains is sandbox concern.

PU-15 note: this probing will be driven by ``LanguageSpec`` descriptors
(``sandbox/languages.py``) once that lands. For now the probe table
below is the single place that knows about compiler invocations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text

from app.config import settings
from app.db import _resolve_sync_url

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


def probe_cpp() -> ProbeResult:
    """Probe the configured C++ compiler."""
    compiler = settings.cpp_compiler_path
    ok, version, error = _run_version_command([compiler, "--version"])
    resolved = _resolve_executable(compiler)
    return ProbeResult(
        id="cpp",
        display_name="C++",
        available=ok,
        version=version if ok else None,
        path=resolved,
        detail=None if ok else (error or "compiler not runnable"),
    )


def probe_python() -> ProbeResult:
    """Probe the Python interpreter used to run Python submissions.

    Defaults to the interpreter running the sidecar itself. That is the
    one interpreter guaranteed to exist in this deployment (the venv the
    sidecar was started from), so it is the honest default rather than a
    guess at some system Python.

    PU-15 will introduce an explicit ``python_interpreter_path`` setting
    so submissions can be pointed at a separate interpreter.
    """
    interpreter = sys.executable
    ok, version, error = _run_version_command([interpreter, "--version"])
    return ProbeResult(
        id="python",
        display_name="Python",
        available=ok,
        version=version if ok else None,
        path=interpreter,
        detail=None if ok else (error or "interpreter not runnable"),
    )


def probe_java() -> ProbeResult:
    """Probe for a JDK (``javac`` — compilation needs the full JDK).

    On this machine the only JDK is 1.8.0_291 at
    ``C:\\Program Files\\Java\\jdk1.8.0_291`` and it is **not on PATH**,
    so this probe reports unavailable until PU-15 wires an explicit
    ``java_compiler_path`` setting. That is intentional: PU-3 only
    reports reality, it does not install or configure a JDK.
    """
    javac = "javac"
    resolved = shutil.which(javac)

    if resolved is None:
        return ProbeResult(
            id="java",
            display_name="Java",
            available=False,
            version=None,
            path=None,
            detail=(
                "javac not found on PATH. A JDK is required (a JRE alone "
                "cannot compile). Set JAVA_COMPILER_PATH once PU-15 lands."
            ),
        )

    ok, version, error = _run_version_command([javac, "-version"])
    return ProbeResult(
        id="java",
        display_name="Java",
        available=ok,
        version=version if ok else None,
        path=resolved,
        detail=None if ok else (error or "javac not runnable"),
    )


def sandbox_tmp_base() -> Path:
    """Directory that sandbox temporary files are created under.

    On Windows this is ``C:\\Windows\\Temp`` rather than the OS default,
    because MinGW's linker cannot open output files on non-ASCII paths
    and this machine's user profile contains CJK characters ("雪团子").

    NOTE: ``sandbox/judge.py`` carries an identical inline computation.
    That copy should be folded into this function once PU-15/PU-16 touch
    the judge pipeline — it is left alone here to keep PU-3's diff tight.
    """
    if os.name == "nt":
        return Path(r"C:\Windows\Temp") / "algoforge_judge"
    return Path(os.environ.get("TMPDIR", "/tmp")) / "algoforge_judge"


def probe_sandbox_tmp() -> ProbeResult:
    """Verify the sandbox temp directory can actually be written to.

    A read-only or missing temp dir is a silent judge-breaker: every
    submission would fail at compile time with an opaque linker error.
    Checking up front turns that into an actionable startup message.
    """
    base = sandbox_tmp_base()
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe_file = base / ".algoforge_write_probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
    except OSError as exc:
        return ProbeResult(
            id="sandbox_tmp",
            display_name="Sandbox temp dir",
            available=False,
            version=None,
            path=str(base),
            detail=f"not writable: {exc}",
        )

    return ProbeResult(
        id="sandbox_tmp",
        display_name="Sandbox temp dir",
        available=True,
        version=None,
        path=str(base),
        detail=None,
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


def probe_all() -> dict:
    """Probe every capability and return a JSON-serialisable report.

    Never raises: each probe degrades into ``available=False`` plus a
    ``detail`` explaining why. A startup check that can itself fail is
    not much of a startup check.
    """
    languages = [probe_cpp(), probe_python(), probe_java()]
    return {
        "languages": [p.to_dict() for p in languages],
        "database": probe_database().to_dict(),
        "sandbox_tmp": probe_sandbox_tmp().to_dict(),
        # Convenience for the UI: everything required for the core
        # (edit -> judge) loop to work.
        "core_ready": any(p.available for p in languages),
    }
