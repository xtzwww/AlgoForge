"""Tests for submission recording and automatic mistake archiving (PU-13).

PU-13 closes gap 7: the ``submissions`` table had no writer, so the
mistake book's normal route was unreachable. These tests cover the write
path itself; the endpoint-level flow lives in ``test_submissions_api.py``.

Two things are asserted here that a main-thread-only test would miss:

- **Recording from a worker thread.** Both real call sites judge inside
  ``asyncio.to_thread``, so the session is created and used off the event
  loop's thread. A test that records on the main thread would never
  exercise that boundary, which is exactly where a "session used from a
  foreign thread" bug would live.
- **Idempotence of archiving.** A WA submission files its own mistake
  entry, so a user who hits submit twice must not get two identical
  entries.

Sessions are injected rather than monkeypatched: ``record_submission``
takes a ``session`` argument so the tests can supply in-memory SQLite.
``StaticPool`` keeps one connection so the two threads below see the same
database.
"""

from __future__ import annotations

import threading
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.records import SubmissionContext, record_submission
from app.sandbox.judge import (
    JudgeResult,
    JudgeStatus,
    TestCaseResult,
)


@pytest.fixture
def engine():
    """In-memory SQLite engine on a StaticPool (shared connection).

    One connection, so every session handed out below sees the same
    database — which is what makes a threaded write visible to the main
    thread's read.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """In-memory SQLite session on a StaticPool (shared connection)."""
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def session_factory(engine):
    """Builds a *new* session per call, for tests that need one per thread.

    A SQLAlchemy ``Session`` is not thread-safe, and sharing one across
    threads fails with "Session is already flushing" — the exact
    limitation the concurrent tests below must not trip over, since
    production gives every recording its own session.
    """
    return sessionmaker(engine, expire_on_commit=False)


def _seed_problem(
    session: Session, *, problem_id: int = 1, tags: list[str] | None = None
) -> Problem:
    problem = Problem(
        id=problem_id,
        title="A + B",
        knowledge_tags=tags if tags is not None else ["基础", "模拟"],
        description="",
        samples=[],
    )
    session.add(problem)
    session.commit()
    return problem


def _result(
    status: JudgeStatus,
    *,
    cases: list[TestCaseResult] | None = None,
    compile_output: str = "",
) -> JudgeResult:
    return JudgeResult(
        status=status, compile_output=compile_output, test_cases=cases or []
    )


def _case(
    status: JudgeStatus,
    *,
    actual: str = "3",
    expected: str = "3",
    stderr: str = "",
    exec_time: float | None = 0.02,
    memory_used: int | None = 12,
) -> TestCaseResult:
    return TestCaseResult(
        status=status,
        input="1 2",
        expected_output=expected,
        actual_output=actual,
        stderr=stderr,
        exit_code=0,
        exec_time=exec_time,
        memory_used=memory_used,
    )


# --- Storing the submission -------------------------------------------


def test_ac_submission_is_stored(session: Session) -> None:
    """An accepted run lands with its verdict and context."""
    _seed_problem(session)
    ctx = SubmissionContext(code="int main(){}", language="cpp", problem_id=1)

    sub_id = record_submission(
        _result(JudgeStatus.AC, cases=[_case(JudgeStatus.AC)]),
        ctx,
        session=session,
    )

    assert sub_id is not None
    sub = session.get(Submission, sub_id)
    assert sub is not None
    assert sub.status == "AC"
    assert sub.problem_id == 1
    assert sub.code == "int main(){}"
    assert sub.language == "cpp"


def test_free_run_is_stored_without_a_problem(session: Session) -> None:
    """A run with no problem selected is still recorded.

    Keeps the history complete: the user's time was spent, and a missing
    row would be unexplainable later.
    """
    ctx = SubmissionContext(code="print(1)", language="python", problem_id=None)

    sub_id = record_submission(
        _result(JudgeStatus.AC, cases=[_case(JudgeStatus.AC)]), ctx, session=session
    )

    sub = session.get(Submission, sub_id)
    assert sub is not None
    assert sub.problem_id is None


def test_stored_output_is_the_first_failing_case(session: Session) -> None:
    """The failing case is what gets stored, not the last one.

    It is where the program went wrong and therefore what the user needs
    to see to fix the code.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="x", language="cpp", problem_id=1)
    result = _result(
        JudgeStatus.WA,
        cases=[
            _case(JudgeStatus.AC, actual="3"),
            _case(JudgeStatus.WA, actual="WRONG", stderr="trace"),
            _case(JudgeStatus.WA, actual="ALSO-WRONG"),
        ],
    )

    sub = session.get(
        Submission,
        record_submission(result, ctx, session=session),
    )
    assert sub is not None
    assert sub.stdout == "WRONG"
    assert sub.stderr == "trace"


