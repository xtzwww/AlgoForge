"""Tests for the Agent's tool functions (P2-6).

The tools execute real source in the real sandbox, so most of these
compile something. That is deliberate: the value of P2-6 is that the
three functions are wired to the judging primitives correctly, and a
mocked sandbox would only prove that the mock was called. What is
asserted is the *contract* — the shape and the meaning of the dict each
tool returns — not the sandbox's own behaviour, which P1 already covers.

The distinction these tests exist to pin down is the ``ok`` flag: a
compilation error, a wrong answer and a timeout are all **successful tool
calls** carrying bad news, while a bad argument or an unregistered
language is a tool that could not do its job. Getting that wrong is how
an infrastructure limit starts reading as a model failure.
"""

import pytest
from pydantic import ValidationError

from app.agent import tools
from app.agent.tools import MAX_OUTPUT_CHARS
from app.sandbox.judge_input import JudgeInputError, resolve

# A correct adder. Used as the "compiles and runs" baseline.
ADDER = """#include <iostream>
using namespace std;
int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
"""

# Does not compile.
BROKEN = "#include <iostream>\nint main() { this is not c++ }\n"

# Compiles, exits non-zero: a runtime error without any undefined
# behaviour, so the assertion is about the contract rather than about how
# this particular toolchain reports a crash.
EXITS_THREE = "#include <iostream>\nint main() { return 3; }\n"

# Never returns.
SPINS = "#include <iostream>\nint main() { while (true) {} return 0; }\n"

# Prints 20,000 characters — five times MAX_OUTPUT_CHARS.
CHATTY = (
    "#include <iostream>\n"
    "int main() { for (int i = 0; i < 2000; ++i) std::cout << \"abcdefghij\"; }\n"
)


# ------------------------------------------------------------- the registry


def test_only_implemented_tools_are_offered_by_default():
    """A tool that can only fail is not sent to the model.

    Offering it would spend a turn to learn nothing. The placeholder still
    exists — defining it is this slice's job — but choosing to advertise it
    is the caller's decision, and the default is the safe one.
    """
    offered = [schema["function"]["name"] for schema in tools.tool_schemas()]

    assert offered == ["run_code", "judge"]


def test_placeholders_can_be_included_explicitly():
    """The full set is available when a caller asks for it."""
    offered = [
        schema["function"]["name"] for schema in tools.tool_schemas(True)
    ]

    assert sorted(offered) == ["judge", "run_code", "search_knowledge"]


def test_every_tool_is_well_formed():
    """Each entry is an OpenAI-compatible function definition.

    Checked structurally rather than by comparing to a literal, so adding
    a tool cannot pass this by being left out of a hand-written list.
    """
    for schema in tools.tool_schemas(True):
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] in tools.TOOLS
        assert function["description"].strip()
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)


def test_the_registry_key_matches_the_tool_name():
    """A mismatch would make the name in the request undispatchable."""
    for key, tool in tools.TOOLS.items():
        assert key == tool.name
        assert tool.schema()["function"]["name"] == key


def test_the_schema_is_generated_from_the_argument_model():
    """The advertised parameters and the validated ones are one thing.

    This is the property that makes the module safe to extend: a schema
    written by hand can describe a field the implementation never reads,
    and the model then fills in a parameter that does nothing.
    """
    schema = tools.TOOLS["run_code"].schema()
    advertised = set(schema["function"]["parameters"]["properties"])
    declared = set(tools.RunCodeArgs.model_fields)

    assert advertised == declared
    assert schema["function"]["parameters"]["required"] == ["code"]


# ---------------------------------------------------------------- run_code


def test_run_code_returns_the_programs_output():
    """The happy path: stdin is fed in and stdout comes back.

    ``timed_out`` is asserted, not just the output. Before the runner
    drained its pipes on a helper thread, stdin was written and the pipe
    closed before the poll loop, and a fast-exiting child could miss the
    data — the program then blocked on ``cin`` until the limit killed it,
    producing empty stdout. Asserting only on the output made that read as
    a wrong answer; asserting the flag makes it read as what it is.
    """
    result = tools.run_code(code=ADDER, stdin="3 7\n")

    assert result["ok"] is True
    assert result["compiled"] is True
    assert result["timed_out"] is False
    assert result["stdout"] == "10\n"
    assert result["exit_code"] == 0
    assert result["memory_exceeded"] is False


