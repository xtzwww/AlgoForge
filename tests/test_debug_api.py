"""Tests for the debug session API (PU-17).

Covers the five routes in ``app/api/debug.py``:

- ``GET    /api/v1/debug/availability``
- ``POST   /api/v1/debug/sessions``
- ``PUT    /api/v1/debug/sessions/{id}/breakpoints``
- ``POST   /api/v1/debug/sessions/{id}/step``
- ``DELETE /api/v1/debug/sessions/{id}``

**What this file is for.** The parser, the session sequencing and the
store each have their own suite; what none of them cover is the *route
layer* — and the route layer is where three of this feature's four
guarantees live:

* **Authentication.** Every route but ``availability`` requires a logged-in
  user. It is easy to add a route and forget ``Depends(require_user)``,
  and the failure is silent: the endpoint works, for everyone, including
  nobody. Pinned per route, not once, because the guard is per route.
* **The action vocabulary.** ``step`` takes a client-supplied string, and
  the only thing between it and gdb is ``STEPS``. A route that passed the
  string through would be a command-injection surface, so the allowlist is
  asserted from the outside: a well-formed but unsupported action must be
  refused, and the refusal must not be a 500.
* **Idempotent cleanup.** ``DELETE`` on an unknown id answers
  ``closed: false`` rather than 404, because cleanup after an expiry must
  not be reported as an error. The panel calls this on unmount, so a 404
  there would put an error on screen for a successful cleanup.

**Why the gdb calls are stubbed.** Building and stepping shell out to a
matched g++/gdb pair (see ``app/sandbox/debugger.py``). Those exist on
this machine but not in CI, and a test that skipped without them would
stop covering the routes the moment the toolchain moved. The routes are
what this file is about, so ``build`` and ``command`` are replaced with
recorders and the assertions are about what the *route* did — status
codes, payload shapes, what reached the session.

The store is per-module global state, so it is cleared around every test.
Sessions are given real ``tempfile`` directories and the real
``close_session`` is patched out: ``close_session`` is
``shutil.rmtree(session.work_dir, ignore_errors=True)``, and a session
whose ``work_dir`` was ever the repository root would delete it quietly.
See ``tests/test_debug_store.py`` for the incident that rule comes from.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import debug_store
from app.api import auth as auth_api
from app.api import debug as debug_api
from app.auth import create_user, issue_session
from app.debug_session import DebugError, create_session
from app.main import app
from app.models.base import Base
from conftest import TEST_SIDECAR_HEADERS

BASE = "/api/v1/debug"


# --------------------------------------------------------------- fixtures


@pytest.fixture
def db_session() -> Iterator[Session]:
    """In-memory SQLite with the schema, mirroring ``test_problems_api``.

    ``StaticPool`` + one connection: every connection to
    ``sqlite:///:memory:`` gets its own empty database, so the default
    pool would hand the auth lookup a database with no users in it.
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
def work_root() -> Iterator[Path]:
    """A real, disposable directory for every session this test creates.

    Real because ``close_session`` takes a path and would be handed a
    fake one; disposable because that is the only thing standing between a
    failed patch and a deleted working tree.
    """
    root = Path(tempfile.mkdtemp(prefix="dbgapi_"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_store():
    """The store is module-level state; every test starts and ends empty.

    Also patches ``close_session`` **as the store resolves it** —
    ``debug_store`` does ``from app.debug_session import close_session``
    and holds its own reference, so patching the defining module would
    leave the real, destructive function in place.
    """
    with patch("app.debug_store.close_session"):
        debug_store.clear()
        yield
        debug_store.clear()


@pytest.fixture(autouse=True)
def _isolate_sessions(work_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Real scratch directories, under this test's own temp root.

    ``create_session`` writes a directory and does not remove it on
    failure, so pointing it at a per-test root bounds anything a failing
    test leaves behind.
    """
    real_create = create_session

    def scoped_create(session_id: str, code: str, stdin: str = "") -> object:
        return real_create(session_id, code, stdin=stdin, base_dir=work_root)

    monkeypatch.setattr(debug_api, "create_session", scoped_create)


@pytest.fixture(autouse=True)
def _stub_toolchain(monkeypatch: pytest.MonkeyPatch):
    """Pretend g++/gdb are present, so the routes run on any machine.

    ``debug_available`` is a file-existence check, so on a machine without
    the pair every session test would 400 for a reason that has nothing to
    do with the route.

    Patched on ``debug_api``, not on ``app.sandbox.debugger``:
    ``app/api/debug.py`` does ``from app.sandbox.debugger import
    debug_available`` and holds its own reference, so patching the
    defining module leaves the real probe in place. Same trap as
    ``close_session`` — see the module docstring.
    """
    monkeypatch.setattr(debug_api, "debug_available", lambda: True)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Client whose auth dependency reads the in-memory session.

    ``Depends(require_user)`` resolves through ``app.api.auth``, which
    looks the session up in *its own* namespace — so patching only the
    debug module would leave the guard reading the real PostgreSQL DSN
    and every authenticated request would 401 because the token is not in
    that database. The symptom points at the token rather than at the
    session source, which is why this is worth stating.
    """

    def fake_get_session() -> Session:
        return db_session

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(auth_api, "get_session", fake_get_session)
    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            yield c
    finally:
        monkeypatch.undo()


@pytest.fixture
def student(db_session: Session) -> dict[str, str]:
    """Authorization headers for a logged-in student.

    Per-test, because ``db_session`` is per-test and a token minted
    against a torn-down session would 401 for the wrong reason — a test
    would then pass its "unauthenticated is refused" assertion while
    actually failing authentication.
    """
    user = create_user(db_session, username="stu", password="pw")
    return {"Authorization": f"Bearer {issue_session(db_session, user)}"}


def _built_session(work_root: Path, session_id: str = "s1"):
    """A session whose ``built`` flag is set, without running g++."""
    session = create_session(session_id, "int main() {}", base_dir=work_root)
    session.built = True
    debug_store.put(session)
    return session


# ------------------------------------------------------------ availability


def test_availability_is_readable_without_logging_in(client: TestClient):
    """A student has to be able to learn debugging is unavailable *before*
    signing in to find out — the check is a file-existence test and tells
    an anonymous caller nothing they could not learn from the filesystem."""
    res = client.get(f"{BASE}/availability")

    assert res.status_code == 200
    assert res.json()["available"] is True


def test_availability_names_the_missing_files(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """「调试不可用」 alone gives a student nothing to act on."""
    monkeypatch.setattr(debug_api, "debug_available", lambda: False)

    body = client.get(f"{BASE}/availability").json()

    assert body["available"] is False
    # The reason must name the expected paths, because the audience is a
    # student who cannot debug and has to ask someone who can.
    assert body["reason"]
    assert "调试工具链" in body["reason"]


# ------------------------------------------------------------------ create


def test_create_requires_a_logged_in_user(client: TestClient):
    """Pinned per route: the guard is per route, so one test proves one route."""
    res = client.post(f"{BASE}/sessions", json={"code": "int main(){}"})

    assert res.status_code == 401


def test_create_compiles_and_returns_an_id(
    client: TestClient, student: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    """The id must be in the response: it is the only handle on the session."""
    compiled: list[str] = []

    def stub_build(session):
        # The real ``build`` compiles and sets ``built``; a no-op stub that
        # did not would report every session as unbuilt, and the assertion
        # below would be checking the stub rather than the route.
        compiled.append(session.session_id)
        session.built = True

    monkeypatch.setattr(debug_api, "build", stub_build)

    res = client.post(
        f"{BASE}/sessions", json={"code": "int main(){}"}, headers=student
    )

    assert res.status_code == 200
    body = res.json()
    assert body["session_id"]
    assert body["built"] is True
    assert compiled == [body["session_id"]]
    assert debug_store.count() == 1


def test_create_accepts_breakpoints_up_front(
    client: TestClient, student: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    """A student sets breakpoints, *then* presses 运行 — that is the order
    they expect, and a session that could only take them afterwards would
    run straight past the line they marked."""
    monkeypatch.setattr(
        debug_api, "build", lambda session: setattr(session, "built", True)
    )

    body = client.post(
        f"{BASE}/sessions",
        json={"code": "int main(){}", "breakpoints": [9, 3, 3]},
        headers=student,
    ).json()

    # Sorted and de-duplicated: a Set on the server, and the client
    # renders an ordering, so the order has to be the server's.
    assert body["breakpoints"] == [3, 9]


def test_a_compile_failure_is_a_400_and_does_not_hold_a_slot(
    client: TestClient, student: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    """A session that cannot be built is unusable, so it must not survive.

    Left in the store it would consume one of ``MAX_SESSIONS`` until its
    TTL — and a student who mistypes and retries five times would evict
    somebody else's working session.
    """

    def failing_build(session):
        raise DebugError("main.cpp:3:5: error: 'x' was not declared")

    monkeypatch.setattr(debug_api, "build", failing_build)

    res = client.post(f"{BASE}/sessions", json={"code": "bad"}, headers=student)

    assert res.status_code == 400
    assert "was not declared" in res.json()["detail"]
    assert debug_store.count() == 0


# ------------------------------------------------------------- breakpoints


def test_breakpoints_require_a_logged_in_user(client: TestClient):
    res = client.put(f"{BASE}/sessions/nope/breakpoints", json={"lines": [1]})

    assert res.status_code == 401


def test_breakpoints_replace_the_whole_set(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """A full replace, not a delta — the gutter already owns the truth."""
    session = _built_session(work_root)
    session.breakpoints = {5}

    body = client.put(
        f"{BASE}/sessions/{session.session_id}/breakpoints",
        json={"lines": [1, 2]},
        headers=student,
    ).json()

    assert body["breakpoints"] == [1, 2]
    assert 5 not in body["breakpoints"]


def test_breakpoints_say_when_they_take_effect(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """The program is not running between steps, so a new breakpoint cannot
    stop it now. Saying so lets the panel write 「下次单步生效」 instead of
    implying an immediate stop the student would wait for."""
    session = _built_session(work_root)

    body = client.put(
        f"{BASE}/sessions/{session.session_id}/breakpoints",
        json={"lines": [4]},
        headers=student,
    ).json()

    assert body["takes_effect"] == "next_step"


def test_breakpoints_on_an_unknown_session_are_404(
    client: TestClient, student: dict[str, str]
):
    res = client.put(
        f"{BASE}/sessions/gone/breakpoints", json={"lines": [1]}, headers=student
    )

    assert res.status_code == 404


# -------------------------------------------------------------------- step


def test_step_requires_a_logged_in_user(client: TestClient):
    res = client.post(f"{BASE}/sessions/nope/step", json={"action": "run"})

    assert res.status_code == 401


def test_step_rejects_an_action_outside_the_allowlist(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """The action is looked up in ``STEPS`` and never handed to gdb as text.

    Asserted from the outside because this is the feature's one
    command-injection surface: a route that forwarded the string would
    happily accept ``shell rm -rf /``.
    """
    session = _built_session(work_root)
    called: list[str] = []

    with patch.object(debug_api, "command", lambda s, a: called.append(a)):
        res = client.post(
            f"{BASE}/sessions/{session.session_id}/step",
            json={"action": "shell rm -rf /"},
            headers=student,
        )

    assert res.status_code == 400
    # The important half: nothing reached the session.
    assert called == []


def test_step_rejects_an_unknown_action_with_a_400_not_a_500(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """A bad action is the *request* being wrong, not the server breaking."""
    session = _built_session(work_root)

    res = client.post(
        f"{BASE}/sessions/{session.session_id}/step",
        json={"action": "teleport"},
        headers=student,
    )

    assert res.status_code == 400
    assert "teleport" in res.json()["detail"]


def test_step_returns_the_whole_new_state(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """Every step answers with the full position, not a delta — that is what
    stops the panel's gutter from drifting away from what gdb honours."""
    session = _built_session(work_root)

    with patch.object(debug_api, "command", lambda s, a: None):
        body = client.post(
            f"{BASE}/sessions/{session.session_id}/step",
            json={"action": "run"},
            headers=student,
        ).json()

    assert set(body) == {"session_id", "breakpoints", "built", "finished", "stop"}


def test_a_failed_step_keeps_the_session(
    client: TestClient, student: dict[str, str], work_root: Path
):
    """A timeout or a missing binary is worth reporting, but the session may
    still be good — dropping it would throw away the position the student
    is looking at."""

    def failing_command(session, action):
        raise DebugError("调试超时")

    session = _built_session(work_root)
    with patch.object(debug_api, "command", failing_command):
        res = client.post(
            f"{BASE}/sessions/{session.session_id}/step",
            json={"action": "next"},
            headers=student,
        )

    assert res.status_code == 400
    assert debug_store.get(session.session_id) is not None


def test_step_on_an_unknown_session_is_404(client: TestClient, student: dict[str, str]):
    res = client.post(
        f"{BASE}/sessions/gone/step", json={"action": "run"}, headers=student
    )

    assert res.status_code == 404


# ------------------------------------------------------------------- close


def test_close_requires_a_logged_in_user(client: TestClient):
    res = client.delete(f"{BASE}/sessions/nope")

    assert res.status_code == 401


def test_close_reports_that_it_closed_something(
    client: TestClient, student: dict[str, str], work_root: Path
):
    session = _built_session(work_root)

    body = client.delete(
        f"{BASE}/sessions/{session.session_id}", headers=student
    ).json()

    assert body == {"session_id": session.session_id, "closed": True}
    assert debug_store.get(session.session_id) is None


def test_closing_an_unknown_session_is_not_an_error(
    client: TestClient, student: dict[str, str]
):
    """Idempotent by design.

    The panel calls this on unmount, and a session can already be gone
    (expired, evicted). A 404 here would put an error on screen for a
    cleanup that succeeded — the client's intent, "make sure this is
    gone", is satisfied either way.
    """
    res = client.delete(f"{BASE}/sessions/nonexistent", headers=student)

    assert res.status_code == 200
    assert res.json()["closed"] is False


def test_closing_twice_reports_the_second_as_a_no_op(
    client: TestClient, student: dict[str, str], work_root: Path
):
    session = _built_session(work_root)

    first = client.delete(
        f"{BASE}/sessions/{session.session_id}", headers=student
    ).json()
    second = client.delete(
        f"{BASE}/sessions/{session.session_id}", headers=student
    ).json()

    assert first["closed"] is True
    assert second["closed"] is False
