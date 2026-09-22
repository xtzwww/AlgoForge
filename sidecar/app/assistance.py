"""AI 辅助强度的五档定义（阶段 5 · 因材施教）。

这是档位的**单一真相来源**。老师发布作业时选一档，学生做题时后端按档位
决定「AI 能做什么、不能做什么」，两侧都必须读这里，不许各自判断。

为什么要落在后端而不是前端
--------------------------
前端限制是建议，后端限制是规则。如果把「三档只能对话不能写代码」做在
界面上，学生改几个字符就能绕过；而这是**教学**场景 —— 老师压辅助强度
是评分公平的一部分，不是 UI 偏好。所以档位由作业带入请求、在服务端裁决，
前端只负责把允许的东西渲染出来。

五档在做什么
------------
=========  ==========================  ==========  ==========
档位        学生能得到的帮助               能拿代码?    消耗模型?
=========  ==========================  ==========  ==========
1 · 无辅助  本地查表补全（不联网）           能（自己的） 否
2 · 补全    联网的 AI 补全                  能（片段）   是
3 · 提示    侧栏对话，只给思路              不能         是
4 · 部分    可以给出一部分代码              能（不完整） 是
5 · 代做    直接给完整解法                  能           是
=========  ==========================  ==========  ==========

**一档是唯一不调用模型的档位。** 这不是省钱的取舍，而是它的定义：
「不联网」意味着补全完全由本机根据已有文本算出，所以断网可用、零延迟、
零成本。任何需要模型的实现都不能叫一档。

与 S7 提示阶梯的关系
--------------------
`agent_hint_ladder`（提示阶梯）是**一档到三档的实现载体**：它按由浅入深
分级，档位决定学生能翻到第几级。两者不是两套机制，是三档共用一套内容、
用档位卡住深度。所以这里只定「允许什么」，内容仍归 S7。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 档位的合法范围。写出来而不是散落在校验里，是为了让「有几档」只有一处答案。
MIN_ASSISTANCE_LEVEL = 1
MAX_ASSISTANCE_LEVEL = 5

DEFAULT_ASSISTANCE_LEVEL = 3


@dataclass(frozen=True)
class AssistancePolicy:
    """一档允许什么。

    冻结，因为它描述的是规则而不是状态：一个档位的含义不该在运行期被改，
    否则「同一份作业对两个学生给了不同的帮助」会变成一个没人能复现的 bug。
    """

    level: int
    #: 面向老师的名字（发布作业时选的就是它）。
    label: str
    #: 面向学生的名字（学生看到的是「我现在被允许做什么」）。
    student_label: str
    #: 一句话说明，两端共用，避免两边措辞漂移。
    description: str
    #: 补全是否走模型。一档为 False —— 这是它「不联网」的实现含义。
    completion_uses_model: bool
    #: 是否允许开对话（三档起）。
    allows_chat: bool
    #: 对话是否只给思路、不得给出可直接粘贴的代码（三档为 True，四档放宽）。
    chat_is_hint_only: bool
    #: 是否允许把代码写进学生的编辑器（四档「一部分」、五档「完整」）。
    allows_code_insertion: bool
    #: 是否允许直接给完整解法（只有五档）。
    allows_full_solution: bool


#: 五档的全部定义。顺序即强弱，列表下标 + 1 就是档位数字。
_POLICIES: tuple[AssistancePolicy, ...] = (
    AssistancePolicy(
        level=1,
        label="一档 · 无 AI 辅助",
        student_label="本地补全",
        description="只有本机的代码补全，不联网、不调用 AI。用于检验基本功。",
        completion_uses_model=False,
        allows_chat=False,
        chat_is_hint_only=True,
        allows_code_insertion=False,
        allows_full_solution=False,
    ),
    AssistancePolicy(
        level=2,
        label="二档 · AI 补全",
        student_label="AI 补全",
        description="补全由 AI 提供，可以把代码补全得更聪明；不能对话、不能整段代写。",
        completion_uses_model=True,
        allows_chat=False,
        chat_is_hint_only=True,
        allows_code_insertion=False,
        allows_full_solution=False,
    ),
    AssistancePolicy(
        level=3,
        label="三档 · 只给提示",
        student_label="AI 提示",
        description="可以问 AI 要思路，但它只讲方向、不给能直接粘贴的代码。",
        completion_uses_model=True,
        allows_chat=True,
        chat_is_hint_only=True,
        allows_code_insertion=False,
        allows_full_solution=False,
    ),
    AssistancePolicy(
        level=4,
        label="四档 · 部分代写",
        student_label="AI 部分代写",
        description="AI 可以帮你写出一部分代码，剩下的仍要自己想。",
        completion_uses_model=True,
        allows_chat=True,
        chat_is_hint_only=False,
        allows_code_insertion=True,
        allows_full_solution=False,
    ),
    AssistancePolicy(
        level=5,
        label="五档 · AI 代做",
        student_label="AI 代做",
        description="AI 可以直接给出完整解法。用于对照学习。",
        completion_uses_model=True,
        allows_chat=True,
        chat_is_hint_only=False,
        allows_code_insertion=True,
        allows_full_solution=True,
    ),
)


def normalize_level(level: Optional[int]) -> int:
    """把任意输入收进合法档位。

    越界或缺失一律落到 :data:`DEFAULT_ASSISTANCE_LEVEL`（三档）。**故意不抛错**：
    这个值来自数据库里的历史行，一条脏数据不该让页面打不开。取中间档而不是
    最高档，是因为「配置读坏了」时给学生的帮助应当是中等而非全开 —— 出错时
    偏向严格的那一侧。
    """
    if level is None:
        return DEFAULT_ASSISTANCE_LEVEL
    if not isinstance(level, int) or isinstance(level, bool):
        return DEFAULT_ASSISTANCE_LEVEL
    if level < MIN_ASSISTANCE_LEVEL or level > MAX_ASSISTANCE_LEVEL:
        return DEFAULT_ASSISTANCE_LEVEL
    return level


def policy_for(level: Optional[int]) -> AssistancePolicy:
    """取某个档位的规则。任何输入都返回一个规则，不会失败。"""
    return _POLICIES[normalize_level(level) - 1]


def all_policies() -> list[AssistancePolicy]:
    """全部档位，按由弱到强。老师发布作业时用它渲染选项。"""
    return list(_POLICIES)


def policy_to_dict(policy: AssistancePolicy) -> dict:
    """序列化成 API 能返回的形状。

    字段名用 ``level`` / ``label`` / ``allows_*`` 而不是缩写，因为前端和
    答辩讲解都要直接读它 —— 一个字段名的含义不该靠补文档才能看懂。
    """
    return {
        "level": policy.level,
        "label": policy.label,
        "student_label": policy.student_label,
        "description": policy.description,
        "completion_uses_model": policy.completion_uses_model,
        "allows_chat": policy.allows_chat,
        "chat_is_hint_only": policy.chat_is_hint_only,
        "allows_code_insertion": policy.allows_code_insertion,
        "allows_full_solution": policy.allows_full_solution,
    }


def describe_for_student(level: Optional[int]) -> str:
    """学生界面顶部的说明。写在这里，两端共用同一句话。"""
    return policy_for(level).description
