"""Schema + loader for the S2 error-analysis evaluation set (P2-5).

The evaluation set lives in ``corpus/eval_cases/`` as one YAML file per
case. This module owns:

* the :class:`EvalCase` dataclass (the contract every case file fills),
* the canonical root-cause taxonomy the Agent must classify into,
* :func:`load_eval_cases` for reading the directory.

Why a fixed taxonomy
--------------------
P2-5 requires "20 道真题准确率 ≥ 60%". Accuracy is meaningless without
a closed label set — free-text error causes can't be scored. Every case
therefore carries an ``expected_root_cause`` drawn from
:data:`ROOT_CAUSE_LABELS`, and scoring is exact-match on that label.

The taxonomy is deliberately small (12 labels). A smaller set is easier
for the Agent to hit reliably at S2 (single LLM call, no RAG yet) and
still covers the overwhelming majority of competitive-programming WA
bugs. Labels can be split later once accuracy justifies it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Canonical root-cause labels. These are the values the Agent must emit
# and the values eval cases are scored against.
#
# Grouped by the phase of the solution they affect, which is also a
# useful hint ordering for prompts.
#
# Every label names a *cause*, not an observable. `no_output_on_edge`
# used to sit in the I/O group and was removed on 2026-09-16: "prints
# nothing" describes a symptom, and on its only eval case the cause was
# an unhandled boundary input. Keeping a symptom label competing with a
# cause label made that case ambiguous by construction. `inverted_condition`
# took the slot — the case whose notes called it "比较方向写反" had no
# honest home, since `off_by_one` covers index arithmetic, not operators.
ROOT_CAUSE_LABELS: tuple[str, ...] = (
    # --- Understanding / modelling ---
    "misread_problem",      # solved a different problem than asked
    "wrong_complexity",     # correct logic, but too slow / too much memory
    # --- Algorithm choice ---
    "wrong_algorithm",      # chose an approach that cannot solve the problem
    "missing_case",         # a required branch was never written
    # --- Boundaries ---
    "off_by_one",           # loop bounds / array indices wrong at the edge
    "boundary_condition",   # edge *values* break otherwise-correct logic
    "inverted_condition",   # comparison / condition written the wrong way round
    "overflow",             # result exceeds the integer type used
    # --- Data handling ---
    "wrong_data_type",      # float where int needed, signed vs unsigned, etc.
    "uninitialized_state",  # accumulator / DP table / visited array not reset
    "wrong_traversal",      # wrong order: BFS vs DFS, reversed, skipping
    # --- I/O ---
    "io_format_error",      # correct content, wrong spelling of it
)

# Human-readable Chinese descriptions, useful for prompts and reports.
#
# These are rendered verbatim into the prompt (see
# ``prompts/loader.py::render_taxonomy``), so each one has to be
# *single-line* — ``test_taxonomy_has_one_line_per_label`` counts lines —
# and each one has to be discriminating, because this is the only place
# the model learns where the labels' boundaries are.
#
# The wording was tightened on 2026-09-16 after measuring 35% on the eval
# set. Several descriptions were narrower than the label they described
# (``wrong_data_type`` omitted signed/unsigned even though the code
# comment above has it; ``io_format_error`` never mentioned case, which is
# the whole point of one of its two cases). Others did not say where the
# boundary with a neighbouring label lies, so the model had to guess:
# `wrong_traversal` and `wrong_algorithm` both claimed BFS-vs-DFS, and
# `boundary_condition` / `missing_case` overlapped on "the input was 0".
ROOT_CAUSE_DESCRIPTIONS: dict[str, str] = {
    "misread_problem": "理解错题意，求解了与题目要求不同的问题",
    "wrong_complexity": "思路正确但复杂度过高，导致超时或超内存",
    "wrong_algorithm": "算法选错，换一种遍历或搜索方式也救不了（如该用 DP 却用了贪心）",
    "missing_case": "漏写了必须处理的一个分支（如 if/else 少一支、漏了重复元素）",
    "off_by_one": "循环边界或下标偏移差一，中间尺度的输入也会错",
    "boundary_condition": "分支齐全，但空/单个/0/极值等边界取值下逻辑不成立",
    "inverted_condition": "比较方向或条件写反（如求最小值却返回较大者、< 写成 <=）",
    "overflow": "结果超出所用整数类型的表示范围（中间表达式里溢出也算）",
    "wrong_data_type": "类型选错：该用浮点却用整数、该用有符号却用无符号",
    "uninitialized_state": "累加器/DP 表/访问标记未初始化或未重置",
    # Wrapped in parentheses for the line-length limit only: the value is
    # still one line at runtime, which ``render_taxonomy`` and
    # ``test_taxonomy_has_one_line_per_label`` both require.
    "wrong_traversal": (
        "遍历或搜索的顺序/方向错误，换一种遍历方式就能对（如该用 BFS 却用了 DFS）"
    ),
    "io_format_error": (
        "内容算对了，只是输出形式不符（分隔符、大小写、多余或缺失的空格换行）"
    ),
}


@dataclass(frozen=True)
class TestCase:
    """A single stdin/stdout pair for the judging harness."""

    input: str
    expected_output: str


@dataclass(frozen=True)
class EvalCase:
    """One labelled WA sample for the S2 accuracy evaluation.

    Attributes:
        case_id: Stable identifier, also the filename stem.
        title: Short human-readable problem name.
        statement: The problem statement the solver was given.
        knowledge_tags: Knowledge points this problem exercises. Used by
            the profile/statistics layer and, later, by RAG.
        wrong_code: The C++ submission that fails.
        expected_root_cause: The gold label from ROOT_CAUSE_LABELS.
        observable_verdict: The judge verdict the harness should observe
            (almost always "WA"; a few cases are TLE on purpose).
        test_cases: Inputs that expose the bug. At least one must fail.
        correct_code: A known-good solution, used to sanity-check that
            the test cases are themselves valid (the reference must AC).
        notes: Why this is the root cause — the human reasoning behind
            the label. This is what makes the label auditable.
        difficulty: "easy" | "medium" | "hard", for slicing accuracy
            by difficulty when the model struggles.
    """

    case_id: str
    title: str
    statement: str
    knowledge_tags: list[str]
    wrong_code: str
    expected_root_cause: str
    test_cases: list[TestCase]
    correct_code: str = ""
    observable_verdict: str = "WA"
    notes: str = ""
    difficulty: str = "medium"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expected_root_cause not in ROOT_CAUSE_LABELS:
            raise ValueError(
                f"{self.case_id}: expected_root_cause "
                f"{self.expected_root_cause!r} is not in ROOT_CAUSE_LABELS"
            )
        if not self.test_cases:
            raise ValueError(f"{self.case_id}: needs at least one test case")
        if self.observable_verdict not in ("WA", "TLE", "MLE", "RE"):
            raise ValueError(
                f"{self.case_id}: observable_verdict must be a failure "
                f"verdict, got {self.observable_verdict!r}"
            )
        if self.difficulty not in ("easy", "medium", "hard"):
            raise ValueError(
                f"{self.case_id}: difficulty must be easy/medium/hard, "
                f"got {self.difficulty!r}"
            )


# Default location of the eval set, relative to the repository root.
DEFAULT_EVAL_DIR = Path(__file__).resolve().parents[2] / "corpus" / "eval_cases"


def _case_from_mapping(data: dict[str, Any], *, source: Path) -> EvalCase:
    """Build an :class:`EvalCase` from a parsed YAML mapping."""
    raw_cases = data.get("test_cases") or []
    if not isinstance(raw_cases, list):
        raise ValueError(f"{source}: test_cases must be a list")

    cases = [
        TestCase(
            input=str(tc.get("input", "")),
            expected_output=str(tc.get("expected_output", "")),
        )
        for tc in raw_cases
    ]

    known = {
        "id", "title", "statement", "knowledge_tags", "wrong_code",
        "correct_code", "expected_root_cause", "observable_verdict",
        "test_cases", "notes", "difficulty",
    }

    return EvalCase(
        case_id=str(data.get("id") or source.stem),
        title=str(data.get("title", "")),
        statement=str(data.get("statement", "")).strip(),
        knowledge_tags=list(data.get("knowledge_tags") or []),
        wrong_code=str(data.get("wrong_code", "")),
        correct_code=str(data.get("correct_code", "")),
        expected_root_cause=str(data.get("expected_root_cause", "")),
        observable_verdict=str(data.get("observable_verdict", "WA")),
        test_cases=cases,
        notes=str(data.get("notes", "")).strip(),
        difficulty=str(data.get("difficulty", "medium")),
        # Preserve anything unrecognised so future fields don't get lost.
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_eval_cases(directory: str | Path | None = None) -> list[EvalCase]:
    """Load every ``*.yaml`` / ``*.yml`` case in *directory*, sorted by id.

    Raises:
        FileNotFoundError: if the directory does not exist.
        ValueError: if a case file is malformed (surfaced from
            :meth:`EvalCase.__post_init__` with the filename attached).
    """
    import yaml

    root = Path(directory) if directory is not None else DEFAULT_EVAL_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"eval case directory not found: {root}")

    cases: list[EvalCase] = []
    for path in sorted(root.glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: top level must be a mapping")
        try:
            cases.append(_case_from_mapping(data, source=path))
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc

    return cases


def root_cause_distribution(cases: list[EvalCase]) -> dict[str, int]:
    """Count cases per expected root cause — used to check label balance.

    A set where one label dominates makes the accuracy number
    misleadingly easy to move, so this is worth asserting on in tests.
    """
    dist: dict[str, int] = {}
    for case in cases:
        dist[case.expected_root_cause] = dist.get(case.expected_root_cause, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))
