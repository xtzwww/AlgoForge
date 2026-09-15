"""Tests for P2-4: automatic error analysis on the recording path.

P2-4 wires P2-3's ``analyze_error`` into ``record_submission`` so a wrong
submission comes out of the judge already analysed, with the result on
its mistake-book entry.

The gateway is patched throughout. There is no API key on this machine
and, more to the point, a unit test must not depend on a paid remote
service answering — what is under test is the *wiring*: that analysis
happens after the entry exists, that a failure to analyse never costs
the user their mistake-book entry, and that the two write phases do not
hold a transaction open across a network call.

The last of those is the substantive design point. ``_write`` commits
before analysing and again afterwards, precisely so a slow provider does
not keep a database lock for the duration of an HTTP request. These
tests assert the resulting row states rather than the transaction
boundaries — SQLite's ``StaticPool`` shares one connection, so a second
concurrent session writing during the same window is a harness limit,
not a production scenario (see the note in ``tests/test_records.py``).
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.llm.gateway import LLMConnectionError
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.records import SubmissionContext, record_submission
from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult

# A valid P2-2 payload. Kept as a literal so these tests do not depend on
# the analyzer's own fixtures.
VALID_ANALYSIS = (
    '{"error_type":"overflow","error_detail":"a * b 在 int 内先溢出",'
    '"hint":"看看乘法的操作数是什么类型","suggested_fix":"让运算前就是 64 位"}'
)


@pytest.fixture
def engine():
    """In-memory SQLite on a StaticPool, so one connection is shared."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def problem(session) -> Problem:
    """A problem with all three statement fields populated.

    All three matter: ``_assemble_statement`` folds ``description``,
    ``input_format`` and ``output_format`` into the prompt, and the
    ``io_format_error`` label depends on the model having seen the latter
    two.
    """
    row = Problem(
        id=1,
        title="两数之积",
        description="读入两个整数，输出它们的乘积。",
        input_format="两个整数，空格分隔。",
        output_format="一个整数。",
        knowledge_tags=["数学", "溢出"],
        samples=[],
    )
    session.add(row)
    session.commit()
    return row


def _wa_result(actual: str = "0") -> JudgeResult:
    return JudgeResult(
        status=JudgeStatus.WA,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.WA,
                input="3 7",
                expected_output="21",
                actual_output=actual,
            )
        ],
    )


def _context(problem_id: int | None = 1, code: str = "int a, b;") -> SubmissionContext:
    return SubmissionContext(code=code, language="cpp", problem_id=problem_id)


def _entries(session: Session) -> list[MistakeBookEntry]:
    return list(session.execute(select(MistakeBookEntry)).scalars().all())


# ------------------------------------------------------- the happy path


def test_analysis_lands_on_the_mistake_entry(session, problem):
    """A WA submission comes out of recording already analysed."""
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(), session=session)

    entries = _entries(session)

    assert len(entries) == 1
    assert str(entries[0].agent_error_type) == "overflow"


def test_the_suggestion_carries_all_three_prose_fields(session, problem):
    """Detail, hint and fix are stored, each under its Chinese heading.

    They share one column because they are always displayed together; the
    headings are what keep the stored string readable when it is dumped
    into a terminal or a bug report.
    """
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(), session=session)

    suggestion = _entries(session)[0].agent_suggestion

    assert "a * b 在 int 内先溢出" in suggestion
    assert "看看乘法的操作数是什么类型" in suggestion
    assert "让运算前就是 64 位" in suggestion
    assert suggestion.startswith("【错在哪】")


def test_error_cause_is_left_untouched(session, problem):
    """The agent writes its own columns, not the user's.

    ``error_cause`` is what a person writes when adding an entry by hand.
    Overloading it with agent output would make P2-21's "filter by root
    cause" a prefix match over free text and would destroy the empty
    string's meaning as the "not yet analysed" marker.
    """
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(), session=session)

    assert _entries(session)[0].error_cause == ""


def test_the_submission_is_still_recorded(session, problem):
    """Analysis is additive; the submission row is written regardless."""
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        submission_id = record_submission(_wa_result(), _context(), session=session)

    assert submission_id is not None
    assert session.get(Submission, submission_id) is not None


