"""Structured output schema for error analysis (P2-2).

AGENTS.md §二.1 (必须做的事) makes validation a red line: "所有 LLM
输出必须过 JSON Schema 校验：不合规则重试或降级，绝不入库". Everything
the model returns for error analysis is parsed through
:class:`ErrorAnalysisResult` before it can reach the database.

Typos are not just typos
------------------------
``error_type`` is typed against :data:`app.eval_cases.ROOT_CAUSE_LABELS`
rather than as a free string. That is the whole point of this module:
P2-5 scores the agent by exact match on that label set, so a model that
answers ``"overflow "``, ``"Overflow"`` or ``"整数溢出"`` has not
produced a near-miss — it has produced a value that cannot be scored and
that no downstream filter (`GET /mistakes?error_type=overflow`, P2-21)
would ever match. Rejecting it here, while the model's output is still
in hand and a retry is cheap, is far better than writing it to the
mistake book.

The practical consequence is that this schema is *strict on purpose*.
Fields the model is likely to answer in Chinese are documented as
Chinese; only the label itself is constrained to the English enum.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS, ROOT_CAUSE_LABELS

# The closed set of root-cause labels, as an enum.
#
# Built with the functional API rather than a `class` statement because
# enum members cannot be created from a runtime iterable inside a class
# body: `ROOT_CAUSE_LABELS` is a value, not a literal, so the class
# statement cannot see it during class construction. Declaring the
# members literally instead would duplicate the list — and duplication
# is exactly what this module exists to prevent, since P2-5 scores
# against `ROOT_CAUSE_LABELS` and an enum that disagreed with it would
# either reject correct answers or accept unscorable ones.
#
# `StrEnum` (not `type=str`) because the two differ in `str()`:
# with `type=str`, `str(ErrorType.overflow)` returns "ErrorType.overflow"
# while `f"{member}"` and `json.dumps` return "overflow". That split
# would silently corrupt the value the moment it is interpolated into a
# message or a `Text` column, which is precisely what happens in
# `label_description` and in the mistake-book writer. `StrEnum` makes
# `str()` return the value, so every route agrees.
ErrorType = StrEnum(  # type: ignore[misc,assignment]
    "ErrorType",
    {label: label for label in ROOT_CAUSE_LABELS},
    module=__name__,
    qualname="ErrorType",
)


def _require_non_blank(value: str) -> str:
    """Reject the obvious "model gave up" strings.

    ``min_length=1`` alone accepts ``"N/A"``. These values are shown
    directly to the student, so an empty-ish string is worse than an
    outright failure: it looks like an answer.
    """
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class ErrorAnalysisResult(BaseModel):
    """One structured root-cause analysis of a failing submission.

    Attributes:
        error_type: The root cause, from the closed label set.
        error_detail: Why this label — must point at a specific line or
            expression and explain the observed output. Chinese.
        hint: A nudge that helps the student find the bug themselves.
            Chinese. Deliberately not the fix.
        suggested_fix: What to change, described rather than pasted.
            Chinese.
    """

    # ``extra="forbid"`` rather than ignore: a model inventing fields
    # ("confidence", "explanation") is a sign the prompt has drifted, and
    # silently dropping them makes that invisible. It also means the
    # retry in P2-3 gets an actionable error naming the stray field.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    error_type: ErrorType = Field(
        description="Root cause, exactly one value from the label taxonomy."
    )
    error_detail: str = Field(
        min_length=1,
        description=(
            "Chinese. Locates the divergence between expected and actual "
            "output, names the line or expression responsible, and says why "
            "the nearest alternative labels were rejected."
        ),
    )
    hint: str = Field(
        min_length=1,
        description=(
            "Chinese. One or two sentences pointing the student at the next "
            "thing to check, without giving away the fix."
        ),
    )
    suggested_fix: str = Field(
        min_length=1,
        description=(
            "Chinese. What to change, referencing the key expression rather "
            "than pasting corrected code."
        ),
    )

    @field_validator("error_type", mode="before")
    @classmethod
    def _normalise_label(cls, value: Any) -> Any:
        """Tolerate cosmetic differences in the label, nothing more.

        A model asked to emit an exact token will still sometimes wrap it
        in backticks, quote it, or capitalise it. Those are presentation
        slips that do not change the answer, and rejecting them would
        burn a retry — and, at P2-5, a scored case — on nothing.

        What is *not* tolerated: a synonym or a translation. Those are
        genuinely different answers, and folding them in here would make
        the accuracy number meaningless.
        """
        if isinstance(value, str):
            cleaned = value.strip().strip("`").strip().strip('"').strip("'").strip()
            lowered = cleaned.lower()
            if lowered in ROOT_CAUSE_LABELS:
                return lowered
            return cleaned
        return value

    @field_validator("error_detail", "hint", "suggested_fix")
    @classmethod
    def _reject_placeholder_text(cls, value: str) -> str:
        """Reject the obvious "model gave up" strings.

        ``min_length=1`` alone accepts ``"N/A"``. These values are shown
        directly to the student, so an empty-ish string is worse than an
        outright failure: it looks like an answer.
        """
        return _require_non_blank(value)

    @model_validator(mode="after")
    def _hint_must_not_leak_the_fix(self) -> ErrorAnalysisResult:
        """Guard the pedagogical contract, at least for the blatant cases.

        The prompt asks for a hint that does *not* give away the fix
        (that is the point of the feature — the student should find it).
        A model that returns the same text for both has ignored the
        instruction, and the result would actively harm the learning
        loop by handing over the answer under a "hint" label.

        Only exact duplication is rejected. Detecting a paraphrased leak
        needs semantic comparison, which is out of scope here and would
        itself be an LLM call.
        """
        if self.hint.strip() == self.suggested_fix.strip():
            raise ValueError("hint and suggested_fix must differ")
        return self

    @property
    def label_description(self) -> str:
        """The Chinese description of :attr:`error_type`.

        Convenience for the API layer and the mistake-book UI, which
        show the human-readable cause next to the label.
        """
        return ROOT_CAUSE_DESCRIPTIONS.get(str(self.error_type), "")


#: Rungs in a hint ladder. Three is the smallest number that can be
#: *graded* rather than merely ordered: one rung is an answer, two are a
#: direction plus an answer, three are direction → location → check.
HINT_LEVELS = 3


class HintLevel(BaseModel):
    """One rung of the hint ladder.

    Attributes:
        level: 1-based rung, and the schema pins the set to
            ``1..HINT_LEVELS``: a model that labels its rungs 0-based, or
            repeats a number, has produced a ladder whose order is not
            recoverable from the data.
        text: Chinese. What the student should look at *at this rung*.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level: int = Field(ge=1, le=HINT_LEVELS)
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _reject_placeholder_text(cls, value: str) -> str:
        return _require_non_blank(value)


