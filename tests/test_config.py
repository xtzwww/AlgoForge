"""Tests for app.config.Settings (P1-2).

Verifies default values, environment-variable overrides, and the
auto-generated sidecar token.
"""

from pathlib import Path

import pytest

from app.config import _ENV_FILE, Settings

# A path that is guaranteed not to exist, used to opt a Settings() instance
# out of reading the developer's local .env file. Tests that assert on
# *field defaults* must use this, otherwise a legitimate local .env wins
# and the defaults become untestable.
#
# Every ``Settings()`` in this module passes it. They did not all used to,
# and the gap was invisible: ``env_file`` was a path relative to the
# process's working directory, so running pytest from the repository root
# never found ``sidecar/.env`` and the un-isolated tests passed by
# accident. Anchoring ``env_file`` to the config module (2026-09-16) made
# the local file load for real, and the tests that had been relying on its
# absence started failing — which is what this constant was for all along.
_NO_ENV_FILE = "nonexistent-test-isolation.env"

# All environment variables recognised by Settings. Used to clear the
# environment before asserting defaults, so pre-existing env vars don't
# leak into the test.
_ENV_VARS = [
    "LLM_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_MODEL",
    "LLM_MAX_TOKENS",
    "LLM_TIMEOUT",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "SIDECAR_PORT",
    "SIDECAR_TOKEN",
    "DATABASE_URL",
    "LANGGRAPH_CHECKPOINT_URI",
    "MILVUS_URI",
    "CPP_COMPILER_PATH",
    "CPP_COMPILER_BIN_DIR",
    "CPP_STD",
    "SANDBOX_TIMEOUT",
]


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_default_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should expose the documented defaults when no env vars set."""
    _clear_env(monkeypatch)
    s = Settings(_env_file=_NO_ENV_FILE)

    assert s.llm_provider == "deepseek"
    assert s.deepseek_base_url == "https://api.deepseek.com"
    assert s.deepseek_model == "deepseek-coder"
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.ollama_model == "qwen2.5-coder:7b"
    # The generic provider ships empty: it has no sensible default, and a
    # half-filled .env must fail loudly in the gateway rather than here.
    assert s.openai_compatible_base_url == ""
    assert s.openai_compatible_api_key == ""
    assert s.openai_compatible_model == ""
    # Generous enough for a ~6600-character non-reasoning answer. A cap,
    # so this costs nothing for a model that stops earlier.
    assert s.llm_max_tokens == 4096
    # The gateway's own default, kept so existing local setups are
    # unaffected. Hosted endpoints need this raised; see .env.example.
    assert s.llm_timeout == 60.0
    assert s.langsmith_project == "algoforge"
    assert s.sidecar_port is None
    assert s.sidecar_token is None
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.langgraph_checkpoint_uri.startswith("postgresql://")
    assert s.milvus_uri == "http://localhost:19530"


def test_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables must override field defaults."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SIDECAR_PORT", "9100")
    monkeypatch.setenv("SIDECAR_TOKEN", "my-secret-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    monkeypatch.setenv("MILVUS_URI", "http://milvus:19530")

    s = Settings(_env_file=_NO_ENV_FILE)

    assert s.llm_provider == "ollama"
    assert s.sidecar_port == 9100
    assert s.sidecar_token == "my-secret-token"
    assert s.deepseek_api_key == "sk-test-123"
    assert s.milvus_uri == "http://milvus:19530"


def test_resolved_sidecar_token_generates_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolved_sidecar_token should generate a random token when unset."""
    _clear_env(monkeypatch)
    s = Settings(_env_file=_NO_ENV_FILE)
    token = s.resolved_sidecar_token
    assert isinstance(token, str)
    assert len(token) > 16


def test_resolved_sidecar_token_returns_set_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolved_sidecar_token should return the explicitly set token."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("SIDECAR_TOKEN", "explicit-token-abc")
    s = Settings(_env_file=_NO_ENV_FILE)
    assert s.resolved_sidecar_token == "explicit-token-abc"


def test_blank_env_values_mean_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank SIDECAR_PORT/SIDECAR_TOKEN must parse as None, not crash.

    .env.example ships these two keys as `SIDECAR_PORT=` / `SIDECAR_TOKEN=`
    (AGENTS.md: blank means "pick a random value at runtime"). Before the
    _blank_means_unset validator existed, copying the template to .env
    crashed Settings with an int-parsing ValidationError.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("SIDECAR_PORT", "")
    monkeypatch.setenv("SIDECAR_TOKEN", "   ")
    s = Settings(_env_file=_NO_ENV_FILE)
    assert s.sidecar_port is None
    assert s.sidecar_token is None
    # A generated token is still available.
    assert len(s.resolved_sidecar_token) > 16


def test_compiler_defaults_are_portable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compiler must default to a PATH-resolvable name, not a local path.

    Hardcoding one machine's MinGW install made the sandbox unusable
    anywhere else, so the default must be the bare command name.
    """
    _clear_env(monkeypatch)
    s = Settings(_env_file=_NO_ENV_FILE)
    assert s.cpp_compiler_path == "g++"
    assert s.cpp_compiler_bin_dir is None
    # A bare command name has nothing to prepend to PATH.
    assert s.resolved_compiler_bin_dir == ""


def test_resolved_compiler_bin_dir_derives_from_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only the compiler path is set, its directory is used as bin dir."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("CPP_COMPILER_PATH", "D:/mingw64/bin/g++.exe")
    s = Settings(_env_file=_NO_ENV_FILE)
    # Path.parent normalizes separators, so compare via Path.
    assert Path(s.resolved_compiler_bin_dir) == Path("D:/mingw64/bin")


def test_explicit_bin_dir_wins_over_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly configured bin dir overrides the derived one."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("CPP_COMPILER_PATH", "D:/mingw64/bin/g++.exe")
    monkeypatch.setenv("CPP_COMPILER_BIN_DIR", "D:/runtime/dlls")
    s = Settings(_env_file=_NO_ENV_FILE)
    assert Path(s.resolved_compiler_bin_dir) == Path("D:/runtime/dlls")


def test_env_file_is_absolute_and_points_at_the_sidecar_directory() -> None:
    """``env_file`` must not depend on the process's working directory.

    This is a regression test for a silent bug found 2026-09-16: with
    ``env_file=".env"`` pydantic-settings resolved the path against cwd,
    so ``sidecar/.env`` was read only when the process happened to be
    started from ``sidecar/``. From the repository root — where pytest
    and most ad-hoc scripts run — every setting silently fell back to
    its default, and the symptom was a misleading error about the
    *DeepSeek* key while ``.env`` said ``openai_compatible``.
    """
    assert _ENV_FILE.is_absolute()
    # sidecar/.env, i.e. the parent of the app package.
    assert _ENV_FILE.name == ".env"
    assert _ENV_FILE.parent.name == "sidecar"

