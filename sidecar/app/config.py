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
from pathlib import Path
from typing import Any, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("sidecar_port", "sidecar_token", mode="before")
    @classmethod
    def _blank_means_unset(cls, v: Any) -> Any:
        """Treat an empty string as "not set".

        AGENTS.md documents ``SIDECAR_PORT=`` (blank) as "pick a random
        port at runtime", and the same for ``SIDECAR_TOKEN``. Pydantic
        would otherwise try to parse ``""`` into ``Optional[int]`` and
        fail, so a blank value is normalized to ``None`` here. Without
        this, copying .env.example to .env and leaving the blanks as
        shipped would crash the sidecar on startup.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # ------------------------------------------------------------------ LLM
    llm_provider: str = Field(
        default="deepseek",
        description="LLM provider: deepseek | ollama",
    )
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-coder")

    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder:7b")

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
    def resolved_sidecar_token(self) -> str:
        """Return the sidecar auth token.

        If ``sidecar_token`` is not set, generate a cryptographically
        random URL-safe token.
        """
        if not self.sidecar_token:
            return secrets.token_urlsafe(32)
        return self.sidecar_token


# Module-level singleton. Import as ``from app.config import settings``.
settings = Settings()
