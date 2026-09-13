"""Tests for app.sandbox.compiler.compile_cpp (P1-11).

Verifies the standalone C++ compilation function:
- Successful compilation produces a binary on disk.
- Compilation failure reports the error and produces no binary.
- A non-existent source path is reported as a failure.

The ``ascii_tmp_path`` fixture is provided by the root conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.sandbox.compiler import CompileResult, compile_cpp

# Minimal valid C++ program. Uses <iostream> to ensure the standard
# library is wired up; outputs nothing to keep the binary lean.
_GOOD_CODE = """\
#include <iostream>
int main() {
    return 0;
}
"""

# Missing semicolon after the cout statement — guaranteed syntax error.
_BAD_CODE = """\
#include <iostream>
int main() {
    std::cout << "missing semicolon" << std::endl
    return 0;
}
"""


@pytest.fixture
def good_source(ascii_tmp_path: Path) -> Path:
    p = ascii_tmp_path / "good.cpp"
    p.write_text(_GOOD_CODE, encoding="utf-8")
    return p


@pytest.fixture
def bad_source(ascii_tmp_path: Path) -> Path:
    p = ascii_tmp_path / "bad.cpp"
    p.write_text(_BAD_CODE, encoding="utf-8")
    return p


def test_compile_success(good_source: Path) -> None:
    """A valid C++ source compiles and produces a binary on disk."""
    result = compile_cpp(good_source)

    assert isinstance(result, CompileResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.executable_path is not None
    # The binary must actually exist where we say it does.
    assert Path(result.executable_path).exists()


def test_compile_success_to_dict(good_source: Path) -> None:
    """to_dict() round-trips a successful result into a JSON-safe dict."""
    result = compile_cpp(good_source)
    d = result.to_dict()

    assert d["success"] is True
    assert d["executable_path"] == str(result.executable_path)
    assert d["exit_code"] == 0
    assert "output" in d


def test_compile_failure_reports_error(bad_source: Path) -> None:
    """A source with a syntax error fails and carries diagnostics."""
    result = compile_cpp(bad_source)

    assert isinstance(result, CompileResult)
    assert result.success is False
    assert result.exit_code != 0
    # No binary should be reported when compilation failed.
    assert result.executable_path is None
    # g++ diagnostics contain the word "error"; we only require that
    # some non-empty diagnostic text is surfaced.
    assert result.output, "Expected non-empty compiler output on failure"
    assert "error" in result.output.lower()


def test_compile_failure_to_dict(bad_source: Path) -> None:
    """to_dict() on a failed result reports None for executable_path."""
    result = compile_cpp(bad_source)
    d = result.to_dict()

    assert d["success"] is False
    assert d["executable_path"] is None
    assert d["exit_code"] != 0


def test_compile_nonexistent_source(ascii_tmp_path: Path) -> None:
    """Compiling a non-existent source must not raise; it returns failure."""
    nonexistent = ascii_tmp_path / "does_not_exist.cpp"
    result = compile_cpp(nonexistent)

    assert isinstance(result, CompileResult)
    assert result.success is False
    assert result.exit_code != 0
    assert result.executable_path is None
    assert result.output, "Expected diagnostic output for missing source"
