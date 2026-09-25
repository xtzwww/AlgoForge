"""Classroom endpoints.

Teacher side — all teacher-only, and all scoped to classes they own:
    POST   /api/v1/teacher/classrooms
    GET    /api/v1/teacher/classrooms
    GET    /api/v1/teacher/classrooms/{class_id}/members
    DELETE /api/v1/teacher/classrooms/{class_id}/members/{user_id}
    DELETE /api/v1/teacher/classrooms/{class_id}

Student side:
    POST   /api/v1/classrooms/join
    GET    /api/v1/classrooms/mine

Why a class nobody owns is not a 403
------------------------------------
A teacher asking for another teacher's class gets **404**, not 403. The
403 would confirm the class exists, and one teacher has no business
knowing the shape of another's roster — not even that it is there. The
role check (`require_teacher`) still answers 403, because that one is
about *who you are*, which the caller already knows.

Why joining is idempotent
-------------------------
A student who types the code twice, or reloads and submits again, gets
the class back with ``already_member: true`` rather than an error. There
is nothing they did wrong, and a failure would send them looking for a
mistake that is not theirs.

Why a teacher cannot join
-------------------------
Membership means "this person is taught here", and the roster feeds
every per-student number in the console. Letting a teacher in would put
them in their own class list and quietly count them as a student who
never submits — the one signal the console exists to surface.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.api.auth import require_teacher, require_user
from app.db import get_session
from app.models.assignment import Assignment
from app.models.classroom import ClassMember, Classroom, new_invite_code
from app.models.user import ROLE_STUDENT, User

router = APIRouter(prefix="/api/v1", tags=["classrooms"])

#: How many times to re-roll an invite code before giving up. With a
#: 31-symbol alphabet and six characters the collision chance is
#: negligible, so this bound exists to make an infinite loop impossible
#: rather than to be reached.
_CODE_ATTEMPTS = 20


class ClassroomOut(BaseModel):
    id: int
    name: str
    teacher_id: int
    invite_code: str
    member_count: int = 0
    created_at: Optional[str] = None


class MemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    joined_at: Optional[str] = None


class CreateClassroomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class JoinClassroomRequest(BaseModel):
    """The code a student types.

    Normalised in the route rather than validated as uppercase here: a
    lowercase entry is not a malformed request, it is the same code with
    the shift key up, and answering 422 to that would be pedantry the
    student cannot act on.
    """

    invite_code: str = Field(min_length=1, max_length=16)


class JoinClassroomResult(BaseModel):
    classroom: ClassroomOut
    already_member: bool = False


def _member_count(session, class_id: int) -> int:
    """Roster size for one class."""
    return int(
        session.execute(
            select(func.count(ClassMember.id)).where(ClassMember.class_id == class_id)
        ).scalar()
        or 0
    )


def _member_counts(session, class_ids: list[int]) -> dict[int, int]:
    """Roster sizes for several classes, in one query.

    A count per class in a loop would be N round trips for a screen that
    is always rendering the whole list — the same shape of cost the
    database layer already avoids elsewhere.
    """
    if not class_ids:
        return {}
    rows = session.execute(
        select(ClassMember.class_id, func.count(ClassMember.id))
        .where(ClassMember.class_id.in_(class_ids))
        .group_by(ClassMember.class_id)
    ).all()
    return {int(class_id): int(count) for class_id, count in rows}


def _owned_class(session, class_id: int, teacher_id: int) -> Classroom:
    """The class, if this teacher owns it.

    Raises:
        HTTPException: 404 for both "no such class" and "not yours" —
            see the module docstring for why they are not distinguished.
    """
    row = session.get(Classroom, class_id)
    if row is None or row.teacher_id != teacher_id:
        raise HTTPException(status_code=404, detail="classroom not found")
    return row


def _unique_invite_code(session) -> str:
    """An invite code no other class is using."""
    for _ in range(_CODE_ATTEMPTS):
        code = new_invite_code()
        taken = session.execute(
            select(Classroom.id).where(Classroom.invite_code == code)
        ).scalar_one_or_none()
        if taken is None:
            return code
    # Unreachable in practice; the unique index is the real guarantee.
    raise HTTPException(
        status_code=503, detail="could not allocate an invite code, please retry"
    )


@router.post("/teacher/classrooms", status_code=201, response_model=ClassroomOut)
def create_classroom(
    req: CreateClassroomRequest, teacher: User = Depends(require_teacher)
) -> ClassroomOut:
    """Create a class and return its invite code.

    The code comes back in this response and nowhere else in the create
    flow: it is what the teacher reads out, so it belongs on the screen
    that follows the action that produced it.
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="班级名称不能为空")
    with get_session() as session:
        row = Classroom(
            name=name,
            teacher_id=teacher.id,
            invite_code=_unique_invite_code(session),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return ClassroomOut(**row.to_dict(member_count=0))


@router.get("/teacher/classrooms", response_model=list[ClassroomOut])
def list_classrooms(teacher: User = Depends(require_teacher)) -> list[ClassroomOut]:
    """This teacher's classes, newest first."""
    with get_session() as session:
        rows = (
            session.execute(
                select(Classroom)
                .where(Classroom.teacher_id == teacher.id)
                .order_by(Classroom.id.desc())
            )
            .scalars()
            .all()
        )
        counts = _member_counts(session, [r.id for r in rows])
        return [
            ClassroomOut(**r.to_dict(member_count=counts.get(r.id, 0))) for r in rows
        ]


@router.get("/teacher/classrooms/{class_id}/members", response_model=list[MemberOut])
def list_members(
    class_id: int, teacher: User = Depends(require_teacher)
) -> list[MemberOut]:
    """The roster, in join order."""
    with get_session() as session:
        _owned_class(session, class_id, teacher.id)
        rows = session.execute(
            select(User, ClassMember)
            .join(ClassMember, ClassMember.user_id == User.id)
            .where(ClassMember.class_id == class_id)
            .order_by(ClassMember.id)
        ).all()
        return [
            MemberOut(
                user_id=user.id,
                username=user.username,
                # Falls back to the login name for accounts imported
                # without one — the roster has to be readable either way.
                display_name=user.display_name or user.username,
                joined_at=member.joined_at.isoformat() if member.joined_at else None,
            )
            for user, member in rows
        ]


@router.delete("/teacher/classrooms/{class_id}/members/{user_id}", status_code=204)
def remove_member(
    class_id: int, user_id: int, teacher: User = Depends(require_teacher)
) -> None:
    """Take one student off the roster.

    Removes the membership only. Their submissions are history and stay
    attached to them — being taken off a roster is not the same as never
    having done the work, and the console's per-student numbers would
    become wrong if it were.
    """
    with get_session() as session:
        _owned_class(session, class_id, teacher.id)
        row = session.execute(
            select(ClassMember).where(
                ClassMember.class_id == class_id, ClassMember.user_id == user_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="not a member of this class")
        session.delete(row)
        session.commit()


@router.delete("/teacher/classrooms/{class_id}", status_code=204)
def delete_classroom(class_id: int, teacher: User = Depends(require_teacher)) -> None:
    """Delete a class.

    Assignments set for it are **unassigned, not deleted**: they become
    the "for everyone" kind, which keeps them readable. Deleting the
    homework along with the class would erase the record of work
    students actually did, and nothing on the console could explain where
    it went.
    """
    with get_session() as session:
        row = _owned_class(session, class_id, teacher.id)
        session.execute(
            update(Assignment)
            .where(Assignment.class_id == class_id)
            .values(class_id=None)
        )
        session.execute(delete(ClassMember).where(ClassMember.class_id == class_id))
        session.delete(row)
        session.commit()


@router.post("/classrooms/join", response_model=JoinClassroomResult)
def join_classroom(
    req: JoinClassroomRequest, user: User = Depends(require_user)
) -> JoinClassroomResult:
    """Join a class with an invite code.

    Errors:
        403: The caller is a teacher — see the module docstring.
        404: No class has that code. Reported as a 404 rather than a
            validation error because the request is well-formed; it
            simply names a code that is not in use.
    """
    if user.role != ROLE_STUDENT:
        raise HTTPException(status_code=403, detail="只有学生账号可以加入班级")

    code = req.invite_code.strip().upper()
    with get_session() as session:
        room = session.execute(
            select(Classroom).where(Classroom.invite_code == code)
        ).scalar_one_or_none()
        if room is None:
            raise HTTPException(status_code=404, detail="邀请码不存在，请向老师确认")

        existing = session.execute(
            select(ClassMember).where(
                ClassMember.class_id == room.id, ClassMember.user_id == user.id
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(ClassMember(class_id=room.id, user_id=user.id))
            session.commit()

        return JoinClassroomResult(
            classroom=ClassroomOut(
                **room.to_dict(member_count=_member_count(session, room.id))
            ),
            already_member=existing is not None,
        )


@router.get("/classrooms/mine", response_model=list[ClassroomOut])
def my_classrooms(user: User = Depends(require_user)) -> list[ClassroomOut]:
    """The classes this student has joined.

    Readable by any signed-in user rather than students only: a teacher
    calling it gets an empty list, which is the truth about a teacher's
    memberships, and is a smaller surprise than a 403 on a page the shell
    renders for everyone.
    """
    with get_session() as session:
        rows = (
            session.execute(
                select(Classroom)
                .join(ClassMember, ClassMember.class_id == Classroom.id)
                .where(ClassMember.user_id == user.id)
                .order_by(Classroom.id.desc())
            )
            .scalars()
            .all()
        )
        counts = _member_counts(session, [r.id for r in rows])
        return [
            ClassroomOut(**r.to_dict(member_count=counts.get(r.id, 0))) for r in rows
        ]
