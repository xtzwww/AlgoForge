"""Tests for accounts, sessions, and submission ownership (B1).

The teacher console cannot exist without this: every question it asks is
about *other people's* learning, and until this change the schema had no
way to say whose a submission was. That makes these tests the floor of
the teacher feature, not a side quest.

Two behaviours are pinned because they are the ones an attacker would
reach for first, and because both are easy to get backwards while
looking reasonable:

* login answers identically for "no such user" and "wrong password";
* a session token is opaque — it encodes nothing about its owner.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import (
    UserSession,
    authenticate,
    create_user,
    hash_password,
    issue_session,
    user_for_token,
    verify_password,
)
from app.main import app
from app.models.base import Base
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, ROLE_TEACHER


@pytest.fixture
def engine():
    """In-memory SQLite engine with the schema created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose auth routes use the test engine.

    ``get_session`` is patched on the module that *uses* it, not on
    ``app.db`` where it is defined — ``from app.api.mistake_book import
    get_session`` binds the function into the route module's namespace,
    so patching the definition would leave every route pointing at
    PostgreSQL.
    """
    from app.api import auth as auth_api

    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)

    with TestClient(app) as c:
        yield c


# --- Passwords ----------------------------------------------------------


def test_the_password_itself_is_never_stored(session: Session) -> None:
    """Only a derived digest lands in the row.

    The obvious failure here is a plain comparison against the
    plaintext — a test that only checks "login works" would pass on an
    implementation that stores passwords verbatim.
    """
    user = create_user(session, username="stu", password="correct horse")

    assert "correct horse" not in user.password_hash
    assert user.password_hash != "correct horse"
    assert user.password_salt


def test_the_same_password_hashes_differently_for_two_users() -> None:
    """A per-user salt, so one hash cannot be replayed against another.

    Two users with the same password are the common case in a class that
    was handed one password, and a shared salt would make that visible
    in the table.
    """
    salt_a = "a" * 32
    salt_b = "b" * 32
    assert hash_password("shared", salt_a) != hash_password("shared", salt_b)


def test_verify_rejects_a_near_miss() -> None:
    salt = "s" * 32
    assert verify_password("right", salt, hash_password("right", salt))
    assert not verify_password("Right", salt, hash_password("right", salt))


# --- Accounts -----------------------------------------------------------


def test_authenticate_accepts_the_right_password(session: Session) -> None:
    create_user(session, username="stu", password="pw123")
    assert authenticate(session, "stu", "pw123") is not None


def test_authenticate_returns_none_for_a_wrong_password(session: Session) -> None:
    create_user(session, username="stu", password="pw123")
    assert authenticate(session, "stu", "nope") is None


def test_a_missing_user_and_a_wrong_password_are_indistinguishable(
    session: Session,
) -> None:
    """Both are ``None``, and neither reveals which happened.

    Distinguishing them turns login into a username oracle: an attacker
    confirms which accounts exist, then spends their guesses only on
    those. The system gives up a little helpfulness to a person who
    mistyped their own name to avoid that.
    """
    create_user(session, username="stu", password="pw123")
    assert authenticate(session, "ghost", "pw123") is None
    assert authenticate(session, "stu", "wrong") is None


def test_a_duplicate_username_is_rejected(session: Session) -> None:
    """Two accounts with one login name has no good resolution."""
    create_user(session, username="stu", password="a")
    with pytest.raises(ValueError, match="already taken"):
        create_user(session, username="stu", password="b")


def test_an_unknown_role_is_rejected(session: Session) -> None:
    """A third role is a third kind of user to design screens for."""
    with pytest.raises(ValueError, match="unknown role"):
        create_user(session, username="x", password="a", role="principal")


def test_the_default_role_is_the_less_powerful_one(session: Session) -> None:
    """An account is a student unless something says otherwise.

    Fail-safe in the direction that matters: a role that defaults to
    teacher would hand a teacher console to anyone who made an account.
    """
    user = create_user(session, username="stu", password="a")
    assert user.role == ROLE_STUDENT


# --- Sessions -----------------------------------------------------------


def test_a_session_round_trips(session: Session) -> None:
    user = create_user(session, username="stu", password="a")
    token = issue_session(session, user)

    assert user_for_token(session, token) is not None
    assert user_for_token(session, token).id == user.id


def test_the_token_is_random_rather_than_derived(session: Session) -> None:
    """A token computed from its owner is a token anyone can compute.

    What is being guarded is not the exact bytes — a random hex string
    will contain the character ``"1"`` about half the time — but that the
    token is not a *function* of the user. Two tokens issued to the same
    user are different, which a derived token could not be, and the
    length is the full 32 bytes of entropy rather than something shorter
    that looked fine.
    """
    user = create_user(session, username="stu", password="a")
    first = issue_session(session, user)
    second = issue_session(session, user)

    assert first != second
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_an_unknown_token_resolves_to_nobody(session: Session) -> None:
    assert user_for_token(session, "not-a-token") is None
    assert user_for_token(session, "") is None


def test_an_expired_session_is_not_a_session(session: Session) -> None:
    """Expiry is checked on read, not swept by a background job.

    There is no sweeper in this system, so a token past its expiry has
    to be rejected at the point of use or it is valid forever.
    """
    user = create_user(session, username="stu", password="a")
    token = issue_session(session, user)

    row = session.execute(
        select(UserSession).where(UserSession.token == token)
    ).scalar_one()
    row.expires_at = datetime.now() - timedelta(seconds=1)
    session.commit()

    assert user_for_token(session, token) is None


# --- Endpoints ----------------------------------------------------------


def test_login_returns_a_token(client: TestClient, session: Session) -> None:
    create_user(session, username="stu", password="pw123", display_name="张三")

    resp = client.post(
        "/api/v1/auth/login", json={"username": "stu", "password": "pw123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["user"]["display_name"] == "张三"
    assert body["user"]["role"] == ROLE_STUDENT


def test_login_never_exports_the_credential(
    client: TestClient, session: Session
) -> None:
    """Neither the hash nor the salt appears anywhere in the response.

    A `to_dict()` that includes them is one careless spread away from
    putting password hashes on the wire, so this is asserted against the
    serialised body rather than against the model.
    """
    user = create_user(session, username="stu", password="pw123")
    body = client.post(
        "/api/v1/auth/login", json={"username": "stu", "password": "pw123"}
    ).json()

    assert user.password_hash not in str(body)
    assert user.password_salt not in str(body)


def test_a_bad_login_answers_401_for_both_kinds_of_failure(
    client: TestClient, session: Session
) -> None:
    """One status, one message, whether the name or the password is wrong.

    The endpoint's promise, not just the helper's: it would be easy for
    the route to add a friendlier "no such user" of its own and undo
    what :func:`authenticate` went out of its way to avoid.
    """
    create_user(session, username="stu", password="pw123")

    wrong_password = client.post(
        "/api/v1/auth/login", json={"username": "stu", "password": "bad"}
    )
    unknown_user = client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "pw123"}
    )

    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


def test_me_requires_a_session(client: TestClient, session: Session) -> None:
    create_user(session, username="stu", password="pw123")

    assert client.get("/api/v1/auth/me").status_code == 401

    token = client.post(
        "/api/v1/auth/login", json={"username": "stu", "password": "pw123"}
    ).json()["token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "stu"


def test_a_forged_token_is_rejected(client: TestClient, session: Session) -> None:
    create_user(session, username="stu", password="pw123")
    resp = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer made-up-token"}
    )
    assert resp.status_code == 401


def test_logout_ends_only_this_session(client: TestClient, session: Session) -> None:
    """Signing out on one device leaves the other signed in.

    A teacher with the console open on a projector and on their own
    laptop must not be logged out of both by closing one.
    """
    user = create_user(session, username="stu", password="pw123")
    here = issue_session(session, user)
    elsewhere = issue_session(session, user)

    assert (
        client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {here}"})
        .status_code
        == 200
    )
    assert user_for_token(session, here) is None
    assert user_for_token(session, elsewhere) is not None


# --- Ownership ----------------------------------------------------------


def test_a_recorded_submission_carries_its_owner(session: Session) -> None:
    """The link the whole teacher console is built on."""
    from app.records import SubmissionContext, record_submission
    from app.sandbox.judge import JudgeResult, JudgeStatus

    user = create_user(session, username="stu", password="a")
    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.AC),
        SubmissionContext(code="int main(){}", language="cpp", user_id=user.id),
        session=session,
    )

    row = session.get(Submission, submission_id)
    assert row is not None
    assert row.user_id == user.id


def test_a_submission_with_nobody_logged_in_is_still_recorded(
    session: Session,
) -> None:
    """Unowned is a legitimate state, not a reason to drop history.

    The desktop client has worked without an account since the first
    slice. Requiring one would either invent an owner or refuse to
    record, and losing a student's attempt is the worse outcome.
    """
    from app.records import SubmissionContext, record_submission
    from app.sandbox.judge import JudgeResult, JudgeStatus

    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.AC),
        SubmissionContext(code="int main(){}", language="cpp"),
        session=session,
    )

    row = session.get(Submission, submission_id)
    assert row is not None
    assert row.user_id is None


def test_a_teacher_can_be_created(session: Session) -> None:
    """The role the console's guard checks for."""
    user = create_user(session, username="teacher", password="a", role=ROLE_TEACHER)
    assert user.role == ROLE_TEACHER
