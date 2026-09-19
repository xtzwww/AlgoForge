"""Tests for the analysis status and the retry endpoint (P2-24).

Two halves of the same gap. ``analysis_status`` is what an entry carries
after each possible analysis outcome, and the retry endpoint is what lets
a user ask for another attempt when the outcome was "nothing".

The failure path needs no stubbing: ``conftest``'s autouse fixture makes
every real LLM call raise ``LLMConfigError``, which is exactly the
provider outage this feature exists for. Tests that need a *successful*
analysis patch the tool loop's own call, same as
``test_agent_analysis_recording`` does.

Retrying re-judges the submission, so those tests stub both the judge and
the resolver — otherwise a "retry" test would compile real C++ and take
seconds to prove something about a status column.
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import mistake_book as mb_module
from app.llm.gateway import Completion
from app.main import app
from app.models.base import Base
from app.models.mistake_book import (
    ANALYSIS_DONE,
    ANALYSIS_FAILED,
    ANALYSIS_PENDING,
    MistakeBookEntry,
)
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.records import (
    SubmissionContext,
    reanalyse_entry,
    record_submission,
    will_analyse,
)
from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult
from app.sandbox.judge_input import JudgeInputError, ResolvedJudgeInput
from conftest import TEST_SIDECAR_HEADERS

VALID_ANALYSIS = (
    '{"error_type":"overflow","error_detail":"a * b 在 int 内先溢出",'
    '"hint":"看看乘法的操作数是什么类型","suggested_fix":"让运算前就是 64 位"}'
)


def _stub_analysis(*replies):
    """Patch the gateway call inside P2-7's tool loop.

    The recording path hands the analysis to the loop, so that is the
    seam. Each reply is a JSON string or an exception to raise.
    """
    items = [
        reply
        if isinstance(reply, Exception)
        else Completion(content=reply, finish_reason="stop")
        for reply in replies
    ]
    return patch("app.agent.tool_caller.complete", side_effect=items)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine, monkeypatch) -> Iterator[Session]:
    """The test session, wired in as the provider every module asks for.

    Both module-level ``get_session`` bindings are replaced, not just the
    one the routes import. ``reanalyse_entry`` and ``analyse_submission``
    live in ``app.records`` and call their own reference; a test that
    patched only the routes would reach the real PostgreSQL URL and hang
    on a connection timeout instead of failing fast. Doing it here means
    no test in this file can forget.
    """
    import app.records as records_module

    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    monkeypatch.setattr(mb_module, "get_session", lambda: db)
    monkeypatch.setattr(records_module, "get_session", lambda: db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(session) -> Iterator[TestClient]:
    with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
        yield c


@pytest.fixture
def problem(session) -> Problem:
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
    session.add(
        TestCase(
            id=1,
            problem_id=1,
            ordinal=1,
            input="3 7",
            expected_output="21",
            is_sample=True,
        )
    )
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


def _ce_result() -> JudgeResult:
    """A CE carries no cases — the compiler never got as far as running one."""
    return JudgeResult(
        status=JudgeStatus.CE, compile_output="error: expected ';'", test_cases=[]
    )


def _context(problem_id: int | None = 1) -> SubmissionContext:
    return SubmissionContext(code="int a, b;", language="cpp", problem_id=problem_id)


def _entries(session: Session) -> list[MistakeBookEntry]:
    return list(session.execute(select(MistakeBookEntry)).scalars().all())


def _seed_entry(session: Session, status: str = ANALYSIS_FAILED) -> MistakeBookEntry:
    """A filed mistake with a submission behind it, ready to retry."""
    submission = Submission(
        problem_id=1, status="WA", code="int a, b;", language="cpp", stdout="0"
    )
    session.add(submission)
    session.flush()
    entry = MistakeBookEntry(
        submission_id=submission.id,
        problem_id=1,
        error_cause="",
        knowledge_tags=["数学"],
        analysis_status=status,
    )
    session.add(entry)
    session.commit()
    return entry


# ------------------------------------------------ what the status says


def test_a_successful_analysis_marks_the_entry_done(session, problem):
    with _stub_analysis(VALID_ANALYSIS):
        record_submission(_wa_result(), _context(), session=session)

    entry = _entries(session)[0]

    assert entry.analysis_status == ANALYSIS_DONE
    assert entry.agent_error_type is not None


def test_a_failed_analysis_marks_the_entry_failed(session, problem):
    """The whole point: a provider outage is no longer invisible.

    No stub here, so the autouse fixture's ``LLMConfigError`` stands in
    for the outage. Before this column the entry came out looking exactly
    like one that had never been analysed.
    """
    record_submission(_wa_result(), _context(), session=session)

    entry = _entries(session)[0]

    assert entry.analysis_status == ANALYSIS_FAILED
    assert entry.agent_error_type is None
    # The mistake itself is still filed — an outage costs the analysis,
    # never the record of what went wrong.
    assert entry.error_cause == ""


def test_a_compile_error_is_marked_failed(session, problem):
    """CE has no case to analyse, so it must not sit on pending forever.

    An entry left on ``pending`` reads as work that is still coming. It
    is not: the judge never ran a case, so there is no I/O to build a
    prompt from.
    """
    record_submission(_ce_result(), _context(), session=session)

    entry = _entries(session)[0]

    assert entry.analysis_status == ANALYSIS_FAILED


def test_an_accepted_run_files_nothing(session, problem):
    """AC is not a mistake, so there is no entry and no status to set."""
    result = JudgeResult(
        status=JudgeStatus.AC,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.AC,
                input="3 7",
                expected_output="21",
                actual_output="21",
            )
        ],
    )
    record_submission(result, _context(), session=session)

    assert _entries(session) == []


# --------------------------------------------------- will_analyse agrees


def test_will_analyse_is_true_for_a_wrong_answer():
    assert will_analyse(_wa_result(), _context()) is True


def test_will_analyse_is_false_for_a_compile_error():
    """A CE is filed but never analysed.

    The stream route reads this to decide whether more events are coming.
    Saying yes for a CE would leave a client waiting on an ``agent_done``
    that was never scheduled.
    """
    assert will_analyse(_ce_result(), _context()) is False


def test_will_analyse_is_false_for_a_free_run():
    assert will_analyse(_wa_result(), _context(problem_id=None)) is False


# ------------------------------------------------------- the response


def test_the_entry_response_carries_the_analysis(session, problem, client):
    """The agent's fields reach the client — they did not before.

    P2-4 wrote ``agent_error_type`` / ``agent_suggestion`` to the row and
    the API never served them, so the mistake book looked the same as it
    had before the agent existed.
    """
    with _stub_analysis(VALID_ANALYSIS):
        record_submission(_wa_result(), _context(), session=session)

    body = client.get("/api/v1/mistake-book").json()[0]

    assert body["analysis_status"] == ANALYSIS_DONE
    assert body["agent_error_type"] == "overflow"
    assert "hint" in body["agent_suggestion"] or body["agent_suggestion"]


# --------------------------------------------------------- the retry


def test_retry_answers_before_the_analysis_runs(session, problem, client):
    """202 with the entry already marked pending.

    The status is written by the route, not the background task, so the
    client that polls sees the retry start. TestClient runs background
    tasks before returning, which is why the assertion is on the response
    body rather than on a later read.
    """
    entry = _seed_entry(session)

    with patch("app.sandbox.judge.judge", return_value=_wa_result()), patch(
        "app.sandbox.judge_input.resolve",
        return_value=ResolvedJudgeInput(
            test_cases=[], time_limit=10.0, mem_limit_mb=256, problem_id=1
        ),
    ):
        response = client.post(f"/api/v1/mistake-book/{entry.id}/analyze")

    assert response.status_code == 202
    assert response.json()["analysis_status"] == ANALYSIS_PENDING


def test_retry_re_runs_the_analysis_and_lands_it(session, problem, client):
    """The retry actually annotates the entry when the provider answers."""
    entry = _seed_entry(session)

    with patch("app.sandbox.judge.judge", return_value=_wa_result()), patch(
        "app.sandbox.judge_input.resolve",
        return_value=ResolvedJudgeInput(
            test_cases=[], time_limit=10.0, mem_limit_mb=256, problem_id=1
        ),
    ), _stub_analysis(VALID_ANALYSIS):
        response = client.post(f"/api/v1/mistake-book/{entry.id}/analyze")

    assert response.status_code == 202
    body = client.get(f"/api/v1/mistake-book/{entry.id}").json()
    assert body["analysis_status"] == ANALYSIS_DONE
    assert body["agent_error_type"] == "overflow"


def test_retry_leaves_the_entry_failed_when_the_provider_is_still_down(
    session, problem, client
):
    """A retry that fails again says so, rather than sitting on pending."""
    entry = _seed_entry(session)

    with patch("app.sandbox.judge.judge", return_value=_wa_result()), patch(
        "app.sandbox.judge_input.resolve",
        return_value=ResolvedJudgeInput(
            test_cases=[], time_limit=10.0, mem_limit_mb=256, problem_id=1
        ),
    ):
        client.post(f"/api/v1/mistake-book/{entry.id}/analyze")

    body = client.get(f"/api/v1/mistake-book/{entry.id}").json()
    assert body["analysis_status"] == ANALYSIS_FAILED


def test_retry_refuses_an_entry_that_already_has_an_analysis(session, problem, client):
    """409 rather than spending a second call on an answered question."""
    entry = _seed_entry(session, status=ANALYSIS_DONE)

    response = client.post(f"/api/v1/mistake-book/{entry.id}/analyze")

    assert response.status_code == 409
    assert "already has an analysis" in response.json()["detail"]


def test_retry_refuses_a_free_run(session, problem, client):
    """A submission with no problem has no cases to re-judge against."""
    submission = Submission(problem_id=None, status="WA", code="int a;", language="cpp")
    session.add(submission)
    session.flush()
    entry = MistakeBookEntry(
        submission_id=submission.id,
        problem_id=1,
        error_cause="",
        knowledge_tags=[],
        analysis_status=ANALYSIS_FAILED,
    )
    session.add(entry)
    session.commit()

    response = client.post(f"/api/v1/mistake-book/{entry.id}/analyze")

    assert response.status_code == 400
    assert "free run" in response.json()["detail"]


def test_retry_404s_on_an_unknown_entry(client):
    response = client.post("/api/v1/mistake-book/999/analyze")

    assert response.status_code == 404


# ------------------------------------------- reanalyse_entry on its own


def test_reanalyse_returns_false_for_a_missing_entry(session):
    assert reanalyse_entry(999) is False


def test_reanalyse_returns_false_when_the_problem_no_longer_resolves(session, problem):
    """No cases left to re-judge against, so no retry is possible."""
    entry = _seed_entry(session)

    with patch(
        "app.sandbox.judge_input.resolve",
        side_effect=JudgeInputError("problem 1 has no cases"),
    ):
        assert reanalyse_entry(entry.id) is False

    session.expire_all()
    # Marked failed rather than left on the pending it wrote on the way in.
    assert session.get(MistakeBookEntry, entry.id).analysis_status == ANALYSIS_FAILED
