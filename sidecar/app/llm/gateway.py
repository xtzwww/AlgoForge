"""LLM Gateway — one function, two providers, one wire protocol (P2-0).

``chat_completion`` is the only entry point the rest of the app should
use to talk to a model. It selects a provider from ``settings`` and
speaks the OpenAI-compatible ``POST /chat/completions`` dialect, which
both DeepSeek and Ollama implement. That is what AGENTS.md §二.2 means
by "换模型只改配置，不改代码": adding a third provider means adding one
:class:`ProviderSpec` to :data:`_PROVIDERS`, not editing callers.

Why httpx rather than the ``openai`` SDK or LangChain
-----------------------------------------------------
The spec's 先学课节 points at ``ChatOpenAI`` / ``init_chat_model``, but
AGENTS.md's dependency table does not list ``openai`` or ``langchain``,
and neither is installed. A hand-rolled client also keeps the failure
modes visible: this module must distinguish "the provider returned
garbage" from "the provider is unreachable" from "we sent a bad
request", because P2-3's retry logic keys off exactly that difference.
Later phases that genuinely need the LangChain ecosystem (P2-7's tool
calling, P2-11's LangGraph) can wrap this function rather than replace
it.

Non-empty is a guarantee, not a hope
------------------------------------
``chat_completion`` raises :class:`EmptyCompletionError` when the model
returns an empty or whitespace-only string. Downstream (P2-2) this text
is handed to a JSON parser, and an empty string would surface as a
confusing schema error instead of "the model produced nothing". Callers
must never receive ``""`` from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings


class LLMGatewayError(RuntimeError):
    """Base class for every failure raised by this module.

    Callers that want to degrade gracefully (P2-4 must not break judging
    just because the LLM is down) catch this, not ``Exception``.
    """


class LLMConfigError(LLMGatewayError):
    """The gateway is misconfigured — e.g. DeepSeek with no API key.

    Raised eagerly, before any network call. A missing key is a
    deployment mistake, and discovering it as a 401 from the provider
    makes for a much worse error message.
    """


class LLMRequestError(LLMGatewayError):
    """The provider was reached but refused or failed the request.

    Carries ``status_code`` when the provider sent an HTTP response, so
    callers can distinguish a transient 5xx (worth retrying) from a 400
    caused by our own payload (not worth retrying).
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMConnectionError(LLMGatewayError):
    """The provider could not be reached at all (DNS, refused, timeout)."""


class EmptyCompletionError(LLMGatewayError):
    """The provider replied successfully but with no usable content.

    Separate from :class:`LLMRequestError` because the HTTP exchange
    succeeded: the provider is up, it simply produced nothing. Retrying
    the identical prompt is plausible here in a way it is not for a 400.
    """


@dataclass(frozen=True)
class ProviderSpec:
    """Everything that differs between providers.

    Kept as data rather than a subclass per provider: the two differ
    only in base URL, model name, credential and whether an
    ``Authorization`` header is sent. Encoding that as behaviour would
    be two classes and a factory for no gain.
    """

    name: str
    base_url: str
    model: str
    api_key: str
    # Ollama ignores auth and rejecting a request that carries a bogus
    # ``Authorization`` header is worse than omitting it, so the header
    # is opt-in per provider rather than unconditional.
    send_auth: bool


# Names accepted by ``provider=``. Declared next to the resolver that
# reads it so the two cannot drift, and before any function body that
# formats it into an error message.
_PROVIDER_NAMES: frozenset[str] = frozenset({"deepseek", "ollama"})


def _resolve_provider(provider: Optional[str] = None) -> ProviderSpec:
    """Build the :class:`ProviderSpec` for ``provider`` or the configured one.

    Args:
        provider: Explicit provider name. ``None`` (the default) uses
            ``settings.llm_provider``.

    Raises:
        LLMConfigError: Unknown provider name, or DeepSeek selected with
            an empty API key.
    """
    name = (provider or settings.llm_provider or "").strip().lower()

    if name == "deepseek":
        if not settings.deepseek_api_key.strip():
            raise LLMConfigError(
                "LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is empty. "
                "Set it in sidecar/.env, or switch LLM_PROVIDER to ollama."
            )
        return ProviderSpec(
            name="deepseek",
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            send_auth=True,
        )

    if name == "ollama":
        # Ollama exposes an OpenAI-compatible surface under /v1 and
        # needs no credential. It is also the documented fallback when
        # DeepSeek is unavailable, so an empty key must not block it.
        return ProviderSpec(
            name="ollama",
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            api_key="",
            send_auth=False,
        )

    known = ", ".join(sorted(_PROVIDER_NAMES))
    raise LLMConfigError(f"Unknown LLM_PROVIDER {name!r} (expected one of: {known})")


