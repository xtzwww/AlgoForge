"""Tests for the function-calling loop (P2-7).

The loop's mechanics are exercised against a scripted gateway: what
matters here is the *shape of the conversation* the loop builds — the
assistant turn that requests tools, the ``role: "tool"`` replies that
answer it, and the guarantees about failure — none of which needs a real
model to check. A stubbed gateway also lets a test assert on exactly what
was sent, which is how the "tools were actually offered" claim is pinned
rather than assumed.

The one thing a stub cannot prove is that a model *chooses* to call a
tool, which is the slice's stated acceptance criterion. That is the
provider-gated test at the bottom.
"""

import json

import pytest

from app.agent import tool_caller
from app.agent.tool_caller import (
    STOP_ANSWERED,
    STOP_ERROR,
    STOP_STEP_LIMIT,
    run_with_tools,
)
from app.llm import gateway
from app.llm.gateway import Completion, LLMConfigError, LLMConnectionError, ToolCall


def _tool_call(name="run_code", arguments='{"code": "int main(){}"}', call_id="call_1"):
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _asks_for_tools(*calls, content=""):
    """A reply that requests tools rather than answering."""
    return Completion(
        content=content, tool_calls=tuple(calls), finish_reason="tool_calls"
    )


def _answers(text="42"):
    """A reply that answers in prose."""
    return Completion(content=text, finish_reason="stop")


class _ScriptedGateway:
    """Hands out canned completions in order and records every request."""

    def __init__(self, *replies):
        self._queue = list(replies)
        self.requests = []

    def __call__(self, messages, **kwargs):
        # Deep-copied: the loop mutates its transcript in place, so holding
        # the reference would make every recorded request look like the
        # final one.
        self.requests.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        if not self._queue:
            raise AssertionError(
                f"gateway called {len(self.requests)} times; the script only "
                f"allows {len(self.requests) - 1}"
            )
        reply = self._queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted gateway and return it."""

    def _install(*replies):
        stub = _ScriptedGateway(*replies)
        monkeypatch.setattr(tool_caller, "complete", stub)
        return stub

    return _install


@pytest.fixture
def stub_tools(monkeypatch):
    """Replace tool execution with a recorder, so no C++ is compiled."""

    calls = []

    def fake_execute(name, arguments):
        calls.append({"name": name, "arguments": arguments})
        return {"ok": True, "echo": arguments}

    monkeypatch.setattr(tool_caller, "execute", fake_execute)
    return calls


# ------------------------------------------------------------- the answer


def test_an_immediate_answer_needs_one_step(scripted, stub_tools):
    """A model that does not need a tool is not forced through the loop."""
    gateway_stub = scripted(_answers("hello"))

    result = run_with_tools([{"role": "user", "content": "hi"}])

    assert result.ok
    assert result.stop_reason == STOP_ANSWERED
    assert result.content == "hello"
    assert result.steps == 1
    assert result.calls == []
    assert stub_tools == []
    assert len(gateway_stub.requests) == 1


def test_the_tools_are_offered_on_every_request(scripted, stub_tools):
    """The schema is attached to each turn, not just the first.

    A follow-up turn without ``tools`` would leave the model unable to ask
    for a second tool, which is the whole point of looping.
    """
    gateway_stub = scripted(_answers())

    run_with_tools([{"role": "user", "content": "hi"}])

    tools = gateway_stub.requests[0]["kwargs"]["tools"]
    assert [entry["function"]["name"] for entry in tools] == ["run_code", "judge"]


def test_placeholders_are_excluded_unless_requested(scripted, stub_tools):
    """P2-6's unimplemented tool is not advertised by default."""
    gateway_stub = scripted(_answers())

    run_with_tools([{"role": "user", "content": "hi"}], include_placeholders=True)

    tools = gateway_stub.requests[0]["kwargs"]["tools"]
    assert "search_knowledge" in [entry["function"]["name"] for entry in tools]


# -------------------------------------------------------------- the loop


def test_a_tool_call_is_executed_and_the_result_fed_back(scripted, stub_tools):
    """The core loop: ask, run, return the result, ask again."""
    scripted(
        _asks_for_tools(_tool_call(arguments='{"code": "x", "stdin": "1\\n"}')),
        _answers("done"),
    )

    result = run_with_tools([{"role": "user", "content": "what does x print?"}])

    assert result.ok
    assert result.steps == 2
    assert result.tool_names == ["run_code"]
    assert result.content == "done"
    assert stub_tools == [
        {"name": "run_code", "arguments": {"code": "x", "stdin": "1\n"}}
    ]


def test_the_transcript_has_the_shape_the_api_requires(scripted, stub_tools):
    """Assistant-with-tool_calls, then role=tool carrying the call id.

    Omitting either half makes the follow-up request invalid rather than
    merely less informative, so this is a protocol requirement and not a
    presentation choice.
    """
    scripted(
        _asks_for_tools(_tool_call(call_id="call_abc")),
        _answers("done"),
    )

    result = run_with_tools([{"role": "user", "content": "q"}])

    roles = [message["role"] for message in result.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]

    assistant_turn = result.messages[1]
    assert assistant_turn["tool_calls"][0]["id"] == "call_abc"
    assert assistant_turn["tool_calls"][0]["type"] == "function"
    assert assistant_turn["tool_calls"][0]["function"]["name"] == "run_code"

    tool_turn = result.messages[2]
    assert tool_turn["tool_call_id"] == "call_abc"
    # The wire format for a tool result is a string, not an object.
    assert isinstance(tool_turn["content"], str)
    assert json.loads(tool_turn["content"])["ok"] is True

    # The final answer is part of the record too.
    assert result.messages[3] == {"role": "assistant", "content": "done"}


