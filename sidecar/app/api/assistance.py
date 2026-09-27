"""AI 辅助强度的接口（阶段 5 · 因材施教）。

GET  /api/v1/assistance/levels        五档分别允许什么
POST /api/v1/assistance/complete      补全（一档本机，二档起走模型）
POST /api/v1/assistance/chat          对话（三档起）
POST /api/v1/assistance/insert        AI 写一段代码（四档起）
POST /api/v1/assistance/solution      完整解法（五档）
GET  /api/v1/assistance/config        当前 OpenAI 兼容端点的生效配置
POST /api/v1/assistance/config/test   用给定参数试连一次（不落盘）
POST /api/v1/assistance/config        把给定参数写进 sidecar/.env
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
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_teacher
from app.assistance import all_policies, policy_for, policy_to_dict
from app.assistance_service import (
    AssistanceContext,
    assignment_level,
    effective_level,
    handle_chat,
    handle_completion,
    handle_insert,
    handle_solution,
    set_student_preference,
    student_preference,
    usage_summary,
)
from app.config import env_file_path, settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["assistance"])


class AssistRequest(BaseModel):
    """所有辅助请求共用的字段。

    ``assignment_id`` 可选：自由练习时没有作业，仍应能用 AI，只是落在
    默认档上。

    ``provider`` 是**本次请求**用哪个模型（``deepseek`` / ``ollama`` /
    ``openai_compatible``）。留空用当前生效的那个（老师设的默认或 .env
    的默认）。

    为什么它是逐请求的而不是一个全局开关
    ------------------------------------
    曾经只有一条 ``PUT /assistance/models`` 改一个**进程级变量**，于是
    "换模型"实际是"给所有人换" —— 教师端与学生端共用同一 sidecar 时，
    一个学生把模型切走，其他人的下一次提问就跟着变。而它当初不设权限的
    理由又是对的：学生遇到一个连不上的模型，应该能自己换一个再问一次。

    逐请求的 provider 同时满足两边：**换只影响自己**，所以不需要任何
    权限；而"给全班改默认"是另一件事，那一条路由要求教师身份。
    """

    assignment_id: Optional[int] = None
    problem_id: Optional[int] = None
    language: str = "cpp"
    provider: Optional[str] = None


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


class LevelStateOut(BaseModel):
    """当前生效的档位，以及它由哪两个值合成。

    三个数字都给出来，而不是只给一个结论：学生要知道"我现在是几档"，
    而界面要解释**为什么**是这一档 —— "老师给到五档，你自己选了三档"
    和"老师只给了一档"是完全不同的两句话，用一个数字表达不出来。
    """

    #: 真正生效的那一档。
    level: int
    #: 整套规则，供界面直接渲染，不必自己推断档位含义。
    policy: dict = Field(default_factory=dict)
    #: 老师设的上限。
    ceiling: int
    #: 学生自己选的；``None`` 表示没选过，正在用老师给的上限。
    preference: Optional[int] = None
    #: 这份作业是否由老师设定过（没有作业时为 false）。
    has_assignment: bool = False


class SetLevelRequest(BaseModel):
    """学生调整自己的辅助强度。

    ``level`` 省略或为 ``null`` 表示**清除意愿、回到老师给的上限**。
    它不是"设成某一档"，而是第三种状态 —— 见
    :func:`app.assistance_service.set_student_preference`。
    """

    assignment_id: Optional[int] = None
    level: Optional[int] = None


def _level_state(assignment_id: Optional[int], user: Optional[User]) -> dict:
    """把三个数字算齐。读路径与写路径共用，避免两处算法漂移。"""
    from app import records_session

    student_id = user.id if user else None
    with records_session.get_session() as session:
        ceiling = assignment_level(session, assignment_id)
        preference = student_preference(session, student_id, assignment_id)
        level = effective_level(session, student_id, assignment_id)
    return {
        "level": level,
        "policy": policy_to_dict(policy_for(level)),
        "ceiling": ceiling,
        "preference": preference,
        "has_assignment": assignment_id is not None,
    }


@router.get("/assistance/level", response_model=LevelStateOut)
def get_level(
    assignment_id: Optional[int] = Query(default=None),
    user: Optional[User] = Depends(current_user),
) -> LevelStateOut:
    """当前生效的档位是什么，以及它是怎么来的。

    这是学生端渲染的**唯一**来源。在此之前前端只能拿"默认档"（三档）
    来画界面，于是老师设了五档、学生仍然看到三档的样子 —— 界面对不上
    老师的设置。有了这条，界面渲染的就是真正会被裁决的那个档位。

    未登录时按"没有学生意愿"处理，返回老师设的上限（或默认档）。这不
    放宽任何东西：真正的裁决每次调用时照常进行，这条只回答"现在是什么"。
    """
    return LevelStateOut(**_level_state(assignment_id, user))


@router.put("/assistance/level", response_model=LevelStateOut)
def set_level(
    req: SetLevelRequest,
    user: Optional[User] = Depends(current_user),
) -> LevelStateOut:
    """学生把辅助强度往**更严格**的方向调，或清除自己的选择。

    为什么允许学生调低
    ------------------
    老师设的档位是这次作业允许的上限，不是"每个人都该顶格用"。一个学生
    在复习阶段想要五档、在模拟考时想要一档，是两种诚实的学习方式；压住
    后者的产品会逼他去别处找 AI。

    为什么调低是安全的
    ------------------
    因为服务端把它夹在 ``[1, 老师的上限]`` 里，存下来的值不可能高于上限。
    往上调不是"被界面藏起来"，而是**在这条路径上表达不出来** —— 传入
    99 会被夹成老师设的那一档。见
    :func:`app.assistance_service.set_student_preference`。

    未登录时返回 401：意愿要挂在某个人身上，没有账号就没有"谁想要"。
    这与四个动作接口允许未登录不同 —— 那些是"做题时的辅助"，未登录也
    应当能用；这是一条个人设置，必须有主体。
    """
    if user is None:
        raise HTTPException(status_code=401, detail="需要登录才能保存辅助强度偏好")

    from app import records_session

    with records_session.get_session() as session:
        set_student_preference(
            session,
            student_id=user.id,
            assignment_id=req.assignment_id,
            level=req.level,
        )
    return LevelStateOut(**_level_state(req.assignment_id, user))



class SetModelRequest(BaseModel):
    """切换当前模型。``model_id`` 省略即回到 .env 的默认值。"""

    model_id: Optional[str] = None


@router.get("/assistance/models")
def list_models() -> dict:
    """可用模型，以及当前选中的那个。

    开放给任何人，与 ``/levels`` 同理：列出的是**这台机器上装了什么**，
    不涉及任何人的数据。切换在当前进程内生效，重启回到 .env —— 见
    ``app/model_catalog`` 的模块说明。
    """
    from app import model_catalog

    return model_catalog.snapshot()


@router.put("/assistance/models")
def set_model(
    req: SetModelRequest,
    _teacher: User = Depends(require_teacher),
) -> dict:
    """改**进程默认**模型。传 ``model_id: null`` 恢复 .env 里的默认值。

    要求教师身份，因为它改的是**所有人**共享的那个默认值：本进程只有一个
    变量，教师端与学生端共用同一 sidecar 时，改它会让其他人的下一次提问
    跟着变 —— 这不是"偏好"，是一次影响全班的配置变更。

    学生想把某个模型换成另一个再试一次，走的是另一条路：请求体里的
    ``provider`` 字段（见 :class:`AssistRequest`）。它逐请求生效，只影响
    自己，因此不需要任何权限。两条路分开之后，"老师压辅助强度"仍然只
    管档位、不管模型，而"学生换个能连上的模型"也仍然做得到。
    """
    from app import model_catalog

    try:
        model_catalog.set_provider(req.model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return model_catalog.snapshot()


# --- 自定义 OpenAI 兼容端点（运行时配置，2026-09-27）----------------------
#
# 三条接口服务同一个窗口：学生/老师看到自己这台机器的 .env 里实际是什么
# （GET）、不落盘地试连一次（POST /test）、把新值写回 .env（POST）。
#
# 写的是**文件**而不是进程内 settings：settings 是启动时一次性加载的，
# 改了内存值不会持久化，重启就丢；而 .env 是持久配置，重启后由
# pydantic-settings 重新读入 —— 所以保存成功返回 ``needs_restart=true``，
# 界面据此提示「重启后端后生效」。打包版（PyInstaller）里 .env 不一定是
# 配置来源，直接拒绝写，避免"保存成功但没用"。


class ConfigOut(BaseModel):
    """当前 OpenAI 兼容端点的生效配置。密钥只回传脱敏值。"""

    provider: str
    base_url: str
    api_key_masked: str
    model: str
    env_path: str
    needs_restart: bool = False


class TestConfigRequest(BaseModel):
    base_url: str = Field(min_length=1)
    #: 允许为空：自建端点（vLLM / LM Studio）可以没有鉴权。
    api_key: str = ""
    model: str = Field(min_length=1)


class TestConfigOut(BaseModel):
    ok: bool
    message: str
    latency_ms: Optional[int] = None


class SaveConfigRequest(BaseModel):
    """要写入 .env 的字段。``None`` 表示这一项不改。"""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


def _mask_key(key: str) -> str:
    """密钥脱敏：只留最后 4 个字符，其余用 * 盖住。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    """就地更新 .env 里的若干键，其余行（含注释与空行）原样保留。"""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []
    keys = set(updates)
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(f"{key}={updates[key]}\n")
                written.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}\n")
    path.write_text("".join(out), encoding="utf-8")


