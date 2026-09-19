"""Tests for the language descriptor layer (PU-15).

The descriptor is the single source of truth for what a language *is*:
its required source filename, the argv used to build and run it, and the
starter template shown in the editor. PU-16 will rewrite the compiler
around this layer, so these tests pin the contract — the data and the
commands each spec generates — rather than anything about how the
compiler currently happens to be structured.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.sandbox.languages import (
    CPP_SPEC,
    DEFAULT_LANGUAGE,
    JAVA_SPEC,
    LANGUAGES,
    PYTHON_SPEC,
    LanguageSpec,
    UnknownLanguageError,
    all_languages,
    get_language,
    language_ids,
)

# ------------------------------------------------------------- registry


def test_registry_contains_exactly_the_three_languages() -> None:
    assert language_ids() == ["cpp", "python", "java"]


def test_language_ids_match_their_registry_key() -> None:
    """A spec whose id disagrees with its key would break get_language."""
    for key, spec in LANGUAGES.items():
        assert key == spec.id


def test_all_languages_preserves_display_order() -> None:
    assert [s.id for s in all_languages()] == language_ids()


def test_get_language_returns_the_registered_instance() -> None:
    assert get_language("cpp") is CPP_SPEC
    assert get_language("python") is PYTHON_SPEC
    assert get_language("java") is JAVA_SPEC


def test_get_language_is_case_insensitive_and_strips_space() -> None:
    assert get_language("CPP") is CPP_SPEC
    assert get_language(" Java ") is JAVA_SPEC
    assert get_language("PyThOn") is PYTHON_SPEC


def test_get_language_defaults_when_unset() -> None:
    """None and blank both select the default, so callers need no branch."""
    assert DEFAULT_LANGUAGE == "cpp"
    assert get_language().id == DEFAULT_LANGUAGE
    assert get_language(None).id == DEFAULT_LANGUAGE
    assert get_language("").id == DEFAULT_LANGUAGE
    assert get_language("   ").id == DEFAULT_LANGUAGE


# ------------------------------------------------------- unknown language


def test_unknown_language_raises_a_clear_error() -> None:
    """A bare KeyError('cppp') is not actionable — the message must help."""
    with pytest.raises(UnknownLanguageError) as excinfo:
        get_language("cppp")

    message = str(excinfo.value)
    assert "cppp" in message
    # Every valid id should be listed so the caller can self-correct.
    for known in ("cpp", "python", "java"):
        assert known in message


def test_unknown_language_error_is_not_a_key_error() -> None:
    """Callers must not be able to swallow this as a generic dict miss."""
    error = UnknownLanguageError("nope", ["cpp"])
    assert isinstance(error, LookupError)
    assert not isinstance(error, KeyError)


def test_non_string_language_id_raises_clear_error() -> None:
    """A JSON body can carry a number or list here; it must not TypeError."""
    with pytest.raises(UnknownLanguageError):
        get_language(42)
    with pytest.raises(UnknownLanguageError):
        get_language(["cpp"])


# ------------------------------------------------------ source filenames


def test_source_filenames() -> None:
    assert CPP_SPEC.source_filename == "main.cpp"
    assert PYTHON_SPEC.source_filename == "main.py"
    assert JAVA_SPEC.source_filename == "Main.java"


def test_source_suffix_derives_from_source_filename() -> None:
    assert CPP_SPEC.source_suffix == ".cpp"
    assert PYTHON_SPEC.source_suffix == ".py"
    assert JAVA_SPEC.source_suffix == ".java"


def test_source_path_joins_work_dir() -> None:
    work = Path("C:/work")
    assert CPP_SPEC.source_path(work) == work / "main.cpp"
    assert JAVA_SPEC.source_path(work) == work / "Main.java"


def test_java_source_filename_matches_its_public_class() -> None:
    """The structural difference that makes ``source_filename`` necessary.

    ``javac`` refuses to compile a file whose name disagrees with its
    public class, so this is not a stylistic choice — renaming the Java
    source to ``main.java`` would break Java judging in a way that no
    compiler flag can repair.
    """
    match = re.search(r"public\s+class\s+(\w+)", JAVA_SPEC.default_template)
    assert match, "Java template declares no public class"

    declared = match.group(1)
    filename_stem = Path(JAVA_SPEC.source_filename).stem
    assert declared == filename_stem, (
        f"template declares class {declared!r} but the file is "
        f"{JAVA_SPEC.source_filename!r}; javac will reject it"
    )


# ------------------------------------------------------- command generation

WORK = Path("C:/work")


def test_cpp_compile_command() -> None:
    argv = CPP_SPEC.compile_cmd(CPP_SPEC.source_path(WORK), WORK)

    assert argv[0] == settings.cpp_compiler_path
    assert str(WORK / "main.cpp") in argv
    assert "-o" in argv
    # Output goes into the sandbox-owned work dir, not beside the source.
    assert str(WORK / ("main.exe" if os.name == "nt" else "main")) in argv
    assert f"-std={settings.cpp_std}" in argv


def test_cpp_run_command_executes_the_built_binary() -> None:
    argv = CPP_SPEC.run_cmd(WORK)

    expected = WORK / ("main.exe" if os.name == "nt" else "main")
    assert argv == [str(expected)]


def test_python_compile_command_is_a_syntax_check() -> None:
    """Python has no build phase, but must still be able to yield CE."""
    argv = PYTHON_SPEC.compile_cmd(PYTHON_SPEC.source_path(WORK), WORK)

    assert argv[0] == settings.resolved_python_interpreter
    assert argv[1:3] == ["-m", "py_compile"]
    assert str(WORK / "main.py") in argv


def test_python_run_command_uses_the_interpreter() -> None:
    argv = PYTHON_SPEC.run_cmd(WORK)

    assert argv[0] == settings.resolved_python_interpreter
    assert argv[1] == str(WORK / "main.py")


def test_python_interpreter_setting_is_honoured() -> None:
    """The override must take effect, not just the sys.executable default."""
    with patch.object(settings, "python_interpreter_path", r"C:\py\python.exe"):
        argv = PYTHON_SPEC.run_cmd(WORK)

    assert argv[0] == r"C:\py\python.exe"


def test_unfrozen_python_default_is_the_running_interpreter() -> None:
    """Source runs keep the venv's own interpreter; only frozen builds differ."""
    with patch.object(settings, "python_interpreter_path", None):
        assert settings.resolved_python_interpreter == sys.executable


