"""Seed a realistic, self-consistent demo dataset for showing the project.

Why this exists
---------------
The catalogue (100 problems / 657 cases) and the schema are real, but the
business tables are nearly empty: one classroom with one member, one
assignment, a dozen submissions and two mistake-book rows. Every teacher
page therefore renders *correctly but emptily* — the worst possible state
for a demo, because an empty console looks like a broken console.

This script fills the business tables so that every screen has something
true to show: a class of students, several assignments bound to real
problem sets, a body of submissions with a believable pass/fail mix, a
mistake book carrying valid root-cause labels, SM-2 review state, and a
handful of *explainable* process flags for the teacher's review queue.

What it does NOT do
-------------------
It never touches ``problems`` / ``test_cases``. The catalogue is loaded by
``tools/load_problems.py`` and verified by compiling every reference
solution; this script only *references* existing problem ids.

How it stays safe and repeatable
--------------------------------
* **Idempotent by marker.** Every row it creates is owned by a user whose
  username starts with ``demo_`` (or hangs off the demo classroom), so
  ``--reset`` deletes exactly what this script made and nothing else.
* **Refuses to guess ids.** It reads the live ``problems`` id range and
  fails loudly if the catalogue is empty — writing submissions against
  invented ids is the exact dangling-reference bug the project has been
  bitten by before.
* **Dry-run first.** ``--dry-run`` prints the whole plan and writes
  nothing.

Usage::

    python tools/seed_demo_data.py --dry-run     # show the plan
    python tools/seed_demo_data.py               # write
    python tools/seed_demo_data.py --reset       # remove demo rows, then stop
    python tools/seed_demo_data.py --reset --write   # remove, then reseed

The DSN comes from ``DATABASE_URL`` (same variable the sidecar uses); a
PostgreSQL DSN is the intended target because the aggregates are
dialect-sensitive.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import UserSession, hash_password, new_salt  # noqa: E402
from app.db import get_session  # noqa: E402
from app.eval_cases import ROOT_CAUSE_LABELS  # noqa: E402
from app.models.assignment import Assignment  # noqa: E402
from app.models.classroom import ClassMember, Classroom  # noqa: E402
from app.models.mistake_book import MistakeBookEntry  # noqa: E402
from app.models.problem import Problem  # noqa: E402
from app.models.problem_set import ProblemSet, ProblemSetItem  # noqa: E402
from app.models.review import ReviewRecord  # noqa: E402
from app.models.submission import (  # noqa: E402
    REVIEW_DISMISSED,
    Submission,
)
from app.models.user import ROLE_STUDENT, ROLE_TEACHER, User  # noqa: E402

# --------------------------------------------------------------------------
# Fixed identity of everything this script owns. The prefix is the reset key.
# --------------------------------------------------------------------------

DEMO_PREFIX = "demo_"
DEMO_PASSWORD = "demo1234"  # noqa: S105 - a demo credential, printed on purpose
DEMO_CLASS_NAME = "25 软件工程（专升本）2 班 · 程序设计基础"
DEMO_CLASS_INVITE = "DEMO01"
DEMO_TEACHER_USERNAME = f"{DEMO_PREFIX}teacher"

#: Fixed seed so two runs of the same code produce the same dataset — a
#: demo that changes shape every reseed is impossible to rehearse against.
RNG = random.Random(20261004)

#: Students, with a deliberately varied ability profile so the roster has a
#: top and a bottom and the alerts have someone to fire on. ``skill`` is the
#: probability of an AC on any given attempt; ``n`` is how many attempts.
STUDENTS: list[tuple[str, str, float, int]] = [
    ("demo_s01", "陈思远", 0.86, 22),  # strong, steady
    ("demo_s02", "林可欣", 0.78, 19),  # strong
    ("demo_s03", "黄浩然", 0.64, 20),  # middling
    ("demo_s04", "苏映雪", 0.58, 17),  # middling
    ("demo_s05", "周子墨", 0.42, 21),  # struggling
    ("demo_s06", "吴嘉言", 0.30, 16),  # struggling; will trip "stuck"
    ("demo_s07", "郑一诺", 0.72, 12),  # decent, but went quiet
    ("demo_s08", "何若曦", 0.50, 8),   # light activity
]

#: Root causes a struggling student plausibly repeats. Ordered by how
#: common each is in a real intro class, so the error distribution chart
#: has a believable head and tail rather than a flat twelve.
_COMMON_CAUSES = [
    "off_by_one",
    "boundary_condition",
    "missing_case",
    "uninitialized_state",
    "wrong_algorithm",
    "io_format_error",
    "wrong_traversal",
    "inverted_condition",
    "overflow",
    "wrong_complexity",
    "misread_problem",
    "wrong_data_type",
]
# Weighted so the first few dominate. Weights sum to whatever; used by
# RNG.choices.
_CAUSE_WEIGHTS = [18, 16, 13, 11, 10, 8, 6, 5, 4, 4, 3, 2]


#: Per-label demo prose, written in the exact format
#: ``app.records_suggestion._format_suggestion`` produces so the split at
#: read time yields the three sections the student UI releases one at a
#: time (【错在哪】/【提示】/【怎么改】). Generic filler here would make
#: every mistake look identical in the demo, which is worse than showing
#: fewer of them.
_LABEL_PROSE: dict[str, str] = {
    "misread_problem": (
        "【错在哪】程序逻辑本身没有明显错误，但求解的目标与题目要求不一致——"
        "把「求最大值」当成了「求最小值」这一类。\n"
        "【提示】先把题目要求的输入输出各抄一遍，圈出动词。\n"
        "【怎么改】对照样例数据确认输出含义后再改比较方向。"
    ),
    "wrong_complexity": (
        "【错在哪】解法思路正确，但复杂度超出限制，规模稍大的用例会超时。\n"
        "【提示】数一数最坏情况下内层循环执行了多少次。\n"
        "【怎么改】尝试用哈希表或排序把嵌套循环降一维。"
    ),
    "wrong_algorithm": (
        "【错在哪】所选的算法类型不适用于本题的数据范围，换一种遍历方式也救不回来。\n"
        "【提示】想一想题目是否具有「最优子结构」或「重叠子问题」。\n"
        "【怎么改】把贪心替换为动态规划，明确状态与转移方程。"
    ),
    "missing_case": (
        "【错在哪】漏写了必须处理的一个分支，某类输入直接没有进入任何判断。\n"
        "【提示】列出所有可能的输入类别，逐一检查是否都有对应处理。\n"
        "【怎么改】补上缺失的 if/else 分支，或增加重复元素的处理。"
    ),
    "off_by_one": (
        "【错在哪】循环边界或下标偏移差一，中间规模的输入也会出错，不只是边界。\n"
        "【提示】用 n=3 手工走一遍循环，写下每一轮的下标。\n"
        "【怎么改】检查是 < 还是 <=，下标是 i 还是 i±1。"
    ),
    "boundary_condition": (
        "【错在哪】分支齐全，但空输入、单个元素、0 或极值等边界取值下逻辑不成立。\n"
        "【提示】分别用 n=0、n=1 和最大规模各测一次。\n"
        "【怎么改】在最前面单独处理这些边界，再进入主逻辑。"
    ),
    "inverted_condition": (
        "【错在哪】比较方向或条件写反了，例如求最小值却返回较大者。\n"
        "【提示】把每个比较符号读出声，确认「大/小」与题意一致。\n"
        "【怎么改】修正 < 与 >、<= 与 >=。"
    ),
    "overflow": (
        "【错在哪】中间结果超出所用整数类型的表示范围。\n"
        "【提示】估算最大可能的中间值，再对比 int 的上限。\n"
        "【怎么改】把累加变量改用 long long，注意乘法前先转换类型。"
    ),
    "wrong_data_type": (
        "【错在哪】类型选错：该用浮点却用了整数，或该用有符号却用了无符号。\n"
        "【提示】检查除法是否发生在整数之间。\n"
        "【怎么改】把相关变量改为 double，或显式转换为浮点后再除。"
    ),
    "uninitialized_state": (
        "【错在哪】累加器、DP 表或访问标记没有初始化，或跨用例没有重置。\n"
        "【提示】查找所有只声明未赋初值的变量。\n"
        "【怎么改】声明时即初始化，多组数据时每组开始前清零。"
    ),
    "wrong_traversal": (
        "【错在哪】遍历或搜索的顺序/方向不对，换一种遍历方式就能通过。\n"
        "【提示】判断题目要的是「最短步数」还是「是否存在路径」。\n"
        "【怎么改】把深度优先改为广度优先，或反过来。"
    ),
    "io_format_error": (
        "【错在哪】内容算对了，只是输出形式与要求不符（分隔符、大小写、空格换行）。\n"
        "【提示】把样例输出逐字符比对一次。\n"
        "【怎么改】照抄样例的输出格式，注意行末是否要有换行。"
    ),
}


@dataclass(frozen=True)
class Plan:
    """Counts the script intends to write, for the dry-run printout."""

    students: int
    problem_sets: int
    set_items: int
    assignments: int
    submissions: int
    mistakes: int
    reviews: int
    flagged: int


def _password_hash(password: str) -> tuple[str, str]:
    """(digest, salt) using the app's own scheme — never a second one."""
    salt = new_salt()
    return hash_password(password, salt), salt


