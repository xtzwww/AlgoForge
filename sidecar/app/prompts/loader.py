"""Prompt template loading with variable interpolation (P2-1).

AGENTS.md §二.1 makes this module a red line: "不在 prompt 里硬编码业务
逻辑：所有 prompt 走 sidecar/app/prompts/ 模板文件，版本化管理". Prompts
therefore live as ``.md`` files next to this module and are loaded by
name, never assembled inline at a call site.

Why a hand-rolled ``{{variable}}`` interpolator instead of
``str.format`` or Jinja2
-------------------------------------
``str.format`` cannot be used at all: prompt templates routinely contain
literal braces (the JSON example the model is asked to imitate, C++ code
with braces in ``{{}}`` initialisers), and ``format`` would either raise
or silently eat them. Jinja2 would work, but it is a dependency for one
feature, and its full expression language is a liability in a file whose
contents are also read by humans as documentation.

The rule implemented here is deliberately narrow: ``{{name}}`` is a
substitution, everything else is literal text. An unmatched ``{{`` is
therefore a typo rather than a template expression, and is reported as
one.

Strictness
----------
``render`` fails on an unknown variable and on a missing one, and it
also fails when the template declares a variable the caller did not
supply. This is on purpose: the failure mode worth preventing is a
prompt that reaches the model with a literal ``{{code}}`` still in it,
which produces a confidently wrong analysis rather than an error.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

# Template directory. Resolved from this file rather than the process
# CWD so loading works the same whether the sidecar was started from the
# repo root, from sidecar/, or by PyInstaller (S10).
PROMPTS_DIR = Path(__file__).resolve().parent

# {{name}} where name is a Python-ish identifier. Anchoring on the
# identifier shape (rather than matching any run of non-brace characters)
# keeps the pattern from walking off the end of a malformed template.
_VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptError(RuntimeError):
    """Base class for template loading and rendering failures."""


class PromptNotFoundError(PromptError):
    """The named template file does not exist."""


class PromptRenderError(PromptError):
    """The template and the supplied variables do not line up.

    Carries the names that were missing and the names that were unused,
    because "render failed" alone does not tell the caller which of the
    two mistakes they made.
    """

    def __init__(self, message: str, missing: set[str], unused: set[str]):
        super().__init__(message)
        self.missing = missing
        self.unused = unused


def prompt_path(name: str) -> Path:
    """Return the file path for template ``name``.

    ``name`` may be given with or without the ``.md`` suffix. A name
    containing a path separator is rejected: templates are a flat,
    versioned set, and allowing ``../`` here would turn a prompt name
    into an arbitrary file read.
    """
    if not name or not name.strip():
        raise PromptNotFoundError("Prompt name must not be empty")

    stem = name.strip()
    if "/" in stem or "\\" in stem:
        raise PromptNotFoundError(
            f"Prompt name {name!r} must not contain a path separator"
        )

    if not stem.endswith(".md"):
        stem = f"{stem}.md"

    return PROMPTS_DIR / stem


def load_prompt(name: str) -> str:
    """Read template ``name`` verbatim.

    Raises:
        PromptNotFoundError: No such template file.
    """
    path = prompt_path(name)
    if not path.is_file():
        raise PromptNotFoundError(
            f"Prompt {name!r} not found (looked for {path.name} in {PROMPTS_DIR})"
        )

    # Explicit encoding: the templates are Chinese, and the default
    # encoding on Windows is cp1252/utf-8-by-locale rather than utf-8.
    return path.read_text(encoding="utf-8")


def extract_variables(template: str) -> set[str]:
    """Return the set of variable names a template references.

    Used by callers to validate a payload before rendering, and by the
    tests that assert every shipped template declares what its caller
    passes.
    """
    return set(_VARIABLE_RE.findall(template))


@cache
def load_prompt_cached(name: str) -> str:
    """Cached variant of :func:`load_prompt`.

    Prompt files do not change while the sidecar runs, and P2-3 renders
    the same template once per judged submission (P2-5 renders it 20
    times per run), so the read is worth caching. ``lru_cache`` is
    applied to a separate function rather than to :func:`load_prompt`
    itself so tests can still read a template straight off disk after
    editing it.
    """
    return load_prompt(name)


def render(name: str, **variables: str) -> str:
    """Load template ``name`` and substitute ``{{variables}}``.

    Args:
        name: Template name, with or without the ``.md`` suffix.
        **variables: Values for every ``{{name}}`` in the template.

    Returns:
        The rendered prompt.

    Raises:
        PromptNotFoundError: No such template.
        PromptRenderError: A referenced variable was not supplied, or a
            supplied variable is not referenced by the template. Both
            are reported together so one fix can resolve both.
    """
    template = load_prompt_cached(name)
    return render_string(template, _label=name, **variables)


def render_string(
    template: str, *, _label: str = "<inline>", **variables: str
) -> str:
    """Substitute ``{{variables}}`` in an already-loaded template.

    Split out from :func:`render` so tests can exercise the
    interpolation rules without a file on disk.

    Values are coerced with ``str()``, which lets callers pass ints and
    lists (a test-case table, a line number) without pre-formatting.

    The label parameter is ``_label`` rather than ``name`` on purpose.
    A template variable called ``{{name}}`` is entirely plausible (this
    module already ships one prompt with a ``{{code}}``-style set), and
    naming this parameter ``name`` would make that variable
    unsuppliable — the caller's ``name="..."`` would be swallowed by the
    signature and reported as an "unused" variable. Reserving a
    leading-underscore name keeps every identifier in the template
    namespace available, and the strictness check below turns any
    remaining collision into a loud error instead of a silent one.
    """
    referenced = extract_variables(template)
    supplied = set(variables)

    missing = referenced - supplied
    unused = supplied - referenced
    if missing or unused:
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(sorted(missing))}")
        if unused:
            problems.append(f"unused: {', '.join(sorted(unused))}")
        raise PromptRenderError(
            f"Prompt {_label!r} variables do not match ({'; '.join(problems)})",
            missing=missing,
            unused=unused,
        )

    def substitute(match: re.Match[str]) -> str:
        # str() because callers legitimately pass non-str values.
        return str(variables[match.group(1)])

    return _VARIABLE_RE.sub(substitute, template)


def list_prompts() -> list[str]:
    """Return the names of every shipped template, sorted.

    Used by the test that guards the red line: every template in the
    directory must be loadable and must declare at least one variable,
    so a half-finished prompt cannot be committed unnoticed.
    """
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


def render_taxonomy() -> str:
    """Format the root-cause taxonomy for insertion into a prompt.

    Lives here rather than being written out inside ``error_analysis.md``
    because the label list is also the *scoring* set: P2-5 checks
    ``error_type`` for exact equality against ``ROOT_CAUSE_LABELS``. If
    the prompt listed the labels independently, adding a label to the
    taxonomy would silently make it unselectable — the model would never
    emit it, and every case expecting it would score as wrong with no
    hint as to why.

    The descriptions come from ``ROOT_CAUSE_DESCRIPTIONS``, so the
    wording the model sees is the same wording the reports use.
    """
    # Imported here rather than at module scope: eval_cases imports
    # nothing from this module, but keeping the prompt directory free of
    # domain imports means a prompt can be read (by a human, or by the
    # test that lints templates) without the whole app being importable.
    from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS, ROOT_CAUSE_LABELS

    lines = []
    for label in ROOT_CAUSE_LABELS:
        description = ROOT_CAUSE_DESCRIPTIONS.get(label, "")
        lines.append(f"- `{label}`：{description}" if description else f"- `{label}`")
    return "\n".join(lines)
