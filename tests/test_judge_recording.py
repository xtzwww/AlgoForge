"""End-to-end tests for PU-13's judge → store → mistake-book chain.

This module is the closure of gap 7 in ``docs/PHASE-UX.md``: before
PU-13, ``submissions`` had no writer while ``MistakeBookEntry`` required
a ``submission_id`` that had to already exist, so the mistake book's
normal route was unreachable. The tests here drive the whole chain —
submit code over the judge route, then read the mistake book back — and
would fail if any link were missing.

The root ``conftest.py`` installs an autouse fixture that stubs out the
write so the suite does not need a live database. Every test in this
module **undoes** that stub with a recording one, because the write is
precisely the behaviour under test. That is why the stubbing lives in a
module-level fixture here rather than being left implicit.

Everything runs against in-memory SQLite through injected sessions, for
the same reason the rest of the suite does: no PostgreSQL on this
machine.

Ordering
--------
These tests read the history immediately after the WebSocket stream
reports ``judge_done``, which is only sound because the WS route sends
that terminal event *after* the write has committed. An earlier revision
let ``judge()``'s own ``judge_done`` through to the client first, so the
read raced the write; the count assertions here are what caught it.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import judge as judge_module
from app.api import mistake_book as mb_module
from app.api import submissions as submissions_module
from app.api import ws as ws_module
from app.main import app
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.records import SubmissionContext, record_submission
from app.sandbox import judge_input as judge_input_module

# A correct A+B program and a wrong one. Kept minimal so the tests are
# about the chain, not about the judge.
_CPP_AC = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""

_CPP_WA = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a - b << std::endl;
    return 0;
}
"""


@pytest.fixture
def engine():
    """In-memory SQLite engine on a StaticPool (one shared connection).

    One shared connection is what makes the in-memory database *shared*
    at all: every session for this test talks to the same DB, which is
    what lets a write from a worker thread be visible to the request
    that reads it back.
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
    """A session on the shared in-memory database."""
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded(session: Session) -> Session:
    """A judgeable problem: two cases, one visible, one hidden."""
    problem = Problem(
        id=1,
        title="A + B",
        knowledge_tags=["基础", "模拟"],
        description="Add two integers.",
        samples=[{"input": "1 2", "output": "3"}],
        time_limit=10.0,
        memory_limit=256,
    )
    session.add(problem)
    session.flush()
    session.add(
        TestCase(
            problem_id=1,
            ordinal=1,
            input="1 2\n",
            expected_output="3\n",
            is_sample=True,
        )
    )
    session.add(
        TestCase(
            problem_id=1,
            ordinal=2,
            input="10 20\n",
            expected_output="30\n",
            is_sample=False,
        )
    )
    session.commit()
    return session


@pytest.fixture
def client(
    engine, seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client whose judge routes read and write through the test engine.

    Two kinds of redirection, both on the module the name is looked up
    in rather than where it was defined:

    * the write hook, so the verdict is stored in the in-memory database;
    * ``get_session`` inside the route modules, so resolution and the
      read endpoints see the same data.

    The write gets a **fresh session per call**, not the fixture's. That
    matters: judging happens on a worker thread, and a session shared
    across threads raises ``IllegalStateChangeError`` as soon as one
    thread commits while another is mid-transaction. Production behaves
    the same way — ``record_submission`` opens its own session — so
    sharing one here would test a topology the app never uses.
    """
    def _record(result: object, context: object, **kwargs: object) -> int | None:
        # ``**kwargs`` carries the route's ``analyse`` / ``on_event``
        # arguments down to the real recorder. Naming only the first two
        # made the route raise ``TypeError`` mid-judge, which surfaced as a
        # ``judge_error`` event and took out every test that goes through
        # this hook — the recording under test never happened.
        #
        # The id is returned rather than discarded: the stream route reads
        # it to decide whether an analysis follows, so a stub that
        # swallowed it would tell the client "nothing more is coming".
        SessionLocal = sessionmaker(engine, expire_on_commit=False)
        with SessionLocal() as write_session:
            return record_submission(
                result,  # type: ignore[arg-type]
                context,  # type: ignore[arg-type]
                session=write_session,
                **kwargs,  # type: ignore[arg-type]
            )

    def _analyse(
        submission_id: int, result: object, context: object, *, on_event=None
    ) -> bool:
        """Stand in for the analysis step.

        The real one opens its own session — against the documented
        PostgreSQL DSN, which is not running here — and calls a paid
        provider. Neither belongs in a test about recording.

        It does emit the terminal agent event, because the stream route
        now waits for one before closing: a stub that stayed silent would
        hang the client rather than fail the test.
        """
        if on_event is not None:
            on_event(
                {"type": "agent_start", "verdict": result.status.value}  # type: ignore[attr-defined]
            )
            on_event(
                {
                    "type": "agent_done",
                    "ok": False,
                    "error": None,
                    "error_type": None,
                    "tools": [],
                }
            )
        return True

    monkeypatch.setattr(judge_module, "record_judge_outcome", _record)
    monkeypatch.setattr(ws_module, "record_judge_outcome", _record)
    monkeypatch.setattr(judge_module, "analyse_submission", _analyse)
    monkeypatch.setattr(ws_module, "analyse_submission", _analyse)

    # The read side, for the judge's problem lookup plus the
    # mistake-book and submission endpoints. Patch each module's own
    # binding: a ``from app.db import get_session`` binds the function
    # into the importing module's namespace, so patching app.db would do
    # nothing.
    def _test_session() -> Session:
        SessionLocal = sessionmaker(engine, expire_on_commit=False)
        return SessionLocal()

    monkeypatch.setattr(judge_input_module, "get_session", _test_session)
    monkeypatch.setattr(mb_module, "get_session", _test_session)
    monkeypatch.setattr(submissions_module, "get_session", _test_session)

    with TestClient(app) as c:
        yield c


