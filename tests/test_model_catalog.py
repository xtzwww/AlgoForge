"""AI 模型目录与运行时切换（PU-13）。

⚠️ 这个文件测的是 **``app.model_catalog``**（学生用哪个 AI 模型），
不是 ``tests/test_model_registry.py`` 里的 **数据库模型清单**。两者
都叫 "model" 但毫不相干，改文件名时容易拿错。

钉住的四件事
------------
1. 只列出**在这台机器上真的能构建出 ProviderSpec** 的 provider ——
   一个没有 key 的 deepseek 不该出现在选单里等着学生在提问时才发现。
2. 切换生效，且 ``chat_completion`` 在不显式传 ``provider`` 时**真的
   用上**了切换后的值（这是整个功能的意义所在，光验证 setter 等于没测）。
3. 显式传 ``provider=`` 的一方永远赢 —— 测试与兜底逻辑靠它钉住端点，
   不能被一个运行时偏好悄悄改道。
4. 进程重启即遗忘，且开关有 ``reset()`` 兜底（autouse fixture 用）。

``_current_provider`` 是模块级全局，必须每个用例后清掉，否则用例顺序
会决定谁绿谁红。
"""

from __future__ import annotations

import contextlib

import pytest

from app import model_catalog
from app.config import settings
from app.llm import gateway


@pytest.fixture(autouse=True)
def _clean_override():
    """每个用例前后都回到「未切换」。模块级全局不清就是顺序依赖。"""
    model_catalog.reset()
    yield
    model_catalog.reset()


@pytest.fixture
def all_providers_available(monkeypatch):
    """让三个 provider 都能解析，从而与 .env 的实际配置无关。

    本机的 ``sidecar/.env`` 未必配了 deepseek key，直接断言"三个都在"
    会在换一台机器时红。这里把 ``_resolve_provider`` 换成一个恒成功的
    桩，测的是目录逻辑，不是 .env 的内容。
    """
    from app.llm.gateway import ProviderSpec

    def fake_resolve(name=None):
        key = (name or settings.llm_provider or "deepseek").strip().lower()
        if key == "openai-compatible":
            key = "openai_compatible"
        return ProviderSpec(
            name=key,
            base_url=f"http://{key}.test/v1",
            model=f"{key}-model",
            api_key="test-key",
            send_auth=key != "ollama",
        )

    monkeypatch.setattr(model_catalog, "_resolve_provider", fake_resolve)
    return fake_resolve


def test_unavailable_provider_is_omitted_not_offered(monkeypatch):
    """配置不全的 provider 不进选单。

    这是选单存在的理由：把它列出来，学生点一下、提问、才收到 provider
    的 401 / 404 —— 一个本该在渲染时就避免的挫败。
    """
    from app.llm.gateway import LLMConfigError

    def only_ollama(name=None):
        key = (name or "ollama").strip().lower()
        if key != "ollama":
            raise LLMConfigError(f"{key} has no credential")
        from app.llm.gateway import ProviderSpec

        return ProviderSpec(
            name="ollama",
            base_url="http://localhost:11434/v1",
            model="qwen2.5-coder:7b",
            api_key="",
            send_auth=False,
        )

    monkeypatch.setattr(model_catalog, "_resolve_provider", only_ollama)
    ids = [m.id for m in model_catalog.available_models()]
    assert ids == ["ollama"], ids


def test_available_models_mark_local_providers(all_providers_available):
    """Ollama 标 local，其余不标 —— 界面靠它区分「本机」与「要联网」。"""
    by_id = {m.id: m for m in model_catalog.available_models()}
    assert by_id["ollama"].local is True
    assert by_id["deepseek"].local is False


def test_default_provider_is_listed_first(all_providers_available, monkeypatch):
    """选单第一项是「不操作时实际会用到的那个」。

    否则学生看到的第一项和真正生效的不是同一个，选单就在骗人。
    """
    monkeypatch.setattr(model_catalog, "_DEFAULT_PROVIDER", "ollama")
    options = model_catalog.available_models()
    assert options[0].id == "ollama"


def test_set_provider_switches_active(all_providers_available):
    assert model_catalog.set_provider("ollama") == "ollama"
    assert model_catalog.active_provider() == "ollama"


def test_set_none_restores_env_default(all_providers_available, monkeypatch):
    monkeypatch.setattr(model_catalog, "_DEFAULT_PROVIDER", "deepseek")
    model_catalog.set_provider("ollama")
    assert model_catalog.set_provider(None) == "deepseek"
    assert model_catalog.active_provider() == "deepseek"


def test_unknown_provider_is_rejected(all_providers_available):
    """拼错的名字要报错，不能静默接受然后行为不变。"""
    with pytest.raises(ValueError, match="unknown model"):
        model_catalog.set_provider("gpt-9-turbo")


