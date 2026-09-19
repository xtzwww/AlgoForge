"""Tests for the S10 guardrail middleware (`AGENTS.md` S10).

Three guardrails wrap one model call — PII 脱敏 / ModelRetry / ModelFallback
— and the whole file runs against a scripted caller, so nothing here touches
a network. What is under test is the *decision* each guardrail makes:

* which failures are worth asking the same provider about again (a 500 and
  a 429 are; a 400, a missing key, a truncation and — by default — a
  timeout are not),
* which failures are worth a *different* provider (everything but a
  truncation, which the next provider would reproduce),
* and what gets redacted out of the prompt (email and card numbers, but
  not the IP / MAC / URL shapes that algorithm problems are *about*).

Two seams are stubbed. The **caller** is injected into every test, which is
also what keeps ``conftest._no_real_llm_calls`` from mattering — that
fixture patches ``guardrails.complete``, and a test that forgot to inject
would otherwise make a real, billed request (several, with a retry policy
attached). One test asserts the fixture is still wired to this module.

The **PII detectors** are LangChain's and are not re-tested here beyond
what this module's policy layer adds: which types are on by default, how
overlaps are handled, and that a ``"block"`` policy is still able to
refuse.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

import pytest
from langchain.agents.middleware.pii import PIIDetectionError

from app.agent import guardrails
from app.config import settings
from app.llm.gateway import (
    Completion,
    EmptyCompletionError,
    LLMConfigError,
    LLMConnectionError,
    LLMGatewayError,
    LLMRequestError,
    TruncatedCompletionError,
)

# --------------------------------------------------------------- helpers

REPLY = Completion(content="分析结果")


class FakeCaller:
    """A model call that plays back a script.

    Each entry is either a :class:`~app.llm.gateway.Completion` to return or
    an exception instance to raise. Running out of script raises
    ``AssertionError`` — deliberately not an ``LLMGatewayError``, so a test
    that under-scripted a retry chain fails loudly instead of being
    absorbed by the code under test as "the provider failed".
    """

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: Any, *, provider: Optional[str] = None, **kwargs: Any):
        self.calls.append(
            {"messages": messages, "provider": provider, "kwargs": kwargs}
        )
        if not self.outcomes:
            raise AssertionError(
                f"the caller was invoked {len(self.calls)} time(s) but only "
                f"{len(self.calls) - 1} outcome(s) were scripted"
            )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def providers(self) -> list[Optional[str]]:
        """The provider each call was routed to, in order."""
        return [call["provider"] for call in self.calls]


class Sleeper:
    """Records backoff delays instead of waiting for them."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def policy(**kwargs: Any) -> guardrails.GuardrailPolicy:
    """A policy with a no-op sleeper, so no test ever actually waits."""
    kwargs.setdefault("sleep", Sleeper())
    return guardrails.GuardrailPolicy(**kwargs)


def http_error(status: int) -> LLMRequestError:
    return LLMRequestError(f"provider returned HTTP {status}", status_code=status)


# ---------------------------------------------------- policy validation


def test_policy_rejects_an_unknown_pii_type() -> None:
    """A typo must fail at construction, not silently stop redacting.

    A guardrail that quietly does nothing is the one failure mode worse
    than not having it, because the configuration says it is on.
    """
    with pytest.raises(ValueError, match="unknown PII type"):
        guardrails.GuardrailPolicy(pii_types=("email", "social_security"))


def test_policy_rejects_an_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown redaction strategy"):
        guardrails.GuardrailPolicy(pii_strategy="scramble")


def test_policy_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown fallback provider"):
        guardrails.GuardrailPolicy(providers=("deepseek", "gpt5"))


def test_policy_rejects_negative_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        guardrails.GuardrailPolicy(max_retries=-1)


def test_policy_accepts_the_configured_provider_as_none() -> None:
    """``None`` in the chain means "whatever LLM_PROVIDER says"."""
    built = guardrails.GuardrailPolicy(providers=(None, "ollama"))
    assert built.providers == (None, "ollama")


def test_policy_normalises_sequences_to_tuples() -> None:
    """Lists are accepted, but the frozen policy stores tuples."""
    built = guardrails.GuardrailPolicy(
        pii_types=["email"], providers=["deepseek"]  # type: ignore[arg-type]
    )
    assert built.pii_types == ("email",)
    assert built.providers == ("deepseek",)