# --------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------


def reset(session: Session) -> dict[str, int]:
    """Delete everything this script owns. Returns per-table counts.

    Deletion order is child-first so foreign keys never block. The key is
    the demo marker: demo users, and rows reachable from them. The real
    ``teacher`` / ``2025021120058`` accounts and the pre-existing rows are
    untouched on purpose — the demo dataset is additive.
    """
    demo_users = list(
        session.scalars(
            select(User).where(User.username.like(f"{DEMO_PREFIX}%"))
        ).all()
    )
    demo_ids = [u.id for u in demo_users]

    # Submissions owned by demo students, plus their mistake rows and any
    # reviews hanging off those mistake rows.
    sub_ids = list(
        session.scalars(
            select(Submission.id).where(Submission.user_id.in_(demo_ids))
        ).all()
    ) if demo_ids else []
    entry_ids = list(
        session.scalars(
            select(MistakeBookEntry.id).where(
                MistakeBookEntry.submission_id.in_(sub_ids)
            )
        ).all()
    ) if sub_ids else []

    counts: dict[str, int] = {}
    if entry_ids:
        counts["reviews"] = session.execute(
            delete(ReviewRecord).where(ReviewRecord.entry_id.in_(entry_ids))
        ).rowcount
        counts["mistake_book"] = session.execute(
            delete(MistakeBookEntry).where(MistakeBookEntry.id.in_(entry_ids))
        ).rowcount
    if sub_ids:
        counts["submissions"] = session.execute(
            delete(Submission).where(Submission.id.in_(sub_ids))
        ).rowcount

    # Demo classes -> members, assignments, sets, items.
    demo_classes = list(
        session.scalars(
            select(Classroom).where(Classroom.invite_code == DEMO_CLASS_INVITE)
        ).all()
    )
    class_ids = [c.id for c in demo_classes]
    if class_ids:
        counts["assignments"] = session.execute(
            delete(Assignment).where(Assignment.class_id.in_(class_ids))
        ).rowcount
        set_ids = list(
            session.scalars(
                select(ProblemSet.id).where(ProblemSet.class_id.in_(class_ids))
            ).all()
        )
        if set_ids:
            counts["problem_set_items"] = session.execute(
                delete(ProblemSetItem).where(
                    ProblemSetItem.problem_set_id.in_(set_ids)
                )
            ).rowcount
            counts["problem_sets"] = session.execute(
                delete(ProblemSet).where(ProblemSet.id.in_(set_ids))
            ).rowcount
        counts["class_members"] = session.execute(
            delete(ClassMember).where(ClassMember.class_id.in_(class_ids))
        ).rowcount
        counts["classrooms"] = session.execute(
            delete(Classroom).where(Classroom.id.in_(class_ids))
        ).rowcount

    if demo_ids:
        # user_sessions has an FK to users and no cascade: a login during
        # earlier testing leaves a token row that blocks the user delete.
        counts["user_sessions"] = session.execute(
            delete(UserSession).where(UserSession.user_id.in_(demo_ids))
        ).rowcount
        counts["users"] = session.execute(
            delete(User).where(User.id.in_(demo_ids))
        ).rowcount

    return {k: int(v or 0) for k, v in counts.items()}