def test_stored_output_is_the_last_case_when_all_pass(session: Session) -> None:
    """With nothing failing, the last case's output is stored.

    Leaves the field populated rather than empty for no reason.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="x", language="cpp", problem_id=1)
    result = _result(
        JudgeStatus.AC,
        cases=[
            _case(JudgeStatus.AC, actual="first"),
            _case(JudgeStatus.AC, actual="last"),
        ],
    )

    sub = session.get(Submission, record_submission(result, ctx, session=session))
    assert sub is not None
    assert sub.stdout == "last"


def test_ce_stores_the_compiler_output_as_stderr(session: Session) -> None:
    """A compile failure puts diagnostics where a user looks for them.

    CE produces no cases at all, so there is no ``actual_output`` to fall
    back on — the compiler message has to be attached explicitly.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="broken", language="cpp", problem_id=1)

    sub = session.get(
        Submission,
        record_submission(
            _result(JudgeStatus.CE, compile_output="error: expected ';'"),
            ctx,
            session=session,
        ),
    )
    assert sub is not None
    assert sub.status == "CE"
    assert sub.stderr == "error: expected ';'"
    assert sub.stdout == ""


def test_measurements_are_the_worst_across_cases(session: Session) -> None:
    """Time is the max and memory the max, not a sum.

    A limit applies per case, so a sum is not a quantity any limit
    applies to and would grow with the case count for no reason.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="x", language="cpp", problem_id=1)
    result = _result(
        JudgeStatus.AC,
        cases=[
            _case(JudgeStatus.AC, exec_time=0.5, memory_used=30),
            _case(JudgeStatus.AC, exec_time=1.25, memory_used=12),
            _case(JudgeStatus.AC, exec_time=0.1, memory_used=64),
        ],
    )

    sub = session.get(Submission, record_submission(result, ctx, session=session))
    assert sub is not None
    assert sub.exec_time == pytest.approx(1.25)
    assert sub.memory_used == 64


def test_unmeasured_run_stores_null_not_zero(session: Session) -> None:
    """A CE run reports no measurement rather than zero.

    "0.0 seconds, 0 MB" would state that something ran instantly using no
    memory. Nothing ran.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="broken", language="cpp", problem_id=1)

    sub = session.get(
        Submission,
        record_submission(
            _result(JudgeStatus.CE, compile_output="boom"), ctx, session=session
        ),
    )
    assert sub is not None
    assert sub.exec_time is None
    assert sub.memory_used is None


def test_partially_measured_run_stores_what_it_has(session: Session) -> None:
    """A missing figure on one case does not blank the other.

    ``memory_used`` is None when no sample was taken; a case that did
    produce a time still contributes it.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="x", language="cpp", problem_id=1)
    result = _result(
        JudgeStatus.AC,
        cases=[
            _case(JudgeStatus.AC, exec_time=0.3, memory_used=None),
            _case(JudgeStatus.AC, exec_time=0.1, memory_used=8),
        ],
    )

    sub = session.get(Submission, record_submission(result, ctx, session=session))
    assert sub is not None
    assert sub.exec_time == pytest.approx(0.3)
    assert sub.memory_used == 8


# --- Automatic archiving ----------------------------------------------


def test_wa_with_a_problem_files_a_mistake_entry(session: Session) -> None:
    """README's promise that wrong answers are archived now holds.

    The entry's ``error_cause`` is empty on purpose: an empty value is
    what marks it as not yet analysed by the S2 agent.
    """
    _seed_problem(session, tags=["基础", "模拟"])
    ctx = SubmissionContext(code="wrong()", language="cpp", problem_id=1)

    sub_id = record_submission(
        _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)]), ctx, session=session
    )

    entries = list(session.execute(select(MistakeBookEntry)).scalars())
    assert len(entries) == 1
    entry = entries[0]
    assert entry.submission_id == sub_id
    assert entry.problem_id == 1
    assert entry.error_cause == ""
    assert entry.knowledge_tags == ["基础", "模拟"]


def test_ac_does_not_file_a_mistake_entry(session: Session) -> None:
    """A correct submission is not a mistake."""
    _seed_problem(session)
    ctx = SubmissionContext(code="right()", language="cpp", problem_id=1)

    record_submission(
        _result(JudgeStatus.AC, cases=[_case(JudgeStatus.AC)]), ctx, session=session
    )

    assert session.execute(select(MistakeBookEntry)).first() is None


@pytest.mark.parametrize("status", [JudgeStatus.TLE, JudgeStatus.MLE, JudgeStatus.RE])
def test_non_wa_failures_are_also_archived(
    status: JudgeStatus, session: Session
) -> None:
    """TLE / MLE / RE are mistakes worth revisiting too.

    Limiting archiving to WA would silently drop the cases the agent is
    most useful for — a timeout and a wrong answer need different advice.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="slow()", language="cpp", problem_id=1)

    record_submission(
        _result(status, cases=[_case(status)]), ctx, session=session
    )

    assert session.execute(select(MistakeBookEntry)).first() is not None


