"""Fingerprint of the sidecar's own sources, published on ``/health``.

Why this exists
---------------
``start-web.bat`` probes ``/health`` and, when something answers, reuses
that process. The probe proves *a* backend is listening; it says nothing
about which revision of the code that process loaded. So editing ``app/``
had no visible effect until somebody killed the backend by hand, and the
symptom is a nasty one: a live server answers ``404`` for a route that
plainly exists on disk, which reads as a routing or frontend bug and is
neither.

Publishing a fingerprint lets the launcher compare "what this process
loaded" against "what is on disk" and restart when they differ. The
comparison lives in ``tools/sidecar-stale.mjs``; that file recomputes the
value defined here, so **the two must stay in step**.

What goes into the fingerprint
------------------------------
Every file under ``app/``, by POSIX-style relative path and raw content,
in sorted path order: ``sha256`` over ``<relpath>\\0<bytes>\\0`` per file,
hex, first :data:`STAMP_LENGTH` characters.

Content rather than mtime on purpose. A checkout, a copy, or an editor
that preserves timestamps all defeat an mtime comparison, and hashing ~100
small files once per process costs nothing. Sorting makes the value
independent of directory enumeration order, which differs between Windows
and Linux.

Deliberately excluded:

* ``__pycache__`` and ``.pyc`` / ``.pyo`` — build output that changes on
  every import.
* ``corpus/`` — data, not code. It lives outside ``app/`` and is anchored
  on ``app.config.DATA_ROOT`` (see :mod:`app.rag.documents`), so it is not
  even at a fixed path. Corpus edits are re-indexed or re-read on their own
  schedule; they must not make the launcher kill a healthy backend.

Prompt templates *are* included even though they are not Python. They live
under ``app/prompts/`` and are cached in-process (see
:func:`app.prompts.loader.load_prompt_cached`), so editing one really does
require a restart.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Root of the package this module belongs to — ``sidecar/app``.
APP_DIR: Path = Path(__file__).resolve().parent

#: Hex characters published. Truncated because the value is only ever
#: compared for equality — it is not a security boundary — and a short
#: string stays readable in a log line or a ``/health`` payload.
STAMP_LENGTH = 16

#: Directory skipped wholesale: build output, not source.
_EXCLUDED_DIR = "__pycache__"

#: Suffixes skipped: compiled output that sits beside its source.
_EXCLUDED_SUFFIXES = (".pyc", ".pyo")


def _source_files() -> list[Path]:
    """Every source file under :data:`APP_DIR`, sorted by relative path.

    Sorted by the POSIX form of the relative path so the order is the same
    on Windows and on Linux, and matches ``tools/sidecar-stale.mjs``.

    Symlinks are skipped, and suffixes are matched against the *name* rather
    than :attr:`Path.suffix` — both so this agrees with the Node side
    exactly. ``Path.suffix`` is empty for a file called ``.pyc``, which Node's
    ``endsWith`` would exclude; there is no such file today, but a silent
    disagreement means the launcher restarts a healthy backend forever.
    """
    return sorted(
        (
            path
            for path in APP_DIR.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and _EXCLUDED_DIR not in path.parts
            and not path.name.endswith(_EXCLUDED_SUFFIXES)
        ),
        key=lambda path: path.relative_to(APP_DIR).as_posix(),
    )


def current_code_stamp() -> str | None:
    """Fingerprint of the sources on disk *right now*.

    Returns:
        A hex digest, or ``None`` when it cannot be computed. A frozen
        build ships no readable ``.py`` files, and one unreadable file is
        not a reason to refuse to start. Callers must read ``None`` as
        "unknown" and prefer restarting: reusing a process whose revision
        is unknown is exactly the failure this module exists to prevent.
    """
    try:
        files = _source_files()
        if not files:
            return None
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(APP_DIR).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()[:STAMP_LENGTH]
