"""User accounts — who a submission belongs to (B1).

Why this model had to exist before the teacher console
------------------------------------------------------
The teacher side of the product is entirely about *other people's*
learning: class-wide mastery, who is struggling, which mistakes are
common. None of those questions can be asked of a schema whose every
row silently belongs to one unnamed person, which is what this database
was until now — ``submissions`` had no owner column at all, and the
profile and mistake-book APIs aggregate over everything.

That makes the user table a prerequisite of the teacher console rather
than a feature that follows it, even though the roadmap lists "user
system" later. The roadmap's ordering was written before the teacher
side was designed.

What this is not
----------------
There is no email, no password reset, no self-registration, no roles
beyond the two the product actually distinguishes. A teaching tool used
by one class on one machine does not need an identity provider, and
every field added here is a field that has to be explained in a
demonstration. Accounts are created by the teacher (or seeded), which is
also the honest description of how a class roster is produced.

Passwords are hashed but not argon2/bcrypt
------------------------------------------
``hashlib.pbkdf2_hmac`` is used deliberately instead of adding a
dependency: it is in the standard library, it is the construction the
platform's own ``hashlib`` documents for password storage, and 200,000
iterations of SHA-256 puts the cost of an offline attack on a stolen
database where it needs to be for this threat model. bcrypt would be a
better answer to a question this system is not being asked — it holds
passwords for a locally-installed teaching tool, not credentials for a
service with money in it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: The two roles the product distinguishes. Anything else would be a
#: third kind of thing to design screens for.
ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLES: tuple[str, ...] = (ROLE_STUDENT, ROLE_TEACHER)


class User(Base):
    """One person who uses the system.

    Attributes:
        id: Primary key.
        username: Login name. Unique, because it is the one thing a
            person types to identify themselves and two people with the
            same one is not a situation with a good resolution.
        display_name: What the teacher sees. Separate from the login
            name because a roster is read by a human and sorted by a
            human, and ``"zhaoliu2025"`` is not how a teacher thinks
            about a student.
        role: ``"student"`` or ``"teacher"``.
        password_hash: Hex digest; see the module docstring.
        password_salt: Per-user salt, stored beside the hash because it
            is not a secret — it is what makes one user's hash useless
            against another's.
        created_at: Server timestamp.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=""
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ROLE_STUDENT, server_default=ROLE_STUDENT
    )
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Serialize for the API — never including the hash or the salt.

        The two credential fields are omitted rather than filtered
        downstream: a ``to_dict`` that exports them is one careless
        ``{**row.to_dict()}`` away from putting password hashes in a
        response, and there is no reason for them to ever be exported.
        """
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r} role={self.role!r}>"