def test_frozen_python_default_is_not_the_sidecar_exe() -> None:
    """Regression: PyInstaller turns ``sys.executable`` into the sidecar's exe.

    The frozen build used to hand that exe to ``python -m py_compile``, so a
    Python submission started a *second* sidecar that ignored the unknown
    arguments, served HTTP and never exited. The sandbox then reported
    ``Compilation timed out (>30s)`` — a CE that blamed the student's code for
    what was really a wrong interpreter. Falling back to a bare ``python``
    keeps the failure honest: a missing interpreter is now a missing-command
    error, and ``PYTHON_INTERPRETER_PATH`` remains the documented escape.
    """
    with (
        patch.object(settings, "python_interpreter_path", None),
        patch.object(sys, "frozen", True, create=True),
    ):
        interpreter = settings.resolved_python_interpreter

    assert interpreter == "python"
    assert interpreter != sys.executable


def test_java_compile_command_pins_the_output_directory() -> None:
    """``-d`` matters: without it .class files land beside the source."""
    argv = JAVA_SPEC.compile_cmd(JAVA_SPEC.source_path(WORK), WORK)

    assert argv[0] == settings.java_compiler_path
    assert "-d" in argv
    assert argv[argv.index("-d") + 1] == str(WORK)
    assert str(WORK / "Main.java") in argv


def test_java_run_command_derives_classpath_from_work_dir() -> None:
    """This is why run_cmd takes a directory rather than an executable."""
    argv = JAVA_SPEC.run_cmd(WORK)

    assert argv[0] == settings.java_runtime_path
    assert "-cp" in argv
    assert argv[argv.index("-cp") + 1] == str(WORK)
    # The class name is the source filename's stem, not a second constant.
    assert argv[-1] == Path(JAVA_SPEC.source_filename).stem


def test_java_run_command_includes_heap_cap() -> None:
    """A JVM without -Xmx trips the OS-level memory check on startup."""
    argv = JAVA_SPEC.run_cmd(WORK)
    assert f"-Xmx{settings.java_max_heap_mb}m" in argv

    with patch.object(settings, "java_max_heap_mb", 512):
        assert "-Xmx512m" in JAVA_SPEC.run_cmd(WORK)


def test_commands_are_functions_not_frozen_lists() -> None:
    """Config must be read at call time, not at import time.

    If a spec froze its argv at import, patching a compiler path in a test
    (or reconfiguring at runtime) would silently have no effect.
    """
    with patch.object(settings, "cpp_compiler_path", r"C:\other\g++.exe"):
        argv = CPP_SPEC.compile_cmd(CPP_SPEC.source_path(WORK), WORK)
    assert argv[0] == r"C:\other\g++.exe"