def _endpoint(spec: ProviderSpec) -> str:
    """Return the full chat-completions URL for ``spec``.

    Handles the two shapes an OpenAI-compatible base URL comes in:
    DeepSeek documents ``https://api.deepseek.com`` (no ``/v1``), while
    Ollama serves the compatible API at ``http://host:11434/v1`` and
    would 404 on a bare ``/chat/completions``. Rather than force users
    to remember which convention each provider wants, ``/v1`` is
    appended only when the configured base URL does not already carry a
    version segment.
    """
    base = spec.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/chat/completions"


def _build_payload(
    spec: ProviderSpec,
    messages: list[dict[str, str]],
    temperature: Optional[float],
    max_tokens: Optional[int],
    extra: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the request body, omitting keys the caller left unset.

    Unset sampling parameters are omitted rather than defaulted here so
    the provider's own defaults apply. Hardcoding a temperature in this
    layer would be an invisible policy decision: P2-3 wants low
    temperature for deterministic labels, but a future summarisation
    feature would want the opposite, and neither should have to fight
    the gateway.
    """
    payload: dict[str, Any] = {"model": spec.model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra:
        payload.update(extra)
    return payload


def _extract_content(body: Any) -> str:
    """Pull the assistant text out of an OpenAI-compatible response body.

    Raises:
        LLMRequestError: The body is not the expected shape.
        EmptyCompletionError: The shape is right but the text is blank.
    """
    if not isinstance(body, dict):
        raise LLMRequestError(f"Unexpected response type: {type(body).__name__}")

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMRequestError("Response contained no 'choices' array")

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMRequestError("First choice was not an object")

    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMRequestError("First choice had no 'message' object")

    content = message.get("content")
    if not isinstance(content, str):
        raise LLMRequestError("Message had no string 'content'")

    if not content.strip():
        raise EmptyCompletionError("Provider returned empty content")

    return content


def chat_completion(
    messages: list[dict[str, str]],
    *,
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
    **extra: Any,
) -> str:
    """Send ``messages`` to the configured LLM and return the reply text.

    Args:
        messages: OpenAI-style message dicts, e.g.
            ``[{"role": "user", "content": "..."}]``.
        provider: Override ``settings.llm_provider``. Used by tests and
            by any future "try the fallback model" logic.
        temperature: Sampling temperature. ``None`` omits the key.
        max_tokens: Response length cap. ``None`` omits the key.
        timeout: Total request timeout in seconds.
        **extra: Extra top-level request fields, forwarded verbatim.
            This is the escape hatch for provider-specific options
            (e.g. ``response_format`` for JSON mode) without teaching
            the gateway about them.

    Returns:
        The assistant's reply, guaranteed non-empty after ``.strip()``.

    Raises:
        LLMConfigError: Misconfiguration detected before the request.
        LLMConnectionError: The provider could not be reached.
        LLMRequestError: The provider rejected or failed the request.
        EmptyCompletionError: The provider returned no usable content.
    """
    if not messages:
        # Cheaper to catch here than to let the provider 400 on it.
        raise LLMConfigError("chat_completion called with no messages")

    spec = _resolve_provider(provider)
    payload = _build_payload(spec, messages, temperature, max_tokens, extra)

    headers = {"Content-Type": "application/json"}
    if spec.send_auth:
        headers["Authorization"] = f"Bearer {spec.api_key}"

    try:
        response = httpx.post(
            _endpoint(spec),
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise LLMConnectionError(
            f"{spec.name} timed out after {timeout}s"
        ) from exc
    except httpx.HTTPError as exc:
        # Covers connect errors, DNS failures and protocol errors.
        raise LLMConnectionError(f"Could not reach {spec.name}: {exc}") from exc

    if response.status_code >= 400:
        # Include the provider's own message: DeepSeek puts the useful
        # part (e.g. "insufficient balance") there, and a bare status
        # code sends people to the wrong place.
        detail = response.text.strip()[:500] or "<no body>"
        raise LLMRequestError(
            f"{spec.name} returned HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise LLMRequestError(
            f"{spec.name} returned non-JSON body: {response.text.strip()[:200]!r}"
        ) from exc

    return _extract_content(body)


def available_providers() -> list[str]:
    """Return the provider names this gateway knows how to route to."""
    return sorted(_PROVIDER_NAMES)