def test_a_compile_error_is_a_successful_tool_call():
    """CE is the answer, not a failure to answer.

    This is the module's central distinction. The model asked what happens
    when this code runs; "it does not compile, here is the diagnostic" is a
    complete and useful reply. Reporting it as ``ok: False`` would suggest
    the tool was broken and invite a retry with identical arguments.
    """
    result = tools.run_code(code=BROKEN)

    assert result["ok"] is True
    assert result["compiled"] is False
    assert "error" in result["compile_output"]
    assert result["stdout"] == ""


def test_a_non_zero_exit_is_reported_not_hidden():
    """The program ran and failed; both facts survive."""
    result = tools.run_code(code=EXITS_THREE)

    assert result["ok"] is True
    assert result["compiled"] is True
    assert result["exit_code"] == 3
    assert result["timed_out"] is False


def test_a_runaway_program_is_stopped_by_the_time_limit():
    """AGENTS.md's red line: never execute untrusted code unbounded.

    The flag is asserted rather than the message, because the message is
    the runner's wording and the flag is the contract.
    """
    result = tools.run_code(code=SPINS, time_limit=1.0)

    assert result["ok"] is True
    assert result["timed_out"] is True
    assert result["exit_code"] == -1


def test_output_is_clipped_and_says_so():
    """A verbose program cannot be handed to the model whole.

    The original size must appear in the clipped text: a truncated tail is
    otherwise indistinguishable from a program that stopped there, and the
    model would reason about a fragment as though it were the whole output.
    """
    result = tools.run_code(code=CHATTY)

    assert result["ok"] is True
    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) < 20000
    assert str(20000) in result["stdout"]
    # The head is kept — the beginning is what usually identifies the bug.
    assert result["stdout"].startswith("abcdefghij")


def test_blank_code_is_a_tool_error_not_a_compile_error():
    """Nothing to run is the tool's problem, not the program's.

    Compiling whitespace produces a compiler diagnostic about an empty
    translation unit, which reads as the model's mistake when in fact it
    sent nothing to run.
    """
    result = tools.run_code(code="   \n  ")

    assert result["ok"] is False
    assert "no code" in result["error"]


def test_an_unregistered_language_is_a_tool_error():
    """And the message names the language, so the model can correct it."""
    result = tools.run_code(code=ADDER, language="brainfuck")

    assert result["ok"] is False
    assert "brainfuck" in result["error"]


# ------------------------------------------------------------------ judge


def test_judge_reports_ac():
    """The passing verdict, with the case detail attached."""
    result = tools.judge(
        code=ADDER, test_cases=[{"input": "3 7\n", "expected_output": "10\n"}]
    )

    assert result["ok"] is True
    assert result["status"] == "AC"
    assert result["passed"] == 1
    assert result["total"] == 1


def test_judge_reports_wa_with_the_actual_output():
    """The failing verdict must carry what the program really printed.

    A bare ``WA`` is useless to a model trying to explain the mistake; the
    actual output is the evidence.
    """
    result = tools.judge(
        code=ADDER, test_cases=[{"input": "3 7\n", "expected_output": "999\n"}]
    )

    assert result["ok"] is True
    assert result["status"] == "WA"
    assert result["passed"] == 0
    assert result["test_cases"][0]["actual_output"] == "10\n"
    assert result["test_cases"][0]["expected_output"] == "999\n"


def test_judge_counts_across_several_cases():
    """Partial success is reported as a count, not as a single boolean."""
    result = tools.judge(
        code=ADDER,
        test_cases=[
            {"input": "3 7\n", "expected_output": "10\n"},
            {"input": "1 1\n", "expected_output": "999\n"},
        ],
    )

    assert result["status"] == "WA"
    assert result["passed"] == 1
    assert result["total"] == 2


def test_judge_reports_ce_without_cases():
    """A build failure short-circuits, and says so through the verdict."""
    result = tools.judge(
        code=BROKEN, test_cases=[{"input": "", "expected_output": "10\n"}]
    )

    assert result["ok"] is True
    assert result["status"] == "CE"
    assert result["total"] == 0
    assert "error" in result["compile_output"]


def test_judge_rejects_blank_code():
    """Same rule as run_code: nothing to judge is the tool's problem."""
    result = tools.judge(code="", test_cases=[{"input": "", "expected_output": ""}])

    assert result["ok"] is False
    assert "no code" in result["error"]


def test_judge_rejects_an_unregistered_language():
    """The judge's own error is converted, not allowed to escape.

    ``judge`` raises ``UnknownLanguageError``; a tool that raises would
    break P2-7's loop instead of telling the model what it got wrong.
    """
    result = tools.judge(
        code=ADDER,
        test_cases=[{"input": "3 7\n", "expected_output": "10\n"}],
        language="cobol",
    )

    assert result["ok"] is False
    assert "cobol" in result["error"]