# ------------------------------------------------------------------ PII


def test_email_is_redacted_by_default() -> None:
    text, matches = guardrails.redact_text("联系我 a@b.com", policy())

    assert "a@b.com" not in text
    assert "[REDACTED_EMAIL]" in text
    assert [match["type"] for match in matches] == ["email"]


def test_credit_card_is_redacted_by_default() -> None:
    text, matches = guardrails.redact_text("卡号 4111 1111 1111 1111", policy())

    assert "4111" not in text
    assert [match["type"] for match in matches] == ["credit_card"]


def test_ip_mac_and_url_are_left_alone_by_default() -> None:
    """The deliberate omission, and the reason for it.

    A problem about IP 地址转换 or URL 解析 hands the model a string of
    exactly this shape. Redacting it would delete the subject matter and
    quietly make the analysis worse — a guardrail that damages the answer
    is worse than the leak it prevents.
    """
    text, matches = guardrails.redact_text(
        "10.0.0.1 aa:bb:cc:dd:ee:ff https://example.com/a", policy()
    )

    assert text == "10.0.0.1 aa:bb:cc:dd:ee:ff https://example.com/a"
    assert matches == []


def test_a_policy_can_opt_into_the_wider_detector_set() -> None:
    text, matches = guardrails.redact_text(
        "10.0.0.1", policy(pii_types=("ip",))
    )

    assert "10.0.0.1" not in text
    assert [match["type"] for match in matches] == ["ip"]


def test_mask_strategy_keeps_the_shape_of_the_address() -> None:
    text, _ = guardrails.redact_text("a@b.com", policy(pii_strategy="mask"))

    assert text == "a@****.com"


def test_hash_strategy_is_stable_across_calls() -> None:
    """A hash redaction has to be deterministic, or a log line cannot be
    correlated with the reply that produced it."""
    first, _ = guardrails.redact_text("a@b.com", policy(pii_strategy="hash"))
    second, _ = guardrails.redact_text("a@b.com", policy(pii_strategy="hash"))

    assert first == second
    assert "a@b.com" not in first


def test_block_strategy_refuses_instead_of_rewriting() -> None:
    """A policy that says "refuse this" must be able to refuse."""
    with pytest.raises(PIIDetectionError):
        guardrails.redact_text("a@b.com", policy(pii_strategy="block"))


def test_redact_text_without_a_match_returns_the_input_unchanged() -> None:
    text, matches = guardrails.redact_text("没有个人信息", policy())

    assert text == "没有个人信息"
    assert matches == []


def test_overlapping_matches_are_dropped_rather_than_spliced_twice() -> None:
    """Two detectors can name the same characters.

    ``apply_strategy`` splices by offset without checking for overlap, so
    handing it overlapping ranges mangles the text. The later match is
    dropped instead.
    """
    url = "https://example.com/a@b.com"
    text, matches = guardrails.redact_text(
        url, policy(pii_types=("url", "email"), pii_strategy="redact")
    )

    # The URL detector wins: it starts first, and the address inside it is
    # skipped rather than redacted a second time over the same span.
    assert len(matches) == 1
    assert matches[0]["type"] == "url"
    assert text == "[REDACTED_URL]"


