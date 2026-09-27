"""Authentication endpoints (B1).

POST /api/v1/auth/register {username, password, display_name?, role?} -> {token, user}
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

Registration (changed 2026-09-25)
---------------------------------
This module used to carry a paragraph headed "What is deliberately not
here" listing registration first, on the grounds that "an open
registration endpoint on a process that compiles arbitrary code is not
something to add before it is needed". It is now needed, so the endpoint
exists — and the reasoning that made it worth deferring is exactly why
the *policy* lives in its own module rather than inline here:

    app/registration.py

That module holds the parts that are security-relevant rather than
mechanical: the role is a whitelisted *parameter* (only the literal
``"teacher"`` can ask for a teacher account, and only while
``ALLOW_TEACHER_REGISTRATION`` is on — anything else is a student),
the whole door has an on/off switch (``ALLOW_OPEN_REGISTRATION``),
credentials have a floor, and attempts are rate limited per client.
Read it before changing anything about how accounts get created.

Still deliberately absent: password reset and email. Each is a real
feature with its own storage and failure modes, and none is required by
"a student can sign up". An unverified email field in particular would
look like a control while being none. Role self-service is *present*
since 2026-09-25 — the teacher web entry's sign-up page creates a
teacher account — and is governed by the switch above, not by the route.

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import delete

from app.auth import (
    UserSession,
    authenticate,
    create_user,
    issue_session,
    user_for_token,
)
from app.db import get_session
from app.models.user import ROLE_TEACHER, User
from app.registration import (
    RegistrationError,
    get_throttle,
    open_registration_enabled,
    registration_role,
    validate_display_name,
    validate_password,
    validate_username,
)

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


class RegisterRequest(BaseModel):
    """A self-service sign-up.

    ``role`` is what the client *asks* for, and nothing more. The only
    value that can produce a teacher account is the literal ``"teacher"``
    (case- and space-insensitive), and it is honoured only while
    ``ALLOW_TEACHER_REGISTRATION`` is on — the actual decision lives in
    :func:`app.registration.registration_role`, which also rejects any
    spelling that is not exactly ``teacher`` and any role this product
    does not have (those fall back to a student). Absent means
    "a student, please", which is the default.

    ``display_name`` is optional and blank means "use the username", so
    a student who does not want to type their real name is not blocked.
    """

    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=64)
    role: str = Field(default="student", max_length=16)


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


def _client_key(request: Request) -> str:
    """The throttle bucket for a request.

    ``request.client`` is ``None`` for a request that never had a socket
    — an ASGI test transport, for instance. Those all share one bucket
    rather than being exempt, because exempting them would mean the
    rate limit could be bypassed by anything that could avoid setting a
    peer address.
    """
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/register", response_model=LoginResponse, status_code=201)
def register(req: RegisterRequest, request: Request) -> LoginResponse:
    """Create an account and sign it in.

    The account is a student one by default; a request for ``role=
    "teacher"`` is honoured only while ``ALLOW_TEACHER_REGISTRATION`` is
    on (the teacher web entry's sign-up page is the caller that sends
    it). Everything else is decided in :mod:`app.registration`.

    Returns the same shape as :func:`login`, including a session token,
    because the alternative — create, then make the client post its own
    credentials straight back — is a second round trip and a second
    place for the password to be handled.

    Status codes, and why each is distinct:

    * **403** — self-registration is switched off
      (``ALLOW_OPEN_REGISTRATION=false``), or a teacher account was
      asked for while ``ALLOW_TEACHER_REGISTRATION=false``. A different
      thing from a bad request: the client should not retry with
      different data.
    * **429** — too many accounts created from this client recently. The
      form shows the retry delay from the message.
    * **400** — the username or password does not meet the floor in
      :mod:`app.registration`. The message names which.
    * **409** — the username is taken. Separate from 400 because the
      client can do something about it (pick another name), and the
      form highlights the username field for exactly this case.
    """
    if not open_registration_enabled():
        raise HTTPException(
            status_code=403,
            detail="本机已关闭自助注册，请联系老师创建账号",
        )

    throttle = get_throttle()
    key = _client_key(request)
    # Checked before validation on purpose: an over-budget client should
    # be told so regardless of whether the body it sent was well formed.
    # Nothing is recorded here — see the module docstring on why only
    # successful registrations count against the budget.
    try:
        throttle.check(key)
    except RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    try:
        username = validate_username(req.username)
        password = validate_password(req.password)
        display_name = validate_display_name(req.display_name)
        # The whitelist + switch decision, made here rather than at the
        # call site below. Raises 403 when a teacher account is asked for
        # while ALLOW_TEACHER_REGISTRATION is off.
        role = registration_role(req.role)
    except RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    try:
        with get_session() as session:
            user = create_user(
                session,
                username=username,
                password=password,
                role=role,
                display_name=display_name,
            )
            token = issue_session(session, user)
            out = _user_out(user)
    except ValueError:
        # create_user raises ValueError only for a taken username or an
        # unknown role; the role came from registration_role's whitelist,
        # so this is the username. 409 rather than 400, matching the
        # teacher's add_student — the client can pick another name.
        raise HTTPException(status_code=409, detail="该用户名已被占用") from None

    throttle.record(key)
    return LoginResponse(token=token, user=out)


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