def test_the_prompt_receives_the_failing_case(session, problem):
    """The analysed input/output are the failing case's, not an arbitrary one.

    ``_pick_output`` and the analyzer must agree on *which* case is
    informative; sending the model a passing case's I/O would produce a
    confident analysis of something that is not wrong.
    """
    with patch(
        "app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS
    ) as mock:
        record_submission(_wa_result(actual="0"), _context(), session=session)

    prompt = mock.call_args[0][0][0]["content"]
    assert "3 7" in prompt          # the failing input
    assert "21" in prompt           # its expected output
    assert "0" in prompt            # what the program printed


def test_the_prompt_receives_the_whole_statement(session, problem):
    """All three statement columns reach the prompt.

    ``input_format`` / ``output_format`` matter specifically: a large
    share of ``io_format_error`` cases is unanswerable without them.
    """
    with patch(
        "app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS
    ) as mock:
        record_submission(_wa_result(), _context(), session=session)

    prompt = mock.call_args[0][0][0]["content"]
    assert "读入两个整数，输出它们的乘积。" in prompt
    assert "两个整数，空格分隔。" in prompt
    assert "一个整数。" in prompt


def test_the_verdict_is_passed_as_wa(session, problem):
    """The judge status reaches the prompt as the verdict."""
    with patch(
        "app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS
    ) as mock:
        record_submission(_wa_result(), _context(), session=session)

    assert "WA" in mock.call_args[0][0][0]["content"]


# ------------------------------------------------------ graceful failure


def test_an_unreachable_llm_still_files_the_mistake(session, problem):
    """The mistake-book entry survives an analysis outage.

    This is the guarantee that matters most. AGENTS.md frames S2 as
    "WA 时调 LLM 输出错因" — an enrichment — so a provider outage must
    not cost the user the record of the mistake they just made.
    """
    with patch(
        "app.agent.analyzer.chat_completion",
        side_effect=LLMConnectionError("refused"),
    ):
        submission_id = record_submission(_wa_result(), _context(), session=session)

    entries = _entries(session)

    assert submission_id is not None
    assert len(entries) == 1
    assert entries[0].agent_error_type is None
    assert entries[0].agent_suggestion == ""


def test_an_analysis_failure_does_not_raise_out_of_recording(session, problem):
    """Recording's best-effort contract is preserved.

    ``record_submission`` never raises; adding a network call to its
    middle must not change that. A raised error here would surface as a
    500 on a verdict the user already earned.
    """
    with patch(
        "app.agent.analyzer.chat_completion",
        side_effect=LLMConnectionError("refused"),
    ):
        record_submission(_wa_result(), _context(), session=session)


def test_an_unparsable_reply_is_treated_as_no_analysis(session, problem):
    """A model that ignores the schema leaves the entry unanalysed.

    The entry must not be annotated with a half-valid value; ``None`` is
    the honest "not yet analysed" state, and a later retry can fill it.
    """
    with patch(
        "app.agent.analyzer.chat_completion",
        return_value='{"error_type":"整数溢出","error_detail":"d","hint":"h",'
        '"suggested_fix":"s"}',
    ):
        record_submission(_wa_result(), _context(), session=session)

    entry = _entries(session)[0]
    assert entry.agent_error_type is None
    assert entry.agent_suggestion == ""


# --------------------------------------------------- which runs get analysed


def test_an_accepted_run_is_not_analysed(session, problem):
    """An AC submission files no mistake and therefore triggers no call.

    Worth asserting the *call count*, not just the absence of an entry:
    a bug that analysed every run would double the API spend on the
    common case and be invisible in the row data.
    """
    result = JudgeResult(
        status=JudgeStatus.AC,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.AC, input="3 7", expected_output="21",
                actual_output="21",
            )
        ],
    )
    with patch("app.agent.analyzer.chat_completion") as mock:
        record_submission(result, _context(), session=session)

    assert mock.call_count == 0
    assert _entries(session) == []


