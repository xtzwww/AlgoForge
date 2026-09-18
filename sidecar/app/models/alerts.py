"""Who needs attention, and why (阶段 6).

The console already answers "who is struggling" — the student list is
sorted worst-first. What it does not answer is *why*, and a teacher who
has to open four students to find out which of them is actually in
trouble will stop opening any of them.

So an alert is a **rule that fired**, carrying the numbers that fired it.
Not a score: this project's stated position is that a single number the
teacher cannot interrogate is a verdict, and the whole point of the
process work (C1) is that a mark has to be explainable. Every
:attr:`Alert.reason` below contains the counts behind it, because "张三
需要关注" is not actionable and "张三最近 5 次提交都没通过" is.

Every rule is also deliberately **narrow and literal**. A model that
weighed ten signals would be more accurate and completely unusable: the
teacher could not tell whether to trust it, and a ranking they cannot
audit is one they will ignore.

Why no alert for "has not started"
----------------------------------
A student with zero submissions is visible in the roster as a row of
zeros, and the roster already flags that by including them. An alert is
for a *trajectory* — somebody who was working and stopped, or who keeps
failing — because that is the thing nobody notices in time. Alerting on
"never submitted" in week one would fire on the whole class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mistake_book import MistakeBookEntry
from app.models.recurrence import count_distinct_problems
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, User

#: Kept working, repeatedly, and getting nowhere.
ALERT_STUCK = "stuck"
#: Was submitting, then stopped.
ALERT_SILENT = "silent"
#: The same root cause on several different problems.
ALERT_REPEATING = "repeating"

#: How many consecutive non-AC submissions count as stuck.
STUCK_RUNS = 5
#: Days without a submission before that is worth mentioning.
SILENT_DAYS = 7
#: How many distinct problems a cause must appear on.
REPEAT_PROBLEMS = 3

#: Sort weight, not shown to anyone. Repeating ranks first because it is
#: the only one of the three a teacher can act on directly — it names the
#: thing to teach. Stuck outranks silent because silence might be a
#: holiday.
_WEIGHT = {ALERT_REPEATING: 3, ALERT_STUCK: 2, ALERT_SILENT: 1}


@dataclass(frozen=True)
class Alert:
    """One student, one reason.

    Attributes:
        user_id / display_name: Who.
        kind: One of :data:`ALERT_STUCK` / :data:`ALERT_SILENT` /
            :data:`ALERT_REPEATING`.
        reason: One Chinese sentence, carrying the numbers that fired the
            rule. Never a score.
    """

    user_id: int
    display_name: str
    kind: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "reason": self.reason,
        }


def compute_alerts(
    session: Session,
    *,
    now: Optional[datetime] = None,
    stuck_runs: int = STUCK_RUNS,
    silent_days: int = SILENT_DAYS,
    repeat_problems: int = REPEAT_PROBLEMS,
) -> list[Alert]:
    """Every student with something to say about them, worst first.

    Args:
        session: A SQLAlchemy session.
        now: "Today", injectable so a test does not have to wait a week.
        stuck_runs / silent_days / repeat_problems: The thresholds.
            Parameters rather than constants-in-code because the right
            values depend on how long the course has been running, and a
            number that cannot be changed without a deploy is a number
            nobody will ever tune.

    Returns:
        Alerts sorted by how directly a teacher can act on them, then by
        name. One student can appear more than once — being stuck and
        being silent are different problems with different fixes, and
        collapsing them into "at risk" would hide which one it is.
    """
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)

    students = (
        session.execute(select(User).where(User.role == ROLE_STUDENT))
        .scalars()
        .all()
    )

    alerts: list[Alert] = []
    for student in students:
        recent = (
            session.execute(
                select(Submission)
                .where(Submission.user_id == student.id)
                .order_by(Submission.id.desc())
                .limit(stuck_runs)
            )
            .scalars()
            .all()
        )
        if not recent:
            continue

        if len(recent) >= stuck_runs and all(s.status != "AC" for s in recent):
            alerts.append(
                Alert(
                    user_id=student.id,
                    display_name=student.display_name,
                    kind=ALERT_STUCK,
                    reason=f"最近 {len(recent)} 次提交都没通过，一次也没过",
                )
            )

        last_at = recent[0].created_at
        if last_at is not None:
            days = (now - last_at).days
            if days >= silent_days:
                alerts.append(
                    Alert(
                        user_id=student.id,
                        display_name=student.display_name,
                        kind=ALERT_SILENT,
                        reason=f"已经 {days} 天没有新的提交",
                    )
                )

        for label, count in _repeated_labels(session, student.id, repeat_problems):
            alerts.append(
                Alert(
                    user_id=student.id,
                    display_name=student.display_name,
                    kind=ALERT_REPEATING,
                    # The actual count, not the threshold: "3 道以上" would
                    # read the same for a student who hit it on three
                    # problems and one who hit it on nine, and those are
                    # two very different conversations.
                    reason=f"「{label}」这个错因在 {count} 道不同的题里反复出现",
                )
            )

    alerts.sort(key=lambda a: (-_WEIGHT.get(a.kind, 0), a.display_name, a.kind))
    return alerts


def _repeated_labels(
    session: Session, user_id: int, threshold: int
) -> list[tuple[str, int]]:
    """Root causes this student has hit on at least `threshold` problems.

    Returns ``(label_in_chinese, how_many_problems)`` pairs. The label is
    translated here rather than left to the client because the console is
    the only place this sentence is rendered, and a raw ``boundary`` in a
    teacher-facing sentence is the kind of leak that makes a demo look
    unfinished.

    Reuses :func:`app.models.recurrence.count_distinct_problems` rather
    than re-deriving it: "the same mistake on three different problems"
    is one question, and the student-facing nudge and this teacher-facing
    alert must agree on the answer. Two counts would drift, and nothing
    looks worse than a console contradicting the student's own feedback.
    """
    from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS

    rows = session.execute(
        select(MistakeBookEntry.agent_error_type.label("label"))
        .join(Submission, MistakeBookEntry.submission_id == Submission.id)
        .where(Submission.user_id == user_id)
        .where(MistakeBookEntry.agent_error_type.is_not(None))
        .group_by(MistakeBookEntry.agent_error_type)
    ).all()

    out: list[tuple[str, int]] = []
    for row in rows:
        raw = str(row.label)
        count = count_distinct_problems(session, user_id=user_id, error_type=raw)
        if count >= threshold:
            out.append((ROOT_CAUSE_DESCRIPTIONS.get(raw, raw), count))
    return out