@router.get("/assistance/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    """当前 OpenAI 兼容端点的生效配置（密钥脱敏）。

    开放给任何人：这是**本机**的配置，不涉及他人数据，且界面要用它预填
    配置窗口。真实密钥不回传 —— 写回时若留空表示"保持原值"，见前端。
    """
    return ConfigOut(
        provider=settings.llm_provider,
        base_url=settings.openai_compatible_base_url,
        api_key_masked=_mask_key(settings.openai_compatible_api_key),
        model=settings.openai_compatible_model,
        env_path=str(env_file_path()),
    )


@router.post("/assistance/config/test", response_model=TestConfigOut)
def test_config(req: TestConfigRequest) -> TestConfigOut:
    """用给定的参数试连一次，**不落盘**。

    与网关同一条 URL 拼法（base_url 缺 ``/v1`` 时补上），所以这里测通
    了，保存后重启网关就能用同一条路发请求。
    """
    base = req.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    headers: dict[str, str] = {}
    if req.api_key.strip():
        headers["Authorization"] = f"Bearer {req.api_key.strip()}"
    started = time.monotonic()
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": req.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            },
            timeout=20.0,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            return TestConfigOut(
                ok=False,
                message=f"HTTP {resp.status_code}：{resp.text[:200]}",
                latency_ms=latency_ms,
            )
        data = resp.json()
        echo = data.get("model") or req.model
        return TestConfigOut(
            ok=True,
            message=f"连接成功，返回模型 {echo}",
            latency_ms=latency_ms,
        )
    except Exception as exc:  # httpx 网络/TLS 错误与 JSON 解析错误都算失败
        latency_ms = int((time.monotonic() - started) * 1000)
        return TestConfigOut(ok=False, message=str(exc), latency_ms=latency_ms)


@router.post("/assistance/config", response_model=ConfigOut)
def save_config(req: SaveConfigRequest) -> ConfigOut:
    """把给定参数写进 sidecar/.env，返回写后的值（脱敏）。

    只写 ``openai_compatible`` 相关键并把 ``LLM_PROVIDER`` 固定成它：
    这是「配置自己的 API」的唯一入口，与 deepseek/ollama 的配置互不干扰。
    写的是文件不是内存 —— 返回 ``needs_restart=true``，界面提示重启后端。
    """
    updates: dict[str, str] = {"LLM_PROVIDER": "openai_compatible"}
    if req.base_url is not None:
        updates["OPENAI_COMPATIBLE_BASE_URL"] = req.base_url.strip()
    if req.api_key is not None:
        updates["OPENAI_COMPATIBLE_API_KEY"] = req.api_key.strip()
    if req.model is not None:
        updates["OPENAI_COMPATIBLE_MODEL"] = req.model.strip()

    if not updates.get("OPENAI_COMPATIBLE_BASE_URL", ""):
        raise HTTPException(status_code=422, detail="需要填写 Base URL")
    if not updates.get("OPENAI_COMPATIBLE_MODEL", ""):
        raise HTTPException(status_code=422, detail="需要填写模型名")

    if getattr(sys, "frozen", False):
        raise HTTPException(
            status_code=400,
            detail="打包版不支持从这里写 .env，请直接编辑 sidecar/.env 后重启",
        )

    path = env_file_path()
    _upsert_env(path, updates)
    return ConfigOut(
        provider="openai_compatible",
        base_url=updates["OPENAI_COMPATIBLE_BASE_URL"],
        api_key_masked=_mask_key(updates.get("OPENAI_COMPATIBLE_API_KEY", "")),
        model=updates["OPENAI_COMPATIBLE_MODEL"],
        env_path=str(path),
        needs_restart=True,
    )


def _level_for(assignment_id: Optional[int], student_id: Optional[int]) -> int:
    """按作业与**学生自己的意愿**取真正生效的档位。

    独立成函数以便四个接口共用同一条取法 —— 这条取法必须是唯一的，
    任何一处"顺手再判一次"都可能判出不同的档位。

    见 :func:`app.assistance_service.effective_level`：结果是
    ``min(老师设的上限, 学生自己选的)``，学生只能收紧不能放宽。
    """
    from app import records_session

    with records_session.get_session() as session:
        return effective_level(session, student_id, assignment_id)


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

    level = _level_for(req.assignment_id, user.id if user else None)
    with records_session.get_session() as session:
        result = handle_completion(
            session,
            _context(req, user),
            level=level,
            prefix=req.prefix,
            language=req.language,
            provider=req.provider,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/chat", response_model=AssistResponse)
def chat(
    req: ChatRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """对话。三档起；三档只给提示，回复像代码会被降级。"""
    from app import records_session

    level = _level_for(req.assignment_id, user.id if user else None)
    with records_session.get_session() as session:
        result = handle_chat(
            session,
            _context(req, user),
            level=level,
            question=req.question,
            statement=req.statement,
            provider=req.provider,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/insert", response_model=AssistResponse)
def insert(
    req: InsertRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """让 AI 写一段代码插入编辑器。四档起。"""
    from app import records_session

    level = _level_for(req.assignment_id, user.id if user else None)
    with records_session.get_session() as session:
        result = handle_insert(
            session,
            _context(req, user),
            level=level,
            instruction=req.instruction,
            current_code=req.current_code,
            language=req.language,
            provider=req.provider,
        )
    return AssistResponse(**result.to_dict())


@router.post("/assistance/solution", response_model=AssistResponse)
def solution(
    req: SolutionRequest,
    user: Optional[User] = Depends(current_user),
) -> AssistResponse:
    """完整解法。只有五档放行。"""
    from app import records_session

    level = _level_for(req.assignment_id, user.id if user else None)
    with records_session.get_session() as session:
        result = handle_solution(
            session,
            _context(req, user),
            level=level,
            statement=req.statement,
            language=req.language,
            provider=req.provider,
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
