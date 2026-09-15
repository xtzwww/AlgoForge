"""Tests for the LLM gateway (P2-0).

Everything here runs against a mocked ``httpx.post``: the real DeepSeek
API needs a key this machine does not have, and even with one, a unit
test must not depend on a remote service's availability or on the
model's willingness to answer. What these tests pin down is the part
that is ours — that each provider is routed to the right URL with the
right payload and headers, and that every failure mode reaches the
caller as a typed exception instead of an empty string.

The spec's second acceptance criterion ("真实调一次 DeepSeek API 返回非空")
is deliberately not covered here. See ``test_live_deepseek_call`` for
the skip-marked placeholder and the note on why it cannot run locally.
"""

import httpx
import pytest

from app.config import settings
from app.llm import gateway


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response``.

    Only the four attributes the gateway reads are implemented. Using a
    duck-typed object instead of ``httpx.Response`` keeps the tests from
    depending on which constructor signature the installed httpx version
    exposes.
    """

    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def captured(monkeypatch):
    """Capture the arguments of every ``httpx.post`` and return a canned reply.

    Yields a dict with a ``calls`` list (one entry per call, holding
    ``url``/``json``/``headers``/``timeout``) plus settable ``response``
    and ``raise_exc`` fields so a test can script the outcome.
    """

    state = {"calls": [], "response": _FakeResponse(json_body=_reply("ok"))}

    def fake_post(url, *, json=None, headers=None, timeout=None, **kwargs):
        state["calls"].append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if state.get("raise_exc") is not None:
            raise state["raise_exc"]
        return state["response"]

    monkeypatch.setattr(gateway.httpx, "post", fake_post)
    return state


def _reply(content):
    """Wrap ``content`` in an OpenAI-compatible response body."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.fixture
def deepseek_configured(monkeypatch):
    """Point the gateway at DeepSeek with a key, without touching .env.

    ``settings`` is a module-level singleton, so each attribute that the
    gateway reads is patched individually and restored by monkeypatch.
    """
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-unit-test")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-coder")