def test_every_spec_can_generate_both_commands() -> None:
    for spec in all_languages():
        assert spec.compile_cmd is not None, spec.id
        compile_argv = spec.compile_cmd(spec.source_path(WORK), WORK)
        run_argv = spec.run_cmd(WORK)
        assert compile_argv and all(isinstance(a, str) for a in compile_argv)
        assert run_argv and all(isinstance(a, str) for a in run_argv)


def test_version_commands_name_the_right_tool() -> None:
    assert CPP_SPEC.version_cmd()[0] == settings.cpp_compiler_path
    assert PYTHON_SPEC.version_cmd()[0] == settings.resolved_python_interpreter
    # javac, not java: a JRE ships `java` and still cannot compile.
    assert JAVA_SPEC.version_cmd()[0] == settings.java_compiler_path
    assert "-version" in JAVA_SPEC.version_cmd()


def test_version_commands_are_also_lazy() -> None:
    with patch.object(settings, "java_compiler_path", r"C:\jdk\bin\javac.exe"):
        assert JAVA_SPEC.version_cmd()[0] == r"C:\jdk\bin\javac.exe"


# --------------------------------------------------------------- templates


def test_every_language_has_a_template_and_a_monaco_id() -> None:
    for spec in all_languages():
        assert spec.display_name, spec.id
        assert spec.monaco_language, spec.id
        assert spec.needs_compile is True, spec.id
        # Long enough to be a real starter rather than a placeholder.
        assert len(spec.default_template.strip()) > 20, spec.id


def test_monaco_ids_are_the_ones_monaco_actually_uses() -> None:
    assert CPP_SPEC.monaco_language == "cpp"
    assert PYTHON_SPEC.monaco_language == "python"
    assert JAVA_SPEC.monaco_language == "java"


def test_templates_declare_an_entry_point() -> None:
    assert "int main()" in CPP_SPEC.default_template
    assert '__name__ == "__main__"' in PYTHON_SPEC.default_template
    assert "public static void main" in JAVA_SPEC.default_template


def test_java_template_avoids_post_java8_syntax() -> None:
    """The only JDK here is 1.8.0_291 — a template that will not compile
    is worse than no template at all.
    """
    template = JAVA_SPEC.default_template

    # `var` as a local-variable type (Java 10+). Guard against matching
    # the substring inside a longer identifier.
    assert not re.search(r"\bvar\s+\w+\s*=", template), "`var` needs Java 10+"
    # Switch expressions (Java 14+) and text blocks (Java 15+).
    assert "->" not in template, "switch arrows need Java 14+"
    assert '"""' not in template, "text blocks need Java 15+"
    # `instanceof` patterns (Java 16+).
    assert not re.search(r"instanceof\s+\w+\s+\w+\)", template), (
        "pattern instanceof needs Java 16+"
    )


# ------------------------------------------- templates actually compile

_TEMPLATE_COMPILE_TIMEOUT = 60


def _tool_available(command: str) -> bool:
    """True when *command* (bare name or path) can be located."""
    if os.sep in command or "/" in command:
        return Path(command).exists()
    return shutil.which(command) is not None


@pytest.mark.skipif(
    not _tool_available(settings.cpp_compiler_path),
    reason="configured C++ compiler is not available",
)
def test_cpp_template_compiles(ascii_tmp_path: Path) -> None:
    """A broken starter template would look like a broken sandbox."""
    source = CPP_SPEC.source_path(ascii_tmp_path)
    source.write_text(CPP_SPEC.default_template, encoding="utf-8")

    argv = CPP_SPEC.compile_cmd(source, ascii_tmp_path)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TEMPLATE_COMPILE_TIMEOUT,
    )

    assert proc.returncode == 0, f"C++ template failed to compile:\n{proc.stderr}"


@pytest.mark.skipif(
    not _tool_available(settings.java_compiler_path),
    reason="configured JDK (javac) is not available",
)
def test_java_template_compiles(ascii_tmp_path: Path) -> None:
    """Guards the Java 8 constraint for real, not just by regex."""
    source = JAVA_SPEC.source_path(ascii_tmp_path)
    source.write_text(JAVA_SPEC.default_template, encoding="utf-8")

    argv = JAVA_SPEC.compile_cmd(source, ascii_tmp_path)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TEMPLATE_COMPILE_TIMEOUT,
    )

    assert proc.returncode == 0, f"Java template failed to compile:\n{proc.stderr}"
    assert (ascii_tmp_path / "Main.class").exists(), "-d did not take effect"


