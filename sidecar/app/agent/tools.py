"""Tool functions the Agent can call (P2-6).

Three tools, one shape
----------------------
Each tool is a Pydantic argument model plus a plain function that takes
the validated arguments and returns a JSON-serialisable ``dict``. From
those two pieces this module derives everything else — the JSON Schema
handed to the model (P2-7), and the argument validation applied to what
the model sends back. There is exactly one place where a tool's name,
description, arguments and behaviour are written down, so a schema
cannot describe something the implementation does not do.

Why not ``@tool`` / LangChain
-----------------------------
The course note for this slice is itself "C1:34 不使用 @tool 定义工具",
and the reason holds here: the OpenAI-compatible protocol takes a plain
JSON Schema array in its ``tools`` request field, which the gateway
already forwards verbatim through ``**extra``. LangChain is not
installed and AGENTS.md's dependency table does not list it as required
for S2, so adding it now would buy a decorator at the cost of a large
dependency. P2-7 can wrap these functions; nothing here needs rewriting
if it does.

``ok`` means "the tool worked", not "the code passed"
-----------------------------------------------------
Every tool returns ``{"ok": True, ...}`` when it did its job and
``{"ok": False, "error": "..."}`` when it could not. The distinction is
deliberate and load-bearing: a compilation error, a wrong answer and a
timeout are all **successful tool calls** — they are the information the
model asked for, and they come back as ``ok: True`` with a status field.
``ok: False`` is reserved for the tool being unable to do its job at all
(bad arguments, an unregistered language, a request for knowledge that
has not been indexed yet).

Collapsing the two is the same mistake that made 7 of 20 S2 eval cases
look like model failures when they were a token-budget problem: a
downstream reader cannot tell "the code is wrong" from "the tool is
broken", and the two call for completely different responses. P2-7 feeds
these dicts back to the model as tool results, so the model gets to make
that call explicitly rather than guessing from an exception.

Untrusted code, always bounded
------------------------------
``run_code`` and ``judge`` execute source the model supplied. AGENTS.md's
red line is "不在沙箱里执行未受信任的代码而不设超时：每次执行必须有超时 +
内存上限", so both tools always pass a wall-clock limit and a memory
ceiling; the argument models additionally range-check them, because a
model can otherwise ask for a 24-hour run. The limits are clamped rather
than trusted, and out-of-range values are a validation error rather than
a silent adjustment — quietly honouring a different limit than the one
requested is how a "why does this time out?" investigation goes wrong.

Output is truncated
-------------------
A runaway program can print megabytes. The S2 eval set already contains
a case whose input is 400,000 characters, so this is not hypothetical.
``MAX_OUTPUT_CHARS`` caps each returned stream and the result says it was
capped, rather than silently handing the model a partial answer it will
read as complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from app.sandbox.compiler import compile_source, sandbox_tmpdir
from app.sandbox.judge import TestCase
from app.sandbox.judge import judge as _sandbox_judge
from app.sandbox.languages import DEFAULT_LANGUAGE, UnknownLanguageError, get_language
from app.sandbox.runner import run_executable

# Ceiling on each returned stream. Generous enough to show a wrong answer
# and the surrounding context, small enough that a few tool calls cannot
# exhaust the model's context window on their own.
MAX_OUTPUT_CHARS = 4000

# Limits applied when the model does not name its own. Same values the
# product uses for an ad-hoc run (``judge_input``'s inline-case defaults),
# so a tool call behaves like the equivalent run in the UI.
DEFAULT_TIME_LIMIT = 10.0
DEFAULT_MEM_LIMIT_MB = 256

# Bounds the model's own values are held to. These mirror the range
# checks in ``app/sandbox/judge_input.py``, which is the authority for the
# wire layer; the tool layer repeats them because a tool call never passes
# through that module. A drift between the two would let the Agent request
# a limit the product refuses — narrow rather than dangerous, but worth
# keeping in step, and ``test_agent_tools.py`` pins the behaviour.
MIN_TIME_LIMIT = 0.1
MAX_TIME_LIMIT = 60.0
MIN_MEM_LIMIT_MB = 1
MAX_MEM_LIMIT_MB = 2048


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Cap *text* at *limit* characters, saying so when it had to.

    Returns ``(text, was_clipped)``. The marker states the original size
    because a truncated tail is otherwise indistinguishable from a program
    that simply stopped there — the model would reason about a partial
    answer as though it were the whole one.
    """
    if len(text) <= limit:
        return text, False
    dropped = len(text) - limit
    return (
        f"{text[:limit]}\n…（此处只显示前 {limit} 个字符，原文共 {len(text)} 个字符，"
        f"已省略后 {dropped} 个）",
        True,
    )