# --------------------------------------------------------------------------
# Seed
# --------------------------------------------------------------------------


def _catalogue_ids(session: Session) -> list[int]:
    """Existing problem ids, ascending. Fails loudly when empty."""
    ids = list(
        session.scalars(select(Problem.id).order_by(Problem.id)).all()
    )
    if not ids:
        raise SystemExit(
            "题库为空：先跑 tools/load_problems.py 再灌演示数据。"
            "（造引用不存在题目 id 的提交正是本项目此前踩过的悬空引用坑。）"
        )
    return ids


def build_plan(catalogue_size: int) -> Plan:
    return Plan(
        students=len(STUDENTS),
        problem_sets=2,
        set_items=min(12, catalogue_size),
        assignments=4,
        submissions=sum(n for _, _, _, n in STUDENTS),
        mistakes=0,  # filled in as we generate; see returned counts
        reviews=0,
        flagged=6,
    )


def seed(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Write the demo dataset. Returns per-table insert counts."""
    now = now or datetime(2026, 10, 4, 18, 0, 0)
    rng = RNG
    catalogue = _catalogue_ids(session)
    counts: dict[str, int] = {}

    # --- teacher -------------------------------------------------------
    digest, salt = _password_hash(DEMO_PASSWORD)
    teacher = User(
        username=DEMO_TEACHER_USERNAME,
        display_name="演示教师",
        role=ROLE_TEACHER,
        password_hash=digest,
        password_salt=salt,
        created_at=now - timedelta(days=60),
    )
    session.add(teacher)
    session.flush()

    # --- classroom -----------------------------------------------------
    classroom = Classroom(
        name=DEMO_CLASS_NAME,
        teacher_id=teacher.id,
        invite_code=DEMO_CLASS_INVITE,
        created_at=now - timedelta(days=56),
    )
    session.add(classroom)
    session.flush()

    # --- students + membership ----------------------------------------
    students: list[tuple[User, float, int]] = []
    for idx, (uname, display, skill, n) in enumerate(STUDENTS):
        d, s = _password_hash(DEMO_PASSWORD)
        stu = User(
            username=uname,
            display_name=display,
            role=ROLE_STUDENT,
            password_hash=d,
            password_salt=s,
            created_at=now - timedelta(days=55 - idx),
        )
        session.add(stu)
        session.flush()
        session.add(
            ClassMember(
                class_id=classroom.id,
                user_id=stu.id,
                joined_at=now - timedelta(days=54 - idx),
            )
        )
        students.append((stu, skill, n))
    counts["users"] = 1 + len(students)
    counts["classrooms"] = 1
    counts["class_members"] = len(students)

    # --- problem sets (two, classroom-visible) ------------------------
    set_a_ids = catalogue[: min(12, len(catalogue))]
    set_b_ids = catalogue[min(12, len(catalogue)) : min(24, len(catalogue))] or set_a_ids
    sets: list[ProblemSet] = []
    for title, desc, ids in (
        (
            "基础语法与分支（12 题）",
            "开学前四周的课堂练习：输入输出、条件分支、循环与数字拆分。",
            set_a_ids,
        ),
        (
            "数组与基础算法（12 题）",
            "期中后的专题练习：数组遍历、排序、简单动态规划。",
            set_b_ids,
        ),
    ):
        ps = ProblemSet(
            title=title,
            description=desc,
            visibility="class",
            class_id=classroom.id,
            created_by=teacher.id,
            created_at=now - timedelta(days=50),
            updated_at=now - timedelta(days=50),
            is_active=True,
        )
        session.add(ps)
        session.flush()
        for pos, pid in enumerate(ids):
            session.add(
                ProblemSetItem(
                    problem_set_id=ps.id, problem_id=pid, position=pos
                )
            )
        sets.append(ps)
    counts["problem_sets"] = len(sets)
    counts["problem_set_items"] = len(set_a_ids) + len(set_b_ids)

    # --- assignments (bound to a class + a set) -----------------------
    # ``source_set_id`` is what makes the teacher's "作业来自哪个题单"
    # view resolve, and ``problem_ids`` is the JSON snapshot the student
    # path reads. Both point at the same ids on purpose.
    # assistance_level must stay inside [MIN=1, MAX=5] — see
    # app/assistance.py. An out-of-range value is *not* rejected at write
    # time, it silently degrades to the default (3) on read, which would
    # make the seeded variety a lie.
    assignment_specs = [
        ("第 3 周练习：输入输出与分支", 0, 12, 5, 3),
        ("第 5 周练习：循环与数字拆分", 0, 14, 2, 4),
        ("第 7 周作业：数组与查找", 1, 7, 1, 5),
        ("第 9 周作业：排序与贪心入门", 1, -3, 4, 6),
    ]
    assignments: list[Assignment] = []
    for title, set_idx, due_offset, level, src_idx in assignment_specs:
        ps = sets[set_idx]
        problem_ids = set_a_ids if set_idx == 0 else set_b_ids
        a = Assignment(
            title=title,
            description=f"课堂作业：{title}。完成后请在错题本回顾本次出现的错因。",
            problem_ids=problem_ids[:6],
            due_at=now + timedelta(days=due_offset),
            created_by=teacher.id,
            created_at=now - timedelta(days=30 - src_idx),
            assistance_level=level,
            class_id=classroom.id,
            source_set_id=ps.id,
        )
        session.add(a)
        session.flush()
        assignments.append(a)
    counts["assignments"] = len(assignments)

    # --- submissions + mistake book + reviews -------------------------
    # Per-student pass/fail mix from their skill; wrong answers get a
    # mistake-book row with a valid label, and some get SM-2 review state.
    target_problems = sorted(set(set_a_ids + set_b_ids))
    n_subs = n_mistakes = n_reviews = n_flagged = 0

    # Three students get a deliberate flag story so the review queue is
    # never empty: two awaiting a verdict (the queue) and one a teacher has
    # already ruled on — which is what makes "the queue only shows what is
    # still open" demonstrable rather than merely asserted.
    flag_students = {
        students[0][0].id: "open",
        students[4][0].id: "open",
        students[2][0].id: "dismiss",
    }

    for s_idx, (stu, skill, attempts) in enumerate(students):
        # Where in the timeline this student's work sits.
        started = now - timedelta(days=48 - s_idx)
        for k in range(attempts):
            pid = target_problems[rng.randrange(len(target_problems))]
            passed = rng.random() < skill
            status = "AC" if passed else rng.choices(
                ["WA", "TLE", "CE", "RE", "MLE"],
                weights=[62, 12, 12, 10, 4],
            )[0]
            created = started + timedelta(
                days=rng.uniform(0, 44), hours=rng.uniform(8, 22)
            )
            code, stdout, stderr, exec_t, mem = _fake_run(status, pid, rng)
            sub = Submission(
                problem_id=pid,
                user_id=stu.id,
                status=status,
                language="cpp",
                code=code,
                stdout=stdout,
                stderr=stderr,
                exec_time=exec_t,
                memory_used=mem,
                created_at=created,
            )

            # Process features: most students show ordinary typing; the
            # flag students get a high-paste / low-rewrite trace on their
            # first run — the exact shape ``process_trace.describe`` flags.
            if stu.id in flag_students and k == 0:
                sub.process_duration_ms = rng.randint(40_000, 90_000)
                sub.process_edit_count = rng.randint(2, 5)
                sub.process_paste_count = 1
                sub.process_pasted_chars = rng.randint(320, 520)
                sub.process_final_length = sub.process_pasted_chars + rng.randint(20, 60)
                sub.process_paste_ratio = 0.9
                sub.process_post_paste_edits = 0
                sub.process_post_paste_chars = rng.randint(0, 8)
                sub.process_rewrite_ratio = 0.01
                sub.process_runs_before = 0
                sub.process_flagged = True
                if flag_students[stu.id] == "dismiss":
                    sub.process_review = REVIEW_DISMISSED
                    sub.process_review_by = teacher.id
                    sub.process_review_at = created + timedelta(hours=6)
                else:
                    sub.process_review = None
                n_flagged += 1
            else:
                sub.process_duration_ms = rng.randint(120_000, 900_000)
                sub.process_edit_count = rng.randint(18, 90)
                sub.process_paste_count = rng.randint(0, 2)
                sub.process_pasted_chars = rng.randint(0, 90)
                sub.process_final_length = rng.randint(180, 420)
                sub.process_paste_ratio = round(
                    (sub.process_pasted_chars or 0) / (sub.process_final_length or 1), 3
                )
                sub.process_post_paste_edits = rng.randint(0, 6)
                sub.process_post_paste_chars = rng.randint(0, 40)
                sub.process_rewrite_ratio = round(rng.uniform(0.2, 0.9), 3)
                sub.process_runs_before = rng.randint(0, 4)
                sub.process_flagged = False

            session.add(sub)
            session.flush()
            n_subs += 1

            # Mistake book: every non-AC attempt, plus an occasional AC
            # that the agent still analysed (a near-miss). The label is
            # always from the 12-label closed set.
            if status != "AC" or rng.random() < 0.15:
                # The first twelve entries walk the full label list so the
                # error-distribution chart is guaranteed to show all 12
                # categories; after that the choice is weighted, giving a
                # believable head and tail. Relying on the weighted draw
                # alone leaves a category or two empty, and a chart
                # missing one of twelve looks like that category is broken.
                if n_mistakes < len(ROOT_CAUSE_LABELS):
                    label = ROOT_CAUSE_LABELS[n_mistakes]
                else:
                    label = rng.choices(_COMMON_CAUSES, weights=_CAUSE_WEIGHTS)[0]
                entry = MistakeBookEntry(
                    submission_id=sub.id,
                    problem_id=pid,
                    error_cause="本次提交未通过，已自动收入错题本。",
                    knowledge_tags=[],
                    reviewed=rng.random() < 0.45,
                    review_count=rng.randint(0, 3),
                    last_reviewed_at=created + timedelta(days=rng.randint(1, 5))
                    if rng.random() < 0.4
                    else None,
                    created_at=created,
                    agent_error_type=label,
                    agent_suggestion=_LABEL_PROSE.get(
                        label, "【错在哪】演示数据。"
                    ),
                    analysis_status="done",
                )
                session.add(entry)
                session.flush()
                n_mistakes += 1

                # SM-2 review state for a subset.
                if rng.random() < 0.55:
                    reps = rng.randint(1, 4)
                    ef = round(rng.uniform(1.9, 2.6), 2)
                    interval = [1, 3, 7, 15][min(reps, 3)]
                    session.add(
                        ReviewRecord(
                            entry_id=entry.id,
                            repetitions=reps,
                            easiness_factor=ef,
                            interval_days=interval,
                            last_reviewed_at=created + timedelta(days=rng.randint(1, 4)),
                            due_at=now + timedelta(days=rng.randint(-2, 9)),
                            created_at=created,
                        )
                    )
                    n_reviews += 1

    # --- shape the alerts so all three kinds fire ---------------------
    # The console has three alert rules (``app.models.alerts``) and a demo
    # that only ever shows "repeating" makes the other two look dead. Each
    # rule needs a specific shape, so the last few rows are nudged rather
    # than left to chance:
    #   * stuck  — the 5 most recent submissions are all non-AC;
    #   * silent — the newest submission is older than SILENT_DAYS;
    #   * repeating — already produced by the weighted cause mix.
    _shape_alerts(session, students, now)

    counts["submissions"] = n_subs
    counts["mistake_book"] = n_mistakes
    counts["reviews"] = n_reviews
    counts["flagged"] = n_flagged
    return counts


#: The student whose recent run is all failures (fires "stuck").
_STUCK_STUDENT_IDX = 5  # 吴嘉言, the weakest profile
#: The student who went quiet (fires "silent").
_SILENT_STUDENT_IDX = 6  # 郑一诺


def _shape_alerts(
    session: Session,
    students: list[tuple[User, float, int]],
    now: datetime,
) -> None:
    """Force the ``stuck`` and ``silent`` rules to have one instance each."""
    stuck_user = students[_STUCK_STUDENT_IDX][0]
    silent_user = students[_SILENT_STUDENT_IDX][0]

    def _recent(user: User, n: int) -> list[Submission]:
        return list(
            session.scalars(
                select(Submission)
                .where(Submission.user_id == user.id)
                .order_by(Submission.id.desc())
                .limit(n)
            ).all()
        )

    # stuck: make the newest 5 attempts all non-AC, and recent.
    for i, sub in enumerate(_recent(stuck_user, 5)):
        if sub.status == "AC":
            sub.status = "WA"
            sub.stdout = ""
        sub.created_at = now - timedelta(days=i, hours=RNG.randint(1, 6))

    # silent: push the whole history back past the threshold.
    for sub in _recent(silent_user, 50):
        sub.created_at = sub.created_at - timedelta(days=14)


# --------------------------------------------------------------------------
# Fake run artefacts (kept minimal and obviously synthetic)
# --------------------------------------------------------------------------


def _fake_run(status: str, problem_id: int, rng: random.Random) -> tuple[str, str, str, float | None, int | None]:
    """Plausible code/stdout/stderr/limits for a given verdict."""
    code = (
        "#include <iostream>\n"
        "using namespace std;\n"
        f"// demo submission for problem {problem_id}\n"
        "int main(){ long long a,b; if(!(cin>>a>>b)) return 0;"
        " cout << a + b << endl; return 0; }\n"
    )
    if status == "AC":
        return code, str(rng.randint(2, 9999)), "", round(rng.uniform(0.002, 0.05), 4), rng.randint(3, 12)
    if status == "CE":
        return (
            "#include <iostream>\n"
            "int main(){ cout << 1 << e; }\n",
            "",
            "demo.cpp:2:20: error: 'cout' was not declared in this scope\n"
            "demo.cpp:2:19: error: 'e' was not declared in this scope\n",
            None,
            None,
        )
    if status == "TLE":
        return code, "", "", None, rng.randint(4, 12)
    if status == "MLE":
        return code, "", "", round(rng.uniform(1.0, 1.6), 3), rng.randint(230, 260)
    if status == "RE":
        return code, "", "terminate called after throwing an instance of 'std::out_of_range'\n", round(rng.uniform(0.01, 0.09), 4), rng.randint(3, 20)
    # WA
    return code, str(max(0, int(rng.randint(-40, 60)))), "", round(rng.uniform(0.002, 0.04), 4), rng.randint(3, 12)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _summary(session: Session) -> None:
    from sqlalchemy import func

    print("\n当前数据概况：")
    for table, model in (
        ("users", User),
        ("classrooms", Classroom),
        ("class_members", ClassMember),
        ("problem_sets", ProblemSet),
        ("assignments", Assignment),
        ("submissions", Submission),
        ("mistake_book", MistakeBookEntry),
        ("reviews", ReviewRecord),
    ):
        n = session.scalar(select(func.count()).select_from(model)) or 0
        print(f"  {table:16s} {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--reset", action="store_true", help="delete demo rows (do this first)")
    ap.add_argument("--write", action="store_true", help="actually write (default: dry-run)")
    ap.add_argument("--seed", type=int, default=20261004, help="RNG seed")
    args = ap.parse_args()

    if args.seed != 20261004:
        RNG.seed(args.seed)

    with get_session() as session:
        catalogue = list(session.scalars(select(Problem.id).order_by(Problem.id)).all())
        plan = build_plan(len(catalogue))

        existing_demo = session.scalar(
            select(User.id).where(User.username.like(f"{DEMO_PREFIX}%")).limit(1)
        )

        print(f"题库 {len(catalogue)} 道（id {catalogue[0]}..{catalogue[-1]}）")
        print(f"已有演示数据：{'是' if existing_demo else '否'}")
        print(
            "\n计划写入：\n"
            f"  学生 {plan.students} 名 + 演示教师 1 名\n"
            f"  题单 {plan.problem_sets} 个（每单 {plan.set_items} 题）\n"
            f"  作业 {plan.assignments} 个（挂到班级 + 题单）\n"
            f"  提交 约 {plan.submissions} 条（含过程特征）\n"
            f"  错题本 约 {int(plan.submissions * 0.35)} 条（12 类合法错因）\n"
            f"  复习记录 约 {int(plan.submissions * 0.2)} 条（SM-2）\n"
            f"  过程标记 {plan.flagged} 条（其中 1 条已处理）\n"
            f"  登录口令统一为：{DEMO_PASSWORD}"
        )

        do_write = args.write and not args.dry_run
        if not do_write:
            print("\n[dry-run] 未写入。加 --write 真正写入。")
            return 0

        if args.reset or existing_demo:
            removed = reset(session)
            session.commit()
            print(f"\n[reset] 已删除演示行：{removed}")
        if args.reset and not args.write:
            _summary(session)
            return 0

        counts = seed(session, now=datetime(2026, 10, 4, 18, 0, 0))
        # ``get_session()`` hands back a plain Session whose context-manager
        # exit *rolls back* — flushing is not enough, the rows only exist
        # inside the transaction. Commit explicitly, or the script prints a
        # cheerful count for data that vanishes on exit.
        session.commit()
        print(f"\n[seed] 已写入：{counts}")
        _summary(session)
        print(
            f"\n演示账号：\n"
            f"  教师  {DEMO_TEACHER_USERNAME} / {DEMO_PASSWORD}\n"
            f"  学生  demo_s01 .. demo_s08 / {DEMO_PASSWORD}\n"
            f"  班级  {DEMO_CLASS_NAME}（邀请码 {DEMO_CLASS_INVITE}）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
