"""Guardrail middleware around one model call (阶段 8 · `AGENTS.md` S10).

`AGENTS.md` §三 S10 asks for "LangChain 1.x 中间件护栏（PIIMiddleware /
ModelFallback / ModelRetry 等）". Three protections are implemented here,
one per named middleware:

``PIIGuard``
    Personal data in a submission — an email in a comment, a card number
    pasted into the wrong window — is redacted before the prompt leaves
    the process for a hosted endpoint.

``ModelRetry``
    A provider that answered ``500`` or ``429`` is asked again with
    exponential backoff.

``ModelFallback``
    When the primary provider cannot serve the request at all, the chain
    moves on to the next one (``AGENTS.md`` §一: "主路径 DeepSeek API，
    兜底 Ollama") instead of leaving the student without feedback.

Why the guardrails are ours and not LangChain's middleware objects
------------------------------------------------------------------
LangChain 1.x's ``PIIMiddleware`` / ``ModelRetryMiddleware`` /
``ModelFallbackMiddleware`` are all ``AgentMiddleware`` subclasses: they
are hooks into ``create_agent``'s model-call runtime, and there is no way
to attach one to a bare ``chat_completion`` call. This project's model
access is :mod:`app.llm.gateway` — a hand-rolled OpenAI-compatible client,
for the reasons that module's docstring gives — and neither ``openai`` nor
``langchain_openai`` is installed. Standing up a ``create_agent`` runtime
purely to host three middlewares would add a second, parallel model path
that nothing else in the codebase uses.

So the *pieces* are reused wherever they are reusable from a public path,
and only the composition is ours:

* The PII detectors and the four redaction strategies come from
  ``langchain.agents.middleware.pii`` — the same code ``PIIMiddleware``
  calls. Reimplementing the email and Luhn-checked card-number patterns
  would be a second set of regexes to keep correct.
* The retry/fallback loop is ours. LangChain's equivalents are not
  separable from the agent runtime, and ``langchain.agents.middleware._retry``
  (where its backoff arithmetic lives) is a private module — importing a
  private path would turn a routine dependency bump into an import-time
  crash of the whole sidecar.

Wired into the analysis path, behind one switch
----------------------------------------------
``analyzer`` and ``team`` route through :func:`guarded_chat_completion` /
:func:`complete_through_guardrails` when ``settings.llm_guardrails`` is set
(the default), and fall back to the plain gateway when it is off. So the
guardrails are live on the analysis path by default; the switch exists so a
bad interaction can be turned off without a code change — it is deliberately
kept as a runtime toggle because it changes what every submission sends and
what every failure costs, see "What turning this on changes" below.

What turning this on changes
----------------------------
Three things a reviewer should agree to before flipping the switch:

1. **Prompts are rewritten.** Redaction is applied to the outgoing text,
   so the model sees ``[REDACTED_EMAIL]`` where the submission had an
   address. That is the point, but it is a real edit to the input.
2. **Failures get more expensive.** A retry re-sends the request, so a
   provider that is genuinely down costs ``1 + max_retries`` requests
   before the fallback even starts. This path runs while a student waits
   for ``judge_done``, which is exactly why ``analyzer`` chose *not* to
   retry gateway failures — see ``should_retry`` for how that decision is
   respected by default.
3. **A different model may answer.** The fallback provider is a different
   model with different output habits, so the same submission can be
   analysed by DeepSeek on one run and Ollama on the next. Anything that
   compares analyses across runs (P2-5's accuracy gate) has to know which
   provider produced them, which is why :class:`GuardedCompletion` reports
   it.

Which PII types are on by default, and why not all five
-------------------------------------------------------
LangChain's ``PIIMiddleware`` can detect email, credit card, IP address,
MAC address and URL. The default set here is **email and credit card
only**. The other three are not personal data in this domain — they are
the *subject matter*: a problem about IP 地址转换 or URL 解析 hands the
model a string of exactly that shape, and redacting it would remove the
thing being analysed. A guardrail that damages the answer is worse than
the leak it prevents, so the wider set is available per-policy rather than
on by default.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from langchain.agents.middleware.pii import (
    PIIMatch,
    apply_strategy,
    detect_credit_card,
    detect_email,
    detect_ip,
    detect_mac_address,
    detect_url,
)

from app.config import settings
from app.llm.gateway import (
    Completion,
    EmptyCompletionError,
    LLMConfigError,
    LLMConnectionError,
    LLMGatewayError,
    LLMRequestError,
    TruncatedCompletionError,
    available_providers,
    complete,
)

logger = logging.getLogger(__name__)

#: The detectors this module can apply, keyed by the name used in a policy.
#: Taken from LangChain rather than re-derived — see the module docstring.
#: The values are the same callables ``PIIMiddleware`` resolves a string
#: ``pii_type`` to.
DETECTORS: Mapping[str, Callable[[str], list[PIIMatch]]] = {
    "email": detect_email,
    "credit_card": detect_credit_card,
    "ip": detect_ip,
    "mac_address": detect_mac_address,
    "url": detect_url,
}

#: Redaction strategies ``apply_strategy`` understands.
STRATEGIES: frozenset[str] = frozenset({"block", "redact", "mask", "hash"})

#: Types applied when a policy does not say otherwise. Email and card
#: numbers are personal data in every context; IP / MAC / URL are the
#: subject matter of algorithm problems often enough that redacting them
#: by default would corrupt the analysis. See the module docstring.
DEFAULT_PII_TYPES: tuple[str, ...] = ("email", "credit_card")

#: Placeholder a policy uses when ``providers`` is empty: "whatever
#: ``LLM_PROVIDER`` says". ``None`` rather than the resolved name so the
#: gateway keeps ownership of that decision.
CONFIGURED_PROVIDER: Optional[str] = None


class AllProvidersFailedError(LLMGatewayError):
    """Every provider in the fallback chain failed.

    A subclass of :class:`~app.llm.gateway.LLMGatewayError` on purpose:
    callers that already degrade on a gateway failure (``analyzer``,
    ``team``) keep working unchanged, and the extra detail — *which*
    providers were tried and *why* each refused — is available to whoever
    wants to log or display it.

    It is only raised when the chain actually has more than one provider.
    A single-provider call re-raises the provider's own exception, so
    nothing about the one-provider behaviour changes.
    """

    def __init__(self, failures: Sequence[tuple[str, LLMGatewayError]]) -> None:
        self.failures: list[tuple[str, LLMGatewayError]] = list(failures)
        detail = "; ".join(
            f"{name}: {type(exc).__name__}: {exc}" for name, exc in self.failures
        )
        super().__init__(
            f"all {len(self.failures)} providers in the fallback chain "
            f"failed — {detail}"
        )


@dataclass(frozen=True)
class GuardrailPolicy:
    """Which guardrails run, and how hard they try.

    Frozen so a policy can be built once at startup and shared: a mutable
    policy passed to several calls is a configuration race waiting to
    happen. Use :func:`dataclasses.replace` to derive a variant.

    Attributes:
        redact_input: Rewrite outgoing message text before it is sent.
        redact_output: Rewrite the reply text before returning it. Off by
            default, matching ``PIIMiddleware``'s own default: the reply
            is the analysis, and redacting it can only lose information.
            Turn it on when the text is destined for a log or the database.
        pii_types: Detector names from :data:`DETECTORS`. Validated here
            rather than at first use, so a typo fails at policy
            construction instead of mid-analysis.
        pii_strategy: One of :data:`STRATEGIES`. ``"block"`` raises
            ``PIIDetectionError`` instead of rewriting — useful in tests
            and in any future "refuse to send this" mode.
        providers: Fallback chain. Empty means "only the configured
            provider". ``None`` as an entry means "the provider
            ``LLM_PROVIDER`` names"; anything else must be a name
            :func:`app.llm.gateway.available_providers` lists.
        max_retries: Retries per provider, *beyond* the first call. 0
            disables retrying.
        retry_timeouts: Whether a connection failure (a timeout, a refused
            connection) is retried. Off by default, and the default is
            deliberate — see :func:`should_retry`.
        initial_delay / backoff_factor / max_delay / jitter: Backoff
            shape. ``backoff_factor=0`` gives a constant delay.
        sleep: Injected so a test can assert the backoff without waiting
            for it.
    """

    redact_input: bool = True
    redact_output: bool = False
    pii_types: tuple[str, ...] = DEFAULT_PII_TYPES
    pii_strategy: str = "redact"
    providers: tuple[Optional[str], ...] = ()
    max_retries: int = 1
    retry_timeouts: bool = False
    initial_delay: float = 0.5
    backoff_factor: float = 2.0
    max_delay: float = 8.0
    jitter: bool = True
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        """Normalise the sequence fields and reject a policy that cannot work.

        ``object.__setattr__`` because the dataclass is frozen — this is
        the one place that is legitimate, and it runs before the instance
        is ever visible to a caller.

        Validating here rather than at first use is the whole point: a
        policy with a misspelled ``pii_types`` entry or an unknown
        provider would otherwise fail on the first submission, i.e. on the
        path a student is waiting on, and a guardrail that silently does
        nothing is worse than one that refuses to be built.
        """
        object.__setattr__(self, "pii_types", tuple(self.pii_types))
        object.__setattr__(self, "providers", tuple(self.providers))

        unknown_types = [t for t in self.pii_types if t not in DETECTORS]
        if unknown_types:
            known = ", ".join(sorted(DETECTORS))
            raise ValueError(
                f"unknown PII type(s) {unknown_types!r} "
                f"(expected any of: {known})"
            )
        if self.pii_strategy not in STRATEGIES:
            known = ", ".join(sorted(STRATEGIES))
            raise ValueError(
                f"unknown redaction strategy {self.pii_strategy!r} "
                f"(expected one of: {known})"
            )
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")

        known_providers = available_providers()
        unknown_providers = [
            p for p in self.providers if p is not None and p not in known_providers
        ]
        if unknown_providers:
            known = ", ".join(known_providers)
            raise ValueError(
                f"unknown fallback provider(s) {unknown_providers!r} "
                f"(expected any of: {known})"
            )


@dataclass
class GuardedCompletion:
    """What one guarded call produced, and what the guardrails did to it.

    The completion itself is unchanged in shape — a caller that only wants
    the answer reads :attr:`text`. The rest is bookkeeping that the plain
    gateway cannot provide, and that matters once the guardrails are on:

    * :attr:`provider` — which provider actually answered. With a fallback
      chain configured this is not necessarily the configured one, and any
      comparison across runs (P2-5's accuracy gate) needs to know.
    * :attr:`attempts` / :attr:`retries` / :attr:`fallbacks` — the cost,
      in model calls, of getting this one answer.
    * :attr:`redactions` — what was taken out of the prompt, so a caller
      can tell "the analysis missed the email address" apart from "the
      model ignored it".
    """

    completion: Completion
    #: Name of the provider that answered. ``settings.llm_provider`` when
    #: the chain left the choice to the gateway.
    provider: str = ""
    #: Total model calls made, retries and fallbacks included. 1 means the
    #: first attempt succeeded.
    attempts: int = 1
    #: Of :attr:`attempts`, the ones that were retries of a provider that
    #: had already failed.
    retries: int = 0
    #: Providers skipped past after the first one failed.
    fallbacks: int = 0
    #: PII removed from the outgoing prompt, in the order found.
    redactions: list[PIIMatch] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The reply text, with output redaction already applied."""
        return self.completion.content


def guardrail_policy() -> GuardrailPolicy:
    """Build the policy ``settings`` describes.

    This is the one place configuration becomes a policy, so the three
    ``LLM_*`` guardrail settings are read here and nowhere else.

    The fallback chain always starts with the configured provider, whether
    or not ``LLM_FALLBACK_PROVIDERS`` names it: listing it again would be
    redundant, and omitting it would silently make the fallback the
    primary. ``LLM_FALLBACK_PROVIDERS`` is therefore "what to try *after*
    the configured provider", which is how ``AGENTS.md`` describes it
    ("主路径 DeepSeek API，兜底 Ollama").
    """
    extras = [
        name.strip()
        for name in (settings.llm_fallback_providers or "").split(",")
        if name.strip()
    ]
    providers: tuple[Optional[str], ...] = (
        (CONFIGURED_PROVIDER, *extras) if extras else ()
    )
    return GuardrailPolicy(
        redact_input=settings.llm_pii_redaction,
        providers=providers,
        max_retries=settings.llm_retry_attempts,
    )


# ------------------------------------------------------------------ PII


def _drop_overlaps(matches: Sequence[PIIMatch]) -> list[PIIMatch]:
    """Keep the first of any two matches that cover the same characters.

    Two detectors can match the same span — an address inside a URL, a
    card number inside a longer digit run — and ``apply_strategy`` splices
    the text by offsets without checking for that. Handing it overlapping
    ranges produces mangled output rather than a double redaction, so the
    later match is dropped. Sorted by start offset, which is also the
    order ``apply_strategy`` expects.
    """
    ordered = sorted(matches, key=lambda match: match["start"])
    kept: list[PIIMatch] = []
    for match in ordered:
        if kept and match["start"] < kept[-1]["end"]:
            continue
        kept.append(match)
    return kept


def redact_text(text: str, policy: GuardrailPolicy) -> tuple[str, list[PIIMatch]]:
    """Apply *policy*'s detectors and strategy to one string.

    Returns ``(text, matches)``; the text is returned unchanged (and the
    list empty) when nothing matched, so a caller can use the match list
    as "did anything happen".

    Raises:
        PIIDetectionError: The strategy is ``"block"`` and something
            matched. Propagated rather than converted: a policy that says
            "refuse this" has to be able to refuse.
    """
    matches: list[PIIMatch] = []
    for pii_type in policy.pii_types:
        matches.extend(DETECTORS[pii_type](text))

    if not matches:
        return text, []

    matches = _drop_overlaps(matches)
    return apply_strategy(text, matches, policy.pii_strategy), matches


def redact_messages(
    messages: Sequence[Mapping[str, Any]],
    policy: GuardrailPolicy,
) -> tuple[list[dict[str, Any]], list[PIIMatch]]:
    """Redact every string ``content`` in a message list.

    Only ``str`` content is touched. A message whose content is a list
    (the multimodal shape the OpenAI protocol allows) is passed through
    untouched rather than guessed at — a guardrail that mangles a payload
    it does not understand is worse than one that leaves it alone.

    The input is never mutated: a caller's message list is a value it may
    still be holding, and redacting it in place would make a retry send
    already-redacted text as if it were the original.
    """
    redacted: list[dict[str, Any]] = []
    found: list[PIIMatch] = []

    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text, matches = redact_text(content, policy)
            if matches:
                found.extend(matches)
                message = {**message, "content": text}
        redacted.append(dict(message))

    return redacted, found


def redact_for_log(text: str, policy: Optional[GuardrailPolicy] = None) -> str:
    """Redact *text* for a log line.

    The reason this exists: ``analyzer`` and ``team`` both log the raw
    model reply when it cannot be parsed — deliberately, because "not
    valid JSON at position 654" is a finding nobody can act on. A raw
    reply is also the one place a student's own text comes back out
    verbatim, so it is the line that most needs scrubbing.

    Never raises: a log line is not worth failing a request over, and a
    ``"block"`` strategy (which raises by design) is treated as "redact"
    here for that reason.
    """
    policy = policy or guardrail_policy()
    if policy.pii_strategy == "block":
        policy = replace(policy, pii_strategy="redact")
    try:
        return redact_text(text, policy)[0]
    except Exception as exc:  # noqa: BLE001 — logging must not raise
        logger.warning("PII redaction for a log line failed: %s", exc)
        return text


# ------------------------------------------------------ retry / fallback


def should_retry(exc: LLMGatewayError, policy: GuardrailPolicy) -> bool:
    """Whether asking the *same* provider again could plausibly work.

    The split is by failure class, because the gateway already
    distinguishes them and the distinctions are exactly the retry
    decision:

    * ``LLMRequestError`` 5xx / 429 — retryable. The provider answered, it
      just could not serve this request; 429 in particular is the textbook
      case for backoff.
    * ``LLMRequestError`` 4xx (anything else) — **not** retryable. A 400
      or a 404 means our payload or URL is wrong, and re-sending it
      produces the same rejection. A 401/402 is a credential or balance
      problem, which is a job for the fallback, not for a retry.
    * ``LLMConfigError`` — not retryable. Detected before the request was
      even sent, so a retry cannot succeed.
    * ``TruncatedCompletionError`` — not retryable. The reply hit
      ``max_tokens``, and the remedy is a bigger budget, not another
      identical request.
    * ``EmptyCompletionError`` — retryable. The provider is up and simply
      produced nothing, which is the one failure where a second draw is
      genuinely a second draw.
    * ``LLMConnectionError`` — **off by default**, on by request.

    That last one is the interesting case, and the default follows
    ``analyzer``'s existing decision rather than fighting it: a timeout
    has already cost the full ``LLM_TIMEOUT``, and retrying it doubles the
    wait of a student sitting in front of ``judge_done`` during an outage
    that is not going to clear in the next two seconds. A 500 comes back
    immediately and is cheap to retry; a 60-second timeout is not.
    ``retry_timeouts=True`` opts into the other trade.
    """
    if isinstance(exc, LLMConfigError):
        return False
    if isinstance(exc, TruncatedCompletionError):
        return False
    if isinstance(exc, EmptyCompletionError):
        return True
    if isinstance(exc, LLMConnectionError):
        return policy.retry_timeouts
    if isinstance(exc, LLMRequestError):
        status = exc.status_code
        if status is None:
            return False
        return status == 429 or status >= 500
    return False


def should_fallback(exc: LLMGatewayError) -> bool:
    """Whether moving to the next provider is worth a call.

    True for everything except a truncation. A truncation is a *budget*
    problem: the next provider gets the same ``max_tokens`` and cuts off
    in the same place, so switching would spend a second call to reproduce
    the first failure — and, worse, would report it as "the fallback also
    failed" instead of "raise ``LLM_MAX_TOKENS``", which is the only thing
    that actually fixes it.

    Everything else — an unreachable host, a 5xx, a rate limit, a missing
    key, an empty reply — is a statement about *that provider*, which is
    precisely what a fallback chain exists to route around.
    """
    return not isinstance(exc, TruncatedCompletionError)


def _delay_for(retry_number: int, policy: GuardrailPolicy) -> float:
    """Seconds to wait before retry ``retry_number`` (0-indexed).

    Exponential backoff with a cap and optional ±25% jitter. Deliberately
    simple arithmetic rather than a call into LangChain's: that helper
    lives in ``langchain.agents.middleware._retry``, a private module, and
    depending on it would turn a routine version bump into an import-time
    failure of the whole sidecar.

    The jitter is not decoration — without it, a provider that comes back
    from a blip receives every client's retry in the same instant.
    """
    if policy.backoff_factor == 0.0:
        delay = policy.initial_delay
    else:
        delay = policy.initial_delay * (policy.backoff_factor**retry_number)
    delay = min(delay, policy.max_delay)

    if policy.jitter and delay > 0:
        spread = delay * 0.25
        delay = max(0.0, delay + random.uniform(-spread, spread))
    return delay


def _call_with_retries(
    call: Callable[..., Completion],
    messages: list[dict[str, Any]],
    provider: Optional[str],
    policy: GuardrailPolicy,
    kwargs: Mapping[str, Any],
) -> tuple[Completion | LLMGatewayError, int]:
    """Call one provider, retrying per *policy*.

    Returns ``(result, calls_made)`` where *result* is either the
    completion or the last :class:`~app.llm.gateway.LLMGatewayError`. The
    error is returned rather than raised so the call count survives it — a
    caller that only learned "it failed" could not say whether the
    provider was asked once or ``1 + max_retries`` times, and that number
    is the whole cost story of a guarded call.
    """
    for retry in range(policy.max_retries + 1):
        # ``calls`` is what this pass will have spent by the time it
        # returns, success or failure: one for the first call, two once a
        # retry has fired.
        calls = retry + 1
        try:
            return call(messages, provider=provider, **kwargs), calls
        except LLMGatewayError as exc:
            if retry >= policy.max_retries or not should_retry(exc, policy):
                return exc, calls
            delay = _delay_for(retry, policy)
            logger.warning(
                "provider %s failed (%s: %s); retrying in %.2fs (%d/%d)",
                provider or settings.llm_provider,
                type(exc).__name__,
                exc,
                delay,
                retry + 1,
                policy.max_retries,
            )
            policy.sleep(delay)

    # Unreachable: every pass returns.
    raise AssertionError("retry loop fell through")


def guard_completion(
    messages: Sequence[Mapping[str, Any]],
    *,
    policy: Optional[GuardrailPolicy] = None,
    caller: Optional[Callable[..., Completion]] = None,
    **kwargs: Any,
) -> GuardedCompletion:
    """Make one model call through the guardrail chain.

    The chain, outermost first::

        messages → PII 脱敏 → provider 兜底链 → 每个 provider 内部做重试

    The order is the point. Redaction happens once, on the way in, so a
    retry and a fallback both send the already-redacted text — doing it
    per attempt would be the same work repeated. Retrying sits *inside*
    the fallback because the two answer different questions: "is this
    provider momentarily unhappy" (retry) versus "is this provider out of
    service entirely" (fallback). Trying a second provider before
    retrying the first would abandon a provider over one 500.

    Args:
        messages: OpenAI-style message dicts.
        policy: The guardrails to apply. Built from ``settings`` when
            omitted.
        caller: The model call to wrap. Defaults to
            :func:`app.llm.gateway.complete`; injected so a test can
            script failures without a network.
        **kwargs: Forwarded to ``caller`` verbatim (``temperature``,
            ``max_tokens``, ``timeout``, ``tools`` …).

    Returns:
        A :class:`GuardedCompletion` — the completion plus what the
        guardrails did.

    Raises:
        LLMGatewayError: The call failed. With one provider the original
            exception is re-raised unchanged, so a caller that keys off
            ``LLMConfigError`` / ``TruncatedCompletionError`` keeps
            working; with a chain, :class:`AllProvidersFailedError`
            carries every provider's failure.
        PIIDetectionError: The policy's strategy is ``"block"`` and the
            prompt contained something it names.
    """
    policy = policy or guardrail_policy()
    call = caller or complete

    prepared: list[dict[str, Any]] = [dict(m) for m in messages]
    redactions: list[PIIMatch] = []
    if policy.redact_input:
        prepared, redactions = redact_messages(messages, policy)
        if redactions:
            logger.info(
                "redacted %d PII match(es) from an outgoing prompt: %s",
                len(redactions),
                ", ".join(redacted_types(redactions)),
            )

    providers: tuple[Optional[str], ...] = policy.providers or (CONFIGURED_PROVIDER,)

    attempts = 0
    retries = 0
    failures: list[tuple[str, LLMGatewayError]] = []

    for index, provider in enumerate(providers):
        result, calls = _call_with_retries(call, prepared, provider, policy, kwargs)
        # Counted for every provider, not just the one that answered: a
        # chain whose first provider burned two retries before the fallback
        # took over has spent three calls, and reporting one would make the
        # cost of a degraded run invisible.
        attempts += calls
        retries += calls - 1

        if isinstance(result, LLMGatewayError):
            error = result
            name = provider or settings.llm_provider
            failures.append((name, error))

            if index == 0 and (not should_fallback(error) or len(providers) == 1):
                # Either there is no chain, or the failure is one a
                # different provider would reproduce (a truncation). The
                # caller gets exactly what the gateway raised — no wrapper
                # to unpack, and the type it already keys off.
                raise error

            if not should_fallback(error) or index == len(providers) - 1:
                raise AllProvidersFailedError(failures) from error

            logger.warning(
                "provider %s is out of service (%s: %s); falling back to %s",
                name,
                type(error).__name__,
                error,
                providers[index + 1] or settings.llm_provider,
            )
            continue

        completion = result
        if policy.redact_output:
            completion = _redact_completion(completion, policy)
        return GuardedCompletion(
            completion=completion,
            provider=provider or settings.llm_provider,
            attempts=attempts,
            retries=retries,
            fallbacks=index,
            redactions=redactions,
        )

    # Unreachable: the loop returns on success and raises on the last
    # provider. Kept so a future edit that breaks that invariant fails
    # loudly instead of returning ``None``.
    raise AllProvidersFailedError(failures)


def _redact_completion(
    completion: Completion, policy: GuardrailPolicy
) -> Completion:
    """Return *completion* with its content redacted.

    ``Completion`` is frozen, so this is a copy rather than an edit — and
    that is the right shape anyway: the caller may still be holding the
    original.
    """
    text, _ = redact_text(completion.content, policy)
    if text == completion.content:
        return completion
    return replace(completion, content=text)


def guarded_chat_completion(
    messages: Sequence[Mapping[str, Any]],
    *,
    policy: Optional[GuardrailPolicy] = None,
    caller: Optional[Callable[..., Completion]] = None,
    **kwargs: Any,
) -> str:
    """The guarded twin of :func:`app.llm.gateway.chat_completion`.

    Same contract, same "never empty" guarantee, plus the guardrails. It
    exists so switching a call site over is a change of function name and
    nothing else — which is what keeps "turn the guardrails on" a
    reviewable, one-line decision rather than a refactor.

    Raises:
        LLMConfigError: ``tools`` or ``tool_choice`` was passed. Same
            reasoning as the gateway's own check: this returns text, and a
            tool-calling reply's content is legitimately empty.
    """
    if "tools" in kwargs or "tool_choice" in kwargs:
        raise LLMConfigError(
            "guarded_chat_completion() returns text only. Use "
            "guard_completion() when passing 'tools' or 'tool_choice'."
        )

    return guard_completion(
        messages, policy=policy, caller=caller, **kwargs
    ).text


def redacted_types(matches: Iterable[PIIMatch]) -> list[str]:
    """Distinct PII types in *matches*, sorted. A convenience for logging."""
    return sorted({match["type"] for match in matches})


def complete_through_guardrails(
    messages: Sequence[Mapping[str, Any]],
    **kwargs: Any,
) -> Completion:
    """A :func:`app.llm.gateway.complete`-shaped call that runs the chain.

    This is what lets the **tool loop** be guarded. :func:`guard_completion`
    returns a :class:`GuardedCompletion` — the completion plus the bookkeeping
    about what the guardrails did — and the loop wants the completion itself,
    so this unwraps it.

    The bookkeeping is not discarded silently: ``attempts`` above 1 means
    retries or a fallback happened, which is exactly the kind of thing worth
    knowing when a student's analysis took three times as long as usual.

    Raises:
        LLMGatewayError: same as the gateway — the chain only adds retries
            and fallbacks, and when all of them fail the last error is the
            one raised.
    """
    guarded = guard_completion(messages, **kwargs)
    if guarded.attempts > 1 or guarded.fallbacks:
        logger.info(
            "guarded completion needed %s attempts (%s retries, %s fallbacks) "
            "before answering",
            guarded.attempts,
            guarded.retries,
            guarded.fallbacks,
        )
    return guarded.completion