def test_ce_is_archived_too(session: Session) -> None:
    """A compile error is a mistake; the entry stores no case output.

    Archived deliberately: a beginner's most common first problem is a
    syntax error, and "file only WA" would hide the pattern.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="broken", language="cpp", problem_id=1)

    record_submission(
        _result(JudgeStatus.CE, compile_output="error"), ctx, session=session
    )

    assert session.execute(select(MistakeBookEntry)).first() is not None


def test_free_run_is_not_archived(session: Session) -> None:
    """A free run has no problem, so there is nothing to file it under.

    ``MistakeBookEntry.problem_id`` is NOT NULL, and a mistake against no
    particular problem is not something the review loop can schedule.
    """
    ctx = SubmissionContext(code="x", language="cpp", problem_id=None)

    sub_id = record_submission(
        _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)]), ctx, session=session
    )

    assert sub_id is not None, "the run itself is still stored"
    assert session.execute(select(MistakeBookEntry)).first() is None


# --- Idempotence -------------------------------------------------------


def test_resubmitting_the_same_wrong_code_does_not_duplicate(
    session: Session,
) -> None:
    """**The idempotence guarantee.** Submitting twice yields one entry.

    Two entries would be noise the user has to clean by hand, and they
    carry no additional information — the code is identical.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="same()", language="cpp", problem_id=1)
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])

    first = record_submission(result, ctx, session=session)
    second = record_submission(result, ctx, session=session)

    assert first != second, "both submissions are recorded"
    assert session.execute(select(Submission)).all().__len__() == 2
    entries = list(session.execute(select(MistakeBookEntry)).scalars())
    assert len(entries) == 1, "the second wrong answer was deduped"


def test_different_code_gets_its_own_entry(session: Session) -> None:
    """A genuinely different wrong answer is a different mistake."""
    _seed_problem(session)
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])

    record_submission(
        result, SubmissionContext(code="attempt_one()", language="cpp", problem_id=1),
        session=session,
    )
    record_submission(
        result, SubmissionContext(code="attempt_two()", language="cpp", problem_id=1),
        session=session,
    )

    assert len(list(session.execute(select(MistakeBookEntry)).scalars())) == 2


def test_same_code_for_a_different_problem_gets_its_own_entry(
    session: Session,
) -> None:
    """The dedupe key includes the problem.

    The same text wrong on two problems is two mistakes — likely for two
    different reasons.
    """
    _seed_problem(session, problem_id=1, tags=["A"])
    _seed_problem(session, problem_id=2, tags=["B"])
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])
    ctx_args = {"code": "same()", "language": "cpp"}

    record_submission(
        result, SubmissionContext(problem_id=1, **ctx_args), session=session
    )
    record_submission(
        result, SubmissionContext(problem_id=2, **ctx_args), session=session
    )

    assert len(list(session.execute(select(MistakeBookEntry)).scalars())) == 2


def test_same_code_in_a_different_language_gets_its_own_entry(
    session: Session,
) -> None:
    """Language is part of the key: the same text means different things."""
    _seed_problem(session)
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])

    record_submission(
        result, SubmissionContext(code="x", language="cpp", problem_id=1),
        session=session,
    )
    record_submission(
        result, SubmissionContext(code="x", language="python", problem_id=1),
        session=session,
    )

    assert len(list(session.execute(select(MistakeBookEntry)).scalars())) == 2


def test_annotated_entry_does_not_block_a_new_one(session: Session) -> None:
    """Once the agent has analysed an entry, a repeat gets its own row.

    The two entries then differ in what they say — one carries an
    analysis, the new one does not yet — so collapsing them would lose
    information rather than noise.
    """
    _seed_problem(session)
    ctx = SubmissionContext(code="same()", language="cpp", problem_id=1)
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])

    record_submission(result, ctx, session=session)
    # Stand in for the S2 agent filling in the analysis.
    entry = session.execute(select(MistakeBookEntry)).scalars().one()
    entry.error_cause = "未处理边界情况"
    session.commit()

    record_submission(result, ctx, session=session)

    entries = list(session.execute(select(MistakeBookEntry)).scalars())
    assert len(entries) == 2
    causes = sorted(e.error_cause for e in entries)
    assert causes == ["", "未处理边界情况"]