# --------------------------------------------------------------- arguments


class RunCodeArgs(BaseModel):
    """run_code 工具的参数。

    这些说明会原样进入发给模型的 JSON Schema，所以用平实的中文，
    不要写 Sphinx 标记。
    """

    code: str = Field(description="要运行的完整源代码。")
    stdin: str = Field(
        default="",
        description="程序标准输入。程序不读输入时留空。",
    )
    language: str = Field(
        default=DEFAULT_LANGUAGE,
        description="语言 id，例如 cpp / java / python。",
    )
    time_limit: float = Field(
        default=DEFAULT_TIME_LIMIT,
        ge=MIN_TIME_LIMIT,
        le=MAX_TIME_LIMIT,
        description=f"墙钟超时秒数，{MIN_TIME_LIMIT}~{MAX_TIME_LIMIT}。",
    )
    mem_limit_mb: int = Field(
        default=DEFAULT_MEM_LIMIT_MB,
        ge=MIN_MEM_LIMIT_MB,
        le=MAX_MEM_LIMIT_MB,
        description=f"内存上限 MB，{MIN_MEM_LIMIT_MB}~{MAX_MEM_LIMIT_MB}。",
    )


class JudgeCaseArgs(BaseModel):
    """一个测试用例：标准输入 + 期望输出。"""

    input: str = Field(default="", description="该用例的标准输入。")
    expected_output: str = Field(default="", description="该用例的期望输出。")


class JudgeArgs(BaseModel):
    """judge 工具的参数。"""

    code: str = Field(description="要判题的完整源代码。")
    test_cases: list[JudgeCaseArgs] = Field(
        min_length=1,
        description="至少一个测试用例。",
    )
    language: str = Field(
        default=DEFAULT_LANGUAGE,
        description="语言 id，例如 cpp / java / python。",
    )
    time_limit: float = Field(
        default=DEFAULT_TIME_LIMIT,
        ge=MIN_TIME_LIMIT,
        le=MAX_TIME_LIMIT,
        description=f"每个用例的墙钟超时秒数，{MIN_TIME_LIMIT}~{MAX_TIME_LIMIT}。",
    )
    mem_limit_mb: int = Field(
        default=DEFAULT_MEM_LIMIT_MB,
        ge=MIN_MEM_LIMIT_MB,
        le=MAX_MEM_LIMIT_MB,
        description=f"每个用例的内存上限 MB，{MIN_MEM_LIMIT_MB}~{MAX_MEM_LIMIT_MB}。",
    )


class SearchKnowledgeArgs(BaseModel):
    """search_knowledge 工具的参数。"""

    query: str = Field(description="要检索的算法/知识点，用自然语言描述。")
    top_k: int = Field(default=3, ge=1, le=10, description="返回条数，1~10。")


# --------------------------------------------------------------- the tools