def test_python_template_passes_py_compile(ascii_tmp_path: Path) -> None:
    source = PYTHON_SPEC.source_path(ascii_tmp_path)
    source.write_text(PYTHON_SPEC.default_template, encoding="utf-8")

    argv = PYTHON_SPEC.compile_cmd(source, ascii_tmp_path)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TEMPLATE_COMPILE_TIMEOUT,
    )

    assert proc.returncode == 0, f"Python template failed to compile:\n{proc.stderr}"


# ------------------------------------------------------- config coherence


def test_specs_are_immutable() -> None:
    """Frozen dataclasses: shared module-level specs must not be mutable."""
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        CPP_SPEC.id = "c++"  # type: ignore[misc]


def test_registry_values_are_all_language_specs() -> None:
    for spec in all_languages():
        assert isinstance(spec, LanguageSpec)


def test_resolved_java_bin_dir_is_derived_from_compiler_path() -> None:
    r"""A bare `javac` implies no PATH hint; an absolute one implies bin\."""
    with patch.object(settings, "java_compiler_path", "javac"):
        assert settings.resolved_java_bin_dir == ""

    with patch.object(
        settings,
        "java_compiler_path",
        r"C:\jdk\bin\javac.exe",
    ):
        assert settings.resolved_java_bin_dir == r"C:\jdk\bin"


# ------------------------------------------- environment facts (PU-16)
#
# PU-16 gave the descriptor three fields describing the *process*
# environment rather than the command line. They are pinned here with the
# rest of the descriptor contract: the sandbox reads them without
# inspecting ``spec.id``, so a wrong value cannot be caught downstream.


def test_cpp_artefact_is_the_binary_the_run_step_launches() -> None:
    """If these two disagreed, build-then-run would look for two files."""
    expected = WORK / ("main.exe" if os.name == "nt" else "main")

    assert CPP_SPEC.build_artefact(WORK) == expected
    assert CPP_SPEC.run_cmd(WORK) == [str(expected)]


def test_interpreted_languages_declare_no_single_artefact() -> None:
    """Java builds a class *tree*; Python is executed from its source.

    Both would have to name an arbitrary file to return anything here, so
    they report None and callers go through ``run_cmd`` instead.
    """
    assert PYTHON_SPEC.build_artefact(WORK) is None
    assert JAVA_SPEC.build_artefact(WORK) is None


def test_only_java_asks_for_a_memory_allowance() -> None:
    """A JVM's RSS is dominated by fixed overhead, not by the submission."""
    assert CPP_SPEC.rss_allowance_mb() == 0
    assert PYTHON_SPEC.rss_allowance_mb() == 0
    assert settings.java_rss_allowance_mb > 0
    assert JAVA_SPEC.rss_allowance_mb() == settings.java_rss_allowance_mb


def test_cpp_bin_dir_honours_the_explicit_override() -> None:
    """``CPP_COMPILER_BIN_DIR`` exists for DLLs that are not beside g++."""
    with patch.object(settings, "cpp_compiler_bin_dir", r"D:\runtime\dlls"):
        assert CPP_SPEC.toolchain_bin_dir() == r"D:\runtime\dlls"


def test_python_bin_dir_is_derived_from_the_interpreter() -> None:
    interpreter = settings.resolved_python_interpreter
    if os.sep not in interpreter and "/" not in interpreter:
        # A bare name resolves through PATH; there is nothing to prepend.
        assert PYTHON_SPEC.toolchain_bin_dir() == ""
    else:
        assert PYTHON_SPEC.toolchain_bin_dir() == str(Path(interpreter).parent)


def test_java_bin_dir_hint_is_lazy() -> None:
    """Config is read when asked, so a JDK outside PATH can still resolve.

    This is what makes ``JAVA_RUNTIME_PATH=java`` work once the compiler
    path is configured: the sandbox prepends the JDK's own bin directory
    rather than trusting the ambient PATH.
    """
    with patch.object(settings, "java_compiler_path", "javac"):
        assert JAVA_SPEC.toolchain_bin_dir() == ""

    with patch.object(settings, "java_compiler_path", r"C:\jdk\bin\javac.exe"):
        assert JAVA_SPEC.toolchain_bin_dir() == r"C:\jdk\bin"
