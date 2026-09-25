"""Classes and membership.

Why a membership table rather than a ``users.class_id`` column
--------------------------------------------------------------
A column on ``users`` is smaller and would answer "which class is this
student in" with no join at all. It is not used here because of what it
cannot express: a 专升本 student takes several courses in one term, and a
single column forces them into one of them. The column also makes
"leave this class" a write to the *user* row — the roster edit and the
account edit become the same operation, which is exactly the coupling
that makes a mistake in one show up as a bug in the other.

What an invite code is and is not
---------------------------------
It is a door label, not a password: six characters that identify a
class, and the only thing knowing one grants is the ability to be
counted in it. That is why it is stored in plain text, printed on a
projector, and never treated as a credential. A student who guesses a
code joins a class they were not in, which the teacher sees in the
roster and removes — a nuisance, not a breach.

The alphabet leaves out ``I``, ``L``, ``O``, ``0`` and ``1`` because the
code is read off a screen and typed by hand, and those five are the
pairs people misread.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Symbols an invite code may contain. See the module docstring.
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

#: Code length. Six characters over 31 symbols is ~30 bits — far too
#: short to be a secret, which is the point: it is an identifier with a
#: pleasant input cost, not a credential.
INVITE_CODE_LENGTH = 6


def new_invite_code() -> str:
    """Generate a fresh invite code.

    ``secrets`` rather than ``random``: the two are the same speed here,
    and reaching for the one that is safe by default means nobody has to
    re-derive whether this call site needed it.
    """
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


class Classroom(Base):
    """A group of students taught by one teacher.

    Attributes:
        id: Primary key.
        name: What the teacher calls it — ``"25 软件工程技术 2 班"``.
        teacher_id: Who owns it. Creating an assignment for it, editing
            its roster and deleting it are all checked against this, so
            the class has one owner rather than a permission list.
        invite_code: What a student types to join. Unique, because two
            classes sharing a code would put a student in whichever one
            the lookup happened to reach first — and that would be a
            coin flip no screen could explain.
        created_at: Server timestamp.
    """

    __tablename__ = "classrooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    teacher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    invite_code: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_dict(self, *, member_count: Optional[int] = None) -> dict:
        """Serialize for the API.

        Args:
            member_count: Roster size, when the caller has already
                counted it. Omitted rather than defaulted to 0 — a
                missing count and an empty class are different facts,
                and a ``0`` the caller never computed would read as the
                second.
        """
        out = {
            "id": self.id,
            "name": self.name,
            "teacher_id": self.teacher_id,
            "invite_code": self.invite_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if member_count is not None:
            out["member_count"] = int(member_count)
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Classroom id={self.id} name={self.name!r}>"


class ClassMember(Base):
    """One student's membership of one class.

    Attributes:
        id: Primary key.
        class_id: Which class.
        user_id: Which student.
        joined_at: When they joined. Kept because a roster is read in
            join order when nobody has been sorted yet, and because
            "joined after the assignment was published" is a question a
            teacher does ask.

    The ``(class_id, user_id)`` unique constraint is what makes joining
    idempotent: a student who submits the invite code twice, or refreshes
    the page and submits again, gets one row and a friendly answer rather
    than a duplicate that makes the roster count wrong.
    """

    __tablename__ = "class_members"
    __table_args__ = (UniqueConstraint("class_id", "user_id", name="uq_class_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("classrooms.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ClassMember class={self.class_id} user={self.user_id}>"