class HintLadderResult(BaseModel):
    """A graded hint for one diagnosed bug (S7's 提示 agent).

    Why a ladder rather than the single ``hint`` already on
    :class:`ErrorAnalysisResult`
        The diagnosis's ``hint`` is written in the same breath as the
        cause and the fix, so it drifts toward whichever of the three the
        model found easiest to phrase. A separate agent is asked for the
        *sequence*: what to look at, then where, then how to check. The
        two are not duplicates — one is a nudge attached to a diagnosis,
        this is a study aid that survives it.

    What this schema can and cannot check
        Ordering, distinctness and non-emptiness are structural and are
        enforced here. *Increasing specificity* is the pedagogical point
        of a ladder and is not: "is rung 2 vaguer than rung 1" is a
        question about meaning, not about shape. The prompt is where that
        instruction lives, and the self-check is what catches a ladder
        that is merely thin.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    levels: list[HintLevel] = Field(
        min_length=HINT_LEVELS,
        max_length=HINT_LEVELS,
        description="Exactly HINT_LEVELS rungs, shallowest first.",
    )
    self_check: list[str] = Field(
        min_length=2,
        max_length=5,
        description=(
            "Chinese. 2-5 yes/no questions the student answers after "
            "fixing the code, to confirm the bug is actually gone."
        ),
    )

    @field_validator("self_check")
    @classmethod
    def _reject_blank_items(cls, value: list[str]) -> list[str]:
        for item in value:
            _require_non_blank(item)
        return value

    @model_validator(mode="after")
    def _rungs_are_ordered_and_distinct(self) -> HintLadderResult:
        """A ladder whose rungs are out of order, or repeated, is not a
        ladder — and neither defect is visible from any single field.
        """
        numbers = [rung.level for rung in self.levels]
        expected = list(range(1, HINT_LEVELS + 1))
        if numbers != expected:
            raise ValueError(f"levels must be {expected} in order, got {numbers}")

        texts = [rung.text for rung in self.levels]
        if len(set(texts)) != len(texts):
            raise ValueError("each rung must say something different")
        return self


class VariantResult(BaseModel):
    """A practice problem targeting the same bug (S7's 变式题 agent).

    The point of a variant is transfer: the student met the trap once in
    the original problem, and meets it again in a setting where the old
    code cannot be copied across. That is why ``difference`` is a required
    field rather than a nicety — a variant that does not say what changed
    is indistinguishable from a reprint of the original, and a reprint
    teaches nothing.

    ``sample_input`` / ``sample_output`` are plain strings rather than
    parsed structures: they are shown to the student verbatim, and the
    judge's own test-case format is a different contract that this module
    has no business coupling to.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: str = Field(
        min_length=1,
        description=(
            "Chinese. Self-contained: background, input format, output "
            "format, limits."
        ),
    )
    sample_input: str = Field(min_length=1)
    sample_output: str = Field(min_length=1)
    knowledge_points: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Chinese phrases naming the concepts the variant drills.",
    )
    difference: str = Field(
        min_length=1,
        description=(
            "Chinese. How the variant differs from the original, and why "
            "the original's bug would recur here."
        ),
    )

    @field_validator("statement", "sample_input", "sample_output", "difference")
    @classmethod
    def _reject_placeholder_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("knowledge_points")
    @classmethod
    def _reject_blank_points(cls, value: list[str]) -> list[str]:
        for point in value:
            _require_non_blank(point)
        return value