def test_manually_created_entry_is_never_treated_as_a_duplicate(
    session: Session,
) -> None:
    """A hand-added entry has no fingerprint, so archiving ignores it.

    Otherwise a user who wrote their own note for a problem would find
    that a subsequent wrong submission did not get filed at all.
    """
    _seed_problem(session)
    # A user-created entry: no fingerprint, references a submission that
    # this test also makes.
    first_sub = Submission(problem_id=1, status="WA", code="other()")
    session.add(first_sub)
    session.flush()
    session.add(
        MistakeBookEntry(
            submission_id=first_sub.id,
            problem_id=1,
            error_cause="我自己记的",
            knowledge_tags=["基础"],
        )
    )
    session.commit()

    record_submission(
        _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)]),
        SubmissionContext(code="same()", language="cpp", problem_id=1),
        session=session,
    )

    entries = list(session.execute(select(MistakeBookEntry)).scalars())
    assert len(entries) == 2, "the auto-archived entry was not suppressed"


def test_tags_are_snapshotted_at_archiving_time(session: Session) -> None:
    """Re-tagging the problem later does not rewrite the entry.

    The snapshot is what makes the knowledge statistics of the past
    stable.
    """
    problem = _seed_problem(session, tags=["旧标签"])

    record_submission(
        _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)]),
        SubmissionContext(code="same()", language="cpp", problem_id=1),
        session=session,
    )

    problem.knowledge_tags = ["新标签"]
    session.commit()

    entry = session.execute(select(MistakeBookEntry)).scalars().one()
    assert entry.knowledge_tags == ["旧标签"]


# --- Thread boundary ---------------------------------------------------


def test_recording_from_a_worker_thread(session: Session) -> None:
    """**PU-13's required integration test.** Recording works off-thread.

    Both real call sites (``app/api/judge.py`` and ``app/api/ws.py``) run
    the judge inside ``asyncio.to_thread``, so the session is created and
    used from a thread that is not the one the app started on. A test that
    records synchronously would never touch that boundary — and a session
    that leaks thread affinity would pass it while failing in production.

    The judge itself is stubbed out; what is under test is that the write
    path survives the thread hop.
    """
    _seed_problem(session)
    result = _result(JudgeStatus.WA, cases=[_case(JudgeStatus.WA)])
    ctx = SubmissionContext(code="threaded()", language="cpp", problem_id=1)

    captured: dict[str, object] = {}

    def worker() -> None:
        try:
            captured["id"] = record_submission(result, ctx, session=session)
        except BaseException as exc:
            # Parked rather than raised: an exception inside a thread does
            # not reach the test, so it would show up only as a missing
            # id and a confusing assertion below.
            captured["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=30)

    assert not thread.is_alive(), "recording deadlocked in the worker thread"
    assert "error" not in captured, repr(captured.get("error"))
    sub_id = captured["id"]
    assert isinstance(sub_id, int)

    sub = session.get(Submission, sub_id)
    assert sub is not None
    assert sub.code == "threaded()"
    assert session.execute(select(MistakeBookEntry)).first() is not None


def test_recordings_from_separate_sessions_do_not_interfere(
    session: Session, session_factory
) -> None:
    """Each recording commits on its own session without clobbering the last.

    This is the property the write path must have when two submissions
    are judged back to back and their recordings overlap as the event
    loop schedules them: every recording owns its session, commits
    independently, and none of them loses a row.

    Why not actually concurrent threads here? The in-memory SQLite
    harness runs on a ``StaticPool``, i.e. **one** physical connection.
    Two sessions writing through a single connection cannot both succeed
    — SQLite and SQLAlchemy legitimately raise ("Session is already
    flushing", "did not produce a new primary key result") because they
    are two writers on one connection, which is not the production
    topology. Production gives each request its own pooled connection.
    So this test drives the same *sequence* of independent sessions that
    overlapping requests produce, without asserting something the harness
    cannot represent.
    """
    _seed_problem(session)
    result = _result(JudgeStatus.AC, cases=[_case(JudgeStatus.AC)])

    ids: list[object] = []
    for n in range(4):
        # Every recording opens and closes its own session, exactly as
        # ``record_submission`` does in production when no session is
        # injected.
        with session_factory() as own_session:
            ids.append(
                record_submission(
                    result,
                    SubmissionContext(
                        code=f"code_{n}", language="cpp", problem_id=1
                    ),
                    session=own_session,
                )
            )

    assert len(set(ids)) == 4, f"expected 4 distinct ids, got {ids}"
    assert len(list(session.execute(select(Submission)).scalars())) == 4