@pytest.fixture
def ollama_configured(monkeypatch):
    """Point the gateway at Ollama, which needs no credential."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5-coder:7b")


# --------------------------------------------------------------- routing


def test_deepseek_routes_to_its_chat_completions_endpoint(
    captured, deepseek_configured
):
    """DeepSeek's documented base URL carries no /v1; the gateway adds it."""
    result = gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert result == "ok"
    assert len(captured["calls"]) == 1
    assert (
        captured["calls"][0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    )
    assert captured["calls"][0]["json"]["model"] == "deepseek-coder"


def test_deepseek_sends_bearer_authorization(captured, deepseek_configured):
    """DeepSeek requires the API key as a bearer token."""
    gateway.chat_completion([{"role": "user", "content": "hi"}])

    headers = captured["calls"][0]["headers"]
    assert headers["Authorization"] == "Bearer sk-unit-test"
    assert headers["Content-Type"] == "application/json"


def test_ollama_routes_to_its_v1_endpoint(captured, ollama_configured):
    """Ollama serves the compatible API under /v1."""
    result = gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert result == "ok"
    assert (
        captured["calls"][0]["url"] == "http://localhost:11434/v1/chat/completions"
    )
    assert captured["calls"][0]["json"]["model"] == "qwen2.5-coder:7b"


def test_ollama_sends_no_authorization_header(captured, ollama_configured):
    """Ollama needs no credential, so no header is invented for it.

    A bogus ``Authorization`` header is a real failure mode: some proxies
    reject a request that carries one rather than ignoring it.
    """
    gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert "Authorization" not in captured["calls"][0]["headers"]


def test_explicit_provider_overrides_configured_one(
    captured, ollama_configured, monkeypatch
):
    """``provider=`` wins over ``settings.llm_provider``.

    The configured provider is Ollama (so the base URL and model are
    distinguishable), and the call asks for DeepSeek instead. Without
    the override the request would go to localhost:11434; asserting on
    the URL is therefore what proves the override took effect.

    This is the seam a future "fall back to the local model" path needs,
    so it is worth a test of its own.
    """
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-override-test")

    gateway.chat_completion(
        [{"role": "user", "content": "hi"}], provider="deepseek"
    )

    assert len(captured["calls"]) == 1
    assert captured["calls"][0]["url"] == (
        "https://api.deepseek.com/v1/chat/completions"
    )
    assert captured["calls"][0]["json"]["model"] == "deepseek-coder"


def test_endpoint_does_not_double_the_v1_segment(captured, monkeypatch):
    """A base URL that already ends in /v1 is used as-is."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://gpu-box:11434/v1")

    gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert (
        captured["calls"][0]["url"] == "http://gpu-box:11434/v1/chat/completions"
    )


# --------------------------------------------------------------- payload


def test_unset_sampling_parameters_are_omitted(captured, ollama_configured):
    """No temperature/max_tokens key when the caller does not ask for them.

    Omission lets the provider's own default apply. Sending a gateway
    default would be an invisible policy decision made on behalf of
    every caller.
    """
    gateway.chat_completion([{"role": "user", "content": "hi"}])

    payload = captured["calls"][0]["json"]
    assert "temperature" not in payload
    assert "max_tokens" not in payload


def test_sampling_parameters_are_forwarded(captured, ollama_configured):
    """Explicit sampling parameters reach the provider."""
    gateway.chat_completion(
        [{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=64
    )

    payload = captured["calls"][0]["json"]
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 64


def test_extra_kwargs_reach_the_request_body(captured, ollama_configured):
    """Provider-specific options pass through without gateway knowledge.

    ``response_format`` is the motivating case: P2-2 wants JSON mode,
    and the gateway should not need a field per provider feature.
    """
    gateway.chat_completion(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
    )

    assert captured["calls"][0]["json"]["response_format"] == {"type": "json_object"}


def test_timeout_is_passed_to_httpx(captured, ollama_configured):
    """The requested timeout reaches httpx rather than a hidden default."""
    gateway.chat_completion([{"role": "user", "content": "hi"}], timeout=12.5)

    assert captured["calls"][0]["timeout"] == 12.5


# ----------------------------------------------------------- config guard


def test_deepseek_without_api_key_raises_before_any_request(captured, monkeypatch):
    """A missing key is a config error, discovered without a network call.

    Letting it through would surface as a 401 whose message points at
    the provider instead of at the empty .env field that caused it.
    """
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "")

    with pytest.raises(gateway.LLMConfigError, match="DEEPSEEK_API_KEY"):
        gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert captured["calls"] == []


def test_unknown_provider_raises_config_error(captured, monkeypatch):
    """A typo in LLM_PROVIDER fails loudly and lists the valid options."""
    monkeypatch.setattr(settings, "llm_provider", "gpt-5")

    with pytest.raises(gateway.LLMConfigError, match="Unknown LLM_PROVIDER"):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


def test_blank_messages_raises_config_error(captured, ollama_configured):
    """An empty message list is rejected here, not by the provider."""
    with pytest.raises(gateway.LLMConfigError, match="no messages"):
        gateway.chat_completion([])


def test_provider_name_is_case_and_space_insensitive(captured, monkeypatch):
    """LLM_PROVIDER= DeepSeek / ollama  both resolve.

    .env files invite stray whitespace, and a config value that is
    almost-right should not be a runtime mystery.
    """
    monkeypatch.setattr(settings, "llm_provider", "  Ollama  ")

    gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert "11434" in captured["calls"][0]["url"]


# ------------------------------------------------------------- transport


def test_timeout_becomes_connection_error(captured, ollama_configured):
    """A timeout is reported as unreachable, not as a generic failure."""
    captured["raise_exc"] = httpx.TimeoutException("timed out")

    with pytest.raises(gateway.LLMConnectionError, match="timed out"):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


def test_transport_error_becomes_connection_error(captured, ollama_configured):
    """A refused connection is reported as unreachable."""
    captured["raise_exc"] = httpx.ConnectError("connection refused")

    with pytest.raises(gateway.LLMConnectionError):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


def test_http_error_status_becomes_request_error_with_status(
    captured, ollama_configured
):
    """A 4xx/5xx carries the status code and the provider's own message."""
    captured["response"] = _FakeResponse(
        status_code=402,
        json_body=None,
        text="Insufficient Balance",
    )

    with pytest.raises(gateway.LLMRequestError) as excinfo:
        gateway.chat_completion([{"role": "user", "content": "hi"}])

    assert excinfo.value.status_code == 402
    # The provider's explanation must survive: it is what tells the user
    # whether to top up, fix the payload, or retry.
    assert "Insufficient Balance" in str(excinfo.value)


def test_non_json_body_becomes_request_error(captured, ollama_configured):
    """An HTML error page from a proxy is not mistaken for a completion."""
    captured["response"] = _FakeResponse(
        status_code=200, json_body=None, text="<html>gateway timeout</html>"
    )

    with pytest.raises(gateway.LLMRequestError, match="non-JSON"):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


# ------------------------------------------------------ response parsing


def test_empty_content_raises_rather_than_returning_blank(captured, ollama_configured):
    """The core contract: callers never receive an empty string.

    Downstream (P2-2) hands this text to a JSON parser, where "" would
    surface as a confusing schema error rather than "the model said
    nothing".
    """
    captured["response"] = _FakeResponse(json_body=_reply("   "))

    with pytest.raises(gateway.EmptyCompletionError):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


def test_content_is_returned_verbatim(captured, ollama_configured):
    """No stripping in the gateway.

    Whitespace can be meaningful inside a code fence or a JSON payload;
    normalising it here would silently alter model output.
    """
    captured["response"] = _FakeResponse(json_body=_reply("  hello  "))

    assert gateway.chat_completion([{"role": "user", "content": "hi"}]) == "  hello  "


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": "not-a-list"},
        {},
    ],
)
def test_malformed_bodies_raise_request_error(captured, ollama_configured, body):
    """Every shape a broken/renamed provider response could take is rejected.

    Parametrised because the failure we are guarding against is a
    provider changing its response envelope, and the specific bad shape
    matters less than that none of them slip through as a ``KeyError``
    or a ``None``.
    """
    captured["response"] = _FakeResponse(json_body=body)

    with pytest.raises(gateway.LLMRequestError):
        gateway.chat_completion([{"role": "user", "content": "hi"}])


# ------------------------------------------------------------- interface


def test_available_providers_lists_both():
    """The introspection helper agrees with the resolver."""
    assert gateway.available_providers() == ["deepseek", "ollama"]


# --------------------------------------------------------- live API (P2-0)


@pytest.mark.skip(
    reason=(
        "P2-0's second acceptance criterion ('真实调一次 DeepSeek API 返回非空') "
        "needs a real DEEPSEEK_API_KEY, which sidecar/.env leaves blank on this "
        "machine, and there is no Ollama at localhost:11434 either. Tracked as "
        "an unclosed verification item; set the key and remove this skip to run it."
    )
)
def test_live_deepseek_call():
    """Placeholder for the live-call check — see the skip reason."""
    result = gateway.chat_completion(
        [{"role": "user", "content": "Reply with the single word: ok"}],
        provider="deepseek",
        max_tokens=8,
    )
    assert result.strip()