def test_arguments_are_echoed_verbatim(scripted, stub_tools):
    """The raw argument text goes back, not a re-serialised version.

    A server matching the call against what it sent would reject a
    re-encoded string, so the original formatting has to survive.
    """
    raw = '{ "code" : "int main(){}" ,  "stdin":"" }'
    scripted(_asks_for_tools(_tool_call(arguments=raw)), _answers())

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.messages[1]["tool_calls"][0]["function"]["arguments"] == raw


def test_several_calls_in_one_turn_are_all_executed(scripted, stub_tools):
    """One step may carry more than one tool call.

    ``max_steps`` counts model round trips, so this must cost a single
    step — otherwise the budget would mean something different from what
    its docstring says.
    """
    scripted(
        _asks_for_tools(_tool_call(call_id="a"), _tool_call(call_id="b")),
        _answers(),
    )

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.steps == 2
    assert result.tool_names == ["run_code", "run_code"]
    assert [message["tool_call_id"] for message in result.messages[2:4]] == ["a", "b"]


def test_tool_names_keep_repeats(scripted, stub_tools):
    """Three calls to one tool is a different story from three tools."""
    scripted(
        _asks_for_tools(_tool_call(call_id="a")),
        _asks_for_tools(_tool_call(call_id="b")),
        _answers(),
    )

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.tool_names == ["run_code", "run_code"]


# ------------------------------------------------------------ bad calls


def test_unparseable_arguments_are_reported_to_the_model(scripted, stub_tools):
    """Malformed JSON must not abandon the conversation.

    The model is mid-task and is the only party that can fix its own
    arguments, so the failure has to reach it as a tool result.
    """
    scripted(_asks_for_tools(_tool_call(arguments="{not json")), _answers())

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.ok
    assert result.calls[0].ok is False
    assert "not valid JSON" in result.calls[0].result["error"]
    # The offending text is quoted back so the model can see what it sent.
    assert result.calls[0].result["arguments_received"] == "{not json"
    # The tool itself was never invoked.
    assert stub_tools == []


def test_non_object_arguments_are_rejected(scripted, stub_tools):
    """A JSON array is valid JSON but not an argument object."""
    scripted(_asks_for_tools(_tool_call(arguments='["code"]')), _answers())

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.calls[0].ok is False
    assert "must be a JSON object" in result.calls[0].result["error"]
    assert stub_tools == []


def test_a_failing_tool_result_is_fed_back_not_swallowed(scripted, monkeypatch):
    """A tool reporting failure is data for the model, not a stop signal.

    P2-6 returns failures as ``ok: False`` rather than raising precisely so
    they can be shown to the model; the loop's job is to pass them through
    unchanged and carry on. (That ``execute`` actually produces ``ok:
    False`` for an unknown name is test_agent_tools.py's business.)
    """

    def failing_execute(name, arguments):
        return {"ok": False, "error": f"unknown tool {name!r}"}

    monkeypatch.setattr(tool_caller, "execute", failing_execute)
    scripted(
        _asks_for_tools(_tool_call(name="rm_rf")),
        _answers("no such tool, I will answer without it"),
    )

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.ok
    assert result.calls[0].ok is False
    assert "rm_rf" in result.calls[0].result["error"]
    # The failure reached the model verbatim...
    assert json.loads(result.messages[2]["content"])["ok"] is False
    # ...and the loop continued to an answer rather than aborting.
    assert result.content == "no such tool, I will answer without it"


def test_missing_arguments_mean_an_empty_object(scripted, stub_tools):
    """The spec allows the key to be absent for a no-argument call."""
    scripted(_asks_for_tools(_tool_call(arguments="")), _answers())

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert stub_tools == [{"name": "run_code", "arguments": {}}]
    assert result.calls[0].ok is True


# ----------------------------------------------------------- the budget


def test_the_step_limit_stops_a_looping_model(scripted, stub_tools):
    """A model that never answers is cut off, not indulged.

    Without a ceiling this is an unbounded spend against a paid API.
    """
    scripted(*[_asks_for_tools(_tool_call(call_id=f"c{i}")) for i in range(10)])

    result = run_with_tools([{"role": "user", "content": "q"}], max_steps=3)

    assert result.stop_reason == STOP_STEP_LIMIT
    assert result.ok is False
    assert result.steps == 3
    assert result.content == ""
    # Three round trips, one tool call each.
    assert len(result.calls) == 3


def test_a_step_limit_below_one_is_a_caller_error(scripted, stub_tools):
    """Not something the model can cause, so it raises rather than returning."""
    with pytest.raises(ValueError, match="max_steps"):
        run_with_tools([{"role": "user", "content": "q"}], max_steps=0)


