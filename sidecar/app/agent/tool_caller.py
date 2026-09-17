"""Function-calling loop (P2-7).

:func:`run_with_tools` sends a conversation to the model with the P2-6
tool schemas attached, executes whatever the model asks for, feeds the
results back, and repeats until the model answers in prose or the step
budget runs out. This is the piece that turns a set of callable functions
into an agent that decides to use them.

Why a bounded loop rather than "call, execute, return"
------------------------------------------------------
A single round trip would only work if the model both chose a tool *and*
produced its final answer in the same reply, which is not how the protocol
works: a tool-calling reply has empty content and no answer in it. The
model needs at least one more turn to read the result. So the shape has to
be a loop, and a loop over a paid API needs a ceiling — S6 makes the same
argument for the self-check cycle. ``max_steps`` counts *model round
trips*, not tool calls, because that is what costs money and latency; one
step may execute several tools.

Failure is absorbed, not propagated
-----------------------------------
Nothing the model or the provider does raises out of this function:

* a tool that fails (unknown name, bad arguments, a language that is not
  registered) comes back as ``{"ok": False, ...}`` and is fed to the model
  as the tool's result, because that is exactly the feedback that lets it
  correct itself;
* arguments that are not valid JSON are reported the same way, quoting
  what arrived;
* a gateway failure ends the loop with ``error`` set instead of retrying,
  matching ``analyze_error``'s reasoning — re-sending during an outage
  multiplies the wait without improving the odds.

Callers get a :class:`ToolLoopResult` and decide what to do about it. The
same split as P2-3: model-side problems are data, genuine bugs inside this
module still propagate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.agent.tools import execute, tool_schemas
from app.llm.gateway import Completion, LLMGatewayError, ToolCall, complete

logger = logging.getLogger(__name__)

# Model round trips allowed before the loop gives up. Four is enough for
# the shape this Agent actually has — look something up, then answer — and
# small enough that a model stuck in a loop cannot run up a bill. A model
# that has not answered in four turns is not about to.
DEFAULT_MAX_STEPS = 4

#: ``stop_reason`` values. Strings rather than an enum because this value
#: crosses into JSON for the frontend (P2-8), and an enum would need a
#: serialisation step on the way out for no benefit.
STOP_ANSWERED = "answered"
STOP_STEP_LIMIT = "step_limit"
STOP_ERROR = "error"


@dataclass
class ToolCallRecord:
    """One tool the model asked for, and what came back.

    ``arguments`` is the raw JSON text the provider sent, kept alongside
    the parsed ``result`` so a failure to parse can be shown to the user
    exactly as it arrived. ``result`` is whatever
    :func:`app.agent.tools.execute` returned, including its ``ok`` flag.
    """

    id: str
    name: str
    arguments: str
    result: dict

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))


@dataclass
class ToolLoopResult:
    """The outcome of a tool-calling conversation.

    Attributes:
        content: The model's final prose answer. Empty when the loop hit
            the step limit or a gateway error.
        calls: Every tool call made, in order, across all steps.
        messages: The full transcript: the original messages, every
            assistant turn that asked for tools, every tool result, and
            the final answer. Ready to hand to another turn or to display.
        steps: Model round trips actually made.
        stop_reason: One of :data:`STOP_ANSWERED`, :data:`STOP_STEP_LIMIT`
            or :data:`STOP_ERROR`.
        error: The failure, when ``stop_reason`` is :data:`STOP_ERROR`.
    """

    content: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    stop_reason: str = STOP_ANSWERED
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.stop_reason == STOP_ANSWERED

    @property
    def tool_names(self) -> list[str]:
        """Names of the tools called, in order, with repeats kept.

        Repeats matter: calling ``run_code`` three times with different
        inputs is a different story from calling three different tools,
        and the frontend (P2-8) shows the sequence.
        """
        return [call.name for call in self.calls]

    def to_dict(self) -> dict:
        """A JSON-safe view for the wire (P2-8) and for logging.

        ``messages`` is deliberately excluded: it holds the full prompt
        and every tool payload, which is far more than a progress display
        needs and would bloat the response.
        """
        return {
            "content": self.content,
            "steps": self.steps,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "ok": call.ok,
                    "result": call.result,
                }
                for call in self.calls
            ],
        }


def _assistant_message(completion: Completion) -> dict[str, Any]:
    """Rebuild the assistant turn so the follow-up request is valid.

    The API requires the assistant message that requested tools to be sent
    back with the same ``tool_calls`` before any ``role: "tool"`` reply can
    refer to them; omitting it makes the request invalid rather than merely
    less informative.

    ``arguments`` is echoed as the original text rather than re-serialised
    from a parsed dict. Re-encoding would change the string for any payload
    whose formatting differed, and a server that matches the call against
    what it sent would reject the mismatch.
    """
    return {
        "role": "assistant",
        # Echoed verbatim, including the empty string this provider sends
        # alongside tool_calls. The spec also permits null here; "" is what
        # was measured to work (2026-09-16), so that is what is sent.
        "content": completion.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in completion.tool_calls
        ],
    }


def _run_tool_call(call: ToolCall) -> ToolCallRecord:
    """Execute one tool call, converting every failure into a result.

    A tool call must always produce something to send back. Raising here
    would abandon the conversation at the point where the model is most
    able to recover — it asked for a tool, and "that tool call was
    malformed, here is how" is usable feedback.
    """
    try:
        arguments = json.loads(call.arguments or "{}")
    except ValueError as exc:
        return ToolCallRecord(
            id=call.id,
            name=call.name,
            arguments=call.arguments,
            result={
                "ok": False,
                "error": f"arguments were not valid JSON: {exc}",
                "arguments_received": call.arguments[:500],
            },
        )

    if not isinstance(arguments, dict):
        return ToolCallRecord(
            id=call.id,
            name=call.name,
            arguments=call.arguments,
            result={
                "ok": False,
                "error": (
                    "arguments must be a JSON object, got "
                    f"{type(arguments).__name__}"
                ),
            },
        )

    return ToolCallRecord(
        id=call.id,
        name=call.name,
        arguments=call.arguments,
        result=execute(call.name, arguments),
    )


def run_with_tools(
    messages: list[dict[str, Any]],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    tool_choice: Optional[Any] = None,
    include_placeholders: bool = False,
    on_tool_call: Optional[Callable[[ToolCallRecord], None]] = None,
    **llm_kwargs: Any,
) -> ToolLoopResult:
    """Run a conversation until the model answers or the budget is spent.

    Args:
        messages: The starting conversation, e.g.
            ``[{"role": "user", "content": "..."}]``. Not modified; the
            growing transcript is returned in the result.
        max_steps: Maximum model round trips. Must be at least 1.
        tool_choice: Forwarded to the provider. ``None`` leaves the key
            out entirely, so the provider's own default applies — which is
            what the earlier text-only behaviour relied on.
        include_placeholders: Also offer tools that are defined but not
            implemented (P2-6's ``search_knowledge``). Off by default; see
            :func:`app.agent.tools.tool_schemas`.
        on_tool_call: Called with each :class:`ToolCallRecord` as soon as
            it is ready, so a caller can stream progress (P2-8's WS
            route). Invoked synchronously on this thread and *after* the
            tool has run, because the record is the interesting part —
            what was asked for and what came back. A sink that raises is
            logged and ignored rather than aborting the run: it is a
            notification, and losing the analysis because a progress
            display misbehaved would be a strictly worse outcome.
        **llm_kwargs: Passed through to
            :func:`app.llm.gateway.complete` (``temperature``,
            ``max_tokens``, ``timeout``, ``provider``, ...).

    Returns:
        A :class:`ToolLoopResult`. Never raises for a model-side or
        provider-side failure.

    Raises:
        ValueError: ``max_steps`` is less than 1 — a caller error, not
            something the model caused.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    def _notify(record: ToolCallRecord) -> None:
        if on_tool_call is None:
            return
        try:
            on_tool_call(record)
        except Exception:
            logger.exception(
                "on_tool_call sink failed for tool %s; continuing the run",
                record.name,
            )

    schemas = tool_schemas(include_placeholders=include_placeholders)
    # A copy: the caller's list is theirs, and the transcript returned to
    # them must not alias whatever they passed in.
    transcript: list[dict[str, Any]] = list(messages)
    calls: list[ToolCallRecord] = []

    for step in range(1, max_steps + 1):
        try:
            completion = complete(transcript, tools=schemas, **llm_kwargs)
        except LLMGatewayError as exc:
            return ToolLoopResult(
                calls=calls,
                messages=transcript,
                steps=step,
                stop_reason=STOP_ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not completion.wants_tools:
            # The final answer joins the transcript too, so `messages` is a
            # faithful record of the conversation rather than everything
            # except the part the caller actually wanted. A plain dict
            # rather than _assistant_message(): that one always carries a
            # ``tool_calls`` key, and an empty array is not something every
            # provider accepts on a message that requests nothing.
            transcript.append(
                {"role": "assistant", "content": completion.content}
            )
            return ToolLoopResult(
                content=completion.content,
                calls=calls,
                messages=transcript,
                steps=step,
                stop_reason=STOP_ANSWERED,
            )

        transcript.append(_assistant_message(completion))

        for call in completion.tool_calls:
            record = _run_tool_call(call)
            calls.append(record)
            _notify(record)
            transcript.append(
                {
                    "role": "tool",
                    "tool_call_id": record.id,
                    # Serialised here rather than left as a dict because
                    # the wire format for a tool result is a string; the
                    # model reads JSON text either way, but a provider
                    # validating the field will not accept an object.
                    "content": json.dumps(record.result, ensure_ascii=False),
                }
            )

    return ToolLoopResult(
        calls=calls,
        messages=transcript,
        steps=max_steps,
        stop_reason=STOP_STEP_LIMIT,
    )


__all__ = [
    "DEFAULT_MAX_STEPS",
    "STOP_ANSWERED",
    "STOP_ERROR",
    "STOP_STEP_LIMIT",
    "ToolCallRecord",
    "ToolLoopResult",
    "run_with_tools",
]
