"""Tests for the error analyzer (P2-3).

The gateway is patched in every test. That is not incidental: this
module's job is orchestration — render the prompt, call the model,
validate, retry once with the failure fed back — and each of those
steps is already tested on its own (P2-0, P2-1, P2-2). What is under
test here is the wiring, and specifically the *decisions* it makes:
when to retry, what to send on the retry, and what to do when the model
or the network refuses to cooperate.

The last of those is the one that matters most in production. P2-4
calls this from the judging path, so an exception escaping here would
turn a successfully judged submission into a 500.
"""

from unittest.mock import patch

import pytest

from app.agent import analyzer
from app.agent.analyzer import AnalysisOutcome, analyze_error
from app.llm.gateway import (
    LLMConnectionError,
    LLMRequestError,
    TruncatedCompletionError,
)

VALID = (
    '{"error_type":"overflow","error_detail":"第 4 行的 a * b 在 int 内先溢出",'
    '"hint":"看看乘法的两个操作数是什么类型","suggested_fix":"让运算前就是 64 位"}'
)

# A reply whose label is a Chinese translation rather than the taxonomy
# token — the most realistic failure, and one P2-2 rejects by design.
BAD_LABEL = (
    '{"error_type":"整数溢出","error_detail":"d","hint":"h",'
    '"suggested_fix":"s"}'
)


def _call(**kwargs):
    """``analyze_error`` with the required positional args filled in."""
    params = {"statement": "读入两个整数，输出它们的乘积。"}
    params.update(kwargs)
    return analyze_error("int a, b; std::cin >> a >> b;", "3 7", "21", "0", **params)


def _prompt_sent(mock):
    """The user prompt from the first gateway call."""
    return mock.call_args_list[0][0][0][0]["content"]


# ------------------------------------------------------------ happy path


def test_valid_reply_yields_a_result_in_one_attempt():
    """The normal path: one call, one result."""
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        outcome = _call()

    assert outcome.ok
    assert outcome.result.error_type == "overflow"
    assert outcome.attempts == 1
    assert mock.call_count == 1


def test_prompt_contains_the_submission_facts():
    """The rendered prompt actually carries the data being analysed.

    Guards against a wiring slip where the template renders but a
    variable is filled from the wrong argument — an error that would
    still produce a valid-looking analysis of the wrong thing.
    """
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        analyze_error(
            "SYNTAX_UNIQUE_TOKEN();",
            "STDIN_TOKEN",
            "EXPECTED_TOKEN",
            "ACTUAL_TOKEN",
            statement="STATEMENT_TOKEN",
        )

    prompt = _prompt_sent(mock)
    for token in (
        "SYNTAX_UNIQUE_TOKEN();",
        "STDIN_TOKEN",
        "EXPECTED_TOKEN",
        "ACTUAL_TOKEN",
        "STATEMENT_TOKEN",
    ):
        assert token in prompt


def test_prompt_includes_the_full_taxonomy():
    """Every scorable label is offered to the model.

    P2-5 scores exact match against ``ROOT_CAUSE_LABELS``; a label the
    prompt omits can never be emitted, so its cases are unwinnable and
    the accuracy number understates the model.
    """
    from app.eval_cases import ROOT_CAUSE_LABELS

    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call()

    prompt = _prompt_sent(mock)
    for label in ROOT_CAUSE_LABELS:
        assert label in prompt


def test_prompt_leaves_no_unsubstituted_placeholders():
    """No ``{{variable}}`` survives into the request sent to the model."""
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call()

    assert "{{" not in _prompt_sent(mock)


def test_missing_statement_is_replaced_with_an_explicit_note():
    """An absent problem statement is *described*, not blanked.

    A judge call with inline test cases has no statement to pass. An
    empty block would read to the model as "the statement is empty",
    which is a different and misleading claim than "none was supplied".
    """
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call(statement="")

    assert "题面未提供" in _prompt_sent(mock)


def test_extra_context_is_appended_for_retrieval():
    """P2-17's retrieval results reach the prompt through this seam."""
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call(extra_context="KNOWLEDGE_CARD_TOKEN")

    assert "KNOWLEDGE_CARD_TOKEN" in _prompt_sent(mock)


