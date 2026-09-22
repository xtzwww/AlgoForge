"""AI 辅助强度的接口（阶段 5 · 因材施教）。

GET  /api/v1/assistance/levels        五档分别允许什么
POST /api/v1/assistance/complete      补全（一档本机，二档起走模型）
POST /api/v1/assistance/chat          对话（三档起）
POST /api/v1/assistance/insert        AI 写一段代码（四档起）
POST /api/v1/assistance/solution      完整解法（五档）
GET  /api/v1/teacher/assistance/usage 老师看使用留痕

为什么把档位单独成一组接口
--------------------------
档位是**跨页面的规则**，不属于某一页。做题页、练习页、错题本都可能提供
AI 辅助，而它们必须拿到同一份规则 —— 挂在某个页面下会让另一页要么重复
实现、要么绕过。所以这里只有"当前档位下这个动作行不行"，不知道页面。

档位从哪来
----------
请求里的 ``assignment_id``。没有它（自由练习、没登录）就是默认档（三档）。
**不由前端直接传档位数字**，这一点是关键的：档位若由客户端自称，那么
改一个数字就能拿到五档，老师压的强度就等于没有。前端可以传
``assignment_id``，但那是它必须**有权访问**的资源，而不是它可以随便声明的权限。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_teacher
from app.assistance import all_policies, policy_to_dict
from app.assistance_service import (
    AssistanceContext,
    assignment_level,
    handle_chat,
    handle_completion,
    handle_insert,
    handle_solution,
    usage_summary,
)
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["assistance"])


class AssistRequest(BaseModel):
    """所有辅助请求共用的字段。

    ``assignment_id`` 可选：自由练习时没有作业，仍应能用 AI，只是落在
    默认档上。
    """

    assignment_id: Optional[int] = None
    problem_id: Optional[int] = None
    language: str = "cpp"


class CompleteRequest(AssistRequest):
    #: 光标之前的文本。可以是整个文件 —— 一档要从里面摘标识符。
    prefix: str = ""


class ChatRequest(AssistRequest):
    question: str = Field(min_length=1)
    #: 题干，可空（学生可能只是想问一个语言用法）。
    statement: str = ""


class InsertRequest(AssistRequest):
    instruction: str = Field(min_length=1)
    current_code: str = ""


class SolutionRequest(AssistRequest):
    statement: str = Field(min_length=1)


class AssistResponse(BaseModel):
    allowed: bool
    action: str
    level: int
    reason: str
    content: str = ""
    candidates: list[str] = Field(default_factory=list)
    policy: dict = Field(default_factory=dict)
    downgraded: bool = False


@router.get("/assistance/levels")
def list_levels() -> dict:
    """五档分别允许什么。

    开放给任何人：档位规则是**公开的**（老师要选、学生要看到自己被允许
    什么），它不是秘密。真正的约束在每次调用时按作业裁决，而不是靠隐藏
    规则来实现。
    """
    return {"levels": [policy_to_dict(p) for p in all_policies()]}


def _level_for(assignment_id: Optional[int]) -> int:
    """按作业取档位。独立成函数以便四个接口共用同一条取法。"""
    from app import records_session

    with records_session.get_session() as session:
        return assignment_level(session, assignment_id)


def _context(req: AssistRequest, user: Optional[User]) -> AssistanceContext:
    return AssistanceContext(
        # 允许未登录 —— 这与提交记录的处理一致：登录是有意保留的降级，
        # 不是前置条件。少了这个，未登录学生用 AI 的记录会全部丢空。
        student_id=user.id if user else None,
        assignment_id=req.assignment_id,
        problem_id=req.problem_id,
    )


@router.post("/assistance/complete", response_model=AssistResponse)
def complete(
    req: CompleteRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """补全。一档走本机词表，二档起走模型。"""
    from app import records_session

    level = _level_for(req.assignment_id)
    with records_session.get_session() as session:
        result = handle_completion(
            session,
            _context(req, user),
            level=level,
            prefix=req.prefix,
            language=req.language,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/chat", response_model=AssistResponse)
def chat(
    req: ChatRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """对话。三档起；三档只给提示，回复像代码会被降级。"""
    from app import records_session

    level = _level_for(req.assignment_id)
    with records_session.get_session() as session:
        result = handle_chat(
            session,
            _context(req, user),
            level=level,
            question=req.question,
            statement=req.statement,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/insert", response_model=AssistResponse)
def insert(
    req: InsertRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """让 AI 写一段代码插入编辑器。四档起。"""
    from app import records_session

    level = _level_for(req.assignment_id)
    with records_session.get_session() as session:
        result = handle_insert(
            session,
            _context(req, user),
            level=level,
            instruction=req.instruction,
            current_code=req.current_code,
            language=req.language,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/solution", response_model=AssistResponse)
def solution(
    req: SolutionRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """完整解法。只有五档放行。"""
    from app import records_session

    level = _level_for(req.assignment_id)
    with records_session.get_session() as session:
        result = handle_solution(
            session,
            _context(req, user),
            level=level,
            statement=req.statement,
            language=req.language,
        )
    return AssistResponse(**result.to_dict())


class UsageOut(BaseModel):
    total: int
    by_action: dict = Field(default_factory=dict)
    by_action_labeled: dict = Field(default_factory=dict)
    by_student: list = Field(default_factory=list)


@router.get("/teacher/assistance/usage", response_model=UsageOut)
def teacher_usage(
    assignment_id: Optional[int] = Query(default=None),
    _teacher: User = Depends(require_teacher),
) -> UsageOut:
    """老师端：AI 使用留痕。

    ``assignment_id`` 省略则是全量（含自由练习），用于"这个班整体用了多少"。
    """
    from app import records_session

    with records_session.get_session() as session:
        summary = usage_summary(session, assignment_id=assignment_id)
    return UsageOut(**summary)
