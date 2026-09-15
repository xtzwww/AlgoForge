"""Tests for the prompt template system (P2-1).

Two things are being protected here, and the second matters more than
the first:

1. That ``{{variable}}`` interpolation works — the feature.
2. That AGENTS.md §二.1's red line holds — that every prompt actually
   lives in ``sidecar/app/prompts/`` as a file, and that the taxonomy
   the model is asked to pick from is generated from the same constant
   P2-5 scores against.

The second is the one that decays silently. A label added to
``ROOT_CAUSE_LABELS`` but not to the prompt makes that label
unselectable, and every eval case expecting it scores as wrong with no
indication that the prompt was the cause.
"""

import pytest

from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS, ROOT_CAUSE_LABELS
from app.prompts import loader

# ------------------------------------------------------------- loading


def test_shipped_template_is_found():
    """The P2-1 artifact exists and is loadable by name."""
    template = loader.load_prompt("error_analysis")

    assert template.strip()


def test_loading_accepts_a_name_without_the_suffix():
    """``error_analysis`` and ``error_analysis.md`` name the same file.

    Callers should not have to remember the extension, and a mismatch
    here would be a confusing "file not found" for a file that is right
    there.
    """
    assert loader.load_prompt("error_analysis") == loader.load_prompt(
        "error_analysis.md"
    )


def test_missing_template_raises_not_found():
    """A typo in a prompt name is an error, not an empty prompt."""
    with pytest.raises(loader.PromptNotFoundError):
        loader.load_prompt("no_such_prompt")


def test_empty_name_raises_not_found():
    """An empty name cannot silently resolve to the directory itself."""
    with pytest.raises(loader.PromptNotFoundError):
        loader.load_prompt("   ")


@pytest.mark.parametrize("name", ["../config", "sub/dir", "sub\\dir"])
def test_path_separators_are_rejected(name):
    """A prompt name cannot be used to read an arbitrary file.

    Without this, ``render("../../config")`` would happily return the
    settings module — harmless today, but it turns prompt names into a
    file-read primitive.
    """
    with pytest.raises(loader.PromptNotFoundError):
        loader.load_prompt(name)


# --------------------------------------------------------- interpolation


def test_render_substitutes_all_variables():
    """Every ``{{name}}`` is replaced by the supplied value."""
    out = loader.render_string("hello {{name}}, you are {{role}}", name="A", role="B")

    assert out == "hello A, you are B"


def test_no_identifier_is_reserved_in_the_template_namespace():
    """A template variable may be called ``name``.

    Regression guard. ``render_string``'s own label parameter was
    originally spelled ``name``, which made ``{{name}}`` unsuppliable:
    the caller's ``name="..."`` landed in the signature and the variable
    was reported as missing. Renaming the parameter to ``_label`` keeps
    the whole identifier namespace usable — worth a test because the
    symptom (a stray "unused variable" error on a perfectly good
    template) points at the template rather than at the loader.
    """
    assert loader.render_string("hi {{name}}", name="world") == "hi world"


def test_render_tolerates_whitespace_inside_braces():
    """``{{ name }}`` is the same variable as ``{{name}}``.

    Hand-written templates get reflowed; a stray space should not create
    a variable called ``" name "`` that no caller can ever supply.
    """
    assert loader.render_string("x{{ a }}y", a="1") == "x1y"
    assert loader.extract_variables("{{ a }}") == {"a"}


def test_render_leaves_non_variable_braces_alone():
    """Literal braces in code samples survive untouched.

    This is the specific reason ``str.format`` is unusable here: the
    templates show the model JSON and C++ to imitate, and both are full
    of braces.
    """
    out = loader.render_string(
        'initialise as {{}} or {{1, 2}}\n{ "error_type": "x" }\nvalue={{v}}',
        v="9",
    )

    assert "{{}}" in out
    assert "{{1, 2}}" in out
    assert '{ "error_type": "x" }' in out
    assert "value=9" in out


def test_render_coerces_non_string_values():
    """Ints and other scalars can be passed without pre-formatting."""
    assert loader.render_string("n={{n}}", n=42) == "n=42"


def test_repeated_variable_is_substituted_everywhere():
    """A variable used twice gets both occurrences replaced."""
    assert loader.render_string("{{a}}-{{a}}", a="x") == "x-x"


# ------------------------------------------------------------ strictness