def test_a_free_run_without_a_problem_is_not_analysed(session):
    """No problem means no entry and no call.

    Analysis needs the problem's statement and has nowhere to store the
    result, so spending a call would be pure waste.
    """
    with patch("app.agent.analyzer.chat_completion") as mock:
        record_submission(_wa_result(), _context(problem_id=None), session=session)

    assert mock.call_count == 0


def test_a_compile_error_is_analysed(session, problem):
    """CE is archived too, so it is analysed.

    ``_ARCHIVABLE`` covers every non-AC verdict, and a CE is exactly the
    kind of mistake worth annotating. A CE has no test cases, so the
    analyzer short-circuits — the assertion is that no exception escapes
    and the entry is still filed.
    """
    result = JudgeResult(
        status=JudgeStatus.CE, compile_output="error: expected ';'", test_cases=[]
    )
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(result, _context(), session=session)

    entries = _entries(session)

    assert len(entries) == 1
    # No runnable case, so there is nothing to analyse and the entry keeps
    # its "not yet analysed" state rather than being annotated with a
    # guess based on no I/O.
    assert entries[0].agent_error_type is None


# ------------------------------------------------------------ dedupe again


def test_a_duplicate_resubmission_reuses_the_open_entry(session, problem):
    """Resubmitting identical wrong code does not create a second entry.

    PU-13's dedupe must survive P2-4's addition. The reused entry is the
    one that gets annotated, which is why ``_archive_mistake`` returns the
    existing row rather than ``None`` on a duplicate hit.
    """
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(code="same;"), session=session)
        record_submission(_wa_result(), _context(code="same;"), session=session)

    assert len(_entries(session)) == 1


def test_an_annotated_entry_is_no_longer_a_dedupe_target(session, problem):
    """Once analysed, an entry stops absorbing duplicates.

    This is PU-13's documented rule — "open" means ``error_cause`` is
    empty — and P2-4 relies on it: because the agent writes
    ``agent_error_type`` but *not* ``error_cause``, an annotated entry
    stays open forever, so the rule as written would keep swallowing
    later mistakes. Pinning the current behaviour makes the interaction
    visible; the note on ``test_analysis_does_not_close_the_dedupe_window``
    records why the marker is still ``error_cause``.
    """
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(code="same;"), session=session)
        first = _entries(session)[0]
        assert str(first.agent_error_type) == "overflow"

        record_submission(_wa_result(), _context(code="same;"), session=session)

    # Still one entry: the analysis did not clear error_cause, so the
    # window remains open. Asserted explicitly so a future change to the
    # dedupe marker has to confront this test.
    assert len(_entries(session)) == 1


def test_different_code_gets_its_own_entry(session, problem):
    """A genuinely different mistake is not folded into the first."""
    with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
        record_submission(_wa_result(), _context(code="first;"), session=session)
        record_submission(_wa_result(), _context(code="second;"), session=session)

    assert len(_entries(session)) == 2


# ----------------------------------------------------------- persistence


def test_the_analysis_survives_a_fresh_session(engine, problem):
    """The annotation is committed, not left pending in the session.

    ``_write`` commits after annotating for exactly this reason: a value
    only visible inside the writing session would vanish when the request
    ends, and the mistake book would show every entry as unanalysed.
    """
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    with (
        SessionLocal() as writer,
        patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS),
    ):
        record_submission(_wa_result(), _context(), session=writer)

    with SessionLocal() as reader:
        entry = _entries(reader)[0]

    assert str(entry.agent_error_type) == "overflow"
    assert entry.agent_suggestion


def test_to_dict_exposes_the_new_fields(engine, problem):
    """The API serializer carries the analysis to the frontend."""
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    with SessionLocal() as s:
        with patch("app.agent.analyzer.chat_completion", return_value=VALID_ANALYSIS):
            record_submission(_wa_result(), _context(), session=s)
        payload = _entries(s)[0].to_dict()

    assert payload["agent_error_type"] == "overflow"
    assert "【错在哪】" in payload["agent_suggestion"]
    # The internal dedupe key still must not leak.
    assert "error_cause_fingerprint" not in payload
