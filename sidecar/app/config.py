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
_ENV_FILE = Path(os.environ.get("ALGOFORGE_ENV_FILE") or Path(__file__).resolve().parents[1] / ".env")


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