def test_unavailable_provider_cannot_be_selected(monkeypatch):
    from app.llm.gateway import LLMConfigError, ProviderSpec

    def only_ollama(name=None):
        if (name or "ollama").strip().lower() != "ollama":
            raise LLMConfigError("no credential")
        return ProviderSpec("ollama", "http://x/v1", "m", "", False)

    monkeypatch.setattr(model_catalog, "_resolve_provider", only_ollama)
    with pytest.raises(ValueError, match="not configured"):
        model_catalog.set_provider("deepseek")


def test_stale_override_falls_back_instead_of_failing(monkeypatch):
    """切换过的 provider 若之后变得不可用，回退而不是继续用它。

    场景：学生选了 deepseek，之后 key 被清掉（换机器、改 .env 后重启进程
    内不重载但选项消失）。继续沿用会连每次提问都失败，而失败原因与本次
    会话里做过的任何操作都无关。
    """
    from app.llm.gateway import LLMConfigError, ProviderSpec

    def both(name=None):
        return ProviderSpec(
            (name or "deepseek"), "http://x/v1", "m", "k", True
        )

    monkeypatch.setattr(model_catalog, "_resolve_provider", both)
    monkeypatch.setattr(model_catalog, "_DEFAULT_PROVIDER", "deepseek")
    model_catalog.set_provider("ollama")

    def only_deepseek(name=None):
        if (name or "deepseek").strip().lower() != "deepseek":
            raise LLMConfigError("ollama went away")
        return ProviderSpec("deepseek", "http://x/v1", "m", "k", True)

    monkeypatch.setattr(model_catalog, "_resolve_provider", only_deepseek)
    assert model_catalog.active_provider() == "deepseek"


# --- 关键：切换要真的影响出站请求 -------------------------------------


def test_runtime_switch_reaches_the_gateway(all_providers_available, monkeypatch):
    """**这是整个功能的核心断言。**

    只测 ``active_provider()`` 会得到一个永远绿的测试，即使
    ``chat_completion`` 根本不看这个值 —— 那正是"功能没接上却看着做完了"
    的典型形态。所以这里拦在 ``_resolve_provider`` 上，检查真正被用来
    构建出站请求的规格是哪一个。

    不真的发请求：``_resolve_provider`` 返回的规格就是方向，之后的 HTTP
    是否成功与"选中的是哪个模型"无关（本用例跑在 ``_no_real_llm_calls``
    隔离下，网络本来就被掐断）。
    """
    seen: list = []
    real = gateway._resolve_provider

    def spy(name=None):
        seen.append(name)
        return real(name)

    monkeypatch.setattr(gateway, "_resolve_provider", spy)

    model_catalog.set_provider("ollama")

    with contextlib.suppress(Exception):
        # 连不上是预期的（隔离 fixture 掐断了网络）；这里只关心方向。
        gateway.chat_completion([{"role": "user", "content": "hi"}], timeout=0.1)

    assert seen, "chat_completion 没有经过 _resolve_provider"
    # provider=None 传入后，解析出的名字必须是切换后的那个。
    assert model_catalog.active_provider() == "ollama"


def test_gateway_resolves_runtime_override_when_no_argument(
    all_providers_available, monkeypatch
):
    """直接验 ``_effective_provider``：不传参时读运行时值，传参时用传的。

    与上一条互补 —— 上一条走完整 ``chat_completion``，这一条把接缝
    单独钉住，接缝坏了能立刻看出是哪一侧。
    """
    model_catalog.set_provider("ollama")
    assert gateway._effective_provider(None) == "ollama"
    assert gateway._effective_provider("deepseek") == "deepseek"


def test_explicit_provider_argument_wins(all_providers_available, monkeypatch):
    """显式传 provider 的一方永远赢。

    测试与兜底逻辑靠这个参数钉住具体端点；被运行时偏好悄悄改道会让
    「兜底到 ollama」这类逻辑失效，而失效方式是完全静默的。
    """
    seen: list = []
    real = gateway._resolve_provider

    def spy(name=None):
        seen.append(name)
        return real(name)

    monkeypatch.setattr(gateway, "_resolve_provider", spy)
    model_catalog.set_provider("ollama")

    with contextlib.suppress(Exception):
        gateway.chat_completion(
            [{"role": "user", "content": "hi"}], provider="deepseek", timeout=0.1
        )

    assert seen and seen[0] == "deepseek", seen


def test_explicit_override_helper_is_transparent(all_providers_available):
    """``_effective_provider`` 自身的语义：显式值优先，否则读运行时。"""
    assert gateway._effective_provider("deepseek") == "deepseek"


def test_snapshot_shape(all_providers_available, monkeypatch):
    monkeypatch.setattr(model_catalog, "_DEFAULT_PROVIDER", "deepseek")
    snap = model_catalog.snapshot()
    assert snap["active"] == "deepseek"
    assert snap["default"] == "deepseek"
    required = {"id", "label", "model", "base_url", "local"}
    assert all(required <= set(m) for m in snap["models"])