def _judge_via_ws(client: TestClient, code: str) -> list[dict]:
    """Run a submission over the WebSocket route, returning all events."""
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": code,
                "language": "cpp",
                "problem_id": 1,
                "time_limit": 15.0,
                "mem_limit_mb": 256,
            }
        )
        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("judge_done", "judge_error"):
                return events


def _judge_via_ws_with(
    client: TestClient, code: str, *, record: object = ..., extra_raw: bool = False
) -> list[dict]:
    """Like :func:`_judge_via_ws`, but sends an explicit ``record`` value.

    The sentinel default is what lets the "omitted" case be tested as a
    distinct thing from ``record: true``: a helper that always set the
    key could not tell the two apart, and the default is half the
    contract.

    ``extra_raw`` exists for the must-be-a-boolean test, which has to put
    a non-boolean on the wire. It is an opt-in flag rather than a widened
    type so every other call keeps type checking on the value it passes.
    """
    payload: dict = {
        "code": code,
        "language": "cpp",
        "problem_id": 1,
        "time_limit": 15.0,
        "mem_limit_mb": 256,
    }
    if extra_raw or record is not ...:
        payload["record"] = record

    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(payload)
        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("judge_done", "judge_error"):
                return events


# --- The chain ---------------------------------------------------------


def test_ac_submission_is_recorded_and_visible(
    client: TestClient, seeded: Session
) -> None:
    """An accepted run through the WS route lands in the history."""
    events = _judge_via_ws(client, _CPP_AC)
    assert events[-1]["type"] == "judge_done", events[-1]
    assert events[-1]["status"] == "AC"

    body = client.get("/api/v1/submissions").json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["status"] == "AC"
    assert row["problem_id"] == 1
    assert row["language"] == "cpp"


def test_hidden_case_is_used_by_the_judge(
    client: TestClient, seeded: Session
) -> None:
    """Both cases run, hidden included.

    A program that is right on the sample but wrong on the hidden case
    must fail — that is the whole point of the hidden-case mechanism, and
    it only holds if the judge reads the table rather than the request.
    """
    # 1 2 -> 3 (right) but 10 20 -> -10 (wrong).
    events = _judge_via_ws(client, _CPP_WA)
    assert events[-1]["type"] == "judge_done", events[-1]
    assert events[-1]["status"] == "WA"
    assert events[-1]["total"] == 2, "the hidden case did not run"


