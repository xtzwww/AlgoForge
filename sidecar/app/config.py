"""Application configuration loaded from environment variables.

All settings correspond to the ``.env`` definitions in AGENTS.md.
Secrets (API keys) are stored as plain strings but never logged.

Pydantic Settings reads from (in order of precedence):
1. Environment variables
2. ``.env`` file (if present)
3. Field defaults
"""

import os
import secrets
import sys
from pathlib import Path
from typing import Any, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to ``sidecar/.env``, anchored to this file rather than to
# the process's working directory.
#
# ``env_file=".env"`` is resolved against the cwd, so a bare relative path
# silently stops being read the moment anything runs from the repository
# root instead of ``sidecar/`` — which is exactly what the test suite and
# ad-hoc scripts do. The failure is silent in the worst way: settings
# fall back to their defaults, so a provider configured in ``.env`` looks
# unconfigured and the error message blames the wrong thing. Anchoring it
# here makes the setting independent of where the process was started.
# Split into two statements only to stay inside the 88-column lint limit;
# behaviour is unchanged — the environment variable still wins.
_DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_ENV_FILE = Path(os.environ.get("ALGOFORGE_ENV_FILE") or _DEFAULT_ENV_FILE)


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator(
        "sidecar_port",
        "sidecar_token",
        "python_interpreter_path",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, v: Any) -> Any:
        """Treat an empty string as "not set".

        AGENTS.md documents ``SIDECAR_PORT=`` (blank) as "pick a random
        port at runtime", and the same for ``SIDECAR_TOKEN``. Pydantic
        would otherwise try to parse ``""`` into ``Optional[int]`` and
        fail, so a blank value is normalized to ``None`` here. Without
        this, copying .env.example to .env and leaving the blanks as
        shipped would crash the sidecar on startup.

        ``PYTHON_INTERPRETER_PATH`` is included because shipping it blank
        is the documented way to say "use the sidecar's own interpreter".
        Normalising to ``None`` keeps ``Optional`` meaning what it says
        instead of having two spellings of "unset" (``None`` and ``""``)
        that every caller would have to remember to check.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # ------------------------------------------------------------------ LLM
    llm_provider: str = Field(
        default="deepseek",
        description="LLM provider: deepseek | ollama | openai_compatible",
    )
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-coder")

    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder:7b")

    # Generic OpenAI-compatible endpoint. Any provider that speaks the
    # OpenAI chat-completions protocol can be pointed at by setting
    # ``LLM_PROVIDER=openai_compatible`` and the three values below,
    # without touching code — which is what AGENTS.md's "换模型只改配置"
    # actually requires once the provider is not DeepSeek or Ollama.
    #
    # The API key is deliberately *not* required: self-hosted endpoints
    # (vLLM, LM Studio, a gateway with no auth) legitimately have none,
    # and a 401 from the server names the problem well enough. When a key
    # is present it is sent.
    openai_compatible_base_url: str = Field(default="")
    openai_compatible_api_key: str = Field(default="")
    openai_compatible_model: str = Field(default="")

    # Response cap for analysis calls, in tokens. Applies to every
    # provider, because how many tokens an answer needs is a property of
    # the *model*, not of the caller.
    #
    # This is a cap and not a target: a model that finishes on its own
    # emits ``finish_reason: stop`` and is billed only for what it wrote,
    # so raising it costs nothing for a terse model. Lowering it too far,
    # on the other hand, is a hard failure — the reply is cut off
    # mid-string and the JSON parser reports "not valid JSON", which
    # blames the model's formatting for what is really a budget mistake.
    #
    # Two calibration facts, measured 2026-09-16:
    #   * A *non-reasoning* model answering the four-field error analysis
    #     produced ~6600 characters (~3k tokens). The original 800 was
    #     already too low for it; it only ever worked because
    #     qwen2.5:3b happens to be terse.
    #   * A *reasoning* model (agnes-2.5-flash) bills its hidden
    #     ``reasoning_content`` against this same budget. It spent 8000
    #     tokens reasoning and emitted an empty answer, then converged at
    #     16000 with 4142 reasoning + 322 answer tokens. Reasoning models
    #     therefore need this raised by roughly an order of magnitude.
    llm_max_tokens: int = Field(default=4096)

    # Per-request timeout for analysis calls, in seconds. Also model
    # dependent, and for the same reason as ``llm_max_tokens``: a hosted
    # endpoint generating thousands of tokens takes far longer than a
    # local 3B model writing a sentence.
    #
    # Measured 2026-09-16: with the gateway's 60s default, 2 of the 20
    # eval cases died as ``LLMConnectionError: timed out after 60.0s``
    # and were scored as misses. Those are infrastructure failures
    # masquerading as model errors, and they depress the accuracy figure
    # that the whole go/no-go decision rests on.
    llm_timeout: float = Field(default=60.0)

    # ------------------------------------------------------ LLM 护栏（S10）
    # 这三个设置由 ``app.agent.guardrails.guardrail_policy()`` 读取，护栏
    # 本身默认**没有**接进判题主路径（``analyzer`` / ``team`` 仍直调网关），
    # 所以改这里不会立刻改变系统行为——要生效还得把调用点换成
    # ``guarded_chat_completion``。这一点在 S10 的收尾里是刻意的：
    # 打开护栏会改变「每次提交发出去什么」和「每次失败花多少钱」，
    # 值得作为一次单独的、有人看过 diff 的改动。
    #
    # 发送前是否脱敏邮箱与卡号。默认开——学生代码的注释里可能带着个人
    # 信息，而提示词是要发给第三方端点的。**不包含** IP / MAC / URL：
    # 那三种在这个领域里是题目本身（IP 地址转换、URL 解析），脱掉就等于
    # 把要分析的东西删了。
    llm_pii_redaction: bool = Field(default=True)

    # 分析调用是否走护栏链（PII 脱敏 → provider 兜底 → 瞬时重试）。
    #
    # 默认开：这三层都是保护，关掉它们只会让失败更脆。留这个开关是为了
    # 一键回退——护栏一旦真的接进主路径，它就站在学生等 judge_done 的路上，
    # 出问题时需要有比「改代码」更快的办法。关掉之后调用点直连网关，行为
    # 与接线前逐字一致。
    #
    # 下面三项是链内部的策略，与这个开关独立：关掉开关它们也就不生效了。
    llm_guardrails: bool = Field(default=True)

    # 兜底 provider 链，逗号分隔，写在**主路径之后**。留空表示只用
    # LLM_PROVIDER 那一个。例如 ``ollama`` 表示「DeepSeek 不行就用 Ollama」
    # —— AGENTS.md §一 的「主路径 DeepSeek API，兜底 Ollama」。
    # 名字必须是网关认识的 provider，写错会在构建策略时直接报错。
    llm_fallback_providers: str = Field(default="")

    # 每个 provider 的瞬时故障重试次数（不含首次调用）。0 表示不重试。
    #
    # 默认 1，且**默认不重试超时**（见 guardrails.should_retry）：一次超时
    # 已经耗掉整个 LLM_TIMEOUT，再重试一次就是让学生在 judge_done 前面
    # 多等一个超时周期。真正值得重试的是 5xx 与 429 —— 它们立刻返回，
    # 而且 429 本来就是退避重试的经典场景。
    llm_retry_attempts: int = Field(default=1, ge=0)

    # ------------------------------------------------- 编排器选择（S4/S7 接线）
    # 判题主路径用哪个编排器做错因分析。取值由
    # ``app.agent.pipeline.resolved_pipeline()`` 解析：
    #
    #   ``direct``（**默认**）—— 直接调 ``analyzer.analyze_error``，即 S2 的
    #     单次调用路径。默认值刻意保持不动：见下面「为什么默认不换」。
    #   ``graph`` —— 走 S4 的 LangGraph 状态图（read_code → retrieve →
    #     analyse → self_check，自检不满意时带一条回边重来），检索到的知识
    #     卡片自动拼进 prompt。
    #
    # 为什么默认不换
    # --------------
    # 两个原因，都不是「图不好」：
    #
    # 1. **成本与延迟翻倍**。图每轮调用 ``analyze_error`` 用的还是它自己的
    #    ``max_attempts=2``，再叠上 ``DEFAULT_MAX_RETRIES=1`` 的自检回边，
    #    最坏情况一次分析 **4 次模型调用**，而 direct 是 2 次。这段代码跑在
    #    学生等 ``judge_done`` 的路上，预算既是钱也是延迟。
    # 2. **换掉就验证不了**。S2 的「20 道真题准确率 ≥ 60%」是 AGENTS.md 定的
    #    生死线，而验证它要跑 ``pytest -m eval_set``（真打付费端点、按量计费），
    #    日常与夜间都不跑。也就是说：换了编排器，**没人能回答准确率有没有退**。
    #    所以这里只提供开关，把「换」留成一次有人在场、能当场跑评估集的改动。
    #
    # 非法取值（拼错、写成 ``langgraph``）一律回退到 ``direct`` 并记一条警告，
    # 不抛异常：这条路径的契约是「分析失败要降级，不能把判题带崩」，
    # 见 ``app/agent/pipeline.py``。
    analysis_pipeline: str = Field(default="direct")

    # ----------------------------------------------------------- LangSmith
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="algoforge")

    # ------------------------------------------------------------- Sidecar
    sidecar_port: Optional[int] = Field(
        default=None,
        description="Listen port; None means a random port chosen at runtime",
    )
    sidecar_token: Optional[str] = Field(
        default=None,
        description="Auth token; None means auto-generate on startup",
    )
    cors_allow_origins: str = Field(
        default=(
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:4173,http://127.0.0.1:4173"
        ),
        description=(
            "Comma-separated allowed CORS origins for the Electron "
            "renderer in dev (Vite serves on 5173, preview on 4173). "
            "Production uses file:// or a custom scheme, which sends no "
            "Origin header and therefore needs no CORS entry. Set to '*' "
            "to allow everything (not recommended)."
        ),
    )

    # ------------------------------------------------------------ Database
    database_url: str = Field(
        default="postgresql+asyncpg://algoforge:algoforge@localhost:5432/algoforge"
    )
    database_url_sync: Optional[str] = Field(
        default=None,
        description=(
            "Optional blocking-driver DSN for the synchronous API routes. "
            "When unset, database_url is used and async Postgres drivers "
            "are rewritten to their sync equivalent (see app/db.py)."
        ),
    )
    langgraph_checkpoint_uri: str = Field(
        default="postgresql://algoforge:algoforge@localhost:5432/algoforge"
    )

    # -------------------------------------------------------------- Milvus
    milvus_uri: str = Field(default="http://localhost:19530")

    # Milvus 的内嵌构建（milvus-lite）：给它一个 .db 文件路径就走内嵌引擎，
    # 不需要 Docker。留空才用 milvus_uri 去连服务。
    #
    # 默认指向项目内的 data/ 而不是留空，是因为本机 Docker 不通
    # （``wsl.exe`` 被安全策略拉黑），留空等于检索永久不可用。
    milvus_lite_path: str = Field(
        default=str(Path(__file__).resolve().parents[1] / "data" / "milvus.db"),
        description="Embedded Milvus database file; empty means use MILVUS_URI.",
    )

    # ------------------------------------------------- Knowledge retrieval
    # 检索层能不能用，取决于有没有一个「把文字变成向量」的模型。没有它，
    # :func:`app.rag.retriever.build_retriever` 只能降级成空检索，检索节点
    # 就是空转 —— 这才是「检索已实现但真实提交拿不到任何卡片」的根因，
    # 不是某个开关没打开。
    rag_enabled: bool = Field(
        default=True,
        description="Whether the analysis path may consult the knowledge base.",
    )
    embedding_provider: str = Field(
        default="ollama",
        description="ollama (local, free) | openai_compatible | none",
    )
    embedding_model: str = Field(default="bge-m3")
    embedding_base_url: str = Field(default="http://127.0.0.1:11434")
    embedding_api_key: str = Field(default="")

    # 首次调用要把模型从磁盘装进内存（实测 bge-m3 在本机 52 秒），之后每次
    # 约 4 秒。30 秒的默认值会把「模型还在加载」报成超时，看上去像是服务
    # 没起来，所以默认给到 180 秒。
    embedding_timeout: float = Field(default=180.0)

    # 模型在内存里留多久。留短了，隔一会儿的下一个学生提交就要再付一次
    # 52 秒的加载；留长了占内存（bge-m3 约 1.2 GB）。
    embedding_keep_alive: str = Field(default="30m")

    # ------------------------------------------------------- C++ Compiler
    # These are intentionally env-driven (see .env.example). The previous
    # hardcoded default pointed at one specific machine's MinGW install,
    # which made the sandbox untestable anywhere else.
    cpp_compiler_path: str = Field(
        default="g++",
        description=(
            "Path to the C++ compiler executable (g++). Plain 'g++' "
            "means 'resolve from PATH'."
        ),
    )
    cpp_compiler_bin_dir: Optional[str] = Field(
        default=None,
        description=(
            "Directory containing the compiler and its runtime DLLs "
            "(added to PATH for both compilation and execution). "
            "Defaults to the compiler's own directory, which is what "
            "MinGW needs so compiled binaries can find libstdc++."
        ),
    )
    cpp_std: str = Field(default="c++17", description="C++ standard for compilation")
    sandbox_timeout: int = Field(
        default=10,
        description="Timeout in seconds for compiled program execution",
    )

    # ------------------------------------------------ Python Interpreter
    python_interpreter_path: Optional[str] = Field(
        default=None,
        description=(
            "Interpreter used to run Python submissions. None means "
            "'use the interpreter running the sidecar itself' — the one "
            "interpreter guaranteed to exist in this deployment (the "
            "venv the sidecar was started from), which is a more honest "
            "default than guessing at a system Python."
        ),
    )

    # -------------------------------------------------------------- Java
    # A full JDK is required: a JRE ships `java` but no `javac`, and
    # without a compiler nothing can be judged. Both paths are bare
    # command names by default so the sandbox stays portable; a machine
    # with a JDK outside PATH (e.g. the 1.8.0_291 install on this dev
    # box) points JAVA_COMPILER_PATH / JAVA_RUNTIME_PATH at it in .env.
    java_compiler_path: str = Field(
        default="javac",
        description=(
            "Path to the Java compiler (javac). Plain 'javac' means "
            "'resolve from PATH'."
        ),
    )
    java_runtime_path: str = Field(
        default="java",
        description=(
            "Path to the Java launcher (java). Plain 'java' means "
            "'resolve from PATH'."
        ),
    )
    java_max_heap_mb: int = Field(
        default=256,
        ge=1,
        description=(
            "Heap cap passed to the JVM as -Xmx. Java needs an explicit "
            "bound because the runner measures OS-level memory, which a "
            "JVM trips on startup (it reserves tens of MB before running "
            "any user code). With a capped heap the JVM instead raises "
            "OutOfMemoryError at a predictable point."
        ),
    )
    java_rss_allowance_mb: int = Field(
        default=512,
        ge=0,
        description=(
            "Extra resident-memory allowance granted to Java runs on top "
            "of the request's mem_limit_mb before the runner's OS-level "
            "memory check fires. A JVM's RSS is dominated by metaspace, "
            "the code cache and GC structures rather than by the "
            "submission's own allocation, so checking it against a "
            "C++-sized ceiling yields false MLE verdicts on correct code. "
            "The real Java memory bound is java_max_heap_mb; this value "
            "only keeps the OS-level check from firing first. Raise it if "
            "a correct Java submission is still reported as MLE; set it "
            "to 0 to measure Java's RSS directly."
        ),
    )

    # --------------------------------------------------------- Convenience
    @property
    def resolved_cors_origins(self) -> list[str]:
        """Parse ``cors_allow_origins`` into a list for CORSMiddleware."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def resolved_compiler_bin_dir(self) -> str:
        """Directory to prepend to PATH when compiling / running C++.

        Falls back to the compiler executable's own directory so the two
        settings cannot drift out of sync. Returns an empty string when
        the compiler is a bare name (resolved via PATH), in which case
        there is nothing to prepend.
        """
        if self.cpp_compiler_bin_dir:
            return self.cpp_compiler_bin_dir
        # A bare command name (no path separators) is looked up on PATH.
        if os.sep not in self.cpp_compiler_path and "/" not in self.cpp_compiler_path:
            return ""
        return str(Path(self.cpp_compiler_path).parent)

    @property
    def resolved_python_interpreter(self) -> str:
        """Interpreter used to run / syntax-check Python submissions.

        Falls back to ``sys.executable`` so the sandbox uses the very
        interpreter the sidecar is running under. That is deliberate: the
        sidecar's own venv is the only interpreter we can be certain
        exists, and it keeps the Python toolchain identical between the
        application and any code it executes.

        That reasoning stops holding once the sidecar is frozen. Under
        PyInstaller ``sys.executable`` is the sidecar's own ``.exe``, so
        the default would ask the sidecar to syntax-check Python
        submissions *with itself*: the unknown arguments are ignored, a
        second HTTP server starts and never exits, and the sandbox reports
        the result as ``Compilation timed out (>30s)`` — a CE that says
        nothing about the student's code and looks like a broken judge
        rather than a misconfigured interpreter. A frozen build therefore
        falls back to a bare ``python`` resolved through PATH, and a
        machine whose interpreter is not on PATH points
        ``PYTHON_INTERPRETER_PATH`` at it.
        """
        if self.python_interpreter_path:
            return self.python_interpreter_path
        if getattr(sys, "frozen", False):
            return "python"
        return sys.executable

    @property
    def resolved_java_bin_dir(self) -> str:
        """Directory to prepend to PATH so bare ``java``/``javac`` resolve.

        Mirrors :attr:`resolved_compiler_bin_dir`: when the compiler path
        is absolute we can infer the JDK's ``bin`` directory from it, which
        is what makes a bare ``java`` setting work on a machine whose JDK
        is not on PATH. Returns an empty string when the compiler is a bare
        command name, in which case PATH already resolves it.
        """
        compiler = self.java_compiler_path
        if os.sep not in compiler and "/" not in compiler:
            return ""
        return str(Path(compiler).parent)

    @property
    def resolved_sidecar_token(self) -> str:
        """Return the sidecar auth token.

        If ``sidecar_token`` is not set, generate a cryptographically
        random URL-safe token **once** and keep it.

        The generation used to be per-call, which is harmless for a
        launcher that reads this once and passes it to the child process
        -- but it makes the value unusable as a credential to *compare
        against*: two calls would disagree, so a request could never
        match. Writing it back rather than returning a fresh value each
        time is what makes ``SIDECAR_TOKEN=`` (blank = "make one up") and
        the token check in :mod:`app.security` able to coexist.
        """
        if not self.sidecar_token:
            self.sidecar_token = secrets.token_urlsafe(32)
        return self.sidecar_token


# Module-level singleton. Import as ``from app.config import settings``.
settings = Settings()