def test_verdict_is_forwarded():
    """The judge verdict reaches the prompt.

    Without it the model cannot distinguish WA from TLE, and
    ``wrong_complexity`` becomes unreachable.
    """
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call(verdict="TLE")

    assert "TLE" in _prompt_sent(mock)


def test_temperature_is_zero_for_reproducibility():
    """Analysis runs cold, so the same submission yields the same label.

    P2-5's accuracy number is only meaningful if a repeated run scores
    the same; sampling variation would show up as noise in the gate.
    """
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        _call()

    assert mock.call_args.kwargs["temperature"] == 0.0


def test_the_token_budget_comes_from_settings():
    """The cap is configuration, not a constant in this module.

    A reasoning model bills its hidden reasoning against the same budget,
    so the value that works is a property of the model. Hardcoding it
    here would make "换模型只改配置" (AGENTS.md §二.2) false, and the
    failure it produces is a truncated reply that looks like a formatting
    bug rather than a budget one.
    """
    from app.config import settings

    with (
        patch.object(settings, "llm_max_tokens", 12345),
        patch.object(analyzer, "chat_completion", return_value=VALID) as mock,
    ):
        _call()

    assert mock.call_args.kwargs["max_tokens"] == 12345


def test_the_timeout_comes_from_settings():
    """The timeout is configurable for the same reason as the cap.

    The gateway's 60s default is fine for a local 3B model and far too
    short for a hosted endpoint writing thousands of tokens. Getting this
    wrong does not raise a config error — it produces a timeout that the
    accuracy harness scores as the *model* answering incorrectly, which
    is exactly the kind of misattribution worth pinning down.
    """
    from app.config import settings

    with (
        patch.object(settings, "llm_timeout", 123.5),
        patch.object(analyzer, "chat_completion", return_value=VALID) as mock,
    ):
        _call()

    assert mock.call_args.kwargs["timeout"] == 123.5


# ---------------------------------------------------------------- retry


def test_retry_fires_once_on_a_validation_failure():
    """The spec's "重试一次": one extra call, no more."""
    with patch.object(
        analyzer, "chat_completion", side_effect=[BAD_LABEL, VALID]
    ) as mock:
        outcome = _call()

    assert outcome.ok
    assert outcome.attempts == 2
    assert mock.call_count == 2


def test_retry_feeds_the_failure_back_to_the_model():
    """The retry is a correction, not a second opinion.

    This is the substance of the design decision: resending the identical
    prompt tends to reproduce the identical mistake, so the second call
    carries the previous reply, the validation error, and an explicit
    restatement of the constraint.
    """
    with patch.object(
        analyzer, "chat_completion", side_effect=[BAD_LABEL, VALID]
    ) as mock:
        _call()

    second_messages = mock.call_args_list[1][0][0]
    # The prior reply is echoed as an assistant turn...
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["content"] == BAD_LABEL
    # ...and the correction names a concrete requirement.
    correction = second_messages[-1]["content"]
    assert second_messages[-1]["role"] == "user"
    assert "无法被解析" in correction
    assert "error_type" in correction


def test_retry_keeps_the_original_prompt_in_the_conversation():
    """The retry is a continuation, so the model still has the submission.

    Dropping the first user turn would leave the corrective message
    asking the model to fix output it can no longer see.
    """
    with patch.object(
        analyzer, "chat_completion", side_effect=[BAD_LABEL, VALID]
    ) as mock:
        _call()

    second_messages = mock.call_args_list[1][0][0]
    assert len(second_messages) == 3
    assert second_messages[0]["role"] == "user"
    assert "int a, b;" in second_messages[0]["content"]


def test_two_failures_return_an_unsuccessful_outcome_without_raising():
    """Exhausting the retries is a returned failure, never an exception."""
    with patch.object(analyzer, "chat_completion", return_value=BAD_LABEL) as mock:
        outcome = _call()

    assert not outcome.ok
    assert outcome.result is None
    assert outcome.attempts == 2
    assert mock.call_count == 2