def test_wa_submission_creates_a_mistake_book_entry(
    client: TestClient, seeded: Session
) -> None:
    """**Gap 7 closed.** A wrong answer reaches the mistake book.

    Before PU-13 this could not happen: no route wrote ``submissions``,
    and the mistake-book create endpoint rejected a ``submission_id``
    that did not exist. This asserts the whole path end to end rather
    than each link in isolation.
    """
    _judge_via_ws(client, _CPP_WA)

    entries = client.get("/api/v1/mistake-book").json()
    assert len(entries) == 1, "the mistake book is still unreachable"
    entry = entries[0]
    assert entry["problem_id"] == 1
    assert entry["knowledge_tags"] == ["基础", "模拟"]
    assert entry["error_cause"] == "", "the agent fills this in during S2"
    assert entry["submission_id"] is not None

    # The referenced submission really exists — this is the FK the old
    # flow could never satisfy.
    sub = client.get(f"/api/v1/submissions/{entry['submission_id']}")
    assert sub.status_code == 200
    assert sub.json()["status"] == "WA"


def test_recorded_submission_keeps_the_full_source(
    client: TestClient, seeded: Session
) -> None:
    """The detail endpoint returns the code that was actually run.

    Read back from the stored row, not from the request, so a bug that
    stored the wrong field would show up here.
    """
    _judge_via_ws(client, _CPP_WA)

    sub_id = client.get("/api/v1/submissions").json()["items"][0]["id"]
    detail = client.get(f"/api/v1/submissions/{sub_id}").json()

    assert detail["code"] == _CPP_WA
    assert detail["status"] == "WA"


def test_recorded_submission_keeps_measurements(
    client: TestClient, seeded: Session
) -> None:
    """A judged run reports a time and a memory figure.

    Anything else means the runner's measurements are not reaching the
    row, which is the whole reason PU-13 extended ``ExecutionResult``.
    """
    _judge_via_ws(client, _CPP_AC)

    row = client.get("/api/v1/submissions").json()["items"][0]
    assert row["exec_time"] is not None
    assert row["exec_time"] > 0.0
    assert row["memory_used"] is not None
    assert row["memory_used"] >= 1


def test_resubmitting_the_same_wrong_code_creates_one_entry(
    client: TestClient, seeded: Session
) -> None:
    """Submitting the same wrong code twice yields one mistake entry.

    Two submissions are recorded — the user did run twice — but only one
    mistake, because the second teaches nothing new.
    """
    _judge_via_ws(client, _CPP_WA)
    _judge_via_ws(client, _CPP_WA)

    assert client.get("/api/v1/submissions").json()["total"] == 2
    assert len(client.get("/api/v1/mistake-book").json()) == 1


def test_ac_after_wa_leaves_the_mistake_entry(
    client: TestClient, seeded: Session
) -> None:
    """Fixing the code does not erase the mistake.

    The entry is a record that the mistake happened, which is what the
    review loop schedules against; deleting it on success would quietly
    empty the mistake book as the user improves.
    """
    _judge_via_ws(client, _CPP_WA)
    _judge_via_ws(client, _CPP_AC)

    history = client.get("/api/v1/submissions").json()
    assert history["total"] == 2
    assert {row["status"] for row in history["items"]} == {"AC", "WA"}
    assert len(client.get("/api/v1/mistake-book").json()) == 1


# --- Listing and filtering --------------------------------------------


def test_submissions_are_listed_newest_first(
    client: TestClient, seeded: Session
) -> None:
    """The most recent run is at the top.

    The usual reason to open this list is "what did I just run", which an
    ascending order would put on the last page.
    """
    _judge_via_ws(client, _CPP_WA)
    _judge_via_ws(client, _CPP_AC)

    items = client.get("/api/v1/submissions").json()["items"]
    assert len(items) == 2
    assert items[0]["status"] == "AC", "the newer run is not first"


def test_list_omits_the_source(client: TestClient, seeded: Session) -> None:
    """A list row carries no code and no captured output."""
    _judge_via_ws(client, _CPP_WA)

    body = client.get("/api/v1/submissions").json()
    row = body["items"][0]
    assert "code" not in row
    assert "stdout" not in row
    assert "stderr" not in row
    assert _CPP_WA not in client.get("/api/v1/submissions").text


