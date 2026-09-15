"""Tests for the error-analysis schema (P2-2).

AGENTS.md §二.1 makes this module a red line: "所有 LLM 输出必须过 JSON
Schema 校验…绝不入库". These tests therefore focus on the rejection
paths as much as the accept path — a validator that lets a bad value
through is worth less than nothing, because the mistake book is then
wrong in a way that looks authoritative.

The label-strictness tests carry the most weight. P2-5's accuracy number
is exact-match on ``ROOT_CAUSE_LABELS``, so anything this schema accepts
is something the scorer will match against that same set.
"""

import json

import pytest

from app.agent import schemas
from app.agent.schemas import (
    ErrorAnalysisParseError,
    ErrorAnalysisResult,
    ErrorType,
    parse_error_analysis,
)
from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS, ROOT_CAUSE_LABELS


def _payload(**overrides):
    """A valid payload, with individual fields overridable."""
    base = {
        "error_type": "overflow",
        "error_detail": "第 4 行的 a * b 在 int 内先溢出，再赋值给 long long 已太晚。",
        "hint": "看看乘法发生在赋值之前还是之后。",
        "suggested_fix": "让参与乘法的操作数在运算前就是 64 位。",
    }
    base.update(overrides)
    return base


def _json(**overrides):
    return json.dumps(_payload(**overrides), ensure_ascii=False)


# ------------------------------------------------- the label enum


def test_enum_covers_exactly_the_scorable_labels():
    """The enum and the scoring key are the same set.

    This is the drift guard for the whole module: P2-5 compares
    ``error_type`` against ``ROOT_CAUSE_LABELS``, so an enum member that
    is not a label would accept an unscorable answer, and a label
    missing from the enum would make that label unanswerable.
    """
    assert {member.value for member in ErrorType} == set(ROOT_CAUSE_LABELS)


def test_enum_members_str_to_their_value():
    """``str(member)`` is the label, not "ErrorType.<label>".

    Regression guard for a real trap. Built with ``type=str``, the
    functional enum API makes ``str(ErrorType.overflow)`` return
    ``"ErrorType.overflow"`` while ``json.dumps`` and ``==`` return
    ``"overflow"``. That split silently corrupts the value as soon as it
    is interpolated into a message or written to a text column, which is
    exactly what ``label_description`` and the mistake-book writer do.
    ``StrEnum`` is what keeps every route agreeing.
    """
    for label in ROOT_CAUSE_LABELS:
        member = ErrorType(label)

        assert str(member) == label
        assert f"{member}" == label
        assert member == label
        assert member.value == label


def test_member_serialises_as_a_plain_string():
    """The enum survives a JSON round trip through an API response."""
    result = parse_error_analysis(_json())

    assert json.loads(result.model_dump_json())["error_type"] == "overflow"


# --------------------------------------------------------- accept path


def test_valid_payload_parses():
    """A well-formed response becomes a validated result."""
    result = parse_error_analysis(_json())

    assert result.error_type == ErrorType.overflow
    assert result.error_detail
    assert result.hint
    assert result.suggested_fix


def test_label_description_is_attached():
    """The Chinese description is available for the UI without a lookup."""
    result = parse_error_analysis(_json(error_type="off_by_one"))

    assert result.label_description == ROOT_CAUSE_DESCRIPTIONS["off_by_one"]


@pytest.mark.parametrize("label", ROOT_CAUSE_LABELS)
def test_every_label_is_accepted(label):
    """No label in the taxonomy is unreachable through the parser.

    Worth parametrising over the whole set: a hand-written enum could
    easily omit a label, and the omission would only show up as an
    unexplained accuracy ceiling at P2-5.
    """
    assert parse_error_analysis(_json(error_type=label)).error_type == label


# -------------------------------------------------- label normalisation


@pytest.mark.parametrize(
    "raw",
    ["overflow", " Overflow ", "`overflow`", '"overflow"', "'overflow'", "OVERFLOW"],
)
def test_cosmetic_label_variants_are_normalised(raw):
    """Presentation slips are folded in; they are not different answers.

    A model asked to emit an exact token still wraps it in backticks or
    capitalises it. Rejecting those would burn a P2-3 retry — and, at
    P2-5, a scored case — on a difference that does not change the
    answer.
    """
    assert parse_error_analysis(_json(error_type=raw)).error_type == "overflow"