def _strip_code_fence(text: str) -> str:
    """Remove a markdown code fence wrapping the whole payload.

    The prompt explicitly asks for bare JSON, but models routinely wrap
    it in ```json ... ``` anyway. This is the single most common
    deviation seen in practice, and it is unambiguous to undo — unlike
    JSON extracted from surrounding prose, which would require guessing
    where the object starts and ends.
    """
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    # Drop the opening fence line (``` or ```json) and a closing fence.
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


class StructuredOutputParseError(ValueError):
    """Raw model output could not be turned into a validated model.

    Carries the raw text so the caller (P2-3's retry) can show the model
    what it actually produced, and so a failure can be diagnosed without
    reproducing the call.

    Shared by every parser in this module. S7 added two more structured
    outputs, and a parser that raised a differently-named error for each
    would force every caller to know which of the three it was holding —
    the worker agents, which only want to record "this reply was unusable"
    and carry on, would have to import all of them.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class ErrorAnalysisParseError(StructuredOutputParseError):
    """The specific failure :func:`parse_error_analysis` raises.

    Kept as its own name because P2-3's retry catches it by name and
    because the eval harness reports it; it is now a subclass so a caller
    that only cares about "the reply was unusable" can catch the base.
    """


def _load_json_object(raw: str) -> dict[str, Any]:
    """Parse raw model output into a JSON object, or explain why not.

    The three failure modes are kept distinct — no content, not JSON, not
    an object — because they call for different retry prompts: "you
    emitted prose around the JSON" reads very differently to "your label
    was not in the list".
    """
    if not isinstance(raw, str) or not raw.strip():
        raise StructuredOutputParseError("model returned no content", raw=raw or "")

    text = _strip_code_fence(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputParseError(
            f"model output is not valid JSON ({exc.msg} at position {exc.pos})",
            raw=raw,
        ) from exc

    if not isinstance(data, dict):
        raise StructuredOutputParseError(
            f"model output must be a JSON object, got {type(data).__name__}",
            raw=raw,
        )

    return data


def _validate_into(raw: str, model: type[Any]) -> Any:
    """Run ``_load_json_object`` and then ``model.model_validate``."""
    data = _load_json_object(raw)
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise StructuredOutputParseError(
            f"model output failed schema validation: {exc}", raw=raw
        ) from exc


def parse_error_analysis(raw: str) -> ErrorAnalysisResult:
    """Parse raw model output into a validated :class:`ErrorAnalysisResult`.

    Args:
        raw: The completion text, possibly fenced, possibly malformed.

    Returns:
        A validated result.

    Raises:
        ErrorAnalysisParseError: ``raw`` is not JSON, is not an object,
            or fails validation. The message distinguishes those cases
            because they call for different retry prompts: "you emitted
            prose around the JSON" reads very differently to "your label
            was not in the list".
    """
    try:
        return _validate_into(raw, ErrorAnalysisResult)
    except StructuredOutputParseError as exc:
        raise ErrorAnalysisParseError(str(exc), raw=exc.raw) from exc


def parse_hint_ladder(raw: str) -> HintLadderResult:
    """Parse raw model output into a validated :class:`HintLadderResult`.

    Raises:
        StructuredOutputParseError: not JSON, not an object, or fails
            validation (wrong number of rungs, out-of-order levels, a
            repeated rung).
    """
    return _validate_into(raw, HintLadderResult)


def parse_variant(raw: str) -> VariantResult:
    """Parse raw model output into a validated :class:`VariantResult`.

    Raises:
        StructuredOutputParseError: not JSON, not an object, or fails
            validation (a missing field, or a blank one).
    """
    return _validate_into(raw, VariantResult)


def error_analysis_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`ErrorAnalysisResult`.

    Used by tests to assert the schema is stable, and available to P2-7
    when the same shape is needed as a tool/response schema.
    """
    return ErrorAnalysisResult.model_json_schema()
