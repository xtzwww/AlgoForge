"""把 S10 的护栏接进调用点的那几行——单独测，因为默认测不到。

``settings.llm_guardrails`` 在测试套件里被 ``conftest._no_real_llm_calls`` 关掉了：
一旦打开，``analyzer`` / ``team`` 里的调用点就不再是模块级的 ``chat_completion``，
于是二十多处 ``patch.object(analyzer, "chat_completion", ...)`` 全部落在一个没人读的
名字上（那段注释里有完整解释）。

但那也意味着**接线本身在默认配置下从没被执行过**：开关是关的，走的是老路径。
这两行如果哪天写错了——比如把 ``guarded_chat_completion`` 写成别的、或者工具循环
忘了传 ``caller``——没有任何测试会红。本文件就是为这个存在：把开关显式打开，
验证电流真的从护栏那头过去了。

钉住三条：

1. 文本分析在开关打开时走 ``guarded_chat_completion``；
2. **工具循环也一样**——这条最容易漏，主路径走的正是它，而
   ``guarded_chat_completion`` 明确拒绝 ``tools``，所以循环必须用另一个入口；
3. 开关关掉时回到网关，行为与接线前逐字一致。
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import patch

import pytest

from app.agent import analyzer, tool_caller
from app.agent.guardrails import complete_through_guardrails
from app.agent.team import _ask_worker
from app.config import settings

_VALID = """```json
{"error_type": "overflow", "error_detail": "x", "hint": "y", "suggested_fix": "z"}
```"""


@pytest.fixture
def guardrails_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """打开总开关——整个套件里只有本文件这么做。"""
    monkeypatch.setattr(settings, "llm_guardrails", True)


def _patched(monkey: object, name: str) -> Any:
    """Patch one of a module's two completion entry points."""
    return patch.object(monkey, name, return_value=_VALID)


def test_text_path_routes_through_the_guardrails(guardrails_on: None) -> None:
    """开关打开时，文本分析发的是护栏版调用。"""
    with (
        _patched(analyzer, "guarded_chat_completion") as guarded,
        _patched(analyzer, "chat_completion") as plain,
    ):
        content, error, calls = analyzer._analyse_once(
            [{"role": "user", "content": "p"}]
        )

    assert error is None
    assert "overflow" in content
    assert guarded.call_count == 1
    assert plain.call_count == 0, "护栏开着却直连了网关"


def test_tool_loop_is_guarded_too(guardrails_on: None) -> None:
    """工具循环的每一轮都要过护栏——主路径走的是这一条。

    ``guarded_chat_completion`` 会在传了 ``tools`` 时抛 ``LLMConfigError``，
    所以循环必须收到另一个入口（``complete_through_guardrails``）。这条断言
    盯的就是那个选择：写成文本版入口，主路径会直接炸。
    """
    seen: dict[str, object] = {}

    def fake_loop(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise AssertionError("循环体不需要真的跑起来")

    # Patch it where it is *imported from*: `run_with_tools` is imported
    # lazily inside the tool branch (so a text-only analysis does not drag
    # the sandbox in), which means `analyzer` has no such attribute.
    with (
        contextlib.suppress(AssertionError),
        patch.object(tool_caller, "run_with_tools", fake_loop),
    ):
        analyzer._analyse_once([{"role": "user", "content": "p"}], use_tools=True)

    assert seen.get("caller") is complete_through_guardrails


def test_switch_off_keeps_the_plain_gateway() -> None:
    """开关关掉（套件的默认）时行为与接线前一致。"""
    assert settings.llm_guardrails is False

    with (
        _patched(analyzer, "guarded_chat_completion") as guarded,
        _patched(analyzer, "chat_completion") as plain,
    ):
        analyzer._analyse_once([{"role": "user", "content": "p"}])

    assert plain.call_count == 1
    assert guarded.call_count == 0


def test_team_agents_are_guarded(guardrails_on: None) -> None:
    """三智能体的两个工作者同样走护栏——它们各自发一次请求。"""
    from app.agent import team

    with (
        _patched(team, "guarded_chat_completion") as guarded,
        _patched(team, "chat_completion") as plain,
    ):
        _ask_worker(
            "prompt",
            parser=lambda raw: {"ok": True},
            label="hint",
        )

    assert guarded.call_count == 1
    assert plain.call_count == 0