@pytest.mark.parametrize(
    "raw",
    [
        "整数溢出",          # translation
        "integer_overflow",  # synonym, not a label
        "overflo",           # typo
        "boundary",          # prefix of a different label
        "offbyone",          # no underscores
    ],
)
def test_synonyms_and_typos_are_rejected(raw):
    """A genuinely different answer is not normalised into a match.

    Folding these in would make the P2-5 accuracy number meaningless:
    the score has to reflect whether the model identified the right
    cause, not whether a fuzzy matcher could be persuaded it did.
    """
    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis(_json(error_type=raw))


def test_label_whitespace_is_trimmed_before_matching():
    """Surrounding whitespace never survives into the stored value.

    ``str_strip_whitespace=True`` on the model does the trimming. Without
    it, ``"overflow "`` would pass validation as a distinct value and
    then fail to match the filter in ``GET /mistakes?error_type=overflow``.
    """
    result = parse_error_analysis(_json(error_type="   overflow   "))

    assert str(result.error_type) == "overflow"


# ------------------------------------------------------- reject paths


def test_missing_field_is_rejected():
    """Every field is required — there is no meaningful partial result."""
    payload = _payload()
    del payload["hint"]

    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis(json.dumps(payload, ensure_ascii=False))


def test_blank_field_is_rejected():
    """A whitespace-only field is not an answer.

    ``min_length=1`` alone would accept ``" "``; these values are shown
    straight to the student, so a blank string is worse than a failure
    because it looks like a result.
    """
    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis(_json(error_detail="   "))


def test_hint_may_not_equal_the_fix():
    """The pedagogical contract is enforced for the blatant violation.

    The point of the feature is that the student finds the bug; a model
    that returns the fix under the "hint" label hands over the answer
    and defeats it. Paraphrased leaks need semantic comparison and are
    out of scope, but exact duplication is free to catch.
    """
    same = "把 int 改成 long long。"

    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis(_json(hint=same, suggested_fix=same))


def test_extra_field_is_rejected():
    """An invented field means the prompt has drifted; do not silently drop it.

    ``extra="forbid"`` makes that visible and gives P2-3's retry an
    actionable message naming the stray key.
    """
    payload = _payload()
    payload["confidence"] = 0.9

    with pytest.raises(ErrorAnalysisParseError):
        parse_error_analysis(json.dumps(payload, ensure_ascii=False))


def test_non_object_json_is_rejected():
    """A JSON array or scalar is not an analysis."""
    with pytest.raises(ErrorAnalysisParseError, match="must be a JSON object"):
        parse_error_analysis('["overflow"]')


def test_invalid_json_is_rejected():
    """Prose instead of JSON is reported as a JSON error, not a schema one.

    The distinction matters for P2-3: "you added text around the JSON"
    needs a different corrective retry than "your label was wrong".
    """
    with pytest.raises(ErrorAnalysisParseError, match="not valid JSON"):
        parse_error_analysis("The root cause is overflow, obviously.")


def test_empty_output_is_rejected():
    """An empty completion is reported as such, not as malformed JSON."""
    with pytest.raises(ErrorAnalysisParseError, match="no content"):
        parse_error_analysis("   ")


# ------------------------------------------------------- parse details


def test_fenced_json_is_unwrapped():
    """A markdown fence around the payload is undone.

    The prompt asks for bare JSON but models wrap it anyway; this is the
    most common deviation and the only one that is unambiguous to undo.
    """
    fenced = f"```json\n{_json()}\n```"

    assert parse_error_analysis(fenced).error_type == "overflow"


def test_bare_fence_is_unwrapped():
    """A fence with no language tag works too."""
    assert parse_error_analysis(f"```\n{_json()}\n```").error_type == "overflow"


def test_parse_error_carries_the_raw_output():
    """The raw text is preserved so a retry can show the model its own output."""
    with pytest.raises(ErrorAnalysisParseError) as excinfo:
        parse_error_analysis("not json at all")

    assert excinfo.value.raw == "not json at all"


# ------------------------------------------------------------ schema


def test_json_schema_documents_the_label_enum():
    """The exported schema constrains the label to the taxonomy.

    P2-7 may reuse this shape for function calling, where the enum is
    what stops the model inventing a label in the first place.
    """
    schema = schemas.error_analysis_json_schema()

    assert schema["properties"]["error_type"]
    assert schema.get("required") == [
        "error_type",
        "error_detail",
        "hint",
        "suggested_fix",
    ]


def test_model_forbids_extra_fields_by_construction():
    """The policy is on the model, not only exercised via the parser."""
    assert ErrorAnalysisResult.model_config.get("extra") == "forbid"
