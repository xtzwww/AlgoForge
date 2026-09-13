"""Root pytest configuration.

Adds the ``sidecar`` directory to ``sys.path`` so that the ``app``
package can be imported from tests located at the repository root.
Also provides shared fixtures used across sandbox test modules.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

SIDECAR_DIR = Path(__file__).parent / "sidecar"
if str(SIDECAR_DIR) not in sys.path:
    sys.path.insert(0, str(SIDECAR_DIR))


@pytest.fixture
def ascii_tmp_path() -> Path:
    """A per-test temp directory on an ASCII-only path.

    pytest's default ``tmp_path`` lives under the user profile, which
    on this machine contains non-ASCII characters. MinGW's ``ld.exe``
    cannot open output files on non-ASCII paths, so we place test
    sources under ``C:\\Windows\\Temp`` (guaranteed ASCII, writable by
    all users on Windows) instead.

    Shared by ``test_compiler.py`` (P1-11) and ``test_runner.py``
    (P1-12) so both can compile and run C++ fixtures without hitting
    the non-ASCII-path linker failure.
    """
    base = Path(r"C:\Windows\Temp") / "algoforge_test"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=str(base)))
    yield d
    shutil.rmtree(str(d), ignore_errors=True)
