"""学生的 AI 使用记录（阶段 5 · 因材施教留痕）。

为什么需要它
------------
老师压辅助强度，是为了让「这一次是检验」而不是「这一次是练习」。但压了
强度和**真的没被绕过**是两件事：只有把每次调用记下来，才能回答
「这份作业里，有多少人是自己做的」「谁在用三档反复追问同一道题」。

这也是答辩时唯一能把「因材施教」讲成数据的依据 —— 否则只能演示界面，
说不了效果。

记什么、不记什么
----------------
记：**哪一次作业、哪一道题、哪个学生、哪一档、做了什么动作、什么时候**。

不记：**对话内容**。留痕的目的是让老师看到「用了多少」，不是审阅学生问了
什么 —— 后者需要完全不同的授权与告知，而且会把一张统计表变成一份监控
档案。真要引用对话，应当另开一张表并单独取得同意，而不是顺手存进这里。

表为什么不挂在 Assignment 上
----------------------------
一次调用可能不属于任何作业 —— 学生在题库里自由练习时也会用 AI。所以
``assignment_id`` 可空，读到 NULL 就是「自由练习时的使用」。把它做成必填
会逼出一个假的「默认作业」，那比允许 NULL 更难解释。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: 一次调用做了什么。写成常量而不是裸字符串，因为老师端要按键统计，
#: 拼错一个单词会让统计里凭空多出一类。
ACTION_COMPLETE = "complete"
ACTION_CHAT = "chat"
ACTION_INSERT = "insert"
ACTION_FULL_SOLUTION = "full_solution"

#: 全部动作，供校验与统计枚举。
ALL_ACTIONS = (ACTION_COMPLETE, ACTION_CHAT, ACTION_INSERT, ACTION_FULL_SOLUTION)

#: 动作的中文名。放这里而不是前端：老师端和学生端要显示同一个词，
#: 两边各写一份必然漂移。
ACTION_LABELS = {
    ACTION_COMPLETE: "请求补全",
    ACTION_CHAT: "向 AI 提问",
    ACTION_INSERT: "采纳 AI 写的代码",
    ACTION_FULL_SOLUTION: "请求完整解法",
}


class AssistanceUsage(Base):
    """一条 AI 使用记录。

    Attributes:
        id: Primary key.
        student_id: Who used it. Nullable for the same reason
            submissions are — the app deliberately allows browsing and
            solving while logged out, and a record that refused to save
            without a user would silently lose exactly the usage a
            teacher would most want to see.
        assignment_id: Which assignment, or ``None`` for free practice.
        problem_id: Which problem. Nullable because a "hint me" call is
            about the problem the student has open, and the editor may
            legitimately have nothing loaded yet.
        level: The assistance level in force at the time. Stored per
            row rather than read from the assignment later, because the
            teacher can change the level and the historical record must
            still say what was actually allowed when the student acted.
        action: One of :data:`ALL_ACTIONS`.
        created_at: When.
    """

    __tablename__ = "assistance_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assignment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "assignment_id": self.assignment_id,
            "problem_id": self.problem_id,
            "level": self.level,
            "action": self.action,
            "action_label": ACTION_LABELS.get(self.action, self.action),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AssistanceUsage id={self.id} level={self.level} action={self.action!r}>"
        )
