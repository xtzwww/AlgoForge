"""Tests for the layered feedback release: ``split_suggestion`` and the
three structured fields the mistake-book API serves.

The feature these tests protect has no visible failure mode when it
breaks quietly: if the split stops matching the stored format, every
section collapses into ``error_detail`` and the student gets the old
"here is the answer" screen back, with no error raised anywhere. So the
round-trip between :func:`app.records._format_suggestion` and
:func:`app.records.split_suggestion` is asserted directly, not inferred
from either side alone.

Two layers are covered:

- **Unit** — the parser itself, including the lenient paths (unlabelled
  free text, a subset of sections, a label appearing inside prose).
- **API** — that ``_entry_to_out`` actually calls it, and that the
  Chinese error-cause description reaches the client. The API test exists
  because the parser can be perfect while the route forgets to use it,
  which is the failure this project has already shipped once (P2-24's
  analysis fields were written to the row for a phase without ever being
  served).
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import mistake_book as mb_module
from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS
from app.main import app
from app.models.base import Base
from app.models.mistake_book import (
    ANALYSIS_DONE,
    MistakeBookEntry,  # noqa: F401 — registers the table on Base.metadata
)
from app.models.problem import Problem
from app.models.submission import Submission
from app.records import _SUGGESTION_LABELS, _format_suggestion, split_suggestion

# --- The parser --------------------------------------------------------


def test_all_three_sections_round_trip() -> None:
    """The documented format splits back into exactly its three parts."""
    stored = (
        "【错在哪】你在 n=0 时提前返回了\n"
        "【提示】想一想循环的终止条件是谁决定的\n"
        "【怎么改】把 return 0 移到循环之外"
    )
    assert split_suggestion(stored) == {
        "error_detail": "你在 n=0 时提前返回了",
        "hint": "想一想循环的终止条件是谁决定的",
        "suggested_fix": "把 return 0 移到循环之外",
    }


def test_missing_sections_come_back_empty_not_absent() -> None:
    """Every key is always present — the client indexes without guarding.

    ``sectionsOf`` in the view filters on ``!== ''``; a missing key would
    be ``undefined`` and render literally nothing, which is a different
    (and worse) outcome than an empty string.
    """
    result = split_suggestion("【错在哪】数组开小了")
    assert result == {"error_detail": "数组开小了", "hint": "", "suggested_fix": ""}
    assert set(result) == {"error_detail", "hint", "suggested_fix"}


def test_empty_input_returns_all_empty() -> None:
    """An un-analysed entry has an empty column and must not crash."""
    assert split_suggestion("") == {
        "error_detail": "",
        "hint": "",
        "suggested_fix": "",
    }


def test_unlabelled_free_text_falls_back_to_error_detail() -> None:
    """Rows written before the labels existed still render.

    The fallback direction matters: putting the text under
    ``error_detail`` means the student sees all of it (the old
    behaviour) rather than seeing an empty card. Losing feedback is worse
    than revealing it too fast.
    """
    legacy = "你的代码在处理边界时有问题，建议检查循环范围。"
    assert split_suggestion(legacy) == {
        "error_detail": legacy,
        "hint": "",
        "suggested_fix": "",
    }


def test_a_label_inside_prose_does_not_start_a_new_section() -> None:
    """Only the first occurrence of each label is a boundary.

    A model that writes a section's heading inside an earlier section's
    prose would otherwise split that paragraph in half and hand the tail
    to the later section — the tail here being a fragment of the
    diagnosis served as the fix, which is both wrong and early.

    The tooltip case (a heading quoted at the *end* of a later section)
    is the one this cannot solve: the quoted heading is a real
    occurrence, so it becomes a boundary and the quote itself is parsed
    away. That is accepted rather than fixed — see the "cost of being
    wrong" note in ``split_suggestion``'s docstring. It cannot be
    resolved by looking harder, because a quotation of a heading is
    lexically identical to the heading.
    """
    stored = (
        "【错在哪】你漏了空输入，详见【提示】里的说明\n"
        "【提示】注意读题的范围描述\n"
        "【怎么改】加一个 if (n == 0) return;"
    )
    result = split_suggestion(stored)
    # The head of 错在哪 is intact and the sections are in the right
    # relation to each other (提示 before 怎么改, so the fix is still
    # gated). What is wrong is that 错在哪 lost its tail — the quoted
    # heading stole it — and 提示 now opens with that orphaned fragment.
    assert result["error_detail"] == "你漏了空输入，详见"
    assert result["hint"].startswith("里的说明")
    assert "注意读题的范围描述" in result["hint"]
    assert result["suggested_fix"] == "加一个 if (n == 0) return;"
    # The important property: the fix did not move earlier in the reveal
    # order than the hint. A student pressing 还是不会 twice still sees
    # 提示's text before 怎么改's.
    assert stored.index("注意读题") < stored.index("加一个")


def test_sections_may_appear_out_of_order() -> None:
    """The parser orders by position in the string, not by the dict.

    ``_SUGGESTION_LABELS`` iterates in declaration order, so a naive
    implementation would slice a later section before an earlier one and
    produce overlapping ranges. The stored format is written in order,
    but a hand-edited row need not be.
    """
    stored = "【提示】先看边界\n【错在哪】下标越界"
    result = split_suggestion(stored)
    assert result["hint"] == "先看边界"
    assert result["error_detail"] == "下标越界"
    assert result["suggested_fix"] == ""


def test_format_suggestion_is_the_inverse_of_the_parser() -> None:
    """Round-trip through the writer, not a hand-written string.

    This is the test that catches a label being reworded in
    ``_SUGGESTION_LABELS`` without the other side following: the
    hand-written strings above would keep passing because they would
    disagree with *both* sides consistently.
    """

    class FakeAnalysis:
        error_detail = "细节"
        hint = "提示"
        suggested_fix = "改法"

    stored = _format_suggestion(FakeAnalysis())
    assert split_suggestion(stored) == {
        "error_detail": "细节",
        "hint": "提示",
        "suggested_fix": "改法",
    }


def test_format_suggestion_labels_are_the_shared_constant() -> None:
    """The writer reads its labels from ``_SUGGESTION_LABELS``.

    Guards against a literal being reintroduced at the call site, which
    is what makes the writer/parser pair desynchronise silently.
    """

    class FakeAnalysis:
        error_detail = "细节"
        hint = ""
        suggested_fix = ""

    stored = _format_suggestion(FakeAnalysis())
    assert stored == f"{_SUGGESTION_LABELS['error_detail']}细节"


def test_format_suggestion_omits_blank_sections() -> None:
    """A section the model skipped leaves no dangling label.

    A bare 【提示】 with nothing after it would be parsed back as a
    ``hint`` of ``""``, which the view then filters out — so the label
    would be invisible, but it would also appear mid-string and could
    swallow the following section's text.
    """

    class FakeAnalysis:
        error_detail = "细节"
        hint = ""
        suggested_fix = "改法"

    stored = _format_suggestion(FakeAnalysis())
    assert "【提示】" not in stored
    assert split_suggestion(stored) == {
        "error_detail": "细节",
        "hint": "",
        "suggested_fix": "改法",
    }


def test_format_suggestion_tolerates_missing_attributes() -> None:
    """The analyser result may predate a field; blank is the safe default."""

    class Bare:
        error_detail = "只有这一段"

    assert split_suggestion(_format_suggestion(Bare()))["error_detail"] == "只有这一段"


# --- The API -----------------------------------------------------------


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Client over an in-memory DB, with one annotated entry seeded.

    Seeded directly through the ORM rather than through the agent: the
    split is a serialization concern and must be testable without a
    model call. ``get_session`` is monkeypatched because the routes call
    it as a module-level name.
    """
    db_session.add(Problem(id=1, title="A+B", knowledge_tags=["基础"]))
    db_session.add(Submission(id=1, problem_id=1, status="WA"))
    db_session.add(
        MistakeBookEntry(
            id=1,
            submission_id=1,
            problem_id=1,
            error_cause="",
            agent_error_type="off_by_one",
            agent_suggestion=(
                "【错在哪】循环上界取成了 n-1\n"
                "【提示】数一数下标的最大值\n"
                "【怎么改】写成 i < n"
            ),
            analysis_status=ANALYSIS_DONE,
        )
    )
    db_session.commit()

    real_get_session = mb_module.get_session

    def fake_get_session() -> Session:
        return db_session

    mb_module.get_session = fake_get_session  # type: ignore[assignment]
    try:
        with TestClient(app) as c:
            yield c
    finally:
        mb_module.get_session = real_get_session  # type: ignore[assignment]


