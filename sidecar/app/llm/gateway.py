"""LLM Gateway — three providers, one wire protocol (P2-0).

:func:`complete` is the general entry point the rest of the app should use
to talk to a model; :func:`chat_completion` is the text-only wrapper over
it. Both select a provider from ``settings`` and speak the
OpenAI-compatible ``POST /chat/completions`` dialect, which DeepSeek,
Ollama and any third-party gateway implement. That is what AGENTS.md §二.2
means by "换模型只改配置，不改代码".

Two entry points rather than one because the reply has two shapes. A plain
answer is a string; a tool-calling reply has empty content and a
``tool_calls`` array instead, which is a complete answer and not a
failure. Keeping ``chat_completion`` returning ``str`` lets P2-3's
analysis keep its "never empty" guarantee without knowing that tool calls
exist, while P2-7 goes through :func:`complete` and handles both.

There are three provider names, and the third is the general case:
``openai_compatible`` takes its base URL, key and model from
``OPENAI_COMPATIBLE_*`` settings, so a new hosted endpoint is a ``.env``
edit rather than a code change. ``deepseek`` and ``ollama`` stay as named
providers only because they are the two the project documents, and each
has a default base URL worth shipping.

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


class TruncatedCompletionError(LLMGatewayError):
    """The provider stopped because it hit ``max_tokens``.

    Distinct from :class:`EmptyCompletionError` and from a plain parse
    failure because the remedy is different from both: the prompt, the
    model and the wire protocol are all fine, and the only thing to
    change is the budget.

    This exists because of a real misdiagnosis (2026-09-16). A reasoning
    model spent an entire 800-token budget on its hidden reasoning and
    returned empty content, or a cut-off half-object; downstream that
    surfaced as "model output is not valid JSON", which reads as a
    *formatting* problem and sends the reader to the prompt. It was a
    budget problem. Reporting the truncation at the point where
    ``finish_reason`` is still visible keeps that distinction.
    """


@dataclass(frozen=True)
class ProviderSpec:
    """Everything that differs between providers.

    Kept as data rather than a subclass per provider: they differ only in
    base URL, model name, credential and whether an ``Authorization``
    header is sent. Encoding that as behaviour would be one class per
    provider and a factory for no gain.
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
_PROVIDER_NAMES: frozenset[str] = frozenset(
    {"deepseek", "ollama", "openai_compatible"}
)


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

    if name in ("openai_compatible", "openai-compatible"):
        # The escape hatch for everything that is not DeepSeek or Ollama:
        # set the three ``OPENAI_COMPATIBLE_*`` values and this provider
        # reaches it. Required here are only the two values with no
        # sensible default — a base URL and a model name. The key is
        # optional; see the field comment in ``config.py``.
        if not settings.openai_compatible_base_url.strip():
            raise LLMConfigError(
                "LLM_PROVIDER=openai_compatible but "
                "OPENAI_COMPATIBLE_BASE_URL is empty. Set it in "
                "sidecar/.env (e.g. https://host/v1)."
            )
        if not settings.openai_compatible_model.strip():
            raise LLMConfigError(
                "LLM_PROVIDER=openai_compatible but "
                "OPENAI_COMPATIBLE_MODEL is empty. Set it in sidecar/.env."
            )
        api_key = settings.openai_compatible_api_key.strip()
        return ProviderSpec(
            name="openai_compatible",
            base_url=settings.openai_compatible_base_url,
            model=settings.openai_compatible_model,
            api_key=api_key,
            # Sent only when there is one: a self-hosted endpoint with no
            # auth rejects a request carrying a bogus header, the same
            # reason Ollama's is off.
            send_auth=bool(api_key),
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


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for.

    ``arguments`` is kept as the raw JSON *text* the provider sent rather
    than a parsed dict. That is the wire format, and keeping it means a
    malformed payload is reported by whoever parses it — quoting the
    original text — instead of being quietly coerced to ``{}`` here and
    turning a broken call into a call with no arguments.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Completion:
    """A parsed chat-completion reply.

    ``content`` is empty exactly when the model is asking for tools, which
    is an ordinary reply rather than a failure — hence ``wants_tools``.
    Callers that only want text should use :func:`chat_completion`, which
    keeps the "never empty" guarantee; callers that pass ``tools`` must
    use :func:`complete` and handle both shapes.
    """

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _parse_tool_calls(message: dict) -> tuple[ToolCall, ...]:
    """Read ``tool_calls`` out of an assistant message.

    Raises:
        LLMRequestError: The field is present but not the expected shape.
            Skipping a malformed entry instead would make the loop answer
            from a partial set of tool results without saying so.
    """
    raw = message.get("tool_calls")
    if raw is None:
        # Absent and null both mean "no tools wanted"; providers differ on
        # whether they send the key at all for a plain text reply.
        return ()
    if not isinstance(raw, list):
        raise LLMRequestError("'tool_calls' was not an array")

    calls: list[ToolCall] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LLMRequestError(f"tool_calls[{index}] was not an object")

        function = entry.get("function")
        if not isinstance(function, dict):
            raise LLMRequestError(f"tool_calls[{index}] had no 'function' object")

        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise LLMRequestError(f"tool_calls[{index}] had no function name")

        # The spec allows the key to be omitted for a call with no
        # arguments, which is not the same as an empty string.
        arguments = function.get("arguments")
        if arguments is None:
            arguments = "{}"
        if not isinstance(arguments, str):
            raise LLMRequestError(
                f"tool_calls[{index}] 'arguments' was "
                f"{type(arguments).__name__}, not a JSON string"
            )

        # An id is required to match the result back to the call. OpenAI
        # always sends one; a compatible server that omits it would make
        # the follow-up turn unaddressable, so a positional stand-in is
        # used rather than sending null and being rejected later.
        call_id = entry.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"

        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

    return tuple(calls)


def _parse_completion(body: Any) -> Completion:
    """Turn an OpenAI-compatible response body into a :class:`Completion`.

    Raises:
        LLMRequestError: The body is not the expected shape.
        TruncatedCompletionError: The provider hit ``max_tokens``.
        EmptyCompletionError: No text and no tool call — nothing usable.
    """
    if not isinstance(body, dict):
        raise LLMRequestError(f"Unexpected response type: {type(body).__name__}")

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMRequestError("Response contained no 'choices' array")

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMRequestError("First choice was not an object")

    # Checked before the content is even inspected: when the model was cut
    # off, whatever it did manage to emit is not a usable answer, and
    # saying so is more useful than describing the shape of the fragment.
    # Providers disagree on whether this is "length" or "max_tokens", so
    # both are accepted. Note "tool_calls" is a *complete* reply, not a
    # truncation, and must not be caught here.
    finish_reason = first.get("finish_reason")
    if finish_reason in ("length", "max_tokens"):
        raise TruncatedCompletionError(
            "Provider hit the max_tokens limit before finishing. The reply "
            "is incomplete; raise settings.llm_max_tokens (reasoning models "
            "bill their hidden reasoning against the same budget)."
        )

    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMRequestError("First choice had no 'message' object")

    tool_calls = _parse_tool_calls(message)

    content = message.get("content")
    if content is None:
        # Legal only alongside a tool call: the spec has the assistant
        # message carry null content when it is asking for tools instead of
        # answering. On its own it is still a broken reply.
        if not tool_calls:
            raise LLMRequestError("Message had no string 'content'")
        content = ""
    if not isinstance(content, str):
        raise LLMRequestError("Message had no string 'content'")

    if not content.strip() and not tool_calls:
        raise EmptyCompletionError("Provider returned empty content")

    return Completion(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason if isinstance(finish_reason, str) else "",
    )


def complete(
    messages: list[dict[str, Any]],
    *,
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    **extra: Any,
) -> Completion:
    """Send ``messages`` and return the whole reply, tool calls included.

    This is the general entry point; :func:`chat_completion` is the
    text-only convenience wrapper over it.

    Args:
        messages: OpenAI-style message dicts. A tool-calling conversation
            also carries ``{"role": "assistant", ..., "tool_calls": [...]}``
            and ``{"role": "tool", "tool_call_id": ..., "content": ...}``
            entries; the gateway forwards them untouched.
        provider: Override ``settings.llm_provider``.
        temperature: Sampling temperature. ``None`` omits the key.
        max_tokens: Response length cap. ``None`` omits the key.
        timeout: Total request timeout in seconds.
        tools: OpenAI function definitions (see
            :func:`app.agent.tools.tool_schemas`). ``None`` omits the key,
            so a text-only call is byte-for-byte what it was before this
            parameter existed.
        tool_choice: ``"auto"`` / ``"none"`` / ``"required"`` or an object
            naming one function. Forwarded verbatim.
        **extra: Extra top-level request fields, forwarded verbatim.

    Returns:
        A :class:`Completion`. ``content`` may be empty when
        ``completion.wants_tools`` is true.

    Raises:
        LLMConfigError: Misconfiguration detected before the request.
        LLMConnectionError: The provider could not be reached.
        LLMRequestError: The provider rejected or failed the request.
        TruncatedCompletionError: The provider hit ``max_tokens``.
        EmptyCompletionError: Neither text nor a tool call came back.
    """
    if not messages:
        # Cheaper to catch here than to let the provider 400 on it.
        raise LLMConfigError("complete called with no messages")

    spec = _resolve_provider(provider)
    payload = _build_payload(spec, messages, temperature, max_tokens, extra)
    # Added after _build_payload rather than inside it so that a call
    # without tools produces exactly the same request body as before.
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

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

    return _parse_completion(body)


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
    **extra: Any,
) -> str:
    """Send ``messages`` to the configured LLM and return the reply text.

    The text-only view of :func:`complete`. It exists so that callers whose
    reply really is a string — P2-3's analysis — cannot accidentally
    receive a tool call and treat its empty content as an answer.

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
        LLMConfigError: Misconfiguration detected before the request, or
            ``tools`` was passed (use :func:`complete` for tool calls).
        LLMConnectionError: The provider could not be reached.
        LLMRequestError: The provider rejected or failed the request.
        TruncatedCompletionError: The provider hit ``max_tokens``.
        EmptyCompletionError: The provider returned no usable content.
    """
    if "tools" in extra or "tool_choice" in extra:
        # Silently ignoring them would leave the caller waiting for text
        # that is never coming; silently honouring them would return an
        # empty string from a function that promises text.
        raise LLMConfigError(
            "chat_completion() returns text only. Use complete() when "
            "passing 'tools' or 'tool_choice'."
        )

    return complete(
        messages,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        **extra,
    ).content


def available_providers() -> list[str]:
    """Return the provider names this gateway knows how to route to."""
    return sorted(_PROVIDER_NAMES)
