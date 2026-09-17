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


@pytest.fixture(autouse=True)
def _no_real_submission_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the judge routes from writing to a real database.

    Since PU-13 the judge records every verdict by calling
    ``app.records.record_submission``, whose default session comes from
    ``app.db.get_session``. That opens an engine against the documented
    PostgreSQL DSN — which is not running here — so a judge test that
    does not opt out would hang on a connection instead of failing.

    The replacement records nothing. Tests that *do* care about the write
    assert it through their own explicit hook (see
    ``tests/test_judge_recording.py``); this fixture only guarantees that
    the default is quiet.

    ``autouse`` because this is a correctness requirement for the suite as
    a whole, not an opt-in for individual tests — forgetting it in one
    module would make that module hang rather than fail.
    """
    from app.api import judge as judge_module
    from app.api import ws as ws_module

    def _skip(result: object, context: object, **kwargs: object) -> None:
        # ``**kwargs`` because P2-8's WS route passes ``on_event`` so the
        # Agent's tool calls can be streamed. A replacement that named only
        # the first two arguments would raise ``TypeError`` from inside the
        # route, turning "we did not record anything" into a judge error.
        return None

    monkeypatch.setattr(judge_module, "record_judge_outcome", _skip)
    monkeypatch.setattr(ws_module, "record_judge_outcome", _skip)


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the network — and off the bill.

    P2-4 wired error analysis into recording, so any test that records a
    *failing* submission carrying a ``problem_id`` reaches a real
    provider: ``sidecar/.env`` points at a hosted endpoint. P2-8 then made
    that path go through P2-7's tool loop, which may spend
    ``max_attempts * max_steps`` requests on a single submission. The
    result was a suite that took a quarter of an hour and needed an
    external service to go green — slow, unreproducible, and billed.

    The replacement raises ``LLMConfigError``, which is exactly what a
    misconfigured gateway raises and what both call sites already handle:
    ``analyzer._analyse_once`` catches it on the text-only path, and
    ``tool_caller.run_with_tools`` absorbs it on the tool path. The
    analysis therefore comes out ``ok=False`` while the recording under
    test proceeds unchanged — which is the behaviour these tests are
    actually about.

    Tests that want a *successful* analysis patch the same seam
    themselves, and because they do it inside the test body their patch
    lands on top of this one (see ``_stub_analysis`` in
    ``tests/test_agent_analysis_recording.py`` and the
    ``patch.object(analyzer, "chat_completion", ...)`` calls in
    ``tests/test_analyzer.py``).

    ``autouse`` because a module that forgets it does not fail — it just
    gets slower and flakier, and that is the failure mode nobody notices.
    """
    from app.agent import analyzer, tool_caller
    from app.llm.gateway import LLMConfigError

    def _refuse(*args: object, **kwargs: object) -> None:
        raise LLMConfigError(
            "LLM calls are disabled during tests; patch the call site to "
            "script a reply instead (see conftest._no_real_llm_calls)"
        )

    monkeypatch.setattr(analyzer, "chat_completion", _refuse)
    monkeypatch.setattr(tool_caller, "complete", _refuse)


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