def _run_once(
    code: str,
    stdin: str,
    language: str,
    time_limit: float,
    mem_limit_mb: int,
) -> tuple[Optional[dict], Optional[dict]]:
    """Build *code* once and run it against *stdin* under both limits.

    Returns ``(result, error)`` — exactly one of the two is set.

    This deliberately does not reuse :func:`app.sandbox.judge.judge` with a
    single case. Judging compares against an expected output, and this tool
    has none: passing a placeholder expectation would return a fabricated
    WA that the model would then have to be told to ignore. What is shared
    is everything below that — the same scratch-directory policy, the same
    compile step and the same bounded runner — so only the comparison is
    absent.
    """
    try:
        spec = get_language(language)
    except UnknownLanguageError as exc:
        return None, {
            "ok": False,
            "error": f"unknown language {language!r}: {exc}",
        }

    with sandbox_tmpdir() as tmpdir:
        tmp = Path(tmpdir)
        # The filename comes from the descriptor, not a literal: javac
        # refuses a file that is not named after its public class.
        src_path = spec.source_path(tmp)
        src_path.write_text(code, encoding="utf-8")

        compiled = compile_source(src_path, spec.id)
        if not compiled.success:
            # A compile error is a result, not a failure of this tool: it is
            # exactly what the model needs in order to fix the code.
            return {
                "ok": True,
                "compiled": False,
                "compile_output": compiled.output,
                "stdout": "",
                "stderr": "",
                "exit_code": compiled.exit_code,
                "timed_out": False,
                "memory_exceeded": False,
                "exec_time": 0.0,
            }, None

        run = run_executable(
            spec.run_cmd(tmp),
            stdin=stdin,
            time_limit=time_limit,
            mem_limit_mb=mem_limit_mb,
            language=spec.id,
        )

    return {
        "ok": True,
        "compiled": True,
        "compile_output": compiled.output,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "exit_code": run.exit_code,
        "timed_out": run.timed_out,
        "memory_exceeded": run.memory_exceeded,
        "exec_time": run.exec_time,
    }, None


def run_code(
    code: str,
    stdin: str = "",
    language: str = DEFAULT_LANGUAGE,
    time_limit: float = DEFAULT_TIME_LIMIT,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
) -> dict:
    """Build *code*, run it once against *stdin*, and report what happened.

    Args:
        code: Source to build and run.
        stdin: Text fed to the program's standard input.
        language: Language id; an unregistered one is a tool error.
        time_limit: Wall-clock timeout in seconds.
        mem_limit_mb: Memory ceiling in megabytes.

    Returns:
        ``{"ok": True, "compiled": bool, "compile_output": str,
        "stdout": str, "stderr": str, "exit_code": int, "timed_out": bool,
        "memory_exceeded": bool, "exec_time": float}`` plus
        ``stdout_truncated`` / ``stderr_truncated`` booleans, or
        ``{"ok": False, "error": str}`` when the tool could not run at all.

    The stream fields carry no expectation of correctness — this tool
    answers "what does this program print", which is the question an
    agent asks when it needs evidence rather than a verdict. Note that
    this is more than the ``POST /api/v1/run-code`` route offers: that
    one takes no stdin at all, and a wrong answer cannot be investigated
    without the input that produced it.
    """
    if not (code or "").strip():
        return {"ok": False, "error": "no code to run"}

    result, error = _run_once(code, stdin, language, time_limit, mem_limit_mb)
    if error is not None:
        return error
    assert result is not None  # narrowed: error is None

    stdout, stdout_clipped = _clip(result["stdout"])
    stderr, stderr_clipped = _clip(result["stderr"])
    result["stdout"] = stdout
    result["stderr"] = stderr
    result["stdout_truncated"] = stdout_clipped
    result["stderr_truncated"] = stderr_clipped
    return result