def test_rejected_replies_are_recorded_for_diagnosis():
    """Every rejected reply is kept, so a systematic prompt bug is visible.

    Knowing *what* the model kept saying is the difference between "the
    prompt needs work" and "this case is genuinely hard".
    """
    with patch.object(analyzer, "chat_completion", return_value=BAD_LABEL):
        outcome = _call()

    assert len(outcome.rejected) == 2
    assert all("整数溢出" in reply for reply in outcome.rejected)


def test_max_attempts_of_one_disables_the_retry():
    """The retry count is configurable — P2-5 may want a cheaper run."""
    with patch.object(analyzer, "chat_completion", return_value=BAD_LABEL) as mock:
        outcome = _call(max_attempts=1)

    assert not outcome.ok
    assert mock.call_count == 1


def test_zero_max_attempts_is_rejected():
    """A nonsensical budget is an error, not a silent no-op."""
    with pytest.raises(ValueError, match="max_attempts"):
        _call(max_attempts=0)


# ----------------------------------------------------- gateway failures


def test_connection_failure_does_not_retry():
    """An unreachable provider is not retried.

    Re-sending the same request would only double the latency of the
    student-facing path during an outage, and P2-4 already degrades by
    leaving the entry unanalysed. The call count is the assertion.
    """
    with patch.object(
        analyzer, "chat_completion", side_effect=LLMConnectionError("refused")
    ) as mock:
        outcome = _call()

    assert not outcome.ok
    assert mock.call_count == 1


def test_request_failure_does_not_retry():
    """An HTTP error from the provider is not retried either."""
    with patch.object(
        analyzer,
        "chat_completion",
        side_effect=LLMRequestError("bad request", status_code=400),
    ) as mock:
        outcome = _call()

    assert not outcome.ok
    assert mock.call_count == 1


def test_truncation_does_not_retry():
    """A reply cut off by the token budget is not retried.

    The retry exists to let the model *correct its formatting*, and it
    re-sends the same request with the same budget. When the budget is
    what ran out, a second identical call cannot help — it will be cut
    off in the same place. The remedy is configuration, and the error
    the caller receives names it.
    """
    with patch.object(
        analyzer,
        "chat_completion",
        side_effect=TruncatedCompletionError("hit the max_tokens limit"),
    ) as mock:
        outcome = _call()

    assert not outcome.ok
    assert mock.call_count == 1
    assert "TruncatedCompletionError" in outcome.error
    assert "max_tokens" in outcome.error


def test_gateway_error_is_reported_with_its_type():
    """The failure reason keeps the exception type, not just the message.

    P2-0's whole point was distinguishing "unreachable" from "rejected
    our payload"; collapsing that here would throw the distinction away.
    """
    with patch.object(
        analyzer, "chat_completion", side_effect=LLMConnectionError("refused")
    ):
        outcome = _call()

    assert "LLMConnectionError" in outcome.error


def test_unexpected_exception_is_not_swallowed():
    """Only LLM failures are absorbed; a genuine bug still surfaces.

    ``analyze_error`` promises not to raise for *provider* problems. A
    ``TypeError`` from a coding mistake is not that, and hiding it would
    leave a silently analysis-free application with no trace of why.
    """
    with patch.object(
        analyzer, "chat_completion", side_effect=TypeError("a real bug")
    ), pytest.raises(TypeError):
        _call()


# ------------------------------------------------------------- guards


def test_blank_code_short_circuits_without_a_call():
    """Nothing is sent when there is nothing to analyse.

    Worth an explicit guard rather than letting the model receive an
    empty code block and invent a cause for it.
    """
    with patch.object(analyzer, "chat_completion", return_value=VALID) as mock:
        outcome = analyze_error("   ", "3 7", "21", "0")

    assert not outcome.ok
    assert mock.call_count == 0
    assert "no code" in outcome.error


def test_outcome_ok_reflects_holding_a_result():
    """``AnalysisOutcome.ok`` is the single thing callers branch on."""
    assert AnalysisOutcome(result=object()).ok is True
    assert AnalysisOutcome(error="boom").ok is False
