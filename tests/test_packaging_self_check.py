"""Tests for the packaging entry point's ``--self-check``.

Why this file exists
--------------------
``sidecar/packaging/build_sidecar.sh`` declares five modules with
``--hidden-import`` precisely because the source only imports them **inside
functions** (the graph on the ``graph``/``team`` branches, ``pymilvus`` when the
retriever really connects, the PG checkpoint/store backends when a PG URI is
configured). PyInstaller does follow in-function imports, so today they are
bundled anyway — but that is incidental, and when it stops being true the
failure is silent: the build stays green and the ``ImportError`` only appears in
a packaged deployment.

The declaration lives in the shell script and the check lives in the entry
point, so the two can drift apart without anything going red. The first test
below is what stops that.

``packaging/`` is deliberately not an importable package — it holds a
PyInstaller entry *script*, not app code — so the module is loaded by path.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENTRY = REPO / "sidecar" / "packaging" / "sidecar_main.py"
BUILD_SCRIPT = REPO / "sidecar" / "packaging" / "build_sidecar.sh"


@lru_cache(maxsize=1)
def _entry():
    """Load ``sidecar_main.py`` under a name that is not ``__main__``.

    The name matters: the module ends with ``if __name__ == "__main__":`` and
    importing it as ``__main__`` would start uvicorn.
    """
    spec = importlib.util.spec_from_file_location("sidecar_main_under_test", ENTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_in_build_script() -> list[str]:
    """Module names from ``build_sidecar.sh``'s ``LAZY_IMPORTS=( ... )``."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"LAZY_IMPORTS=\((.*?)\n\)", text, re.DOTALL)
    assert match, "build_sidecar.sh no longer declares a LAZY_IMPORTS array"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_every_declared_hidden_import_is_verified():
    """Anti-drift: the two lists must not part ways.

    The relation is *containment*, not equality, and the direction is what
    matters:

    * Every module ``build_sidecar.sh`` declares must also be verified here.
      Otherwise a declaration could be added, the build could succeed because
      PyInstaller happened to follow the import anyway, and the runtime check
      would keep passing while proving nothing about the new module.
    * The check may verify **more** than the script declares. It currently
      does: ``app.rag.retriever`` needs no declaration (``app.agent.graph``
      imports it statically), but it is worth checking by name because it is
      the module that owns the lazy ``pymilvus`` import — the one whose
      disappearance would be hardest to notice.

    Requiring exact equality would force a cosmetic entry into the build script
    for a module PyInstaller does not need a hint for, which is how a build
    script accumulates lines nobody can justify.
    """
    declared = set(_declared_in_build_script())
    checked = {name for _, name in _entry()._SELF_CHECK_MODULES}
    assert declared <= checked, f"declared but never verified: {declared - checked}"
    # Guard against the check being emptied out to satisfy the line above.
    assert len(checked) >= len(declared)
    assert checked, "the runtime check must verify something"


def test_every_self_check_module_imports():
    for _label, name in _entry()._SELF_CHECK_MODULES:
        importlib.import_module(name)


def test_self_check_reports_one_line_per_step_and_passes(capsys):
    """The check must actually report, not just return 0.

    A check that returns 0 without printing would be indistinguishable from a
    check that silently did nothing — which is the exact failure mode this
    project has hit three times with the ``env`` stub. So the output is
    asserted, not just the exit code.
    """
    entry = _entry()
    assert entry.self_check() == 0

    out = capsys.readouterr().out
    expected = len(entry._SELF_CHECK_MODULES) + 1  # modules + graph build
    assert out.count("OK") == expected
    assert "FAIL" not in out
    assert f"{expected}/{expected}" in out


def test_parse_args_still_ignores_unknown_flags():
    """``--self-check`` is intercepted in ``main()``, not taught to the parser.

    The parser's "ignore what you do not know" contract is what lets the
    Electron caller pass flags this build has never heard of. Teaching it
    ``--self-check`` would mean a flag the desktop app must never send, so the
    flag is handled before the parser instead — and this pins that split.
    """
    entry = _entry()
    assert entry.parse_args(["--self-check"]) == ("127.0.0.1", 0)
    assert entry.parse_args(["--self-check", "--port", "8123"]) == ("127.0.0.1", 8123)
    assert entry.parse_args(["--host", "0.0.0.0", "--port", "9"]) == ("0.0.0.0", 9)