def judge(
    code: str,
    test_cases: list[dict],
    language: str = DEFAULT_LANGUAGE,
    time_limit: float = DEFAULT_TIME_LIMIT,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
) -> dict:
    """Judge *code* against *test_cases* and return the verdict.

    Args:
        code: Source to judge.
        test_cases: Objects with ``input`` / ``expected_output`` keys.
        language: Language id; an unregistered one is a tool error.
        time_limit: Per-case wall-clock timeout in seconds.
        mem_limit_mb: Per-case memory ceiling in megabytes.

    Returns:
        ``{"ok": True, "status": str, "passed": int, "total": int,
        "compile_output": str, "test_cases": [...]}``, or
        ``{"ok": False, "error": str}``.

    ``status`` is a :class:`~app.sandbox.judge.JudgeStatus` value
    (``AC`` / ``WA`` / ``TLE`` / ``MLE`` / ``CE`` / ``RE``). A ``WA`` here
    is a successful tool call carrying bad news, not a tool failure.

    Passing ``test_cases`` as plain dicts rather than model instances is
    intentional: this is the shape the model sends, and P2-7 hands its
    parsed arguments straight through. The shape is validated by
    :class:`JudgeArgs` before this function is reached, so the defensive
    conversion below cannot be reached with a malformed element.
    """
    if not (code or "").strip():
        return {"ok": False, "error": "no code to judge"}

    try:
        result = _sandbox_judge(
            code,
            [TestCase(**case) for case in test_cases],
            time_limit=time_limit,
            mem_limit_mb=mem_limit_mb,
            language=language,
        )
    except UnknownLanguageError as exc:
        return {"ok": False, "error": f"unknown language {language!r}: {exc}"}

    compile_output, compile_clipped = _clip(result.compile_output)

    cases: list[dict] = []
    for case in result.test_cases:
        actual, actual_clipped = _clip(case.actual_output)
        stderr, stderr_clipped = _clip(case.stderr)
        cases.append(
            {
                "status": case.status.value,
                "input": case.input,
                "expected_output": case.expected_output,
                "actual_output": actual,
                "stderr": stderr,
                "exit_code": case.exit_code,
                "exec_time": case.exec_time,
                "memory_used": case.memory_used,
                "output_truncated": actual_clipped or stderr_clipped,
            }
        )

    return {
        "ok": True,
        "status": result.status.value,
        "passed": result.passed_count,
        "total": result.total_count,
        "compile_output": compile_output,
        "compile_output_truncated": compile_clipped,
        "test_cases": cases,
    }


def search_knowledge(query: str, top_k: int = 3) -> dict:
    """Retrieve algorithm knowledge relevant to *query*.

    **Not implemented.** RAG arrives in P2-13/P2-17 (Milvus + embeddings);
    until a corpus is indexed there is nothing to retrieve, and this
    returns ``ok: False`` saying so.

    The schema and the argument model are defined now anyway, because the
    slice's point is to settle the tool surface before P2-7 binds it. The
    alternative — omitting the tool until it works — would leave the
    placeholder unnamed in the design, and the model would have no way to
    discover that knowledge retrieval is a thing it will be able to do.

    Returning a plausible-looking empty result instead would be worse than
    failing: the model would conclude the knowledge base holds nothing
    relevant and reason on without it, which is a different claim from
    "the knowledge base does not exist yet".

    Args:
        query: What to look up, in natural language.
        top_k: How many entries to return.

    Returns:
        ``{"ok": False, "error": str, "query": str}`` until P2-13 lands.
    """
    return {
        "ok": False,
        "error": (
            "search_knowledge is not implemented yet: no knowledge base has "
            "been indexed (RAG arrives in P2-13). Reason from the code and "
            "the inputs alone."
        ),
        "query": query,
        "top_k": top_k,
    }


# ------------------------------------------------------------------ registry