def test_list_filters_by_status(client: TestClient, seeded: Session) -> None:
    """The status filter narrows the history."""
    _judge_via_ws(client, _CPP_WA)
    _judge_via_ws(client, _CPP_AC)

    body = client.get("/api/v1/submissions", params={"status": "WA"}).json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "WA"


def test_list_rejects_an_unknown_status(
    client: TestClient, seeded: Session
) -> None:
    """An unknown verdict is a 422, not a silently-empty page."""
    resp = client.get("/api/v1/submissions", params={"status": "NOPE"})
    assert resp.status_code == 422
    assert "AC" in resp.json()["detail"]


def test_list_filters_by_language(client: TestClient, seeded: Session) -> None:
    """The language filter narrows the history."""
    _judge_via_ws(client, _CPP_AC)

    assert (
        client.get("/api/v1/submissions", params={"language": "cpp"}).json()[
            "total"
        ]
        == 1
    )
    assert (
        client.get("/api/v1/submissions", params={"language": "java"}).json()[
            "total"
        ]
        == 0
    )


def test_list_paginates(client: TestClient, seeded: Session) -> None:
    """``page`` / ``page_size`` slice the history; ``total`` does not.

    Three rows at a page size of two is deliberately not a multiple of
    the page size: the last page is short, and conflating "short page"
    with "wrong page" is the bug this shape catches.
    """
    for _ in range(3):
        _judge_via_ws(client, _CPP_AC)

    first = client.get(
        "/api/v1/submissions", params={"page": 1, "page_size": 2}
    ).json()
    assert first["total"] == 3, "total counts the whole history, not the page"
    assert len(first["items"]) == 2

    second = client.get(
        "/api/v1/submissions", params={"page": 2, "page_size": 2}
    ).json()
    assert second["total"] == 3
    assert len(second["items"]) == 1, "the last page is short, not full"

    # The pages must partition the history: no row on both, none missing.
    ids = [row["id"] for row in first["items"] + second["items"]]
    assert sorted(ids) == [1, 2, 3]


def test_list_rejects_page_size_above_ceiling(
    client: TestClient, seeded: Session
) -> None:
    """``MAX_PAGE_SIZE`` is enforced."""
    assert (
        client.get("/api/v1/submissions", params={"page_size": 101}).status_code
        == 422
    )


def test_detail_404s_on_unknown_id(client: TestClient, seeded: Session) -> None:
    """Unknown id → 404 naming the id."""
    resp = client.get("/api/v1/submissions/999")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]


def test_empty_history_is_a_valid_page(
    client: TestClient, seeded: Session
) -> None:
    """No submissions yet is a 200 with an empty list."""
    body = client.get("/api/v1/submissions").json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


# --- The REST twin -----------------------------------------------------


def test_rest_judge_also_records(client: TestClient, seeded: Session) -> None:
    """The HTTP route records too, not just the WebSocket.

    Both entry points must behave the same; a history that depends on
    which transport the client used would be a silent trap.
    """
    resp = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_WA,
            "language": "cpp",
            "problem_id": 1,
            "time_limit": 15.0,
            "mem_limit_mb": 256,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WA"

    assert client.get("/api/v1/submissions").json()["total"] == 1
    assert len(client.get("/api/v1/mistake-book").json()) == 1