def test_list_serves_the_three_sections_separately(client: TestClient) -> None:
    """The list endpoint the UI actually reads returns split sections."""
    body = client.get("/api/v1/mistake-book").json()
    assert len(body) == 1
    entry = body[0]
    assert entry["error_detail"] == "循环上界取成了 n-1"
    assert entry["hint"] == "数一数下标的最大值"
    assert entry["suggested_fix"] == "写成 i < n"


def test_detail_endpoint_splits_the_same_way(client: TestClient) -> None:
    """GET by id agrees with the list — both go through ``_entry_to_out``."""
    entry = client.get("/api/v1/mistake-book/1").json()
    assert entry["error_detail"] == "循环上界取成了 n-1"
    assert entry["hint"] == "数一数下标的最大值"
    assert entry["suggested_fix"] == "写成 i < n"


def test_raw_suggestion_is_still_served_for_compatibility(client: TestClient) -> None:
    """The joined column stays in the payload.

    It is no longer what the mistake book renders, but dropping a field
    from a response is a breaking change for any other consumer, and the
    stored text is also the only faithful copy of what the model said.
    """
    entry = client.get("/api/v1/mistake-book/1").json()
    assert entry["agent_suggestion"].startswith("【错在哪】")
    assert "【怎么改】写成 i < n" in entry["agent_suggestion"]


