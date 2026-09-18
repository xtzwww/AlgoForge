"""Password hashing, login, and session tokens (B1).

Three decisions worth recording, because each has an obvious-looking
alternative that is worse:

**Why PBKDF2 and not a third-party hasher.** See
:mod:`app.models.user`. Short version: it is in the standard library, it
is the documented construction for this job, and adding bcrypt would be
answering a threat model this system does not have.

**Why sessions are rows, not signed tokens.** A stateless signed token
needs a signing secret that survives a restart. The obvious place to
keep one is ``SIDECAR_TOKEN``, which is generated *randomly at every
start* — so every restart would log out every user, and a teacher in the
middle of a class would be blamed for it. A session row needs no secret
at all, is revocable, and can carry an expiry that is a real column
rather than a claim inside a blob nobody has read.

**Why login answers the same way for every failure.** "No such user" and
"wrong password" are reported identically. Distinguishing them turns the
login form into a username oracle, which is how an attacker confirms
which accounts exist before guessing at them. The cost is a slightly
less helpful error message for someone who mistyped their own name.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base
from app.models.user import ROLE_STUDENT, ROLES, User

#: PBKDF2 rounds. Raised from the 1990s-era defaults to something that
#: makes an offline attack on a stolen database actually cost something;
#: at ~100 ms per login it is still invisible to a person at a keyboard.
_ROUNDS = 200_000

#: How long a session stays valid. Long enough that a lesson does not
#: expire mid-class, short enough that a shared machine is not a
#: permanent credential.
SESSION_TTL = timedelta(hours=12)


class UserSession(Base):
    """A logged-in user.

    Attributes:
        token: The bearer credential. Random, 32 bytes, never derived
            from anything about the user — a token that encodes its
            owner is a token that can be forged by editing it.
        user_id: Whose session it is.
        created_at / expires_at: When it was issued and when it stops
            being accepted.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


def new_salt() -> str:
    """A fresh per-user salt."""
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    """Derive the stored digest for *password*.

    Args:
        password: The plaintext. Not validated for strength here: what
            counts as an acceptable password is a policy question for
            whoever creates the account, and rejecting one at hash time
            would silently produce accounts that cannot be logged into.
        salt: The user's salt, from :func:`new_salt`.
    """
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _ROUNDS
    ).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    """Check a password against the stored digest.

    Compared with ``hmac.compare_digest`` rather than ``==``: a plain
    comparison returns as soon as it finds a differing byte, which leaks
    how much of the guess was right through timing.
    """
    return hmac.compare_digest(hash_password(password, salt), expected)


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: str = ROLE_STUDENT,
    display_name: str = "",
) -> User:
    """Create an account.

    Args:
        session: Session to write through.
        username: Login name; must be unused.
        password: Plaintext; hashed before it is stored.
        role: One of :data:`app.models.user.ROLES`.
        display_name: Optional human-facing name. Defaults to the
            username rather than being required, because a roster
            imported without names is still a usable roster.

    Raises:
        ValueError: The username is taken, or the role is unknown. Both
            are caller errors, and both are checked before anything is
            written so a failure cannot leave a half-built row.
    """
    username = username.strip()
    if not username:
        raise ValueError("username must not be empty")
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")

    existing = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"username {username!r} is already taken")

    salt = new_salt()
    user = User(
        username=username,
        display_name=display_name or username,
        role=role,
        password_hash=hash_password(password, salt),
        password_salt=salt,
    )
    session.add(user)
    session.commit()
    return user


def authenticate(session: Session, username: str, password: str) -> Optional[User]:
    """Look up a user by credential, or return ``None``.

    Returns ``None`` for both "no such user" and "wrong password" — see
    the module docstring on why they are not distinguished.
    """
    user = session.execute(
        select(User).where(User.username == username.strip())
    ).scalar_one_or_none()
    if user is None:
        return None
    if not verify_password(password, user.password_salt, user.password_hash):
        return None
    return user


def issue_session(session: Session, user: User) -> str:
    """Create a session for *user* and return its token."""
    token = secrets.token_hex(32)
    session.add(
        UserSession(
            token=token,
            user_id=user.id,
            expires_at=datetime.now() + SESSION_TTL,
        )
    )
    session.commit()
    return token


def user_for_token(session: Session, token: str) -> Optional[User]:
    """The user a bearer token belongs to, or ``None``.

    ``None`` covers unknown tokens and *expired* ones — an expired
    session is not a session, and answering "unknown" is the same
    action for the caller either way (ask them to log in again).
    """
    if not token:
        return None
    row = session.execute(
        select(UserSession).where(UserSession.token == token)
    ).scalar_one_or_none()
    if row is None or row.expires_at < datetime.now():
        return None
    return session.get(User, row.user_id)
