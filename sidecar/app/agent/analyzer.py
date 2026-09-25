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
from typing import TYPE_CHECKING, Callable, Optional

from app.agent.guardrails import complete_through_guardrails, guarded_chat_completion
from app.agent.schemas import (
    ErrorAnalysisParseError,
    ErrorAnalysisResult,
    HintLadderResult,
    VariantResult,
    parse_error_analysis,
)
from app.agent.tracing import traceable_if_enabled
from app.config import settings
from app.llm.gateway import LLMGatewayError, chat_completion
from app.prompts import loader

if TYPE_CHECKING:
    # Annotation-only: the tool stack (P2-6/P2-7) pulls in the sandbox, and
    # a text-only analysis has no reason to import it. See _analyse_once.
    from app.agent.tool_caller import ToolCallRecord

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

# Model round trips allowed per attempt when the tools are enabled. Two
# would be the bare minimum — one to ask for the code to be run, one to
# read the result and answer — so three leaves room for a second look
# without letting a confused model run up a bill.
#
# The parse-failure retry re-enters the loop, so the *uncapped* worst case
# would be ``max_attempts * DEFAULT_TOOL_STEPS`` round trips. Instead the
# loop draws from a single shared ``DEFAULT_TOOL_BUDGET`` (see
# :func:`analyze_error`), so a retry cannot multiply the cost — the second
# attempt gets only what the budget has left. That matters more than it
# looks: this runs while the student is waiting for ``judge_done`` (the
# analysis happens before the terminal event), so the budget is a latency
# decision as much as a cost one.
DEFAULT_TOOL_STEPS = 3

#: Total model round trips the tool path may spend across *all* attempts of
#: one analysis. ``DEFAULT_TOOL_STEPS + 1`` lets the first attempt take its
#: full three and the parse-failure retry take one more — a correction, not
#: a second full try. This is the cap that replaces the old
#: ``max_attempts * DEFAULT_TOOL_STEPS`` product.
DEFAULT_TOOL_BUDGET = DEFAULT_TOOL_STEPS + 1


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
    # worth being able to count across the eval set. With ``use_tools``
    # this counts *attempts*, each of which may itself be several model
    # round trips (see ``tool_calls``).
    attempts: int = 0
    # Labels the model produced that failed validation, in order. Useful
    # for diagnosing a systematic prompt problem.
    rejected: list[str] = field(default_factory=list)
    # Every tool the model called, across all attempts, in order. Empty on
    # the text-only path. P2-8 streams these to the frontend as they
    # happen; keeping them on the outcome as well means a caller can show
    # what the Agent did after the fact without having captured a stream.
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # Titles of the knowledge cards handed to the model as context, in
    # retrieval order. Carried so the interface can say where an
    # explanation came from — and, just as importantly, so a demo can
    # *show* that retrieval ran at all.
    #
    # Retrieval degrades silently on purpose: a missing enrichment must
    # never fail a verdict. "Silently" is exactly the problem, though —
    # with nothing on screen, "the knowledge base is off" and "it is
    # working" look identical. Naming the sources makes the difference
    # visible without turning a soft dependency into a hard failure.
    #
    # Empty means no material was used. It does not distinguish "not
    # configured" from "nothing relevant found"; the log line does.
    knowledge_sources: list[str] = field(default_factory=list)

    # --- S7's extra output, carried rather than dropped.
    #
    # These two live on the *shared* outcome even though only the ``team``
    # orchestrator can fill them, and that is deliberate: ``pipeline``
    # promises its callers one type regardless of which orchestrator ran,
    # so a caller cannot branch on the type to find them. Before this they
    # were logged and thrown away, which made the team's three-times-higher
    # model bill buy nothing the student could see.
    #
    # ``None`` is the honest value for every other pipeline and for a
    # worker that failed — ``direct`` and ``graph`` never produce either,
    # and an empty ladder would be a claim that the model answered with
    # nothing rather than a claim that it was never asked.
    hint_ladder: Optional[HintLadderResult] = None
    variant: Optional[VariantResult] = None

    @property
    def ok(self) -> bool:
        return self.result is not None

    @property
    def tool_names(self) -> list[str]:
        """Names of the tools called, in order, with repeats kept."""
        return [call.name for call in self.tool_calls]


# The prompt's tool section, which has to differ by mode. Telling a model
# about tools it has not been given invites it to write out a call as text
# and then wonder why nothing happened; telling it there are no tools when
# there are makes it guess instead of looking.
_TOOL_GUIDANCE_WITH_TOOLS = """\
## 可用工具

你可以先调用工具取得证据，再下结论：

- `run_code`：编译并运行这段代码，看到它**真实的**输出。当你不确定某段表达式
  到底会算出什么、或想确认某个分支是否被走到时，用它。
- `judge`：把代码对着测试用例判题，返回每个用例的实际输出。

**当现有信息不足以确定错因时，先用工具取得证据再下结论**；如果工具返回的
结果与你的推测冲突，以工具的结果为准。证据够了就直接作答，不必为了用工具而用。
调用完工具后，仍然按下面的格式只输出一个 JSON 对象。"""

_TOOL_GUIDANCE_WITHOUT_TOOLS = """\
## 可用工具

本次你没有可用工具，只能根据上面给出的代码、输入和输出推断。不要描述你
「将会」运行什么——直接给出结论。"""

# Substituted for a missing statement. An empty block reads to the model as
# "the statement is blank", which is a different and misleading claim — so
# the absence is stated outright. Module-level rather than inline because
# S7's worker agents take the same statement through their own templates
# and must say the same thing about a missing one.
NO_STATEMENT_NOTE = (
    "（题面未提供。请只根据输入、期望输出、实际输出和代码本身推断，"
    "并在 error_detail 中说明你缺少题面。）"
)


