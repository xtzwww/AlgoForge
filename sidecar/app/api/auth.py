"""Authentication endpoints (B1).

POST /api/v1/auth/login   {username, password} -> {token, user}
POST /api/v1/auth/logout  -> {ok: true}
GET  /api/v1/auth/me      -> {user}

Why a bearer token and not a cookie
-----------------------------------
The Electron renderer is not a page on an origin the browser considers
trustworthy — in production it loads from ``file://`` — and the sidecar
is already called with an explicit credential (``SIDECAR_TOKEN``) on
both its HTTP and WebSocket entry points. One more bearer header is the
shape that fits what is already there; cookies would need CSRF defence
for a threat that does not exist in a desktop app.

What is deliberately not here
-----------------------------
Registration, password reset, email, and role self-service. Accounts are
created by a teacher (``POST /api/v1/teacher/students``), which is both
the honest description of how a class roster is produced and a smaller
attack surface: an open registration endpoint on a process that compiles
arbitrary code is not something to add before it is needed.

The token is not the sidecar token
----------------------------------
``SIDECAR_TOKEN`` authenticates the *app* to the sidecar and is shared by
every client; this token authenticates one *person* and is what makes the
teacher's view possible. They solve different problems and neither can
stand in for the other — knowing the app's token must not make you a
teacher.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import delete

from app.api.mistake_book import get_session
from app.auth import UserSession, authenticate, issue_session, user_for_token
from app.models.user import ROLE_TEACHER, User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

#: ``auto_error=False`` so an absent credential yields ``None`` rather
#: than FastAPI answering 403 before the route runs. Several routes are
#: readable while logged out (the problem catalogue, for instance), and a
#: dependency that refuses to be optional would force every one of them
#: to be behind a login.
_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class MeResponse(BaseModel):
    user: UserOut


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[User]:
    """The logged-in user, or ``None``.

    Optional on purpose. The desktop client has worked without any
    account since the first slice, and a login wall would break every
    existing screen at once. Routes that need a user — and routes that
    need a *teacher* — say so themselves via :func:`require_user` and
    :func:`require_teacher`, so the requirement is visible at the route
    that has it rather than imposed globally.

    Returns:
        The user, or ``None`` for an absent, unknown, or expired token.
        All three are the same decision for the caller: treat the request
        as unauthenticated.
    """
    if credentials is None:
        return None
    with get_session() as session:
        return user_for_token(session, credentials.credentials)


def require_user(user: Optional[User] = Depends(current_user)) -> User:
    """Like :func:`current_user`, but 401 when there is nobody."""
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_teacher(user: User = Depends(require_user)) -> User:
    """The logged-in user, guaranteed to be a teacher.

    403 rather than 401 for a student: they *are* authenticated, they are
    just not allowed here, and conflating the two would send a student's
    client back to the login screen in a loop it cannot escape.
    """
    if user.role != ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="Teacher role required")
    return user


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest) -> LoginResponse:
    """Exchange a username and password for a session token.

    Every failure answers 401 with the same message. Distinguishing
    "no such user" from "wrong password" would turn this endpoint into a
    username oracle, and the extra help it gives a person who mistyped
    their own name is not worth that.
    """
    with get_session() as session:
        user = authenticate(session, req.username, req.password)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        token = issue_session(session, user)
        return LoginResponse(token=token, user=_user_out(user))


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(require_user)) -> MeResponse:
    """Who the supplied token belongs to.

    Exists so a client can re-establish the session on startup without
    storing the user's own profile — the token is the only thing it has
    to keep, and this is how it finds out what that token is worth.
    """
    return MeResponse(user=_user_out(user))


@router.post("/logout")
def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    user: User = Depends(require_user),
) -> dict:
    """End *this* session.

    The token is taken from the credential rather than from the user,
    because "log out" must not sign the user out of their other devices
    — a teacher with the console open on a projector and on their own
    laptop should not be logged out of both by closing one.

    Deleting the row rather than marking it expired: there is no
    "logged-out session" anything needs to read afterwards, and a row
    that lingers is one more credential that works.
    """
    with get_session() as session:
        session.execute(
            delete(UserSession).where(UserSession.token == credentials.credentials)
        )
        session.commit()
    return {"ok": True}
