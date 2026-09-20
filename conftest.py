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

#: 测试用的进程凭证（``SIDECAR_TOKEN``）。
#:
#: 侧车要求每个请求都带 ``X-Sidecar-Token``（见 ``app/security.py``），所以
#: 每一个 ``TestClient`` 都必须出示它。凭证值本身无关紧要 —— 测试关心的是
#: 「带了才放行」，不是某个具体字符串 —— 但所有测试必须用**同一个**值，否则
#: 每个文件都会去猜别的模块用的是什么。
#:
#: 它同时被下面的 autouse fixture 写进 ``settings``，让侧车认这个凭证有效。
#: 只有这两件事一起做，测试才既过得了守卫又不是在测一个被关掉的守卫。
TEST_SIDECAR_TOKEN = "test-sidecar-token"

#: 传给 ``TestClient(..., headers=...)`` 的请求头。
TEST_SIDECAR_HEADERS = {"X-Sidecar-Token": TEST_SIDECAR_TOKEN}

#: 断言「程序是对的」的测试给判题的时间上限。
#:
#: **不是** 5 秒，因为在这台机器上**启动一个进程就要 5 秒以上**：
#: ``subprocess.run(['whoami'])`` 实测 5.2 s，而进程还没开始跑。任何小于这个
#: 数的上限都会让正确的程序报 TLE —— 判题器没错，是机器慢。2026-09-19 的
#: 全量运行里 8 个用例就是这样红的（``stderr: Time Limit Exceeded (>5.0s)``，
#: ``memory_used=6``，``exit_code=-1``：程序根本没起来）。
#:
#: 所以这个上限只是**预算**，不是被测对象：断言的是「正确代码判 AC / 错误代码
#: 判 WA」，而「超时会被杀」由 ``test_timeout`` 和 ``test_judge_tle`` 用 1 秒
#: 的上限各自覆盖。一个跑不满 30 秒的机器不会因为这里的 30 而变慢 —— 上限只在
#: 程序真的卡住时才用得上。
#:
#: 如果哪天 ``time whoami`` 回到几十毫秒，这个注释就该删掉，上限也能降回去。
EXEC_TIME_LIMIT = 30.0


@pytest.fixture(autouse=True)
def _fixed_sidecar_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the sidecar a known process credential for the test's duration.

    ``SIDECAR_TOKEN`` is blank in ``.env``, and a blank token means "make
    one up" — so without this the value the server compares against would
    differ from anything a test could send, and every route would answer
    401 regardless of the header. Pinning it is what makes
    {@link TEST_SIDECAR_HEADERS} worth sending.

    ``autouse`` because the guard is on every route: a module that
    forgot this would not fail in one place, it would fail in all of them.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "sidecar_token", TEST_SIDECAR_TOKEN)


@pytest.fixture(autouse=True)
def _no_real_submission_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the judge routes from writing to a real database.

    Since PU-13 the judge records every verdict by calling
    ``app.records.record_submission``, whose default session comes from
    ``app.records_session.get_session`` — one binding over
    ``app.db.get_session``, deliberately the only one, so a test can
    redirect the whole write path by replacing it in that single place.
    Unredirected it opens an engine against the documented PostgreSQL DSN,
    which is not running here, so a judge test that does not opt out would
    hang on a connection instead of failing.

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

    S7's team module is patched too, and it is the one that would bite:
    it imports ``chat_completion`` into its own namespace, so patching
    ``analyzer``'s copy does not reach it, and its two worker agents make
    a call each. A test that exercised the team without patching would go
    to a real hosted endpoint — which is precisely the failure this
    fixture exists to make impossible rather than merely unlikely.

    S10's ``guardrails`` holds ``complete`` for the same reason, and it is
    the *second* module with that shape: its whole job is to call the
    gateway, so a guardrail test that forgot to inject a fake caller would
    otherwise make a real, billed request — and with a retry policy
    attached it would make several.

    ``autouse`` because a module that forgets it does not fail — it just
    gets slower and flakier, and that is the failure mode nobody notices.
    """
    from app.agent import analyzer, guardrails, team, tool_caller
    from app.llm.gateway import LLMConfigError

    def _refuse(*args: object, **kwargs: object) -> None:
        raise LLMConfigError(
            "LLM calls are disabled during tests; patch the call site to "
            "script a reply instead (see conftest._no_real_llm_calls)"
        )

    monkeypatch.setattr(analyzer, "chat_completion", _refuse)
    monkeypatch.setattr(team, "chat_completion", _refuse)
    monkeypatch.setattr(tool_caller, "complete", _refuse)
    monkeypatch.setattr(guardrails, "complete", _refuse)

    # Turn the guardrail chain off for the suite.
    #
    # `settings.llm_guardrails` picks, at call time, between the gateway and
    # `guarded_chat_completion` inside `analyzer` / `team`. Left on, every
    # `patch.object(analyzer, "chat_completion", ...)` in these tests lands
    # on a name the code no longer reads: the call goes to the guardrails,
    # which reach `guardrails.complete` — refused above — instead of
    # returning the scripted reply. The tests then fail for a reason that
    # has nothing to do with what they assert.
    #
    # None of the three layers is wanted here anyway: redaction would
    # rewrite the prompts the tests script against, the fallback chain
    # would hide which provider answered, and retries would turn one
    # expected failure into several. The wiring itself is covered by
    # `tests/test_guardrail_wiring.py`, which switches it on deliberately.
    from app.config import settings

    monkeypatch.setattr(settings, "llm_guardrails", False)

    # S8: keep the suite off PostgreSQL too.
    #
    # `sidecar/.env` may carry `LANGGRAPH_CHECKPOINT_URI`, and on a machine
    # that *does* have `langgraph-checkpoint-postgres` installed that is
    # enough for `memory.build_checkpointer()` to try to connect. A suite
    # that needs a database to pass is the same problem as one that needs
    # a hosted model: it fails to run on someone else's laptop for a
    # reason unrelated to what is under test.
    #
    # Tests that exercise the PostgreSQL branch set the URI themselves
    # (in the body, so their value lands on top of this one) and inject a
    # fake backend rather than reaching a real server.
    monkeypatch.setattr(settings, "langgraph_checkpoint_uri", "")


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