# --------------------------------------------------------- search_knowledge


def test_search_knowledge_admits_it_is_not_implemented():
    """An honest placeholder, not a plausible empty result.

    Returning ``ok: True`` with no hits would tell the model the knowledge
    base holds nothing relevant — a different and false claim. The message
    also points it at what to do instead, so it does not simply retry.
    """
    result = tools.search_knowledge(query="最长上升子序列")

    assert result["ok"] is False
    assert "not implemented" in result["error"]
    assert "P2-13" in result["error"]
    assert result["query"] == "最长上升子序列"


# ---------------------------------------------------------------- execute


def test_execute_dispatches_by_name():
    """The seam P2-7 uses: a name and a dict in, a tool result out."""
    result = tools.execute("run_code", {"code": ADDER, "stdin": "1 2\n"})

    assert result["ok"] is True
    assert result["stdout"] == "3\n"


def test_execute_treats_no_arguments_as_an_empty_object():
    """What a provider sends for a call with no arguments.

    ``None`` must not reach the validation step as a non-dict, or every
    zero-argument tool would be unreachable.
    """
    result = tools.execute("run_code", None)

    # run_code requires `code`, so this is a validation error rather than a
    # TypeError about spreading None.
    assert result["ok"] is False
    assert "invalid arguments" in result["error"]


def test_execute_rejects_an_unknown_tool_and_lists_the_known_ones():
    """A hallucinated tool name must come back as readable feedback."""
    result = tools.execute("rm_rf", {})

    assert result["ok"] is False
    assert "rm_rf" in result["error"]
    for name in tools.TOOLS:
        assert name in result["error"]


def test_execute_rejects_non_object_arguments():
    """A JSON array or string is not an argument object."""
    result = tools.execute("run_code", ["code"])

    assert result["ok"] is False
    assert "must be an object" in result["error"]


def test_execute_surfaces_a_missing_required_argument():
    """Pydantic's message names the field; that part must survive."""
    result = tools.execute("run_code", {"stdin": "x\n"})

    assert result["ok"] is False
    assert "code" in result["error"]


def test_execute_refuses_a_placeholder():
    """Calling an unimplemented tool is refused before the body runs."""
    result = tools.execute("search_knowledge", {"query": "dp"})

    assert result["ok"] is False
    assert "not implemented" in result["error"]


def test_execute_clamps_an_out_of_range_limit():
    """A model asking for a 9999-second run is rejected, not obeyed.

    Quietly substituting a smaller limit would be worse: the model would
    see a timeout it did not ask for and draw the wrong conclusion about
    the program's speed.
    """
    result = tools.execute("run_code", {"code": ADDER, "time_limit": 9999})

    assert result["ok"] is False
    assert "time_limit" in result["error"]


@pytest.mark.parametrize("field,value", [("time_limit", 0.05), ("mem_limit_mb", 0)])
def test_execute_rejects_limits_below_the_floor(field, value):
    """The lower bounds are enforced too, not just the ceilings."""
    result = tools.execute("run_code", {"code": ADDER, field: value})

    assert result["ok"] is False
    assert field in result["error"]


def test_the_tool_bounds_match_the_wire_layer():
    """The tool's limits must not be looser than the REST layer's.

    ``judge_input`` is the authority for the wire format, and a tool call
    never passes through it. If the two drifted, the Agent could request a
    run the product refuses — so the boundaries are compared directly
    rather than trusted to stay in step.
    """
    inline = [{"input": "", "expected_output": ""}]

    # The top of the range: both layers accept it.
    resolve(test_cases=inline, time_limit=60.0)
    tools.RunCodeArgs(code="x", time_limit=60.0)

    # One step past it: both refuse.
    with pytest.raises(JudgeInputError):
        resolve(test_cases=inline, time_limit=60.1)
    with pytest.raises(ValidationError):
        tools.RunCodeArgs(code="x", time_limit=60.1)


# -------------------------------------------------------------------- clip


def test_clip_leaves_short_text_alone():
    """Ordinary output passes through byte-for-byte."""
    text, clipped = tools._clip("10\n")

    assert text == "10\n"
    assert clipped is False


def test_clip_states_the_original_size():
    """The marker has to be actionable, so it carries both numbers."""
    text, clipped = tools._clip("x" * (MAX_OUTPUT_CHARS + 25))

    assert clipped is True
    assert text.startswith("x" * 10)
    assert str(MAX_OUTPUT_CHARS + 25) in text
    assert "25" in text
