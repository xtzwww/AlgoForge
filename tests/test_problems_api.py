"""Tests for the problem catalogue API (PU-12).

Covers the endpoints in ``app/api/problems.py``:

- GET  /api/v1/problems                    — paged catalogue listing
- GET  /api/v1/problems/{id}               — detail, **visible** cases only
- GET  /api/v1/problems/{id}/test-cases    — every case (judging input)
- POST /api/v1/problems                    — problem + cases in one transaction
- POST /api/v1/problems/{id}/duplicate     — copy, hidden cases included

Two boundaries are load-bearing here, and they run in opposite directions:

* **Reads stay open.** ``/test-cases`` deliberately hands the hidden cases
  to whoever asks. That is the deployment assumption, and
  :func:`test_detail_never_leaks_hidden_case_output` pins the part that
  matters: the *detail* route must not leak them. ``TestCase.is_sample``
  separates practising from cheating, and the model enforces nothing on
  its own — the guarantee lives entirely in the SQL each route runs. The
  assertion is at the raw-response-text level, so a refactor that leaks a
  hidden case's ``expected_output`` through some *other* field still fails
  rather than silently passing.
* **Writes are gated.** Both POST routes carry ``Depends(require_teacher)``
  (added 2026-09-22 — they had no guard at all). Every test that POSTs
  therefore sends teacher credentials; the write-route tests at the bottom
  assert what happens without them. This is why the POST tests take an
  ``admin`` fixture rather than posting bare.

Isolation mirrors ``tests/test_mistake_book.py``: an in-memory SQLite
database with ``StaticPool``, plus a monkeypatch of the module-level
``get_session`` name the routes call. The real ``get_session`` would
build an engine against the documented PostgreSQL DSN, which is not
running in CI or on a dev laptop.
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import problems as problems_module
from app.auth import create_user, issue_session
from app.main import app
from app.models.base import Base
from app.models.problem import Problem
from app.models.test_case import TestCase
from app.models.user import ROLE_TEACHER
from conftest import TEST_SIDECAR_HEADERS

#: Distinctive strings used as hidden-case payloads. They are unlikely to
#: appear anywhere else in a response, so "is this in the body?" is an
#: unambiguous question. See the leak test at the bottom.
HIDDEN_OUTPUT = "SECRET-EXPECTED-42"
HIDDEN_INPUT = "SECRET-INPUT-42"


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Fresh in-memory SQLite session with the schema created.

    ``StaticPool`` + a single connection is required: the default pool
    would hand out a new connection per checkout, and each connection to
    ``sqlite:///:memory:`` gets its own empty database.
    """
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
    """FastAPI client whose routes reuse the in-memory test session.

    The routes call ``get_session()`` as a plain function (not through
    FastAPI's dependency injection), so patching the name inside the
    ``problems`` module is what redirects them. Note this must patch
    ``problems_module``, not ``app.db``: ``from app.db import get_session``
    binds the function into the route module's namespace at import time,
    so patching ``app.db.get_session`` afterwards would have no effect.

    **Two modules need patching, not one** (added 2026-09-22 with the write
    guard). ``Depends(require_teacher)`` resolves through
    ``app.api.auth``, which looks the session up in *its own* namespace —
    so patching only ``problems_module`` leaves the guard reading the real
    PostgreSQL DSN, and every authenticated request fails with 401 because
    the token is not in that database. The symptom points at the token, not
    at the session source, which is what makes this worth a comment.
    """
    from app.api import auth as auth_api

    real_get_session = problems_module.get_session

    def fake_get_session() -> Session:
        return db_session

    problems_module.get_session = fake_get_session  # type: ignore[assignment]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(auth_api, "get_session", fake_get_session)
    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            yield c
    finally:
        monkeypatch.undo()
        problems_module.get_session = real_get_session  # type: ignore[assignment]


@pytest.fixture
def admin(db_session: Session) -> dict[str, str]:
    """Authorization headers for a teacher, for the two POST routes.

    Both write routes are ``Depends(require_teacher)``, so every POST test
    needs these. Built per-test rather than once, because ``db_session``
    is per-test and a token issued against a torn-down session would 401
    for the wrong reason — the test would pass its guard assertion while
    actually failing authentication, which is the failure mode the
    no-credentials tests at the bottom of this file exist to pin down.
    """
    teacher = create_user(
        db_session, username="teacher", password="pw", role=ROLE_TEACHER
    )
    return {"Authorization": f"Bearer {issue_session(db_session, teacher)}"}


