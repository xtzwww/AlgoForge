"""学生自己选的辅助强度（阶段 5 · 因材施教）。

为什么需要这张表
----------------
老师设的档位是**上限**：一份作业最多允许多少 AI 帮助。但上限不等于
"每个人都该顶格用"。一个学生在复习阶段想要五档，在模拟考时想要一档
——这是两种诚实的学习方式，而过去没有地方表达后者。

于是加一层：**学生在老师给的上限之内，可以自己往更严格的方向调。**

三件事必须同时成立
------------------
1. **不能往上调。** 老师压强度是评分公平的一部分，学生自称的档位永远
   被老师的档位截断。这条是安全边界，写在服务端；界面把"高于上限"的
   选项直接不渲染，是体验，不是规则。
2. **调低是自愿的、可撤销的。** 它不产生任何记录、不影响成绩，随时能
   调回来。一个学生想少用 AI 不该被当成需要审批的事。
3. **逐作业而不是全局。** 同一份作业是复习还是检验由老师决定，而这种
   差异可能逐份不同；做成一个全局开关，学生会为了"考试那份"而把
   "复习那份"也一起降掉。

为什么只存 `level` 不存 `assignment_id` 之外的快照
--------------------------------------------------
老师的档位之后可能被改。存快照会让"我当初选的是几档"与"现在生效几档"
分叉，而这里要回答的问题只有一个：**这个人现在想要几档**。所以只存
意愿，生效值由老师的上限与它取小实时算出。

自由练习（没有作业）怎么办
--------------------------
``assignment_id`` 可空，NULL 表示"自由练习时的意愿"。它与有作业的意愿
分开存：一份作业上调低，不该连带把自由练习也降掉。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssistancePreference(Base):
    """一个学生在某一份作业上想要的辅助强度。

    Attributes:
        id: Primary key.
        student_id: Whose preference. Not nullable — unlike the usage
            log, an anonymous preference is meaningless: there is nobody
            to apply it to on the next request.
        assignment_id: Which assignment, or ``None`` for free practice.
            Nullable for the same reason the usage log's column is: a
            student practising in the catalogue has no assignment, and
            forcing one would invent a fake "default assignment".
        level: The level they asked for. Always re-clamped against the
            teacher's level on read — see
            :func:`app.assistance_service.effective_level`.
        updated_at: Last time they changed it. Kept so "我想调回来" can
            later be shown as a change rather than a silent rewrite.
    """

    __tablename__ = "assistance_preferences"
    #: One row per (student, assignment). Two rows would make the
    #: effective level depend on row order, which is the kind of bug
    #: that only shows up under concurrency.
    __table_args__ = (
        UniqueConstraint(
            "student_id", "assignment_id", name="uq_assistance_pref_student_assignment"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "assignment_id": self.assignment_id,
            "level": self.level,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AssistancePreference student={self.student_id} "
            f"assignment={self.assignment_id} level={self.level}>"
        )