def test_redact_messages_touches_only_string_content() -> None:
    """A multimodal content list is passed through, not guessed at."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "a@b.com"}]},
        {"role": "user", "content": "a@b.com"},
    ]
    redacted, matches = guardrails.redact_messages(messages, policy())

    assert redacted[0]["content"] == [{"type": "text", "text": "a@b.com"}]
    assert redacted[1]["content"] == "[REDACTED_EMAIL]"
    assert len(matches) == 1


def test_redact_messages_does_not_mutate_the_input() -> None:
    """The caller may still be holding the original list.

    Redacting in place would make a retry re-send already-redacted text as
    though it were the original prompt.
    """
    messages = [{"role": "user", "content": "a@b.com"}]
    guardrails.redact_messages(messages, policy())

    assert messages[0]["content"] == "a@b.com"


def test_redact_messages_preserves_the_other_message_keys() -> None:
    """A tool-calling message carries more than ``content``."""
    messages = [
        {
            "role": "assistant",
            "content": "a@b.com",
            "tool_calls": [{"id": "call_0"}],
        }
    ]
    redacted, _ = guardrails.redact_messages(messages, policy())

    assert redacted[0]["tool_calls"] == [{"id": "call_0"}]
    assert redacted[0]["role"] == "assistant"


def test_redact_for_log_never_raises_even_under_a_block_policy() -> None:
    """A log line is not worth failing a request over."""
    text = guardrails.redact_for_log(
        "a@b.com", policy(pii_strategy="block")
    )

    assert "a@b.com" not in text


def test_output_redaction_is_off_by_default() -> None:
    caller = FakeCaller(Completion(content="学生的邮箱是 a@b.com"))
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert result.text == "学生的邮箱是 a@b.com"


def test_output_redaction_rewrites_the_reply_when_asked() -> None:
    caller = FakeCaller(Completion(content="学生的邮箱是 a@b.com"))
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(redact_output=True),
        caller=caller,
    )

    assert result.text == "学生的邮箱是 [REDACTED_EMAIL]"


# ---------------------------------------------------------------- retry


def test_a_successful_call_is_not_retried() -> None:
    caller = FakeCaller(REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert result.text == REPLY.content
    assert result.attempts == 1
    assert result.retries == 0
    assert len(caller.calls) == 1


def test_a_500_is_retried_and_can_then_succeed() -> None:
    caller = FakeCaller(http_error(500), REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert result.text == REPLY.content
    assert result.attempts == 2
    assert result.retries == 1


def test_a_429_is_retried() -> None:
    """The textbook case for backoff."""
    caller = FakeCaller(http_error(429), REPLY)
    guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert len(caller.calls) == 2


def test_a_400_is_not_retried() -> None:
    """Our payload is wrong; re-sending it is rejected identically."""
    caller = FakeCaller(http_error(400))
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
        )

    assert len(caller.calls) == 1


def test_a_missing_key_is_not_retried() -> None:
    """A config error is raised before the request is even sent."""
    caller = FakeCaller(LLMConfigError("DEEPSEEK_API_KEY is empty"))
    with pytest.raises(LLMConfigError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
        )

    assert len(caller.calls) == 1


def test_a_truncated_reply_is_not_retried() -> None:
    """The remedy is a bigger budget, not another identical request."""
    caller = FakeCaller(TruncatedCompletionError("hit max_tokens"))
    with pytest.raises(TruncatedCompletionError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
        )

    assert len(caller.calls) == 1


def test_an_empty_reply_is_retried() -> None:
    """The one failure where a second draw really is a second draw."""
    caller = FakeCaller(EmptyCompletionError("returned empty content"), REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert result.attempts == 2


def test_a_timeout_is_not_retried_by_default() -> None:
    """``analyzer``'s existing decision, kept rather than fought.

    A timeout has already cost the whole ``LLM_TIMEOUT``; retrying it
    doubles how long a student waits in front of ``judge_done`` during an
    outage that will not clear in the next two seconds.
    """
    caller = FakeCaller(LLMConnectionError("deepseek timed out after 60s"))
    with pytest.raises(LLMConnectionError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
        )

    assert len(caller.calls) == 1


def test_a_timeout_is_retried_when_the_policy_asks_for_it() -> None:
    caller = FakeCaller(LLMConnectionError("deepseek timed out after 60s"), REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(retry_timeouts=True),
        caller=caller,
    )

    assert result.attempts == 2


def test_retries_are_bounded_and_the_last_error_propagates() -> None:
    caller = FakeCaller(http_error(503), http_error(503), http_error(503))
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(max_retries=2),
            caller=caller,
        )

    assert len(caller.calls) == 3


def test_max_retries_zero_means_one_call() -> None:
    caller = FakeCaller(http_error(500))
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(max_retries=0),
            caller=caller,
        )

    assert len(caller.calls) == 1


def test_backoff_grows_exponentially_and_is_capped() -> None:
    """Jitter off, so the delays are exactly the backoff curve.

    ``max_retries=4`` means five calls and therefore four waits; the last
    one is the ``max_delay`` cap rather than the 8.0 the curve would
    otherwise reach.
    """
    sleeper = Sleeper()
    caller = FakeCaller(*[http_error(500)] * 5)
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(
                max_retries=4,
                initial_delay=1.0,
                backoff_factor=2.0,
                max_delay=5.0,
                jitter=False,
                sleep=sleeper,
            ),
            caller=caller,
        )

    assert sleeper.delays == [1.0, 2.0, 4.0, 5.0]


def test_a_constant_backoff_factor_zero_gives_a_flat_delay() -> None:
    sleeper = Sleeper()
    caller = FakeCaller(*[http_error(500)] * 4)
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(
                max_retries=3,
                initial_delay=0.25,
                backoff_factor=0.0,
                jitter=False,
                sleep=sleeper,
            ),
            caller=caller,
        )

    assert sleeper.delays == [0.25, 0.25, 0.25]


def test_jitter_keeps_the_delay_within_a_quarter_of_the_curve() -> None:
    sleeper = Sleeper()
    caller = FakeCaller(*[http_error(500)] * 3)
    with pytest.raises(LLMRequestError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(
                max_retries=2,
                initial_delay=1.0,
                backoff_factor=1.0,
                jitter=True,
                sleep=sleeper,
            ),
            caller=caller,
        )

    assert len(sleeper.delays) == 2
    assert all(0.75 <= delay <= 1.25 for delay in sleeper.delays)


def test_a_retry_sends_the_same_already_redacted_prompt() -> None:
    """Redaction happens once, on the way in.

    Doing it per attempt would repeat the work and, worse, make the retry
    a different request from the one that failed.
    """
    caller = FakeCaller(http_error(500), REPLY)
    guardrails.guard_completion(
        [{"role": "user", "content": "a@b.com"}], policy=policy(), caller=caller
    )

    for call in caller.calls:
        assert call["messages"][0]["content"] == "[REDACTED_EMAIL]"


def test_redaction_can_be_turned_off() -> None:
    caller = FakeCaller(REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "a@b.com"}],
        policy=policy(redact_input=False),
        caller=caller,
    )

    assert caller.calls[0]["messages"][0]["content"] == "a@b.com"
    assert result.redactions == []


# ------------------------------------------------------------- fallback


def test_a_single_provider_reraises_the_gateway_error_unchanged() -> None:
    """With no chain, the caller gets exactly what the gateway raised.

    That matters for the existing callers: ``analyzer`` keys off
    ``LLMConfigError`` and ``TruncatedCompletionError`` by type, so
    wrapping everything in an "all providers failed" error would silently
    change their behaviour for no benefit.
    """
    caller = FakeCaller(LLMConfigError("no key"))
    with pytest.raises(LLMConfigError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
        )


def test_the_next_provider_answers_when_the_first_cannot() -> None:
    caller = FakeCaller(http_error(503), REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(providers=(None, "ollama"), max_retries=0),
        caller=caller,
    )

    assert result.text == REPLY.content
    assert result.provider == "ollama"
    assert result.fallbacks == 1
    assert result.attempts == 2
    assert caller.providers == [None, "ollama"]


def test_the_chain_is_reported_when_every_provider_fails() -> None:
    caller = FakeCaller(http_error(503), LLMConnectionError("refused"))
    with pytest.raises(guardrails.AllProvidersFailedError) as raised:
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(providers=(None, "ollama"), max_retries=0),
            caller=caller,
        )

    names = [name for name, _ in raised.value.failures]
    assert names == [settings.llm_provider, "ollama"]
    # It is still a gateway error, so callers that degrade keep degrading.
    assert isinstance(raised.value, LLMGatewayError)


def test_a_truncation_does_not_move_to_the_next_provider() -> None:
    """The next provider gets the same ``max_tokens`` and cuts off too.

    Switching would spend a call to reproduce the failure and then report
    it as "the fallback also failed", hiding the one fix that works
    (raising the budget).
    """
    caller = FakeCaller(TruncatedCompletionError("hit max_tokens"))
    with pytest.raises(TruncatedCompletionError):
        guardrails.guard_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(providers=(None, "ollama")),
            caller=caller,
        )

    assert len(caller.calls) == 1


def test_the_fallback_is_not_consulted_after_a_success() -> None:
    caller = FakeCaller(REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(providers=(None, "ollama")),
        caller=caller,
    )

    assert result.fallbacks == 0
    assert len(caller.calls) == 1


def test_each_provider_gets_its_own_retry_budget() -> None:
    """Retry is inside the fallback: the chain moves on only once the
    current provider has exhausted its retries."""
    caller = FakeCaller(
        http_error(500), http_error(500), REPLY
    )
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(providers=(None, "ollama"), max_retries=1),
        caller=caller,
    )

    assert caller.providers == [None, None, "ollama"]
    assert result.attempts == 3
    assert result.retries == 1
    assert result.fallbacks == 1


# ---------------------------------------------------------- entry points


def test_guarded_chat_completion_returns_text() -> None:
    caller = FakeCaller(REPLY)
    text = guardrails.guarded_chat_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert text == REPLY.content


def test_guarded_chat_completion_refuses_tools() -> None:
    """Same reasoning as the gateway's own check: a tool-calling reply has
    legitimately empty content, and this function promises text."""
    with pytest.raises(LLMConfigError, match="text only"):
        guardrails.guarded_chat_completion(
            [{"role": "user", "content": "hi"}],
            policy=policy(),
            caller=FakeCaller(REPLY),
            tools=[{"type": "function"}],
        )


def test_guard_completion_forwards_sampling_parameters() -> None:
    caller = FakeCaller(REPLY)
    guardrails.guard_completion(
        [{"role": "user", "content": "hi"}],
        policy=policy(),
        caller=caller,
        temperature=0.0,
        max_tokens=4096,
    )

    assert caller.calls[0]["kwargs"] == {"temperature": 0.0, "max_tokens": 4096}


def test_guard_completion_reports_the_redactions_it_made() -> None:
    caller = FakeCaller(REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "a@b.com 和 c@d.com"}],
        policy=policy(),
        caller=caller,
    )

    assert guardrails.redacted_types(result.redactions) == ["email"]
    assert len(result.redactions) == 2


def test_the_configured_provider_is_reported_when_the_chain_leaves_it_open() -> None:
    caller = FakeCaller(REPLY)
    result = guardrails.guard_completion(
        [{"role": "user", "content": "hi"}], policy=policy(), caller=caller
    )

    assert result.provider == settings.llm_provider


# ----------------------------------------------------- policy from settings


def test_guardrail_policy_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_fallback_providers", "ollama")
    monkeypatch.setattr(settings, "llm_retry_attempts", 3)
    monkeypatch.setattr(settings, "llm_pii_redaction", False)

    built = guardrails.guardrail_policy()

    # The configured provider is always first — listing it again in
    # LLM_FALLBACK_PROVIDERS would be redundant, omitting it would make the
    # fallback the primary.
    assert built.providers == (None, "ollama")
    assert built.max_retries == 3
    assert built.redact_input is False


def test_guardrail_policy_without_fallbacks_has_no_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_fallback_providers", "")

    assert guardrails.guardrail_policy().providers == ()


def test_guardrail_policy_tolerates_spaces_in_the_provider_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_fallback_providers", " ollama , deepseek ")

    assert guardrails.guardrail_policy().providers == (None, "ollama", "deepseek")


def test_guardrail_policy_rejects_a_provider_the_gateway_does_not_know(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_fallback_providers", "gpt5")

    with pytest.raises(ValueError, match="unknown fallback provider"):
        guardrails.guardrail_policy()


def test_replace_keeps_a_policy_usable() -> None:
    """``dataclasses.replace`` must not bypass ``__post_init__``'s checks."""
    wider = replace(policy(), pii_strategy="mask")

    assert wider.pii_strategy == "mask"
    with pytest.raises(ValueError, match="unknown PII type"):
        replace(policy(), pii_types=("nope",))


# ------------------------------------------------------------ suite guard


def test_the_autouse_guardrail_covers_this_module() -> None:
    """``conftest._no_real_llm_calls`` must patch ``guardrails.complete``.

    This module calls the gateway, so a test that forgot to inject a
    caller would make a real, billed request — and with a retry policy
    attached, several. The fixture is the thing that makes that impossible
    rather than merely unlikely; this pins that it is still wired up.
    """
    from app.llm import gateway

    assert guardrails.complete is not gateway.complete