@pytest.fixture
def student_headers(db_session: Session) -> dict[str, str]:
    """Authorization headers for a *student* — authenticated, not allowed."""
    student = create_user(db_session, username="student", password="pw")
    return {"Authorization": f"Bearer {issue_session(db_session, student)}"}


def _seed_problem(
    session: Session,
    *,
    problem_id: int = 1,
    title: str = "A + B Problem",
    knowledge_tags: list[str] | None = None,
    description: str = "Read two integers and print their sum.",
    samples: list[dict] | None = None,
    time_limit: float = 10.0,
    memory_limit: int = 256,
    difficulty: str | None = "easy",
    source: str | None = "AlgoForge",
    is_active: bool = True,
    cases: list[tuple[str, str, bool]] | None = None,
) -> Problem:
    """Insert a problem and optional cases, then commit.

    ``cases`` is a list of ``(input, expected_output, is_sample)`` in the
    order they should be assigned ordinals, starting at 1.
    """
    problem = Problem(
        id=problem_id,
        title=title,
        knowledge_tags=knowledge_tags if knowledge_tags is not None else ["基础"],
        description=description,
        samples=samples if samples is not None else [],
        time_limit=time_limit,
        memory_limit=memory_limit,
        difficulty=difficulty,
        source=source,
        is_active=is_active,
    )
    session.add(problem)
    session.flush()

    for ordinal, (case_in, case_out, is_sample) in enumerate(cases or [], start=1):
        session.add(
            TestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                input=case_in,
                expected_output=case_out,
                is_sample=is_sample,
            )
        )

    session.commit()
    return problem


# --- GET /api/v1/problems (listing) -----------------------------------


def test_list_returns_page_envelope(client: TestClient, db_session: Session) -> None:
    """The listing returns the documented page envelope."""
    _seed_problem(db_session, problem_id=1, title="First")
    _seed_problem(db_session, problem_id=2, title="Second")

    resp = client.get("/api/v1/problems")
    assert resp.status_code == 200

    body = resp.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert [item["title"] for item in body["items"]] == ["First", "Second"]


def test_list_omits_statement_and_cases(
    client: TestClient, db_session: Session
) -> None:
    """A catalogue row carries no statement prose and no cases.

    This is the payload-shape half of the listing contract; the security
    half is :func:`test_detail_never_leaks_hidden_case_output`.
    """
    _seed_problem(
        db_session,
        description="LONG-STATEMENT-TEXT",
        cases=[("1 2", "3", True), (HIDDEN_INPUT, HIDDEN_OUTPUT, False)],
    )

    body = client.get("/api/v1/problems").json()
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert "description" not in item
    assert "test_cases" not in item
    assert "samples" not in item

    raw = json.dumps(body)
    assert "LONG-STATEMENT-TEXT" not in raw
    assert HIDDEN_OUTPUT not in raw


def test_list_is_empty_when_no_problems(client: TestClient) -> None:
    """An empty catalogue is a valid 200, not a 404."""
    body = client.get("/api/v1/problems").json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_list_paginates(client: TestClient, db_session: Session) -> None:
    """``page`` / ``page_size`` slice the catalogue, ``total`` does not."""
    for i in range(1, 6):
        _seed_problem(db_session, problem_id=i, title=f"P{i}")

    body = client.get("/api/v1/problems", params={"page": 2, "page_size": 2}).json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert [item["title"] for item in body["items"]] == ["P3", "P4"]


def test_list_rejects_page_size_above_ceiling(client: TestClient) -> None:
    """``MAX_PAGE_SIZE`` is enforced so the catalogue cannot be a memory sink."""
    resp = client.get("/api/v1/problems", params={"page_size": 101})
    assert resp.status_code == 422


def test_list_excludes_inactive_by_default(
    client: TestClient, db_session: Session
) -> None:
    """Soft-deleted problems stay out of the catalogue unless asked for."""
    _seed_problem(db_session, problem_id=1, title="Live")
    _seed_problem(db_session, problem_id=2, title="Retired", is_active=False)

    default_body = client.get("/api/v1/problems").json()
    assert default_body["total"] == 1
    assert default_body["items"][0]["title"] == "Live"

    all_body = client.get(
        "/api/v1/problems", params={"include_inactive": "true"}
    ).json()
    assert all_body["total"] == 2
    assert {item["title"] for item in all_body["items"]} == {"Live", "Retired"}