def test_error_type_description_is_the_chinese_reading(client: TestClient) -> None:
    """The student-facing cause text comes from the shared taxonomy."""
    entry = client.get("/api/v1/mistake-book/1").json()
    assert entry["error_type_description"] == ROOT_CAUSE_DESCRIPTIONS["off_by_one"]
    assert entry["agent_error_type"] == "off_by_one"


def test_unknown_error_type_describes_as_empty(
    client: TestClient, db_session: Session
) -> None:
    """An unrecognised label yields ``""``, not the raw label.

    The client shows this under 错在哪 as the cause heading; falling back
    to the identifier would print ``off_by_one`` at a student. Empty
    degrades to "no heading", which is the honest rendering.
    """
    db_session.add(
        MistakeBookEntry(
            id=2,
            submission_id=1,
            problem_id=1,
            error_cause="",
            agent_error_type="model_invented_this",
            agent_suggestion="【错在哪】看不太出来",
            analysis_status=ANALYSIS_DONE,
        )
    )
    db_session.commit()
    entry = client.get("/api/v1/mistake-book/2").json()
    assert entry["error_type_description"] == ""
    assert entry["agent_error_type"] == "model_invented_this"


def test_unanalysed_entry_serves_empty_sections_not_missing(
    client: TestClient, db_session: Session
) -> None:
    """A pending entry has ``""`` for all three, so the client can index.

    The view calls ``sectionsOf`` on every entry in the list, including
    ones whose analysis has not landed. A missing key would be
    ``undefined``, and ``undefined !== ''`` is true — so a missing field
    would make the UI believe there is a section to reveal, and render
    an empty one.
    """
    db_session.add(
        MistakeBookEntry(
            id=10, submission_id=1, problem_id=1, error_cause="", agent_suggestion=""
        )
    )
    db_session.commit()
    entry = client.get("/api/v1/mistake-book/10").json()
    assert entry["error_detail"] == ""
    assert entry["hint"] == ""
    assert entry["suggested_fix"] == ""
    assert entry["error_type_description"] == ""
    assert entry["analysis_status"] == "pending"
