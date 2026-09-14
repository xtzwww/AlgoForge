"""Tests for the environment self-check endpoint (PU-3).

The endpoint's contract is that it **never fails**: a probe that cannot
find its toolchain must degrade to ``available: false`` plus a human
readable ``detail``, not raise a 500. These tests pin that down, because
the whole point of env-check is to be runnable on a machine where things
are missing.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.sandbox import env_probe
from app.sandbox.languages import all_languages

client = TestClient(app)


# --------------------------------------------------------------- route


def test_env_check_returns_200() -> None:
    response = client.get("/api/v1/env-check")
    assert response.status_code == 200


def test_env_check_has_expected_top_level_keys() -> None:
    body = client.get("/api/v1/env-check").json()
    assert set(body) >= {"languages", "database", "sandbox_tmp", "core_ready"}


def test_env_check_reports_all_three_languages() -> None:
    """C++ / Python / Java must all be reported, available or not."""
    languages = client.get("/api/v1/env-check").json()["languages"]
    assert {lang["id"] for lang in languages} == {"cpp", "python", "java"}


def test_env_check_language_entries_are_well_formed() -> None:
    languages = client.get("/api/v1/env-check").json()["languages"]
    for lang in languages:
        assert set(lang) == {
            "id",
            "display_name",
            "available",
            "version",
            "path",
            "detail",
            # Added by PU-17: the editor needs Monaco's language id and a
            # starter template to offer a language at all.
            "monaco_language",
            "default_template",
        }
        assert isinstance(lang["available"], bool)
        # The contract: unavailable implies an explanation, and no
        # version string is claimed for something we could not run.
        if not lang["available"]:
            assert lang["detail"], f"{lang['id']} unavailable without a detail"
            assert lang["version"] is None


def test_env_check_language_entries_match_the_registry() -> None:
    """The report must not drift from the descriptors the sandbox uses.

    A Monaco id that disagreed with the registry would highlight the
    buffer as the wrong language, and a stale template could be syntax
    the configured toolchain rejects.
    """
    specs = {spec.id: spec for spec in all_languages()}
    languages = client.get("/api/v1/env-check").json()["languages"]

    assert {lang["id"] for lang in languages} == set(specs)
    for lang in languages:
        spec = specs[lang["id"]]
        assert lang["display_name"] == spec.display_name
        assert lang["monaco_language"] == spec.monaco_language
        assert lang["default_template"] == spec.default_template


# ------------------------------------------------------ degradation


def test_missing_compiler_degrades_without_raising() -> None:
    """A nonexistent C++ compiler must yield available=False, not a 500."""
    with patch.object(env_probe.settings, "cpp_compiler_path", "definitely-not-a-compiler"):
        result = env_probe.probe_cpp()

    assert result.available is False
    assert result.detail
    assert result.version is None


def test_missing_javac_degrades_without_raising() -> None:
    """A javac that cannot be found must yield available=False, not a 500.

    Patches the *setting* rather than ``shutil.which``: since PU-15 the
    probe honours ``JAVA_COMPILER_PATH``, so a PATH-only patch would be
    defeated on any machine whose JDK is configured explicitly (which is
    the case on this one). Patching the setting tests the real contract.
    """
    with patch.object(env_probe.settings, "java_compiler_path", "definitely-not-javac"):
        result = env_probe.probe_java()

    assert result.available is False
    assert result.version is None
    # The message must be actionable, not just "not found": the realistic
    # cause is a machine with a JRE but no JDK.
    assert "definitely-not-javac" in (result.detail or "")
    assert "JDK" in (result.detail or "")


def test_java_probe_uses_configured_compiler_path() -> None:
    """The probe must run the *configured* tool, not merely search PATH.

    This is the regression guard for PU-15's fix: before it, a JDK in a
    non-standard location was reported unavailable even though the sandbox
    could have used it.
    """
    with patch.object(
        env_probe.settings,
        "java_compiler_path",
        r"C:\nowhere\jdk\bin\javac.exe",
    ):
        result = env_probe.probe_java()

    assert result.available is False
    assert "javac.exe" in (result.detail or "")


def test_env_check_survives_reachable_but_broken_database() -> None:
    """An unreachable DB must be reported, not propagated.

    This is the realistic case on a dev machine with Postgres stopped.
    """
    with patch.object(
        env_probe.settings,
        "database_url",
        "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nope",
    ):
        result = env_probe.probe_database()

    assert result.available is False
    assert result.version == "postgresql"  # dialect still reported
    assert result.detail


def test_env_check_never_raises_on_empty_database_url() -> None:
    with patch.object(env_probe.settings, "database_url", ""):
        result = env_probe.probe_database()

    assert result.available is False
    assert result.detail == "DATABASE_URL is empty"


# ---------------------------------------------------- probe_all()


def test_probe_all_is_json_serialisable() -> None:
    """The route returns this straight to the client — it must serialise."""
    import json

    report = env_probe.probe_all()
    assert json.loads(json.dumps(report)) == report


def test_core_ready_reflects_language_availability() -> None:
    """core_ready is True when at least one language can actually run."""
    report = env_probe.probe_all()
    assert report["core_ready"] is any(lang["available"] for lang in report["languages"])


# ------------------------------------------------- sandbox temp dir


def test_sandbox_tmp_base_is_ascii_on_windows() -> None:
    """MinGW's linker cannot write to non-ASCII paths.

    The user profile on this machine contains CJK characters, so the
    sandbox temp base must not live under it on Windows.
    """
    import os

    base = env_probe.sandbox_tmp_base()
    if os.name == "nt":
        assert str(base).startswith("C:\\Windows\\Temp")


def test_sandbox_tmp_is_actually_writable() -> None:
    result = env_probe.probe_sandbox_tmp()
    assert result.available is True, result.detail
