"""按辅助强度裁决 AI 请求，并把使用留痕（阶段 5）。

这一模块是**服务端唯一的裁决点**。前端也会按档位隐藏按钮，但那只是体验；
真正的规则在这里 —— 老师压辅助强度是评分公平的一部分，绕不过去。

三道门，缺一不可
----------------
1. **能力门**：这一档允不允许这个动作（:mod:`app.assistance`）。
   三档请求「帮我写代码」直接拒，不调用模型 —— 拒绝要发生在花钱之前。
2. **内容门**：三档允许对话，但回话必须是提示而不能是可直接粘贴的代码。
   这一条**不能只靠提示词**：模型不听话是常态，所以拿到回复后再判一次
   （:func:`looks_like_solution`），像代码就降级成提示话术。
3. **留痕门**：无论成功还是被拒，都记一条。被拒也要记 —— 「有人反复
   请求被禁止的辅助」正是老师想看到的行为。

为什么"拒绝"不抛 4xx
--------------------
档位是**教学设置**，不是错误。学生点到被禁用的功能，得到的不该是一个
红色报错，而是「这次作业只用本地补全」这样一句解释。所以被拒的返回是
结构化的 ``allowed=False`` + ``reason``，HTTP 仍然是 200。

为什么一档不调用任何模型
------------------------
一档的定义就是「不联网」。任何需要模型的实现都不叫一档，所以
:func:`handle_completion` 在一档走的是本机的候选词表（
:func:`local_completion_candidates`）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistance import policy_for, policy_to_dict
from app.models.assistance_usage import (
    ACTION_CHAT,
    ACTION_COMPLETE,
    ACTION_FULL_SOLUTION,
    ACTION_INSERT,
    ALL_ACTIONS,
    AssistanceUsage,
)

logger = logging.getLogger(__name__)

#: 一次 AI 动作的结果。
@dataclass
class AssistanceResult:
    """裁决 + 内容。

    ``allowed=False`` 时 ``content`` 必为空、``reason`` 必非空：客户端只要
    看这两个字段就能决定渲染什么，不需要理解档位规则。
    """

    allowed: bool
    action: str
    level: int
    #: 给学生的解释（无论允许与否都给一句，让"为什么"始终可见）。
    reason: str
    #: 允许时是 AI 的内容；一档的补全是一组候选词。
    content: str = ""
    #: 补全候选（一档/二档用）。对话与代写留空。
    candidates: list[str] = field(default_factory=list)
    #: 策略快照，供前端渲染（避免前端自己判断档位含义）。
    policy: dict = field(default_factory=dict)
    #: 是否因为内容门被降级过（三档给了像代码的东西）。
    downgraded: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "level": self.level,
            "reason": self.reason,
            "content": self.content,
            "candidates": list(self.candidates),
            "policy": dict(self.policy),
            "downgraded": self.downgraded,
        }


#: 判定"这段回复像不像可直接粘贴的代码"。
#:
#: 不能只靠提示词约束模型，所以拿到回复后再看一眼。判据刻意保守 —— 宁可
#: 漏判（放过了带代码块的提示）也不要误判（把一段正常的中文提示降级）。
#: 只看**代码块**和**明确的函数定义**这两类强信号，因为三档允许讲思路，
#: 思路里出现 `if`、`for` 这类词是完全正常的。
_CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\s*\n.+?```", re.DOTALL)
_FUNC_DEF_RE = re.compile(
    r"(?:^|\n)\s*(?:def|class|public|private|protected|int main|void \w+\s*\()",
)
#: 中文提示里的常见开场，用来在降级时保留"它本来在讲什么"。
_HINT_PREAMBLE = "这次是提示模式，我不直接给代码。可以这样想："


def looks_like_solution(text: str) -> bool:
    """这段回复是否可以**直接粘贴进编辑器**。

    刻意保守：只认代码块与函数定义这类强信号。把一段正常的中文提示误判
    成代码，会把三档变成"什么都不说"，那比漏判更糟 —— 学生拿不到任何
    帮助，而老师以为三档是正常的。
    """
    if not text:
        return False
    if _CODE_FENCE_RE.search(text):
        return True
    return bool(_FUNC_DEF_RE.search(text))


def downgrade_to_hint(text: str) -> str:
    """把一段像代码的回复改写成提示。

    不走模型第二次 —— 那既费钱又可能再次违规。做法是把代码块摘掉、保留
    它周围说明思路的文字；如果摘完什么都不剩（整段就是代码），给一句
    固定话术，而不是把原文放过去。
    """
    stripped = _CODE_FENCE_RE.sub("", text).strip()
    # 代码块连同定义行一起去掉，避免只删了围栏还留着函数体。
    lines = [
        line
        for line in stripped.splitlines()
        if not _FUNC_DEF_RE.match(line) and line.strip() not in {"{", "}"}
    ]
    body = "\n".join(lines).strip()
    if not body:
        return f"{_HINT_PREAMBLE}先把思路理清楚，再动手写。"
    return f"{_HINT_PREAMBLE}{body}"


def local_completion_candidates(
    prefix: str,
    *,
    limit: int = 5,
) -> list[str]:
    """一档的补全：完全本机算出，不联网、不调用模型。

    做法是最朴素的那种 —— 从学生**已经敲进去的文本**里摘出标识符，再补上
    一小份固定的 C++/Python 常用词表，按前缀匹配。这不是为了和 AI 比聪明，
    而是为了满足一档的定义：断网可用、零延迟、零成本。

    不编造学生没用过的名字，所以候选里只会出现他自己写过的标识符和词表
    里的公共名字 —— 这让"一档什么都不知道"这件事在行为上也是真的。
    """
    prefix = prefix or ""
    if not prefix.strip():
        return []

    # 取光标前的最后一个标识符片段。
    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", prefix)
    if not match:
        return []
    fragment = match.group(0)

    identifiers = _identifiers_in(prefix)
    pool = sorted(identifiers) + list(_KEYWORDS)
    seen: set[str] = set()
    out: list[str] = []
    for word in pool:
        if word == fragment or not word.startswith(fragment):
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= limit:
            break
    return out


def _identifiers_in(text: str) -> set[str]:
    """从已有代码里摘标识符。本机正则，不依赖任何模型。"""
    return set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text or ""))


#: 一档补全的固定词表。写死在这里而不是读语言配置，是因为一档要"不联网"，
#: 而词表是一份**本机常量**，任何外部来源都会让它变成半个联网功能。
_KEYWORDS: tuple[str, ...] = (
    "assert",
    "break",
    "cin",
    "continue",
    "cout",
    "else",
    "endl",
    "false",
    "for",
    "if",
    "include",
    "iostream",
    "namespace",
    "return",
    "sizeof",
    "std",
    "string",
    "struct",
    "swap",
    "true",
    "using",
    "vector",
    "while",
)


@dataclass
class AssistanceContext:
    """一次请求的上下文。字段都可空，因为学生可能没登录、也没在作业里。"""

    student_id: Optional[int] = None
    assignment_id: Optional[int] = None
    problem_id: Optional[int] = None


def record_usage(
    session: Session,
    ctx: AssistanceContext,
    *,
    level: int,
    action: str,
) -> None:
    """记一条使用。

    不抛错。留痕是**附加价值**，它失败不该让学生的请求失败 —— 但会打日志，
    因为"记录悄悄停了"正是那种没人发现的问题。
    """
    if action not in ALL_ACTIONS:
        logger.warning("assistance: 未知动作 %r，已按原样记录", action)
    try:
        session.add(
            AssistanceUsage(
                student_id=ctx.student_id,
                assignment_id=ctx.assignment_id,
                problem_id=ctx.problem_id,
                level=level,
                action=action,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - 见 docstring：留痕失败不该拖垮请求
        logger.exception("assistance: 写入使用记录失败（请求本身不受影响）")
        session.rollback()


def decide(level: Optional[int], action: str) -> tuple[bool, str]:
    """档位允不允许这个动作。

    Returns:
        ``(allowed, reason)``。``reason`` 无论允许与否都给一句 —— 对拒绝
        尤其重要，因为"为什么不行"必须能直接展示给学生。

    拒绝发生在**调用模型之前**，这是刻意的：花钱之前先问规则。
    """
    policy = policy_for(level)
    if action == ACTION_COMPLETE:
        # 补全一档也允许 —— 只是它不走模型。
        return True, policy.description
    if action == ACTION_CHAT:
        if not policy.allows_chat:
            return False, f"{policy.label}不含对话功能。{policy.description}"
        return True, policy.description
    if action == ACTION_INSERT:
        if not policy.allows_code_insertion:
            return False, f"{policy.label}不允许 AI 把代码写进编辑器，只能给提示。"
        return True, policy.description
    if action == ACTION_FULL_SOLUTION:
        if not policy.allows_full_solution:
            return False, f"{policy.label}不提供完整解法。{policy.description}"
        return True, policy.description
    return False, f"未知的辅助动作：{action}"


def handle_completion(
    session: Session,
    ctx: AssistanceContext,
    *,
    level: Optional[int],
    prefix: str,
    language: str = "cpp",
    provider: Optional[str] = None,
) -> AssistanceResult:
    """补全请求。一档走本机词表，二档起走模型。

    ``provider`` 是**本次请求**用哪个模型。它是逐请求的，因此一个人换
    模型不会影响别人 —— 与「老师设定的默认」是两件事，见
    :func:`app.api.assistance.set_model`。
    """
    policy = policy_for(level)
    policy_dict = policy_to_dict(policy)
    allowed, reason = decide(level, ACTION_COMPLETE)

    record_usage(session, ctx, level=policy.level, action=ACTION_COMPLETE)

    if not policy.completion_uses_model:
        candidates = local_completion_candidates(prefix)
        return AssistanceResult(
            allowed=True,
            action=ACTION_COMPLETE,
            level=policy.level,
            reason="本机补全（不联网）",
            candidates=candidates,
            content="",
            policy=policy_dict,
        )

    # 二档起：交给模型。提示词按语言给出，并要求只补光标处那一小段。
    from app.llm.gateway import LLMGatewayError, chat_completion

    messages = [
        {
            "role": "system",
            "content": (
                "你是代码补全引擎。只输出应当插入光标处的**一小段**代码，"
                "不要解释、不要重复已有内容、不要加代码块围栏。"
                f"语言：{language}。"
            ),
        },
        {"role": "user", "content": prefix},
    ]
    try:
        text = chat_completion(
            messages, temperature=0.2, max_tokens=200, provider=provider
        )
    except LLMGatewayError as exc:
        logger.warning("assistance: 补全调用失败：%s", exc)
        # 退回本机候选，而不是空手而归 —— 一档能做到的事，没理由因为
        # 网络问题连它一起丢掉。
        return AssistanceResult(
            allowed=True,
            action=ACTION_COMPLETE,
            level=policy.level,
            reason="AI 补全暂时不可用，已退回本机补全",
            candidates=local_completion_candidates(prefix),
            policy=policy_dict,
        )

    return AssistanceResult(
        allowed=True,
        action=ACTION_COMPLETE,
        level=policy.level,
        reason=policy.description,
        content=text.strip(),
        policy=policy_dict,
    )


def handle_chat(
    session: Session,
    ctx: AssistanceContext,
    *,
    level: Optional[int],
    question: str,
    statement: str = "",
    provider: Optional[str] = None,
) -> AssistanceResult:
    """对话请求（三档起）。三档的内容门在这里。"""
    policy = policy_for(level)
    policy_dict = policy_to_dict(policy)
    allowed, reason = decide(level, ACTION_CHAT)

    record_usage(session, ctx, level=policy.level, action=ACTION_CHAT)

    if not allowed:
        return AssistanceResult(
            allowed=False,
            action=ACTION_CHAT,
            level=policy.level,
            reason=reason,
            policy=policy_dict,
        )

    from app.llm.gateway import LLMGatewayError, chat_completion

    if policy.chat_is_hint_only:
        system = (
            "你是算法学习助手。**只讲思路和方向**，"
            "禁止输出任何可直接粘贴运行的完整代码，禁止输出代码块。"
            "可以用一句话描述算法意图，可以指出学生该检查哪里。"
        )
    else:
        system = (
            "你是算法学习助手。可以给出代码，但只给**学生问到的部分**，"
            "不要顺手把整道题写完。"
        )

    messages = [
        {"role": "system", "content": system},
    ]
    if statement:
        messages.append({"role": "user", "content": f"题目：{statement}"})
    messages.append({"role": "user", "content": question})

    try:
        text = chat_completion(
            messages, temperature=0.4, max_tokens=800, provider=provider
        )
    except LLMGatewayError as exc:
        logger.warning("assistance: 对话调用失败：%s", exc)
        return AssistanceResult(
            allowed=True,
            action=ACTION_CHAT,
            level=policy.level,
            reason="AI 暂时不可用，请稍后再试。",
            policy=policy_dict,
        )

    text = text.strip()
    downgraded = False
    # 内容门：提示词约束不住模型，所以拿到回复后再判一次。
    if policy.chat_is_hint_only and looks_like_solution(text):
        logger.info("assistance: 三档回复里出现代码，已降级为提示")
        text = downgrade_to_hint(text)
        downgraded = True

    return AssistanceResult(
        allowed=True,
        action=ACTION_CHAT,
        level=policy.level,
        reason=reason,
        content=text,
        policy=policy_dict,
        downgraded=downgraded,
    )


def handle_solution(
    session: Session,
    ctx: AssistanceContext,
    *,
    level: Optional[int],
    statement: str,
    language: str = "cpp",
    provider: Optional[str] = None,
) -> AssistanceResult:
    """请求完整解法（只有五档放行）。

    四档的"写一部分"不在这里 —— 那是编辑器里的插入动作
    （:func:`handle_insert`），区别在于四档给的是片段、五档给的是整解。
    """
    policy = policy_for(level)
    policy_dict = policy_to_dict(policy)
    allowed, reason = decide(level, ACTION_FULL_SOLUTION)

    record_usage(session, ctx, level=policy.level, action=ACTION_FULL_SOLUTION)

    if not allowed:
        return AssistanceResult(
            allowed=False,
            action=ACTION_FULL_SOLUTION,
            level=policy.level,
            reason=reason,
            policy=policy_dict,
        )

    from app.llm.gateway import LLMGatewayError, chat_completion

    messages = [
        {
            "role": "system",
            "content": (
                "你是算法竞赛教练。给出这道题的**完整可运行解法**，"
                f"语言：{language}。先一句话点明思路，再给代码。"
            ),
        },
        {"role": "user", "content": statement},
    ]
    try:
        text = chat_completion(
            messages, temperature=0.3, max_tokens=1500, provider=provider
        )
    except LLMGatewayError as exc:
        logger.warning("assistance: 完整解法调用失败：%s", exc)
        return AssistanceResult(
            allowed=True,
            action=ACTION_FULL_SOLUTION,
            level=policy.level,
            reason="AI 暂时不可用，请稍后再试。",
            policy=policy_dict,
        )

    return AssistanceResult(
        allowed=True,
        action=ACTION_FULL_SOLUTION,
        level=policy.level,
        reason="五档：AI 直接给出完整解法，请以对照学习为目的使用。",
        content=text.strip(),
        policy=policy_dict,
    )


def handle_insert(
    session: Session,
    ctx: AssistanceContext,
    *,
    level: Optional[int],
    instruction: str,
    current_code: str = "",
    language: str = "cpp",
    provider: Optional[str] = None,
) -> AssistanceResult:
    """请求 AI 写一段代码插进编辑器（四档起）。

    四档与五档的差别在这里体现：四档要求**只写一部分**并由提示词限制
    篇幅，五档不设此限。
    """
    policy = policy_for(level)
    policy_dict = policy_to_dict(policy)
    allowed, reason = decide(level, ACTION_INSERT)

    record_usage(session, ctx, level=policy.level, action=ACTION_INSERT)

    if not allowed:
        return AssistanceResult(
            allowed=False,
            action=ACTION_INSERT,
            level=policy.level,
            reason=reason,
            policy=policy_dict,
        )

    from app.llm.gateway import LLMGatewayError, chat_completion

    if policy.allows_full_solution:
        system = f"你是算法助手。按学生要求写出代码，语言：{language}。只输出代码。"
    else:
        system = (
            "你是算法助手。**只写学生指出的那一小部分**，不要顺手完成整道题，"
            "不要解释。语言：" + language + "。只输出代码。"
        )

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"当前代码：\n{current_code}\n\n要求：{instruction}",
        },
    ]
    try:
        text = chat_completion(
            messages, temperature=0.3, max_tokens=900, provider=provider
        )
    except LLMGatewayError as exc:
        logger.warning("assistance: 写码调用失败：%s", exc)
        return AssistanceResult(
            allowed=True,
            action=ACTION_INSERT,
            level=policy.level,
            reason="AI 暂时不可用，请稍后再试。",
            policy=policy_dict,
        )

    return AssistanceResult(
        allowed=True,
        action=ACTION_INSERT,
        level=policy.level,
        reason=reason,
        content=text.strip(),
        policy=policy_dict,
    )


def assignment_level(session: Session, assignment_id: Optional[int]) -> int:
    """取某次作业的辅助强度 —— 也就是老师设的**上限**。

    作业不存在或 ``assignment_id`` 为空时返回**默认档**（三档）。默认取
    中间而不是最高，理由同 :func:`app.assistance.normalize_level`：配置缺失
    时偏向严格的那一侧。

    注意这是**上限**不是最终生效值。学生可以自愿往下调，两者取小才是
    真正裁决用的档位 —— 见 :func:`effective_level`。
    """
    from app.assistance import DEFAULT_ASSISTANCE_LEVEL
    from app.models.assignment import Assignment

    if assignment_id is None:
        return DEFAULT_ASSISTANCE_LEVEL
    row = session.execute(
        select(Assignment.assistance_level).where(Assignment.id == assignment_id)
    )
    value = row.scalar_one_or_none()
    if value is None:
        return DEFAULT_ASSISTANCE_LEVEL
    return policy_for(value).level


def student_preference(
    session: Session,
    student_id: Optional[int],
    assignment_id: Optional[int],
) -> Optional[int]:
    """取某个学生在这份作业上**自己选的**档位。没选过返回 ``None``。

    返回 ``None`` 与返回一个数字是不同的事：``None`` 表示"没表达过意愿"，
    调用方应当直接用老师给的档位；一个数字表示"他自己要这个"。**不要**
    在这里兜底成默认档 —— 那会让"没选过"和"选了默认那一档"变得无法区分。
    """
    from app.models.assistance_preference import AssistancePreference

    if student_id is None:
        return None
    row = session.execute(
        select(AssistancePreference.level).where(
            AssistancePreference.student_id == student_id,
            # NULL 的比较在 SQL 里不成立，所以自由练习要显式用 IS NULL。
            (
                AssistancePreference.assignment_id.is_(None)
                if assignment_id is None
                else AssistancePreference.assignment_id == assignment_id
            ),
        )
    )
    value = row.scalar_one_or_none()
    if value is None:
        return None
    return policy_for(value).level


def effective_level(
    session: Session,
    student_id: Optional[int],
    assignment_id: Optional[int],
) -> int:
    """真正生效的档位 = ``min(老师设的上限, 学生自己选的)``。

    **这是全项目唯一的"我这次能用多少 AI"的定义**，四个动作接口都走它。

    取小的方向是这条规则的全部安全性所在：学生的意愿只能**收紧**不能
    放宽。老师把一份作业压到一档而学生想用五档，得到一档 —— 往上调在
    这里根本表达不出来，不是靠界面藏起来。

    学生没表达意愿（``None``）时直接用老师的档位，而不是当成"一档"：
    "没选过"不是"选了最严的"，把它当后者会让每个学生默认被降级。
    """
    ceiling = assignment_level(session, assignment_id)
    wanted = student_preference(session, student_id, assignment_id)
    if wanted is None:
        return ceiling
    return min(ceiling, wanted)


def set_student_preference(
    session: Session,
    student_id: int,
    assignment_id: Optional[int],
    level: Optional[int],
) -> Optional[int]:
    """记录（或清除）学生在某份作业上的辅助强度意愿。

    ``level=None`` 表示**清除意愿、回到老师给的上限**，而不是"设成某一档"。
    这是必要的第三种状态：学生试过一档之后想恢复默认，如果只能选一个
    数字，他就必须记得老师设的是几档，而那个数字可能已经被改过。

    传入的 ``level`` 会先被夹到 ``[1, 老师的上限]``，但**存的是夹过之后
    的值**而不是原样存下再在读取时夹 —— 这样读路径不必每次都多查一次
    老师的档位。代价是老师之后**调高**上限时，学生不会自动跟着变宽，
    这是刻意的：学生的"我要少用 AI"是一个仍然成立的意愿，不该因为老师
    放松了要求就替他作废。

    Returns:
        真正存下来的档位；``level=None`` 时为 ``None``。
    """
    from app.models.assistance_preference import AssistancePreference

    existing = session.execute(
        select(AssistancePreference).where(
            AssistancePreference.student_id == student_id,
            (
                AssistancePreference.assignment_id.is_(None)
                if assignment_id is None
                else AssistancePreference.assignment_id == assignment_id
            ),
        )
    ).scalar_one_or_none()

    if level is None:
        if existing is not None:
            session.delete(existing)
            session.commit()
        return None

    ceiling = assignment_level(session, assignment_id)
    clamped = min(max(policy_for(level).level, 1), ceiling)

    if existing is None:
        session.add(
            AssistancePreference(
                student_id=student_id,
                assignment_id=assignment_id,
                level=clamped,
            )
        )
    else:
        existing.level = clamped
    session.commit()
    return clamped



def usage_summary(
    session: Session,
    *,
    assignment_id: Optional[int] = None,
) -> dict:
    """按学生 × 动作汇总使用情况，给老师端看。

    返回 ``{"total": n, "by_action": {...}, "by_student": {...}}``。

    用 Python 汇总而不是 SQL ``GROUP BY``：数据量是"一次作业的学生数 ×
    四种动作"，最多几百行，而这段代码要同时跑在 PostgreSQL 与测试用的
    SQLite 上 —— 两套方言的日期/字符串函数差异会让一条简单查询变成两个
    分支。真到了需要 GROUP BY 的规模，再换不迟。
    """
    from app.models.assistance_usage import ACTION_LABELS

    query = select(AssistanceUsage)
    if assignment_id is not None:
        query = query.where(AssistanceUsage.assignment_id == assignment_id)
    rows = session.execute(query).scalars().all()

    by_action: dict[str, int] = dict.fromkeys(ACTION_LABELS, 0)
    by_student: dict[int, dict[str, Any]] = {}
    for row in rows:
        by_action[row.action] = by_action.get(row.action, 0) + 1
        key = row.student_id if row.student_id is not None else 0
        bucket = by_student.setdefault(
            key,
            {"student_id": row.student_id, "total": 0, "actions": {}},
        )
        bucket["total"] += 1
        bucket["actions"][row.action] = bucket["actions"].get(row.action, 0) + 1

    return {
        "total": len(rows),
        "by_action": by_action,
        "by_action_labeled": {
            ACTION_LABELS.get(key, key): value for key, value in by_action.items()
        },
        "by_student": list(by_student.values()),
    }