def test_ce_submission_is_recorded_without_measurements(
    client: TestClient, seeded: Session
) -> None:
    """A compile error is recorded, with no time or memory.

    Nothing ran, so there is nothing to measure; the column stays null
    rather than claiming an instant, memory-free execution.
    """
    resp = client.post(
        "/api/v1/judge",
        json={"code": "int main( {", "language": "cpp", "problem_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CE"

    row = client.get("/api/v1/submissions").json()["items"][0]
    assert row["status"] == "CE"
    assert row["exec_time"] is None
    assert row["memory_used"] is None


def test_free_run_is_recorded_but_not_archived(
    client: TestClient, seeded: Session
) -> None:
    """A run with no problem is stored but files no mistake.

    ``MistakeBookEntry.problem_id`` is NOT NULL, and a mistake against no
    particular problem is nothing the review loop can schedule.
    """
    resp = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_WA,
            "language": "cpp",
            "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WA"

    history = client.get("/api/v1/submissions").json()
    assert history["total"] == 1
    assert history["items"][0]["problem_id"] is None
    assert client.get("/api/v1/mistake-book").json() == []


def test_recording_failure_does_not_break_the_verdict(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A history write that blows up still returns the verdict.

    The user waited for the answer; losing the row is a much smaller harm
    than losing it. ``record_submission`` swallows exceptions for this
    reason, and this pins that it does.

    The patch goes on ``app.records.record_submission`` — the *inner*
    function — not on the route's ``record_judge_outcome`` hook. The
    hook **is** the guard, so replacing it with a raising function would
    remove the protection being tested and prove nothing.
    """
    import app.records as records_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database is on fire")

    # A raising inner recorder stands in for a database failure on the
    # write path: it is exactly what record_submission_quietly must
    # absorb.
    monkeypatch.setattr(records_module, "record_submission", _boom)

    resp = client.post(
        "/api/v1/judge",
        json={"code": _CPP_AC, "language": "cpp", "problem_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "AC"


def test_stored_rows_match_the_database(session: Session, seeded: Session) -> None:
    """Sanity check on the rows the tests above produced.

    Reads through the ORM rather than the API, so a serialization bug in
    the endpoints cannot mask a storage bug.
    """
    from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult

    result = JudgeResult(
        status=JudgeStatus.WA,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.WA,
                input="10 20\n",
                expected_output="30\n",
                actual_output="-10\n",
                exec_time=0.03,
                memory_used=9,
            )
        ],
    )
    record_submission(
        result,
        SubmissionContext(code="x", language="cpp", problem_id=1),
        session=session,
    )

    sub = session.execute(select(Submission)).scalars().one()
    assert sub.stdout == "-10\n"
    assert sub.exec_time == pytest.approx(0.03)
    assert sub.memory_used == 9
    assert session.execute(select(MistakeBookEntry)).scalars().one().problem_id == 1


# --- The record flag (PU-8) -------------------------------------------
#
# The practice page splits 运行 from 提交, and the difference is entirely
# this flag. What these tests pin is that the flag changes *whether the
# verdict is stored*, and nothing else: the verdict itself, the stream of
# progress events, and the terminal event must all be identical, because
# the page renders a scratch run through the same output panel as a
# submit.


def test_record_false_skips_the_history_write(
    client: TestClient, seeded: Session
) -> None:
    """A scratch run computes the verdict but writes no row."""
    events = _judge_via_ws_with(client, _CPP_AC, record=False)
    assert events[-1]["type"] == "judge_done", events[-1]
    assert events[-1]["status"] == "AC"
    assert events[-1]["passed"] == 2

    assert client.get("/api/v1/submissions").json()["total"] == 0


def test_record_false_still_sends_the_terminal_event(
    client: TestClient, seeded: Session
) -> None:
    """The stream terminates even though nothing was written.

    This is the trap the WS route has to avoid: the reader loop treats
    ``judge_done`` as "stop", so a no-record path that also skipped the
    terminal event would leave the client waiting on a run that had
    already finished. The assertion above would hang rather than fail,
    which is why it is spelled out here as its own test.
    """
    events = _judge_via_ws_with(client, _CPP_AC, record=False)
    assert [e["type"] for e in events][-1] == "judge_done"


def test_record_false_does_not_archive_a_mistake(
    client: TestClient, seeded: Session
) -> None:
    """A wrong scratch run files nothing in the mistake book.

    Archiving is scoped to failures, so a scratch run over the visible
    sample is exactly the case that would otherwise pollute the book: the
    user is iterating, not attempting. Nothing is scored, so nothing is
    filed.
    """
    events = _judge_via_ws_with(client, _CPP_WA, record=False)
    assert events[-1]["status"] == "WA"

    assert client.get("/api/v1/submissions").json()["total"] == 0
    assert client.get("/api/v1/mistake-book").json() == []


def test_record_defaults_to_true_over_ws(client: TestClient, seeded: Session) -> None:
    """Omitting the flag records, so an older client keeps its history."""
    assert "record" not in _judge_via_ws_with(client, _CPP_AC)
    assert client.get("/api/v1/submissions").json()["total"] == 1


def test_record_false_over_rest(client: TestClient, seeded: Session) -> None:
    """The REST route honours the same flag with the same meaning."""
    resp = client.post(
        "/api/v1/judge",
        json={"code": _CPP_AC, "language": "cpp", "problem_id": 1, "record": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "AC"
    assert client.get("/api/v1/submissions").json()["total"] == 0


def test_record_must_be_a_boolean_over_ws(client: TestClient, seeded: Session) -> None:
    """``"false"`` is rejected rather than coerced.

    A truthy string is precisely the silent bug that would make a
    scratch run write history, so the wire rule refuses it instead of
    guessing what the client meant.
    """
    events = _judge_via_ws_with(client, _CPP_AC, record="false", extra_raw=True)
    assert events[-1]["type"] == "judge_error"
    assert "boolean" in events[-1]["error"]
    assert client.get("/api/v1/submissions").json()["total"] == 0


# -------------------------------- the analysis follows the verdict


def _judge_to_the_end(client: TestClient, code: str) -> list[dict]:
    """Like :func:`_judge_via_ws`, but reads until the stream really ends.

    The stream no longer ends at ``judge_done``: recording returns as soon
    as the rows are committed and the analysis runs afterwards, so its
    events follow on the same socket. A reader that stopped at the verdict
    would miss them — and would leave the server writing into a connection
    nobody is reading.
    """
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": code,
                "language": "cpp",
                "problem_id": 1,
                "time_limit": 15.0,
                "mem_limit_mb": 256,
            }
        )
        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            kind = msg.get("type")
            if kind in ("judge_error", "agent_done"):
                return events
            if kind == "judge_done" and not msg.get("analysing"):
                return events


def test_the_verdict_is_sent_before_the_analysis_starts(
    client: TestClient, seeded: Session
) -> None:
    """The ordering is the whole point of splitting recording from analysis.

    The verdict is ready in milliseconds; the analysis is a paid round
    trip that can take minutes. Sending them in the other order — which is
    what P2-8 did — holds the answer behind the extra work, and the
    student watches a finished-looking progress bar for the difference.
    """
    kinds = [e["type"] for e in _judge_to_the_end(client, _CPP_WA)]

    assert kinds.index("judge_done") < kinds.index("agent_start")
    assert kinds[-1] == "agent_done"


def test_judge_done_says_an_analysis_is_coming(
    client: TestClient, seeded: Session
) -> None:
    """``analysing`` is what tells the client not to close the socket.

    Without it the client has to guess, and both guesses are visibly
    wrong: close at the verdict and the agent events are lost; wait at
    every verdict and an accepted run leaves it hanging.
    """
    events = _judge_to_the_end(client, _CPP_WA)
    verdict = next(e for e in events if e["type"] == "judge_done")

    assert verdict["analysing"] is True


def test_an_accepted_run_says_nothing_follows(
    client: TestClient, seeded: Session
) -> None:
    """An accepted run has no mistake to analyse, and must say so.

    A client told to wait here would sit on a socket that has already
    delivered everything it is going to.
    """
    events = _judge_to_the_end(client, _CPP_AC)
    verdict = next(e for e in events if e["type"] == "judge_done")

    assert verdict["analysing"] is False
    assert events[-1]["type"] == "judge_done"
    assert not [e for e in events if e["type"].startswith("agent_")]


def test_a_scratch_run_says_nothing_follows(
    client: TestClient, seeded: Session
) -> None:
    """``record=False`` writes nothing, so there is nothing to analyse."""
    events = _judge_via_ws_with(client, _CPP_WA, record=False)
    verdict = next(e for e in events if e["type"] == "judge_done")

    assert verdict["analysing"] is False


def test_record_false_still_runs_every_case(
    client: TestClient, seeded: Session
) -> None:
    """A scratch run is not a reduced run — the hidden case still executes.

    The page scopes 运行 to the *visible* cases by sending them itself;
    that is the client's choice, not this flag's. If the flag quietly
    narrowed the case set, a future caller asking for a no-write full run
    would get a different verdict than it asked for.
    """
    events = _judge_via_ws_with(client, _CPP_AC, record=False)
    case_indices = [e["index"] for e in events if e["type"] == "case_done"]
    assert case_indices == [0, 1]