def test_missing_variable_raises():
    """A declared-but-unset variable is an error.

    The failure mode being prevented is a prompt reaching the model with
    a literal ``{{code}}`` still in it, which yields a confident and
    completely wrong analysis instead of a stack trace.
    """
    with pytest.raises(loader.PromptRenderError) as excinfo:
        loader.render_string("{{a}} and {{b}}", a="1")

    assert excinfo.value.missing == {"b"}


def test_unused_variable_raises():
    """Supplying a variable the template does not use is also an error.

    It means the caller and the template have drifted; reporting it
    catches a renamed placeholder in the template at the call site
    rather than as a subtly worse prompt.
    """
    with pytest.raises(loader.PromptRenderError) as excinfo:
        loader.render_string("{{a}}", a="1", typo="2")

    assert excinfo.value.unused == {"typo"}


def test_both_mismatches_are_reported_together():
    """Missing and unused are reported in one error, not two rounds."""
    with pytest.raises(loader.PromptRenderError) as excinfo:
        loader.render_string("{{a}} {{b}}", a="1", c="3")

    assert excinfo.value.missing == {"b"}
    assert excinfo.value.unused == {"c"}


def test_render_reports_the_prompt_name_in_the_error():
    """The error names the template, so the fix location is obvious."""
    with pytest.raises(loader.PromptRenderError, match="error_analysis"):
        loader.render("error_analysis")


def test_extract_variables_on_a_template_without_variables():
    """A static template has no variables and renders as itself."""
    assert loader.extract_variables("no variables here") == set()
    assert loader.render_string("no variables here") == "no variables here"


# ------------------------------------------------- the red line (AGENTS.md)


def test_every_shipped_template_declares_variables():
    """Every template is parameterised, not baked.

    A template with no variables is either a fragment that should not be
    a prompt, or an unfinished file. Either way it should not ship.
    """
    names = loader.list_prompts()

    assert names, "no prompt templates found"
    for name in names:
        assert loader.extract_variables(loader.load_prompt(name)), (
            f"template {name!r} declares no variables"
        )


def test_error_analysis_prompt_asks_for_the_full_variable_set():
    """The prompt's variables are exactly what P2-3 must supply.

    Pinned deliberately: this list is the contract between the template
    and the call in ``analyze_error``. If a placeholder is added here,
    this test fails until the caller is updated, which is the point.
    """
    variables = loader.extract_variables(loader.load_prompt("error_analysis"))

    assert variables == {
        "verdict",
        "stdin",
        "expected_output",
        "actual_output",
        "statement",
        "code",
        "root_cause_taxonomy",
    }


def test_taxonomy_covers_every_scorable_label():
    """The prompt offers every label P2-5 can score.

    This is the drift guard. P2-5 compares ``error_type`` for exact
    equality against ``ROOT_CAUSE_LABELS``; a label missing from the
    rendered taxonomy can never be emitted, so its cases would be
    unwinnable and the accuracy number would understate the model.
    """
    taxonomy = loader.render_taxonomy()

    for label in ROOT_CAUSE_LABELS:
        assert f"`{label}`" in taxonomy, f"label {label!r} missing from taxonomy"


def test_taxonomy_carries_the_shared_descriptions():
    """Each label is shown with the same Chinese description reports use."""
    taxonomy = loader.render_taxonomy()

    for label, description in ROOT_CAUSE_DESCRIPTIONS.items():
        assert description in taxonomy, f"description for {label!r} missing"


def test_taxonomy_has_one_line_per_label():
    """No label is duplicated or dropped in formatting."""
    assert len(loader.render_taxonomy().splitlines()) == len(ROOT_CAUSE_LABELS)


def test_rendering_the_real_prompt_leaves_no_placeholders():
    """End-to-end: a full render substitutes everything.

    The general assertion (no ``{{`` survives) is what actually protects
    the model call; the per-variable tests above just localise failures.
    """
    rendered = loader.render(
        "error_analysis",
        verdict="WA",
        stdin="3 7",
        expected_output="21",
        actual_output="0",
        statement="输出两数之积。",
        code="int a, b; std::cin >> a >> b; std::cout << a * b;",
        root_cause_taxonomy=loader.render_taxonomy(),
    )

    assert "{{" not in rendered
    assert "}}" not in rendered
    # The interpolated values must appear as data.
    assert "21" in rendered
    assert "输出两数之积。" in rendered
    assert ROOT_CAUSE_LABELS[0] in rendered