# ------------------------------------------------------- gateway failure


def test_a_gateway_failure_ends_the_loop_without_raising(scripted, stub_tools):
    """An unreachable provider is reported, not retried.

    Same reasoning as analyze_error: re-sending during an outage
    multiplies the wait without improving the odds.
    """
    gateway_stub = scripted(LLMConnectionError("refused"))

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.stop_reason == STOP_ERROR
    assert result.ok is False
    assert "LLMConnectionError" in result.error
    assert "refused" in result.error
    assert result.steps == 1
    # One attempt, not a retry storm.
    assert len(gateway_stub.requests) == 1


def test_a_failure_after_a_tool_call_keeps_that_call(scripted, stub_tools):
    """Work already done is not thrown away when the next turn fails."""
    scripted(_asks_for_tools(_tool_call()), LLMConnectionError("refused"))

    result = run_with_tools([{"role": "user", "content": "q"}])

    assert result.stop_reason == STOP_ERROR
    assert result.tool_names == ["run_code"]


# ------------------------------------------------------------- the result


def test_the_callers_messages_are_not_modified(scripted, stub_tools):
    """The loop appends to a copy, so the caller's list is untouched."""
    scripted(_answers())
    original = [{"role": "user", "content": "q"}]

    result = run_with_tools(original)

    assert original == [{"role": "user", "content": "q"}]
    assert len(result.messages) == 2


def test_to_dict_is_json_safe_and_leaves_out_the_transcript(scripted, stub_tools):
    """The wire view carries what a progress display needs and no more.

    ``messages`` holds the whole prompt and every tool payload; sending it
    to the frontend (P2-8) would be a lot of bytes nobody asked for.
    """
    scripted(_asks_for_tools(_tool_call()), _answers("done"))

    result = run_with_tools([{"role": "user", "content": "q"}])
    payload = result.to_dict()

    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    assert payload["content"] == "done"
    assert payload["stop_reason"] == STOP_ANSWERED
    assert payload["tool_calls"][0]["name"] == "run_code"
    assert payload["tool_calls"][0]["ok"] is True
    assert "messages" not in payload


# --------------------------------------------------- through real tools


def test_a_real_tool_call_goes_through_to_the_sandbox(scripted):
    """One end-to-end pass with the real tool, no stub in the way.

    The stubbed tests prove the loop's bookkeeping; this proves the wiring
    between the loop and P2-6 is real, and that the tool result the model
    receives is the sandbox's actual output.
    """
    program = (
        "#include <iostream>\n"
        "int main(){int a,b;std::cin>>a>>b;std::cout<<a*b<<std::endl;}"
    )
    scripted(
        _asks_for_tools(
            _tool_call(
                arguments=json.dumps(
                    {"code": program, "stdin": "3 7\n", "language": "cpp"}
                )
            )
        ),
        _answers("21"),
    )

    result = run_with_tools([{"role": "user", "content": "what does it print?"}])

    assert result.ok
    tool_result = result.calls[0].result
    assert tool_result["ok"] is True
    assert tool_result["compiled"] is True
    assert tool_result["stdout"] == "21\n"
    assert tool_result["timed_out"] is False


# ------------------------------------------------------------- live model


def _provider_ready() -> tuple[bool, str]:
    """Whether a model call can be attempted, per the gateway's own rules.

    Delegates to the gateway's resolver rather than restating the
    provider-by-provider conditions: a second copy of those rules is
    exactly what went stale in the S2 harness when a provider was added.
    """
    try:
        gateway._resolve_provider(None)
    except LLMConfigError as exc:
        return False, str(exc)
    return True, ""


_PROVIDER_READY, _SKIP_REASON = _provider_ready()

requires_provider = pytest.mark.skipif(
    not _PROVIDER_READY,
    reason=(
        "P2-7's acceptance criterion ('验证它主动调 run_code') needs a live "
        f"model. {_SKIP_REASON} A skip here means it has NOT been verified."
    ),
)


@pytest.mark.eval_set
@requires_provider
def test_the_model_chooses_to_run_the_code_itself():
    """P2-7's stated acceptance criterion, against a real model.

    The question is answerable only by running the program: it asks what a
    multiplication prints. A model that answers without calling
    ``run_code`` would be guessing, so the assertion is on the tool call
    and not merely on the final number.
    """
    program = (
        "#include <iostream>\n"
        "int main(){int a,b;std::cin>>a>>b;std::cout<<a*b<<std::endl;}"
    )
    question = (
        "下面这段 C++ 代码在输入 `3 7` 时会输出什么？"
        "请实际运行它再回答，只回答输出内容本身。\n\n" + program
    )

    result = run_with_tools(
        [{"role": "user", "content": question}],
        temperature=0.0,
        tool_choice="auto",
    )

    assert result.ok, result.error
    assert "run_code" in result.tool_names, (
        f"the model answered without running the code: {result.to_dict()}"
    )
    assert result.calls[0].ok, result.calls[0].result
    assert result.calls[0].result["stdout"].strip() == "21"
    assert "21" in result.content