def test_list_filters_by_difficulty(
    client: TestClient, db_session: Session
) -> None:
    """``difficulty`` narrows the listing."""
    _seed_problem(db_session, problem_id=1, title="Easy one", difficulty="easy")
    _seed_problem(db_session, problem_id=2, title="Hard one", difficulty="hard")

    body = client.get("/api/v1/problems", params={"difficulty": "hard"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Hard one"


def test_list_rejects_unknown_difficulty(
    client: TestClient, db_session: Session
) -> None:
    """An unknown difficulty is a 422, not a silently-empty page."""
    _seed_problem(db_session)

    resp = client.get("/api/v1/problems", params={"difficulty": "impossible"})
    assert resp.status_code == 422
    assert "easy" in resp.json()["detail"]


def test_list_filters_by_knowledge_tag(
    client: TestClient, db_session: Session
) -> None:
    """Tag membership is matched in SQL against the JSON array column."""
    _seed_problem(db_session, problem_id=1, title="DP", knowledge_tags=["dp", "图论"])
    _seed_problem(db_session, problem_id=2, title="Greedy", knowledge_tags=["贪心"])

    body = client.get("/api/v1/problems", params={"knowledge_tag": "图论"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "DP"


def test_list_tag_filter_is_exact_not_substring(
    client: TestClient, db_session: Session
) -> None:
    """``图论`` must not match a problem tagged ``图论进阶``.

    The frontend's tag filter (PU-14) offers tags that actually exist, so
    exact matching is what its UI implies — a substring match would show
    a problem under a tag it does not carry.
    """
    _seed_problem(
        db_session, problem_id=1, title="Advanced", knowledge_tags=["图论进阶"]
    )

    body = client.get("/api/v1/problems", params={"knowledge_tag": "图论"}).json()
    assert body["total"] == 0


# --- GET /api/v1/problems/{id} (detail) -------------------------------


def test_detail_returns_statement_and_samples(
    client: TestClient, db_session: Session
) -> None:
    """The detail view carries the statement, samples, and limits."""
    _seed_problem(
        db_session,
        description="Add two numbers.",
        samples=[{"input": "1 2", "output": "3"}],
        time_limit=2.5,
        memory_limit=512,
        cases=[("1 2", "3", True)],
    )

    resp = client.get("/api/v1/problems/1")
    assert resp.status_code == 200

    body = resp.json()
    assert body["title"] == "A + B Problem"
    assert body["description"] == "Add two numbers."
    assert body["samples"] == [{"input": "1 2", "output": "3"}]
    assert body["time_limit"] == 2.5
    assert body["memory_limit"] == 512
    assert [c["ordinal"] for c in body["test_cases"]] == [1]


def test_detail_returns_only_sample_cases(
    client: TestClient, db_session: Session
) -> None:
    """The detail view's ``test_cases`` is filtered to visible cases."""
    _seed_problem(
        db_session,
        cases=[
            ("1 2", "3", True),
            (HIDDEN_INPUT, HIDDEN_OUTPUT, False),
            ("4 5", "9", True),
        ],
    )

    body = client.get("/api/v1/problems/1").json()
    assert [c["ordinal"] for c in body["test_cases"]] == [1, 3]
    assert all(c["is_sample"] is True for c in body["test_cases"])


def test_detail_never_leaks_hidden_case_output(
    client: TestClient, db_session: Session
) -> None:
    """**The load-bearing test.** A hidden case must not be readable.

    Asserted against the raw response text rather than a parsed field so
    that leaking the value through any *other* field — a debug echo, a
    new response key, a nested object — still fails. ``is_sample`` is
    only a column; this route's WHERE clause is the entire guarantee.
    """
    _seed_problem(
        db_session,
        cases=[
            ("1 2", "3", True),
            (HIDDEN_INPUT, HIDDEN_OUTPUT, False),
        ],
    )

    resp = client.get("/api/v1/problems/1")
    assert resp.status_code == 200

    assert HIDDEN_OUTPUT not in resp.text
    assert HIDDEN_INPUT not in resp.text

    # And the same for a problem whose cases are *all* hidden: the list
    # must be empty, not "everything because nothing was filtered".
    _seed_problem(
        db_session,
        problem_id=2,
        title="All hidden",
        cases=[(HIDDEN_INPUT, HIDDEN_OUTPUT, False)],
    )
    all_hidden = client.get("/api/v1/problems/2")
    assert all_hidden.status_code == 200
    assert all_hidden.json()["test_cases"] == []
    assert HIDDEN_OUTPUT not in all_hidden.text


def test_detail_returns_inactive_problem(
    client: TestClient, db_session: Session
) -> None:
    """A retired problem is still resolvable by id.

    Filtering inactive problems belongs to the listing — a caller holding
    an old submission or mistake-book entry must still be able to look
    the problem up.
    """
    _seed_problem(db_session, is_active=False)

    resp = client.get("/api/v1/problems/1")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_detail_404_on_unknown_id(client: TestClient) -> None:
    """Unknown id → 404 with the id in the detail message."""
    resp = client.get("/api/v1/problems/999")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]


def test_detail_422_on_non_integer_id(client: TestClient) -> None:
    """A non-integer path segment never reaches the handler."""
    assert client.get("/api/v1/problems/abc").status_code == 422


# --- GET /api/v1/problems/{id}/test-cases -----------------------------


def test_test_cases_returns_every_case(
    client: TestClient, db_session: Session
) -> None:
    """This is the judging input, so hidden cases are included."""
    _seed_problem(
        db_session,
        cases=[
            ("1 2", "3", True),
            (HIDDEN_INPUT, HIDDEN_OUTPUT, False),
            ("4 5", "9", False),
        ],
    )

    resp = client.get("/api/v1/problems/1/test-cases")
    assert resp.status_code == 200

    body = resp.json()
    assert body["problem_id"] == 1
    assert body["total"] == 3
    assert [c["ordinal"] for c in body["test_cases"]] == [1, 2, 3]
    assert [c["is_sample"] for c in body["test_cases"]] == [True, False, False]

    outputs = {c["ordinal"]: c["expected_output"] for c in body["test_cases"]}
    assert outputs[2] == HIDDEN_OUTPUT


def test_test_cases_ordered_by_ordinal_not_id(
    client: TestClient, db_session: Session
) -> None:
    """Author-controlled ordering survives the round trip.

    Rows are inserted out of ordinal order on purpose: ordering by ``id``
    would coincidentally pass here only if insertion order matched, so
    this pins the ORDER BY clause.
    """
    problem = _seed_problem(db_session, cases=[])
    for ordinal, payload in ((3, "third"), (1, "first"), (2, "second")):
        db_session.add(
            TestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                input=payload,
                expected_output=payload,
                is_sample=False,
            )
        )
    db_session.commit()

    body = client.get("/api/v1/problems/1/test-cases").json()
    assert [c["input"] for c in body["test_cases"]] == ["first", "second", "third"]


def test_test_cases_empty_for_problem_without_cases(
    client: TestClient, db_session: Session
) -> None:
    """A problem with no cases is a 200 with an empty list."""
    _seed_problem(db_session, cases=[])

    body = client.get("/api/v1/problems/1/test-cases").json()
    assert body == {"problem_id": 1, "total": 0, "test_cases": []}


def test_test_cases_404_on_unknown_problem(client: TestClient) -> None:
    """Unknown problem → 404, not an empty list.

    An empty list would be indistinguishable from "this problem has no
    cases", which is a real and different state.
    """
    assert client.get("/api/v1/problems/999/test-cases").status_code == 404


# --- POST /api/v1/problems --------------------------------------------


def test_create_problem_with_cases(
    client: TestClient, admin: dict[str, str]
) -> None:
    """POST creates the problem and its cases in one request."""
    resp = client.post(
        "/api/v1/problems",
        headers=admin,
        json={
            "title": "Two Sum",
            "knowledge_tags": ["哈希表"],
            "description": "Find the pair.",
            "input_format": "n then n integers, then target",
            "output_format": "two indices",
            "samples": [{"input": "4\n2 7 11 15\n9", "output": "0 1"}],
            "time_limit": 3.0,
            "memory_limit": 512,
            "difficulty": "medium",
            "source": "LeetCode 1",
            "test_cases": [
                {
                    "input": "4\n2 7 11 15\n9",
                    "expected_output": "0 1",
                    "is_sample": True,
                },
                {"input": "3\n3 2 4\n6", "expected_output": "1 2"},
            ],
        },
    )
    assert resp.status_code == 201

    body = resp.json()
    assert body["id"] == 1
    assert body["title"] == "Two Sum"
    assert body["knowledge_tags"] == ["哈希表"]
    assert body["description"] == "Find the pair."
    assert body["input_format"] == "n then n integers, then target"
    assert body["output_format"] == "two indices"
    assert body["difficulty"] == "medium"
    assert body["source"] == "LeetCode 1"
    assert body["is_active"] is True
    assert body["time_limit"] == 3.0
    assert body["memory_limit"] == 512

    # The creation response has the detail shape, so it inherits the same
    # filter: only the case the caller marked as a sample comes back.
    assert [c["ordinal"] for c in body["test_cases"]] == [1]
    assert body["test_cases"][0]["is_sample"] is True


def test_create_assigns_ordinals_from_request_order(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Ordinals are 1-based and follow request order, not caller input.

    The request schema has no ``ordinal`` field at all, so a caller
    cannot supply a duplicate or a gap by mistake.
    """
    resp = client.post(
        "/api/v1/problems",
        headers=admin,
        json={
            "title": "Ordinals",
            "test_cases": [
                {"input": "a", "expected_output": "1"},
                {"input": "b", "expected_output": "2"},
                {"input": "c", "expected_output": "3"},
            ],
        },
    )
    assert resp.status_code == 201
    problem_id = resp.json()["id"]

    cases = client.get(f"/api/v1/problems/{problem_id}/test-cases").json()
    assert [c["ordinal"] for c in cases["test_cases"]] == [1, 2, 3]
    assert [c["input"] for c in cases["test_cases"]] == ["a", "b", "c"]


def test_create_hidden_by_default(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Omitting ``is_sample`` hides the case.

    Fail-closed: a caller importing a catalogue must opt *in* to
    exposing a case, never opt out of hiding one, because forgetting the
    field would otherwise publish the expected output.
    """
    resp = client.post(
        "/api/v1/problems",
        headers=admin,
        json={
            "title": "Fail closed",
            "test_cases": [{"input": "1 1", "expected_output": "2"}],
        },
    )
    assert resp.status_code == 201
    problem_id = resp.json()["id"]

    # Not echoed back in the creation response ...
    assert resp.json()["test_cases"] == []

    # ... and stored as hidden.
    cases = client.get(f"/api/v1/problems/{problem_id}/test-cases").json()
    assert cases["test_cases"][0]["is_sample"] is False


def test_create_problem_without_cases(
    client: TestClient, admin: dict[str, str]
) -> None:
    """A problem may be created with no cases yet."""
    resp = client.post("/api/v1/problems", json={"title": "Bare"}, headers=admin)
    assert resp.status_code == 201

    body = resp.json()
    assert body["test_cases"] == []
    assert body["samples"] == []
    assert body["knowledge_tags"] == []
    assert body["time_limit"] == 10.0
    assert body["memory_limit"] == 256
    assert body["difficulty"] is None


def test_create_applies_model_defaults_for_empty_strings(
    client: TestClient,
    admin: dict[str, str],
) -> None:
    """An omitted statement is stored as an empty string, not NULL.

    ``description`` is NOT NULL in the schema (PU-11), so the default has
    to be the empty string rather than None — otherwise the insert fails
    on a fresh dev database whose rows predate the column.
    """
    resp = client.post(
        "/api/v1/problems", json={"title": "No statement"}, headers=admin
    )
    body = resp.json()
    assert body["description"] == ""
    assert body["input_format"] is None
    assert body["output_format"] is None


def test_create_problem_visible_to_listing_and_detail(
    client: TestClient, admin: dict[str, str]
) -> None:
    """End-to-end: POST → appears in the listing → resolvable by detail."""
    created = client.post(
        "/api/v1/problems",
        headers=admin,
        json={
            "title": "Round trip",
            "knowledge_tags": ["模拟"],
            "difficulty": "easy",
            "samples": [{"input": "1", "output": "1"}],
            "test_cases": [
                {"input": "1", "expected_output": "1", "is_sample": True},
                {"input": "2", "expected_output": "2"},
            ],
        },
    ).json()
    problem_id = created["id"]

    listing = client.get("/api/v1/problems").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == problem_id
    assert listing["items"][0]["title"] == "Round trip"

    detail = client.get(f"/api/v1/problems/{problem_id}").json()
    assert detail["samples"] == [{"input": "1", "output": "1"}]
    assert [c["ordinal"] for c in detail["test_cases"]] == [1]

    cases = client.get(f"/api/v1/problems/{problem_id}/test-cases").json()
    assert cases["total"] == 2


def test_create_rejects_blank_title(
    client: TestClient, admin: dict[str, str]
) -> None:
    """``title`` has ``min_length=1``, so an empty string is a 422."""
    assert (
        client.post("/api/v1/problems", json={"title": ""}, headers=admin)
        .status_code
        == 422
    )


def test_create_422_on_missing_title(
    client: TestClient, admin: dict[str, str]
) -> None:
    """``title`` is required."""
    assert (
        client.post("/api/v1/problems", json={}, headers=admin).status_code
        == 422
    )


def test_create_rejects_out_of_range_limits(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Limits are bounded so a bad import cannot make judging impossible.

    A zero time limit is an instant TLE for every submission, and an
    unbounded one defeats the sandbox timeout entirely.
    """
    assert (
        client.post(
            "/api/v1/problems", json={"title": "x", "time_limit": 0},
            headers=admin,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/problems", json={"title": "x", "time_limit": 61},
            headers=admin,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/problems", json={"title": "x", "memory_limit": 0},
            headers=admin,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/problems", json={"title": "x", "memory_limit": 4096},
            headers=admin,
        ).status_code
        == 422
    )


def test_create_rejects_unknown_difficulty(
    client: TestClient, admin: dict[str, str]
) -> None:
    """``difficulty`` is a Literal, so a typo is rejected at the boundary."""
    resp = client.post(
        "/api/v1/problems",
        json={"title": "x", "difficulty": "impossible"},
        headers=admin,
    )
    assert resp.status_code == 422


def test_create_preserves_multiline_io(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Trailing newlines are significant for judging and must survive.

    ``normalize_output`` trims trailing whitespace at judge time, but the
    stored value is the author's ground truth — silently stripping it
    here would make the database disagree with what was authored.
    """
    payload = "3\n1 2 3\n"
    resp = client.post(
        "/api/v1/problems",
        headers=admin,
        json={
            "title": "Multiline",
            "test_cases": [
                {"input": payload, "expected_output": "6\n", "is_sample": True}
            ],
        },
    )
    assert resp.status_code == 201
    case = resp.json()["test_cases"][0]
    assert case["input"] == payload
    assert case["expected_output"] == "6\n"


def test_create_two_problems_do_not_collide(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Ordinal uniqueness is per-problem, not global.

    ``uq_test_cases_problem_ordinal`` is a composite constraint; a naive
    global unique index on ``ordinal`` would reject the second problem's
    case 1.
    """
    first = client.post(
        "/api/v1/problems",
        headers=admin,
        json={"title": "One", "test_cases": [{"input": "a", "expected_output": "a"}]},
    ).json()
    second = client.post(
        "/api/v1/problems",
        headers=admin,
        json={"title": "Two", "test_cases": [{"input": "b", "expected_output": "b"}]},
    ).json()

    assert first["id"] != second["id"]
    assert client.get(f"/api/v1/problems/{first['id']}/test-cases").json()["total"] == 1
    assert (
        client.get(f"/api/v1/problems/{second['id']}/test-cases").json()["total"] == 1
    )


def test_create_does_not_mutate_existing_problem(
    client: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    """Creating a second problem leaves the first untouched."""
    _seed_problem(db_session, problem_id=1, title="Original")

    client.post("/api/v1/problems", json={"title": "Newcomer"}, headers=admin)

    original = client.get("/api/v1/problems/1").json()
    assert original["title"] == "Original"


# --- 题目搜索（阶段 5）--------------------------------------------------


def test_search_filters_by_title_substring(
    client: TestClient, db_session: Session
) -> None:
    _seed_problem(db_session, problem_id=1, title="快速排序")
    _seed_problem(db_session, problem_id=2, title="归并排序")
    _seed_problem(db_session, problem_id=3, title="二分查找")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "排序"}).json()

    assert {p["title"] for p in body["items"]} == {"快速排序", "归并排序"}


def test_search_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    _seed_problem(db_session, problem_id=1, title="Longest Substring")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "substring"}).json()

    assert len(body["items"]) == 1


def test_search_shows_total_of_matches_not_of_catalogue(
    client: TestClient, db_session: Session
) -> None:
    """分页的 total 必须是**命中数**。

    用全库总数的话，搜出来 1 条却显示 total=3，翻页按钮就会指向一个
    空页——看起来像搜索坏了。
    """
    _seed_problem(db_session, problem_id=1, title="快速排序")
    _seed_problem(db_session, problem_id=2, title="归并排序")
    _seed_problem(db_session, problem_id=3, title="二分查找")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "排序"}).json()

    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_search_escapes_the_wildcard_characters(
    client: TestClient, db_session: Session
) -> None:
    """百分号要按字面搜索，不能当通配符。

    选数据是这条测试的关键。第二道题**必须同时含 100 和不含 100%**：
    ``%`` 只会漏配到它之后的部分，所以拿一道压根不含 100 的题来对照，
    转不转义的结果一模一样——测试会通过，而它什么都没测。
    （实测：最初的版本就是这样，注入「去掉转义」后 44 条全绿。）
    """
    _seed_problem(db_session, problem_id=1, title="进度 100% 的题")
    _seed_problem(db_session, problem_id=2, title="100 个数的和")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "100%"}).json()

    assert [p["title"] for p in body["items"]] == ["进度 100% 的题"]


def test_search_escapes_the_underscore_wildcard(
    client: TestClient, db_session: Session
) -> None:
    """下划线同理：``_`` 在 LIKE 里是「任意一个字符」。"""
    _seed_problem(db_session, problem_id=1, title="a_b")
    _seed_problem(db_session, problem_id=2, title="axb")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "a_b"}).json()

    assert [p["title"] for p in body["items"]] == ["a_b"]


def test_search_combines_with_the_tag_filter(
    client: TestClient, db_session: Session
) -> None:
    _seed_problem(db_session, problem_id=1, title="排序一", knowledge_tags=["排序"])
    _seed_problem(db_session, problem_id=2, title="排序二", knowledge_tags=["查找"])
    db_session.commit()

    body = client.get(
        "/api/v1/problems", params={"q": "排序", "knowledge_tag": "排序"}
    ).json()

    assert [p["title"] for p in body["items"]] == ["排序一"]


def test_blank_search_does_not_filter(client: TestClient, db_session: Session) -> None:
    """空搜索框等于没搜索，而不是搜「空字符串」。"""
    _seed_problem(db_session, problem_id=1, title="A")
    _seed_problem(db_session, problem_id=2, title="B")
    db_session.commit()

    body = client.get("/api/v1/problems", params={"q": "   "}).json()

    assert body["total"] == 2


# --- 复制题目（阶段 5）--------------------------------------------------


def test_duplicate_copies_the_hidden_cases_too(
    client: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    """**隐藏用例必须跟着复制。**

    这是这个接口存在的全部理由：一份看起来正常、但没有判题用例的题，
    会让每一次提交都判错，而且从界面上完全看不出来。
    """
    _seed_problem(
        db_session,
        problem_id=1,
        title="原题",
        cases=[
            ("1 2", "3", True),
            (HIDDEN_INPUT, HIDDEN_OUTPUT, False),
        ],
    )
    db_session.commit()

    resp = client.post("/api/v1/problems/1/duplicate", headers=admin)

    assert resp.status_code == 201, resp.text
    new_id = resp.json()["id"]
    assert new_id != 1

    cases = db_session.query(TestCase).filter(TestCase.problem_id == new_id).all()
    assert len(cases) == 2
    assert sum(1 for c in cases if not c.is_sample) == 1
    hidden = next(c for c in cases if not c.is_sample)
    assert hidden.expected_output == HIDDEN_OUTPUT


def test_duplicate_response_does_not_leak_hidden_cases(
    client: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    """复制接口的**响应**仍然只给可见用例。

    上一条保证隐藏用例进了库，这一条保证它没有跟着响应出去——两件事必须
    分别成立，而只看「库里有几条」是看不出泄漏的。
    """
    _seed_problem(
        db_session,
        problem_id=1,
        title="原题",
        cases=[("1 2", "3", True), (HIDDEN_INPUT, HIDDEN_OUTPUT, False)],
    )
    db_session.commit()

    resp = client.post("/api/v1/problems/1/duplicate", headers=admin)

    assert HIDDEN_OUTPUT not in resp.text
    assert HIDDEN_INPUT not in resp.text


def test_duplicate_copies_the_statements_fields(
    client: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    _seed_problem(
        db_session,
        problem_id=1,
        title="原题",
        knowledge_tags=["排序", "分治"],
        description="把两个数加起来",
        time_limit=3.5,
        memory_limit=512,
        difficulty="hard",
        source="课程A",
    )
    db_session.commit()

    body = client.post("/api/v1/problems/1/duplicate", headers=admin).json()

    assert body["knowledge_tags"] == ["排序", "分治"]
    assert body["description"] == "把两个数加起来"
    assert body["time_limit"] == 3.5
    assert body["memory_limit"] == 512
    assert body["difficulty"] == "hard"
    assert body["source"] == "课程A"
    assert body["title"] == "原题（副本）"


def test_duplicate_starts_active(
    client: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    """复制出来的是可用的。

    老师复制一份是为了改它——如果副本是停用状态，他在列表里找不到它，
    而「复制了但看不见」比「复制失败」更难排查。
    """
    _seed_problem(db_session, problem_id=1, title="原题", is_active=False)
    db_session.commit()

    body = client.post("/api/v1/problems/1/duplicate", headers=admin).json()

    assert body["is_active"] is True


def test_duplicate_of_a_missing_problem_is_404(
    client: TestClient, admin: dict[str, str]
) -> None:
    resp = client.post("/api/v1/problems/999/duplicate", headers=admin)
    assert resp.status_code == 404


# --- 写路由的守卫（2026-09-22 新增）--------------------------------------


def test_every_write_route_is_guarded(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    """每个写路由都要拦住学生，一个漏了就等于全漏。

    枚举而不是抽样：模块是照着上一段复制出来的，新增一条抄了全部参数
    却忘了守卫的路由，正是评审时看不见的那种错。加了新写路由就往
    ``write_routes`` 里补一行——这也是它写成显式列表、而不是遍历
    ``app.routes`` 的原因：遍历只能证明「现存的路由都被守住了」，
    证明不了「新加的那条有没有被想起来」。

    用学生身份而不是匿名身份来测，是因为这两种失败必须分开：
    匿名是 401，学生是 403。只测 401 的话，一条把守卫写成
    ``require_user`` 的路由会照样绿。
    """
    write_routes: list[tuple[str, str, dict[str, object]]] = [
        ("POST", "/api/v1/problems", {"json": {"title": "x"}}),
        ("POST", "/api/v1/problems/1/duplicate", {}),
    ]

    for method, path, kwargs in write_routes:
        resp = client.request(method, path, headers=student_headers, **kwargs)
        assert resp.status_code == 403, (
            f"{method} {path} 没拦住学生：{resp.status_code}。"
            "写路由必须带 Depends(require_teacher)。"
        )


def test_write_routes_reject_anonymous_callers(client: TestClient) -> None:
    """没有凭证是 401，不是 403——两种拒绝要能分开。

    与上一条互补：上一条证明「学生被拦住」，这一条证明「匿名也被拦住」，
    且两者用不同的状态码。合并成一条测试会让「守卫被换成 require_user」
    这种改动静默通过。
    """
    write_routes: list[tuple[str, str, dict[str, object]]] = [
        ("POST", "/api/v1/problems", {"json": {"title": "x"}}),
        ("POST", "/api/v1/problems/1/duplicate", {}),
    ]

    for method, path, kwargs in write_routes:
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 401, (
            f"{method} {path} 对匿名调用者返回 {resp.status_code}，应为 401。"
        )


def test_read_routes_stay_open(client: TestClient) -> None:
    """读路由**不**要求凭证，这是有意保留的。

    守卫加在写上而不是整个模块上，因为本部署里做题不需要账号
    （练习页是免登录的）。这条测试把那个决定钉住：将来有人
    「顺手把整个模块都加上守卫」时会红，从而必须是有意为之。
    """
    for path in ("/api/v1/problems", "/api/v1/problems/999"):
        resp = client.get(path)
        assert resp.status_code != 401, f"GET {path} 不该要求登录"


def test_teacher_can_still_write(client: TestClient, admin: dict[str, str]) -> None:
    """守卫不能把老师本人也拦在外面。

    上三条测的都是「该拦的拦住了」；没有这一条，一条把条件写反
    （例如 ``user.role != ROLE_STUDENT``）的守卫会让前三条全绿、
    而功能整体不可用。
    """
    resp = client.post("/api/v1/problems", json={"title": "x"}, headers=admin)
    assert resp.status_code == 201, resp.text
