"""Tests for the S9 LangSmith tracing seam (:mod:`app.agent.tracing`).

The claim under test is narrower than "tracing works" — that would need a
key and a network call. What is pinned here is the part that can silently
go wrong on a machine with neither:

* with no key, nothing is wrapped and nothing is sent;
* with a key, the environment LangSmith actually reads gets set;
* the decorator still *works* — a traced function returns its value,
  which is the only thing the analysis path cares about.

``load_traceable`` is substituted rather than used for real, so no test
here reaches smith.langchain.com.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pytest

from app.agent import pipeline, tracing
from app.config import settings

#: The variables :func:`tracing.configure_tracing` writes.
_ENV_VARS = ("LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT")


@pytest.fixture
def _restore_env() -> Any:
    """Put the LangSmith variables back after the test.

    ``configure_tracing`` writes to the real ``os.environ``, and
    ``monkeypatch`` only restores names it was told about — so the
    snapshot is taken here rather than trusting that.
    """
    saved = {name: os.environ.get(name) for name in _ENV_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _fresh_state() -> Any:
    tracing.reset_for_tests()
    yield
    tracing.reset_for_tests()


# ------------------------------------------------------------------ the switch


def test_without_a_key_tracing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default. Tracing costs a background HTTP request per span, sent
    while a student waits — it must not happen unless asked for.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    assert tracing.tracing_enabled() is False


@pytest.mark.parametrize("key", ["   ", ""])
def test_a_blank_key_is_not_a_key(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.env.example`` ships these blank. A whitespace-only value would
    turn tracing on and then fail to authenticate, which is worse than
    off — the analysis would be fine and the traces would just vanish.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", key)
    assert tracing.tracing_enabled() is False


def test_a_key_turns_tracing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langsmith_api_key", "lsvt_test")
    assert tracing.tracing_enabled() is True


# -------------------------------------------------------------- the decorator


def test_tracing_off_never_looks_for_a_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No import of langsmith, no wrapper built, no HTTP. A process that
    never traces should not so much as reach for the tracer.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "")

    def _boom() -> Any:
        raise AssertionError("langsmith must not be loaded while tracing is off")

    monkeypatch.setattr(tracing, "load_traceable", _boom)

    @tracing.traceable_if_enabled("f")
    def f(value: int) -> int:
        return value * 2

    assert f(21) == 42


def test_tracing_on_builds_the_wrapper_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The traced path is taken when a key is present — and ``load_traceable``
    is consulted once, not per call, so a long-lived sidecar does not
    re-import and re-wrap on every submission.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "lsvt_test")

    calls: list[str] = []

    def _fake_traceable(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("name", "?"))

        def _decorate(func: Any) -> Any:
            return func

        return _decorate

    monkeypatch.setattr(tracing, "load_traceable", lambda: _fake_traceable)

    @tracing.traceable_if_enabled("span_name")
    def f(value: int) -> int:
        return value + 1

    assert f(1) == 2
    assert f(2) == 3
    # One name, resolved once — and the name is the explicit one, not the
    # function's, so renaming f cannot silently move the span.
    assert calls == ["span_name"]


def test_the_entry_points_are_actually_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring test, and the one that would rot silently.

    The three ``@traceable_if_enabled`` decorations are the only reason a
    LangSmith project ever shows a run — the gateway's httpx calls are
    invisible to LangSmith's automatic instrumentation. If someone removes
    one, nothing fails: tracing just quietly stops collecting. So the
    assertion is on the span *names*, which is what the decoration is for.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "lsvt_test")
    monkeypatch.setattr(settings, "analysis_pipeline", pipeline.DIRECT)

    names: list[str] = []

    def _fake_traceable(*args: Any, **kwargs: Any) -> Any:
        names.append(kwargs.get("name", "?"))
        return lambda func: func

    monkeypatch.setattr(tracing, "load_traceable", lambda: _fake_traceable)

    # conftest refuses the gateway call, so this returns ok=False — which
    # is beside the point. What is being measured is that the wrappers ran.
    pipeline.analyse("int main() { return 0; }", "1", "2", "3")

    assert "analyse_submission" in names
    assert "analyze_error" in names


def test_a_missing_tracer_does_not_break_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``langsmith`` cannot be imported: tracing is enrichment, and the
    analysis has to keep running without it.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "lsvt_test")
    monkeypatch.setattr(tracing, "load_traceable", lambda: None)

    @tracing.traceable_if_enabled("f")
    def f(value: int) -> int:
        return value * 3

    assert f(2) == 6


# -------------------------------------------------------------- configuration


def test_configure_tracing_exports_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    _restore_env: None,
) -> None:
    """LangSmith is configured only through environment variables, so a
    key in ``settings`` has to reach them — this is the whole reason
    ``configure_tracing`` exists.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "lsvt_test")
    monkeypatch.setattr(settings, "langsmith_project", "algoforge")

    assert tracing.configure_tracing() is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_API_KEY"] == "lsvt_test"
    assert os.environ["LANGCHAIN_PROJECT"] == "algoforge"


def test_configure_tracing_off_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
    _restore_env: None,
) -> None:
    """With no key, writing ``LANGCHAIN_TRACING_V2=true`` anyway would be
    the worst outcome: tracing on, nothing to authenticate with.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    os.environ.pop("LANGCHAIN_TRACING_V2", None)

    assert tracing.configure_tracing() is False
    assert "LANGCHAIN_TRACING_V2" not in os.environ


def test_the_startup_line_is_logged_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _restore_env: None,
) -> None:
    """It is a startup line about the whole process. Logged per call it
    would appear once per analysis and tell nobody anything.
    """
    monkeypatch.setattr(settings, "langsmith_api_key", "")

    with caplog.at_level(logging.INFO, logger="app.agent.tracing"):
        tracing.configure_tracing()
        tracing.configure_tracing()

    assert len(caplog.records) == 1
    assert "LANGSMITH_API_KEY" in caplog.records[0].getMessage()
