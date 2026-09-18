"""How the class is doing on one assignment (阶段 4).

The question a teacher actually asks about homework is not "what is the
average score" — this system has no scores. It is **"who has not done
it"**, and that is a question about absence, so it has to be answered by
subtraction: the roster minus the people who submitted something.

That is why :func:`compute_assignment_stats` returns two lists and not
one number. A percentage would hide the only actionable part: "21 人已交"
sounds fine right up until the class has 24 people and the three who
have not submitted are the three who were already struggling last week.

What is counted as "submitted"
------------------------------
Any recorded submission on one of the assignment's problems, whatever
the verdict. An attempt that failed is still an attempt, and counting
only accepted ones would list a student who tried twelve times and never
passed right next to one who never opened the problem — which is exactly
the distinction a teacher is looking at this screen to draw.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.assignment import Assignment
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, User


@dataclass(frozen=True)
class ProblemStat:
    """One problem inside the assignment.

    Attributes:
        problem_id: Which problem.
        submissions / accepted: Across everyone, on this problem only.
        accuracy: ``accepted / submissions``, 0.0 when nobody tried.
        students: How many distinct people tried it.
    """

    problem_id: int
    submissions: int
    accepted: int
    accuracy: float
    students: int

    def to_dict(self) -> dict:
        return {
            "problem_id": self.problem_id,
            "submissions": self.submissions,
            "accepted": self.accepted,
            "accuracy": self.accuracy,
            "students": self.students,
        }


@dataclass(frozen=True)
class AssignmentStats:
    """One assignment, as a teacher needs it.

    Attributes:
        assignment: The assignment itself.
        problems: One :class:`ProblemStat` per problem, *in the order
            the teacher listed them* — not sorted by anything. The order
            they chose is the order they will look for.
        students_who_submitted: Distinct people with at least one
            submission on any of the problems.
        students_missing: The rest of the roster, by name. This is the
            actionable part of the screen, which is why it is a list of
            people and not a count.
    """

    assignment: dict
    problems: list[ProblemStat]
    students_who_submitted: int
    students_missing: list[str]

    def to_dict(self) -> dict:
        return {
            "assignment": self.assignment,
            "problems": [p.to_dict() for p in self.problems],
            "students_who_submitted": self.students_who_submitted,
            "students_missing": self.students_missing,
        }


def compute_assignment_stats(session: Session, assignment_id: int) -> AssignmentStats:
    """Everything about one assignment, or ``None``-safe empties.

    Args:
        session: A SQLAlchemy session.
        assignment_id: Which assignment.

    Returns:
        The stats. An assignment nobody has touched is a valid result
        with zeros and the whole roster missing — not an error, and not
        a row that vanishes from the list.

    Raises:
        LookupError: No such assignment. The route turns this into a
            404; raised here rather than returning an empty object
            because "this assignment does not exist" and "nobody has
            submitted" are completely different answers and must not
            look alike to the caller.
    """
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise LookupError(f"no assignment with id {assignment_id}")

    problem_ids = [int(pid) for pid in (assignment.problem_ids or [])]

    problems: list[ProblemStat] = []
    for problem_id in problem_ids:
        row = session.execute(
            select(
                func.count().label("submissions"),
                func.count(func.distinct(Submission.user_id)).label("students"),
            )
            .select_from(Submission)
            .where(Submission.problem_id == problem_id)
        ).one()
        accepted = session.execute(
            select(func.count())
            .select_from(Submission)
            .where(Submission.problem_id == problem_id)
            .where(Submission.status == "AC")
        ).scalar_one()
        submissions = int(row.submissions or 0)
        accepted_count = int(accepted or 0)
        problems.append(
            ProblemStat(
                problem_id=problem_id,
                submissions=submissions,
                accepted=accepted_count,
                accuracy=round(accepted_count / submissions, 4) if submissions else 0.0,
                students=int(row.students or 0),
            )
        )

    if problem_ids:
        people = session.execute(
            select(func.distinct(Submission.user_id))
            .select_from(Submission)
            .where(Submission.problem_id.in_(problem_ids))
            .where(Submission.user_id.is_not(None))
        ).scalars().all()
    else:
        people = []
    submitted = {int(uid) for uid in people}

    roster = (
        session.execute(select(User).where(User.role == ROLE_STUDENT))
        .scalars()
        .all()
    )
    missing = [
        student.display_name or student.username
        for student in roster
        if student.id not in submitted
    ]

    return AssignmentStats(
        assignment=assignment.to_dict(),
        problems=problems,
        students_who_submitted=len(submitted),
        students_missing=sorted(missing),
    )