@dataclass(frozen=True)
class Tool:
    """A callable the model may invoke, with its schema and arguments.

    ``run`` takes the validated arguments as keyword arguments, so the
    implementation functions stay directly callable and readable
    (``run_code(code=..., stdin=...)``) instead of forcing every test and
    future caller through a wrapper object.

    ``implemented`` exists so P2-7 can choose not to offer the model a tool
    that cannot work yet. Defining a placeholder is this slice's job;
    sending it to the model is a decision that belongs to whoever binds the
    tools, and it should be an explicit one.
    """

    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[..., dict]
    implemented: bool = True

    def schema(self) -> dict:
        """This tool as an OpenAI-compatible ``tools`` entry.

        The parameter schema is generated from ``args_model`` rather than
        written out by hand, which is what keeps the description the model
        sees and the validation its arguments receive in agreement.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="run_code",
            description=(
                "编译并运行一段代码，返回它的标准输出、标准错误和退出码。"
                "用它来取得证据（这段代码到底输出什么），而不是判定对错——"
                "要判定对错请用 judge。"
            ),
            args_model=RunCodeArgs,
            run=run_code,
        ),
        Tool(
            name="judge",
            description=(
                "把一段代码对着给定的测试用例判题，返回整体结论"
                "（AC/WA/TLE/MLE/CE/RE）和每个用例的实际输出。"
                "需要知道代码是否通过时用它。"
            ),
            args_model=JudgeArgs,
            run=judge,
        ),
        Tool(
            name="search_knowledge",
            description=(
                "检索与问题相关的算法知识点。（尚未实现：知识库还没建，"
                "P2-13 才接 RAG。调用它只会得到「未实现」。）"
            ),
            args_model=SearchKnowledgeArgs,
            run=search_knowledge,
            implemented=False,
        ),
    )
}


def tool_schemas(include_placeholders: bool = False) -> list[dict]:
    """The ``tools`` array to send with a chat request.

    Placeholders are excluded by default: offering the model a tool that
    can only fail spends a turn to learn nothing. Pass
    ``include_placeholders=True`` to send everything that is defined.
    """
    return [
        tool.schema()
        for tool in TOOLS.values()
        if include_placeholders or tool.implemented
    ]


def execute(name: str, arguments: Optional[dict[str, Any]] = None) -> dict:
    """Validate *arguments* and run the named tool.

    This is the seam P2-7 uses: it hands over the function name and the
    parsed arguments from the model's tool call and gets back a dict to
    serialise as the tool result.

    Args:
        name: Tool name, as it appeared in the request's schema.
        arguments: The model's arguments. ``None`` means ``{}``, which is
            what a provider sends for a no-argument call.

    Returns:
        The tool's dict. Never raises for anything the model can cause —
        an unknown name, a non-object argument list or a validation
        failure all come back as ``{"ok": False, "error": ...}`` so the
        model can read the problem and retry. Genuine bugs inside a tool
        still propagate, following the same split ``analyze_error`` uses.
    """
    tool = TOOLS.get(name)
    if tool is None:
        known = ", ".join(sorted(TOOLS))
        return {"ok": False, "error": f"unknown tool {name!r} (expected: {known})"}

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {
            "ok": False,
            "error": f"arguments for {name!r} must be an object, "
            f"got {type(arguments).__name__}",
        }

    if not tool.implemented:
        return {
            "ok": False,
            "error": f"tool {name!r} is defined but not implemented yet",
        }

    try:
        validated = tool.args_model(**arguments)
    except ValidationError as exc:
        # Pydantic's message already names the offending field and why;
        # flattening it to a one-liner would throw away the part the model
        # needs in order to correct itself.
        return {"ok": False, "error": f"invalid arguments for {name!r}: {exc}"}

    return tool.run(**validated.model_dump())


__all__ = [
    "DEFAULT_MEM_LIMIT_MB",
    "DEFAULT_TIME_LIMIT",
    "MAX_OUTPUT_CHARS",
    "JudgeArgs",
    "JudgeCaseArgs",
    "RunCodeArgs",
    "SearchKnowledgeArgs",
    "TOOLS",
    "Tool",
    "execute",
    "judge",
    "run_code",
    "search_knowledge",
    "tool_schemas",
]
