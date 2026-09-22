"""Schema + loader for the problem catalogue (PU-14).

The catalogue lives in ``corpus/problems/`` as one YAML file per problem.
This module owns the :class:`CatalogueProblem` dataclass that every file
fills, and :func:`load_catalogue` for reading the directory. The CLI that
validates the files and writes them to the database is
``tools/load_problems.py`` — the split mirrors ``app/eval_cases.py``,
which keeps the schema here and the entry point in ``tools/``.

Why the reference solution is part of the schema
------------------------------------------------
Every problem carries a ``correct_reference`` C++ solution, and that is a
hard requirement rather than a nicety:

* It is how the **test cases prove themselves**. A problem whose cases
  the reference solution cannot pass has broken cases, and any accuracy
  measured against them would be meaningless. ``tools/load_problems.py``
  refuses to import such a problem rather than polluting the catalogue.
* It is the raw material for Phase 2's error-analysis evaluation set.
  ``corpus/eval_cases/`` already requires a ``correct_code`` that must
  AC; a problem that ships one can be turned into an eval case
  mechanically instead of being rewritten by hand.

Why ``samples`` is derived rather than declared
-----------------------------------------------
A case marked ``is_sample: true`` is display-only; every other case is
hidden. Declaring the samples a second time as a separate ``samples``
list would mean two copies of the same input/output text, and the two
would drift the moment someone edits one of them. The loader therefore
builds ``Problem.samples`` from the sample-flagged cases, so there is a
single source of truth for each case's text.

``eval_notes`` is free prose for Phase 2 — "what wrong solutions tend to
look like here and why". It is optional, and it is *not* parsed by
anything in this phase.

Bridging to the evaluation taxonomy
-----------------------------------
``eval_notes`` names the failure mode in the author's own words, which is
more precise than the evaluation taxonomy (``greedy_wrong`` and
``dp_order`` are different bugs, and a note that called them both
``wrong_algorithm`` would lose that). Phase 2's grader, however, scores
against :data:`app.eval_cases.ROOT_CAUSE_LABELS` and needs a closed set.
:data:`NOTE_LABEL_ALIASES` is that bridge, and :func:`canonical_labels_for`
resolves a note's labels onto it — so a corpus note can be as specific as
it likes without the grader having to understand every synonym.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import DATA_ROOT
from app.eval_cases import ROOT_CAUSE_LABELS

#: Allowed ``difficulty`` values — mirrors ``Problem.DIFFICULTIES``.
#: Duplicated rather than imported so this module can be used to validate
#: a corpus without importing the ORM layer.
DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

#: Maps the descriptive labels used in ``eval_notes`` onto
#: :data:`app.eval_cases.ROOT_CAUSE_LABELS`.
#:
#: A note says what is actually wrong (``dp_order``, ``greedy_wrong``);
#: the grader needs one of twelve closed labels. Keeping the note
#: specific and translating here is better than flattening the note:
#: the specific wording is what a human reads when auditing a case, and
#: several distinct bugs would collapse into one label otherwise.
#:
#: A label absent from this map is passed through unchanged if it is
#: already canonical — see :func:`canonical_labels_for`.
NOTE_LABEL_ALIASES: dict[str, str] = {
    # --- understanding / modelling ---
    "modeling": "misread_problem",
    "formula": "misread_problem",
    "arithmetic": "misread_problem",
    "digit_extraction": "misread_problem",
    # --- complexity ---
    "complexity": "wrong_complexity",
    "tle": "wrong_complexity",
    "infinite_loop": "wrong_complexity",
    "infinite_recursion": "wrong_complexity",
    # --- algorithm choice ---
    "algorithm_choice": "wrong_algorithm",
    "greedy_wrong": "wrong_algorithm",
    "greedy_choice_wrong": "wrong_algorithm",
    "missing_visited": "wrong_algorithm",
    "condition_too_broad": "wrong_algorithm",
    # --- missing situations ---
    "conditional_logic": "missing_case",
    "duplicate_handling": "missing_case",
    # --- boundaries ---
    "initialization": "uninitialized_state",
    "data_range": "overflow",
    "stack_overflow": "wrong_complexity",
    "recursion_parameter_order": "wrong_traversal",
    "dp_order": "wrong_traversal",
    "loop_direction": "wrong_traversal",
    # --- data handling ---
    "type_mismatch": "wrong_data_type",
    # --- I/O ---
    "output_format": "io_format_error",
    "input_handling": "io_format_error",
    "mixed_io": "io_format_error",
}


def canonical_labels_for(labels: list[str]) -> list[str]:
    """Resolve note-local labels onto the evaluation taxonomy.

    Each label is lowercased, then looked up in
    :data:`NOTE_LABEL_ALIASES`. A label that is already canonical is kept
    as-is. Anything else is kept too rather than dropped — a note that
    invents a label is a signal the taxonomy may need extending, and
    silently discarding it would hide that.

    Order is preserved and duplicates are removed.
    """
    resolved: list[str] = []
    for raw in labels:
        key = raw.strip().lower()
        if not key:
            continue
        # Aliases first, then "is it already canonical?" (case-sensitive,
        # since ROOT_CAUSE_LABELS are lowercase snake_case by convention).
        mapped = NOTE_LABEL_ALIASES.get(key) or (
            raw.strip() if raw.strip() in ROOT_CAUSE_LABELS else key
        )
        if mapped not in resolved:
            resolved.append(mapped)
    return resolved


#: Matches the "错因标签：a / b / c。" line the corpus notes use.
_LABEL_LINE_RE = re.compile(r"错因标签[:：]\s*(.+)")


def extract_note_labels(eval_notes: str) -> list[str]:
    """Pull the ``错因标签`` labels out of a problem's ``eval_notes``.

    The notes are prose, so this reads the one convention they share: a
    line of the form ``错因标签：label_a / label_b``. Returns ``[]`` when
    there is no such line, which is the honest answer — not every note
    names labels, and inventing an empty-but-present result would make
    "no labels" and "labelled with nothing" look the same.
    """
    match = _LABEL_LINE_RE.search(eval_notes or "")
    if match is None:
        return []

    raw = match.group(1).strip().rstrip("。.")
    return [part.strip() for part in raw.split("/") if part.strip()]


@dataclass(frozen=True)
class CatalogueTestCase:
    """One stdin/stdout pair, and whether the user gets to see it.

    ``is_sample`` defaults to ``False`` — hidden unless the file says
    otherwise. The default matters: a case whose flag someone forgot to
    write stays hidden, which leaks nothing, whereas defaulting to True
    would publish every judging case the moment a field was omitted.
    """

    input: str
    expected_output: str
    is_sample: bool = False


@dataclass
class CatalogueProblem:
    """One problem in the catalogue corpus.

    Attributes:
        problem_id: Stable identifier, also the filename stem. Used for
            logging and error messages; the database id is assigned by
            the insert, not by this file.
        title: Short human-readable title.
        knowledge_tags: Knowledge-point labels, for filtering and for the
            later per-tag accuracy statistics.
        difficulty: One of :data:`DIFFICULTIES`.
        description: Markdown statement body.
        test_cases: Every judging case. At least two must be samples and
            at least three must be hidden — enforced by
            :meth:`validate_shape`.
        correct_reference: A known-good C++ solution.
        input_format: Optional "输入格式" section.
        output_format: Optional "输出格式" section.
        time_limit: Per-case wall-clock limit in seconds.
        memory_limit: Per-case memory ceiling in megabytes.
        source: Where the problem came from.
        eval_notes: Free prose describing common wrong solutions, for
            Phase 2. Optional.
    """

    problem_id: str
    title: str
    knowledge_tags: list[str]
    difficulty: str
    description: str
    test_cases: list[CatalogueTestCase]
    correct_reference: str
    input_format: str | None = None
    output_format: str | None = None
    time_limit: float = 10.0
    memory_limit: int = 256
    source: str | None = None
    eval_notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def samples(self) -> list[dict[str, str]]:
        """The display cases, in the shape ``Problem.samples`` expects.

        Derived from the sample-flagged cases so both views of a case
        come from one place — see the module docstring.
        """
        return [
            {"input": tc.input.rstrip("\n"), "output": tc.expected_output.rstrip("\n")}
            for tc in self.test_cases
            if tc.is_sample
        ]

    @property
    def hidden_cases(self) -> list[CatalogueTestCase]:
        """The judging-only cases."""
        return [tc for tc in self.test_cases if not tc.is_sample]

    def validate_shape(self) -> list[str]:
        """Return structural problems; empty means the file is usable.

        Only checks what can be judged without running anything —
        whether the cases are *correct* is settled by running the
        reference solution, which the CLI does. Keeping the two apart
        means a shape error is reported instantly instead of after a
        compile, and it is reported for every file at once rather than
        one per run.

        The count requirements are:
        at least two samples (so the user can see what the format looks
        like) and at least three hidden cases (so a solution that only
        satisfies the samples is still caught).
        """
        problems: list[str] = []

        if not self.title.strip():
            problems.append("title is empty")
        if not self.description.strip():
            problems.append("description is empty")
        if not self.correct_reference.strip():
            problems.append(
                "correct_reference is empty — without it the test cases "
                "cannot be verified"
            )
        if self.difficulty not in DIFFICULTIES:
            problems.append(
                f"difficulty must be one of {', '.join(DIFFICULTIES)}; "
                f"got {self.difficulty!r}"
            )
        if not self.knowledge_tags:
            problems.append("knowledge_tags is empty")
        if not self.test_cases:
            problems.append("no test cases")

        sample_count = sum(1 for tc in self.test_cases if tc.is_sample)
        hidden_count = len(self.test_cases) - sample_count
        if sample_count < 2:
            problems.append(
                f"needs at least 2 sample cases; got {sample_count}"
            )
        if hidden_count < 3:
            problems.append(
                f"needs at least 3 hidden cases; got {hidden_count}"
            )

        for i, tc in enumerate(self.test_cases, start=1):
            if not tc.input.strip() and not tc.expected_output.strip():
                problems.append(f"case {i} is empty on both sides")

        return problems


#: Default location of the catalogue.
#:
#: Anchored on ``app.config.DATA_ROOT`` rather than ``__file__`` — the latter
#: points inside the bundle when frozen, so the packaged sidecar looked for
#: ``corpus/`` next to its own ``_internal/`` and found nothing. See
#: ``app.config._project_root`` (found 2026-09-22).
DEFAULT_PROBLEMS_DIR = DATA_ROOT / "corpus" / "problems"

#: Fields the loader understands. Anything else is preserved in
#: ``extra`` so a future field is not silently dropped.
_KNOWN_FIELDS = frozenset(
    {
        "id",
        "title",
        "knowledge_tags",
        "difficulty",
        "description",
        "input_format",
        "output_format",
        "time_limit",
        "memory_limit",
        "source",
        "test_cases",
        "correct_reference",
        "eval_notes",
    }
)


def _problem_from_mapping(data: dict[str, Any], *, source: Path) -> CatalogueProblem:
    """Build a :class:`CatalogueProblem` from a parsed YAML mapping."""
    raw_cases = data.get("test_cases") or []
    if not isinstance(raw_cases, list):
        raise ValueError(f"{source}: test_cases must be a list")

    cases = [
        CatalogueTestCase(
            input=str(tc.get("input", "")),
            expected_output=str(tc.get("expected_output", "")),
            # Fail-closed: an unmarked case is hidden.
            is_sample=bool(tc.get("is_sample", False)),
        )
        for tc in raw_cases
    ]

    time_limit = data.get("time_limit", 10.0)
    memory_limit = data.get("memory_limit", 256)

    return CatalogueProblem(
        problem_id=str(data.get("id") or source.stem),
        title=str(data.get("title", "")),
        knowledge_tags=list(data.get("knowledge_tags") or []),
        difficulty=str(data.get("difficulty", "medium")),
        description=str(data.get("description", "")).strip(),
        test_cases=cases,
        correct_reference=str(data.get("correct_reference", "")),
        input_format=data.get("input_format"),
        output_format=data.get("output_format"),
        time_limit=float(time_limit),
        memory_limit=int(memory_limit),
        source=data.get("source"),
        eval_notes=str(data.get("eval_notes", "")).strip(),
        extra={k: v for k, v in data.items() if k not in _KNOWN_FIELDS},
    )


def load_catalogue(directory: str | Path | None = None) -> list[CatalogueProblem]:
    """Load every ``*.yaml`` / ``*.yml`` problem in *directory*, sorted by id.

    Raises:
        FileNotFoundError: if the directory does not exist.
        ValueError: if a file is malformed. The filename is attached to
            every message, because with twenty files in a directory the
            bare error is nearly useless.
    """
    import yaml

    root = Path(directory) if directory is not None else DEFAULT_PROBLEMS_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"problem corpus directory not found: {root}")

    problems: list[CatalogueProblem] = []
    seen_ids: set[str] = set()

    for path in sorted(root.glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: top level must be a mapping")

        try:
            problem = _problem_from_mapping(data, source=path)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc

        if problem.problem_id in seen_ids:
            raise ValueError(
                f"{path.name}: duplicate problem id {problem.problem_id!r}"
            )
        seen_ids.add(problem.problem_id)
        problems.append(problem)

    return problems


def difficulty_distribution(
    problems: list[CatalogueProblem],
) -> dict[str, int]:
    """Count problems per difficulty — used to check the spread."""
    dist: dict[str, int] = {}
    for problem in problems:
        dist[problem.difficulty] = dist.get(problem.difficulty, 0) + 1
    return dict(sorted(dist.items()))


def tag_distribution(problems: list[CatalogueProblem]) -> dict[str, int]:
    """Count problems per knowledge tag, most common first."""
    dist: dict[str, int] = {}
    for problem in problems:
        for tag in problem.knowledge_tags:
            dist[tag] = dist.get(tag, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))
