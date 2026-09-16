"""Root-cause analysis of a failing submission (P2-3).

P2-3 is where the three S2 pieces meet: the prompt template (P2-1) is
filled with the submission's facts, sent through the LLM gateway
(P2-0), and the reply is validated against the schema (P2-2).
:func:`analyze_error` is the only function the rest of the app calls.

Why the retry carries the failure back to the model
---------------------------------------------------
The spec asks for "失败则重试一次". The obvious implementation — call
again with the same prompt — is close to useless: if the model answered
``"整数溢出"`` or wrapped its JSON in prose once, sampling again tends to
produce the same thing. So the retry sends the *previous* reply, the
validation error, and an explicit restatement of the constraint. That
turns a second independent draw into a correction.

One retry, not three. Each attempt is a paid API call made while a
student waits, and the two failures actually observed in practice
(prose around the JSON, a translated label) are both fixable in a
single corrective turn. A model that cannot fix its output given the
opportunity will not be fixed by a third attempt either.

Failure is absorbed, not propagated
-----------------------------------
``analyze_error`` returns ``None`` rather than raising when analysis
cannot be produced. P2-4 calls it from the judging path, and AGENTS.md's
framing of S2 is "WA 时调 LLM 输出错因" — an enrichment. A missing
analysis must never turn a successfully judged submission into a 500,
and the mistake-book entry it belongs to stays ``error_cause=""``,
which is already the documented marker for "not yet analysed"
(``app/models/mistake_book.py``). Callers that *do* care can inspect the
returned :class:`AnalysisOutcome`'s error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.agent.schemas import (
    ErrorAnalysisParseError,
    ErrorAnalysisResult,
    parse_error_analysis,
)
from app.config import settings
from app.llm.gateway import LLMGatewayError, chat_completion
from app.prompts import loader

# Prompt template this module drives. Named here so the template and the
# call site stay in one place.
PROMPT_NAME = "error_analysis"

# Low by design: the task is classification against a fixed label set,
# not prose generation, and the same submission should yield the same
# label on a repeat run. P2-5's accuracy number is only meaningful if it
# is reproducible.
DEFAULT_TEMPERATURE = 0.0

# Analyses are short (four fields, a few sentences each), but how many
# tokens one actually takes is a property of the *model*: a reasoning
# model bills its hidden reasoning against the same budget, and a
# non-reasoning one still wrote ~6600 characters here. So the cap lives
# in configuration rather than as a constant in this module, which is
# what AGENTS.md §二.2 ("换模型只改配置，不改代码") requires. See
# ``settings.llm_max_tokens`` for the calibration measurements and the
# reasoning-model caveat.
#
# The timeout is the same story and is therefore configured alongside it:
# a hosted endpoint producing thousands of tokens outlasts the gateway's
# 60s default, and the resulting timeout is scored as a *model* miss.


@dataclass
class AnalysisOutcome:
    """The result of an analysis attempt, success or failure.

    Returning this rather than a bare ``Optional[ErrorAnalysisResult]``
    keeps the reason for a failure available to whoever wants it. P2-4
    ignores it and treats ``result is None`` as "no analysis yet", while
    the P2-5 harness and the tests need to know *why* a case was missed —
    a model giving a wrong label and a gateway being unreachable are
    completely different findings.
    """

    result: Optional[ErrorAnalysisResult] = None
    error: Optional[str] = None
    # How many gateway calls were made. 2 means the retry fired, which is
    # worth being able to count across the eval set.
    attempts: int = 0
    # Labels the model produced that failed validation, in order. Useful
    # for diagnosing a systematic prompt problem.
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None


def _render_prompt(
    *,
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    statement: str,
    verdict: str,
    extra_context: Optional[str] = None,
) -> str:
    """Assemble the analysis prompt for one submission."""
    taxonomy = loader.render_taxonomy()

    variables = {
        "verdict": verdict,
        "stdin": stdin,
        "expected_output": expected,
        "actual_output": actual,
        "statement": statement,
        "code": code,
        "root_cause_taxonomy": taxonomy,
    }

    # ``statement`` is optional metadata (a judge call from the
    # ``/judge`` route carries inline test cases but not the problem
    # text). The template requires the variable, so an absent statement
    # is substituted with an explicit note rather than an empty string —
    # an empty block reads to the model as "the statement is blank",
    # which is a different and misleading claim.
    if not (statement or "").strip():
        variables["statement"] = (
            "（题面未提供。请只根据输入、期望输出、实际输出和代码本身推断，"
            "并在 error_detail 中说明你缺少题面。）"
        )

    if extra_context:
        # Retrieval results land here from P2-17. Appended rather than
        # templated so the template does not need a variable that is
        # empty for every call until S5.
        variables["code"] = f"{variables['code']}\n\n{extra_context}"

    return loader.render(PROMPT_NAME, **variables)


def _correction_prompt(previous: str, problem: str) -> str:
    """Build the corrective follow-up sent after a validation failure.

    Kept terse and specific: repeat the offending output, name what was
    wrong with it, and restate the exact constraint. Vague instructions
    ("please output valid JSON") are what the model just failed to
    follow, so they are not repeated verbatim.
    """
    return (
        "你上一次的输出无法被解析。\n\n"
        f"错误：{problem}\n\n"
        "你上一次的输出是：\n"
        f"{previous}\n\n"
        "请重新作答。要求：\n"
        "1. 只输出一个 JSON 对象，前后不要有任何文字、解释或 markdown 代码块。\n"
        "2. error_type 必须逐字符等于给定的标签之一（英文、全小写、下划线分隔）。\n"
        "3. error_detail、hint、suggested_fix 都要非空，用中文。\n"
        "4. 不要添加任何额外的字段。"
    )


def _analyse_once(messages: list[dict[str, str]]) -> tuple[str, Optional[str]]:
    """Make one gateway call.

    Returns:
        ``(raw_text, error)`` — exactly one of the two is meaningful.
    """
    try:
        return (
            chat_completion(
                messages,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout,
            ),
            None,
        )
    except LLMGatewayError as exc:
        return "", f"{type(exc).__name__}: {exc}"


def analyze_error(
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    *,
    statement: str = "",
    verdict: str = "WA",
    extra_context: Optional[str] = None,
    max_attempts: int = 2,
) -> AnalysisOutcome:
    """Analyse why ``code`` produced ``actual`` instead of ``expected``.

    Args:
        code: The submitted source.
        stdin: The input that exposed the failure.
        expected: The expected output for that input.
        actual: What the program actually printed.
        statement: Problem statement, when available. Absent for an
            ad-hoc run against inline test cases.
        verdict: The judge verdict, passed through to the prompt so the
            model can tell "wrong answer" from "too slow".
        extra_context: Retrieved knowledge to append to the code block
            (P2-17). ``None`` until RAG exists.
        max_attempts: Total gateway calls allowed, including the first.
            Defaults to 2 — the spec's "重试一次".

    Returns:
        An :class:`AnalysisOutcome`. ``outcome.ok`` is False when the
        analysis could not be produced; the caller decides what to do
        about that, and P2-4's answer is "leave the entry unanalysed".

    Never raises for an LLM-side failure: see the module docstring.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    # A blank submission is not analysable and it is worth saying so
    # rather than spending a call on it.
    if not (code or "").strip():
        return AnalysisOutcome(error="no code to analyse", attempts=0)

    prompt = _render_prompt(
        code=code,
        stdin=stdin,
        expected=expected,
        actual=actual,
        statement=statement,
        verdict=verdict,
        extra_context=extra_context,
    )
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]

    outcome = AnalysisOutcome()

    for attempt in range(1, max_attempts + 1):
        raw, gateway_error = _analyse_once(messages)
        outcome.attempts = attempt

        if gateway_error is not None:
            outcome.error = gateway_error
            # A gateway failure is not the model's fault and re-sending
            # the same prompt would only repeat it. A 5xx or a timeout
            # has some chance of succeeding, but retrying here would
            # double the latency of the student-facing path on every
            # outage; P2-4 degrades instead.
            return outcome

        try:
            outcome.result = parse_error_analysis(raw)
            return outcome
        except ErrorAnalysisParseError as exc:
            outcome.rejected.append(raw[:500])
            outcome.error = str(exc)

            if attempt >= max_attempts:
                break

            # Carry the failure back to the model so the retry is a
            # correction rather than a second opinion.
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": _correction_prompt(raw, str(exc))},
            ]

    return outcome