def _render_prompt(
    *,
    code: str,
    stdin: str,
    expected: str,
    actual: str,
    statement: str,
    verdict: str,
    extra_context: Optional[str] = None,
    use_tools: bool = False,
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
        "tool_guidance": (
            _TOOL_GUIDANCE_WITH_TOOLS
            if use_tools
            else _TOOL_GUIDANCE_WITHOUT_TOOLS
        ),
    }

    # ``statement`` is optional metadata (a judge call from the
    # ``/judge`` route carries inline test cases but not the problem
    # text). The template requires the variable, so an absent statement
    # is substituted with an explicit note rather than an empty string —
    # an empty block reads to the model as "the statement is blank",
    # which is a different and misleading claim.
    if not (statement or "").strip():
        variables["statement"] = NO_STATEMENT_NOTE

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


def _analyse_once(
    messages: list[dict[str, str]],
    *,
    use_tools: bool = False,
    on_event: Optional[Callable[[ToolCallRecord], None]] = None,
    max_steps: int = DEFAULT_TOOL_STEPS,
) -> tuple[str, Optional[str], list[ToolCallRecord]]:
    """Make one attempt at an analysis.

    Args:
        messages: The conversation so far.
        use_tools: Offer the P2-6 tools and let the model run the code
            before answering. This is the P2-8 production path; the
            text-only path is what P2-5's accuracy gate measures.
        on_event: Called with each tool call as it completes, for
            streaming (P2-8).
        max_steps: Model round trips allowed per attempt on the tool path.

    Returns:
        ``(raw_text, error, tool_calls)`` — ``error`` is ``None`` on
        success, and the tool calls are returned either way so a caller
        can see what the Agent did even when it then failed.
    """
    if use_tools:
        # Imported lazily so a text-only analysis does not drag the
        # sandbox (and the compiler it shells out to) into the import
        # graph. records.py relies on that to keep the judge routes light.
        from app.agent.tool_caller import run_with_tools

        loop = run_with_tools(
            messages,
            max_steps=max_steps,
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout,
            on_tool_call=on_event,
            # None keeps the loop on the plain gateway — see `caller` in
            # tool_caller.run_with_tools. Passing the guarded one means
            # every round trip of the loop is redacted once and allowed to
            # retry, not just the first.
            caller=complete_through_guardrails if settings.llm_guardrails else None,
        )
        if not loop.ok:
            reason = loop.error or f"the tool loop stopped: {loop.stop_reason}"
            return "", reason, loop.calls
        return loop.content, None, loop.calls

    # Guardrails are on by default (settings.llm_guardrails): redaction,
    # the fallback chain and transient retries all apply here. The plain
    # gateway stays reachable via that switch so a bad interaction can be
    # turned off without a code change.
    call = guarded_chat_completion if settings.llm_guardrails else chat_completion
    try:
        return (
            call(
                messages,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout,
            ),
            None,
            [],
        )
    except LLMGatewayError as exc:
        return "", f"{type(exc).__name__}: {exc}", []


@traceable_if_enabled("analyze_error")
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
    use_tools: bool = False,
    on_event: Optional[Callable[[ToolCallRecord], None]] = None,
    max_steps: int = DEFAULT_TOOL_STEPS,
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
        max_attempts: Total attempts allowed, including the first.
            Defaults to 2 — the spec's "重试一次".
        use_tools: Let the model run the code (P2-6/P2-7) before
            answering. Off by default so the text-only path P2-5's
            accuracy gate measures stays reachable and unchanged; P2-4's
            recording path turns it on.
        on_event: Called with each tool call as it completes, for
            streaming to a client (P2-8).
        max_steps: Model round trips per attempt when ``use_tools``.

    Returns:
        An :class:`AnalysisOutcome`. ``outcome.ok`` is False when the
        analysis could not be produced; the caller decides what to do
        about that, and P2-4's answer is "leave the entry unanalysed".

    Note:
        With ``use_tools`` the tool path draws from a single shared budget
        (``DEFAULT_TOOL_BUDGET``) across all attempts, so the worst case is
        that budget plus one compile-and-run per ``run_code`` call — not
        ``max_attempts * max_steps``, which the parse-failure retry would
        otherwise multiply. That cost is paid while the student waits for
        ``judge_done``, so the budget is a latency decision, not just a cost
        one.

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
        use_tools=use_tools,
    )
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]

    outcome = AnalysisOutcome()

    # Shared step budget for the tool path (P1-5): the parse-failure retry
    # re-enters the loop, so without this the worst case is
    # ``max_attempts * max_steps`` round trips. Each attempt draws down this
    # single pool instead. The text-only path ignores it (it makes one
    # round trip regardless), which is also why the P2-5 accuracy gate is
    # unaffected.
    steps_left = DEFAULT_TOOL_BUDGET

    for attempt in range(1, max_attempts + 1):
        step_budget = max_steps
        if use_tools:
            step_budget = min(max_steps, steps_left)
            if step_budget < 1:
                # Out of budget but nothing parsed yet: stop rather than
                # spend a round trip we have already paid for.
                break
        raw, gateway_error, tool_calls = _analyse_once(
            messages,
            use_tools=use_tools,
            on_event=on_event,
            max_steps=step_budget,
        )
        outcome.attempts = attempt
        # Accumulated across attempts, not replaced: a retry that also
        # used a tool should not erase the first attempt's calls from the
        # record of what the Agent did.
        outcome.tool_calls.extend(tool_calls)

        if use_tools:
            # Each tool call is one model round trip; the final answer is
            # one more. Debit at least one so a no-tool reply still counts
            # toward the cap.
            steps_left -= max(1, len(tool_calls))

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
