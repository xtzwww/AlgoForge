"""辅助强度的规则与裁决（阶段 5）。

钉的是**规则本身**，因为这些错误全都是静默的：

* 档位越界悄悄变成最高档 → 老师压的强度等于没有，但界面上一切正常；
* 三档的内容门失效 → AI 直接把答案给出来，老师以为三档是"只给提示"；
* 拒绝发生在调用模型之后 → 功能上"被拒了"，但钱已经花了；
* 留痕只在成功时记 → 老师看到的使用量比真实少，而且少的是"被拒绝的尝试"
  这一类最该被看到的行为。

不测的：真实模型调用（要端点、要钱），以及界面（要人眼）。
"""

from __future__ import annotations

import pytest

from app.assistance import (
    DEFAULT_ASSISTANCE_LEVEL,
    MAX_ASSISTANCE_LEVEL,
    MIN_ASSISTANCE_LEVEL,
    all_policies,
    normalize_level,
    policy_for,
    policy_to_dict,
)
from app.assistance_service import (
    decide,
    downgrade_to_hint,
    local_completion_candidates,
    looks_like_solution,
)
from app.models.assistance_usage import (
    ACTION_CHAT,
    ACTION_COMPLETE,
    ACTION_FULL_SOLUTION,
    ACTION_INSERT,
)


def test_合法档位原样返回():
    for level in range(MIN_ASSISTANCE_LEVEL, MAX_ASSISTANCE_LEVEL + 1):
        assert normalize_level(level) == level

def test_越界落到默认档而不是钳到边界():
    # 钳到边界会静默地把 0 变成"一档"、99 变成"五档"，两者都改变
    # 了老师的意思。落到中间档是明确的"配置坏了"。
    assert normalize_level(0) == DEFAULT_ASSISTANCE_LEVEL
    assert normalize_level(99) == DEFAULT_ASSISTANCE_LEVEL
    assert normalize_level(-3) == DEFAULT_ASSISTANCE_LEVEL

def test_缺失与脏类型都落到默认档而不抛错():
    # 这个值来自数据库里的历史行，一条脏数据不该让页面打不开。
    for bad in (None, "3", 3.5, True, False, [], {}):
        assert normalize_level(bad) == DEFAULT_ASSISTANCE_LEVEL

def test_默认档取中间而不是最高():
    # 出错时偏向严格的一侧：既不能"全开"，也不该"全关"到改变原意。
    assert DEFAULT_ASSISTANCE_LEVEL == 3
    assert MIN_ASSISTANCE_LEVEL < DEFAULT_ASSISTANCE_LEVEL < MAX_ASSISTANCE_LEVEL


def test_五档齐全且编号连续():
    policies = all_policies()
    assert [p.level for p in policies] == [1, 2, 3, 4, 5]

def test_一档不调用模型_这是它的定义():
    assert policy_for(1).completion_uses_model is False
    # 其余四档都走模型 —— 否则"联网补全"和"AI 提示"就没有区别了。
    for level in (2, 3, 4, 5):
        assert policy_for(level).completion_uses_model is True

def test_能力随档位单调递增():
    # 五档是包含关系：高档允许的一定比低档多。反过来（低档允许高档
    # 不允许的事）会让"调高辅助强度"变成一种惩罚。
    levels = [policy_for(n) for n in range(1, 6)]
    for lower, higher in zip(levels, levels[1:], strict=False):
        assert higher.allows_chat >= lower.allows_chat
        assert higher.allows_code_insertion >= lower.allows_code_insertion
        assert higher.allows_full_solution >= lower.allows_full_solution

def test_只有三档起能对话_只有四档起能写码_只有五档给整解():
    assert [p.allows_chat for p in all_policies()] == [False, False, True, True, True]
    assert [p.allows_code_insertion for p in all_policies()] == [
        False,
        False,
        False,
        True,
        True,
    ]
    assert [p.allows_full_solution for p in all_policies()] == [
        False,
        False,
        False,
        False,
        True,
    ]

def test_三档只给提示是开着的_四档起才关():
    assert [p.chat_is_hint_only for p in all_policies()] == [
        True,
        True,
        True,
        False,
        False,
    ]

def test_序列化带上前端要用的每个字段():
    data = policy_to_dict(policy_for(3))
    for key in (
        "level",
        "label",
        "student_label",
        "description",
        "completion_uses_model",
        "allows_chat",
        "chat_is_hint_only",
        "allows_code_insertion",
        "allows_full_solution",
    ):
        assert key in data, f"缺字段 {key}"


def test_三档拒绝写码_理由必须说出来():
    allowed, reason = decide(3, ACTION_INSERT)
    assert allowed is False
    # 一句"不允许"不够 —— 学生要知道为什么、以及什么可以做。
    assert reason.strip()
    assert "不允许" in reason or "只能" in reason

def test_三档拒绝整解_五档放行():
    assert decide(3, ACTION_FULL_SOLUTION)[0] is False
    assert decide(5, ACTION_FULL_SOLUTION)[0] is True

def test_一二档拒绝对话():
    assert decide(1, ACTION_CHAT)[0] is False
    assert decide(2, ACTION_CHAT)[0] is False
    assert decide(3, ACTION_CHAT)[0] is True

def test_补全在任何档位都允许_差别在走不走模型():
    # 一档的补全是本机的，所以"允许" —— 只是它不联网。
    for level in range(1, 6):
        assert decide(level, ACTION_COMPLETE)[0] is True

def test_未知动作不放过():
    allowed, reason = decide(5, "delete_everything")
    assert allowed is False
    assert reason.strip()


def test_从已写代码里摘标识符():
    prefix = "int main() { int total = 0; total += 1; ret"
    candidates = local_completion_candidates(prefix)
    assert "return" in candidates or "ret" in "".join(candidates)

def test_只给前缀匹配的候选():
    candidates = local_completion_candidates("cou")
    # 全部应当以 cou 开头，否则补全会插入一个学生没在打的名字。
    assert all(c.startswith("cou") for c in candidates), candidates
    assert "cout" in candidates

def test_光标不在标识符中间时不给候选():
    # 在运算符后给候选等于替学生猜他要写什么 —— 一档不做这种事。
    assert local_completion_candidates("a + ") == []
    assert local_completion_candidates("") == []

def test_候选数量有上限():
    candidates = local_completion_candidates("c", limit=3)
    assert len(candidates) <= 3

def test_不重复给出同一个词():
    candidates = local_completion_candidates("int counter; int cou")
    assert len(candidates) == len(set(candidates))


def test_认得出带围栏的代码块():
    assert looks_like_solution("可以这样写：\n```cpp\nint x = 1;\n```") is True

def test_认得出函数定义():
    assert looks_like_solution("def solve():\n    pass") is True
    assert looks_like_solution("int main() {\n  return 0;\n}") is True

def test_不把正常的中文提示当成代码():
    # 这条是保守判据的理由：误判会让三档变成"什么都不说"，比漏判更糟。
    hint = "你可以想想：如果数组已经有序，能不能跳过一些比较？"
    assert looks_like_solution(hint) is False
    assert looks_like_solution("注意 for 循环的边界，想想循环不变式是什么。") is False


def test_空回复不算代码():
    assert looks_like_solution("") is False
    assert looks_like_solution("   ") is False


def test_摘掉代码块但留下思路():
    text = (
        "思路是先排序再双指针。\n"
        "```cpp\nsort(a.begin(), a.end());\n```\n"
        "这样能把 O(n²) 降到 O(n log n)。"
    )
    result = downgrade_to_hint(text)
    # 代码没了
    assert "sort(" not in result
    # 但它本来在讲什么还在 —— 整段丢掉等于什么都没帮
    assert "排序" in result
    assert "O(n log n)" in result

def test_整段就是代码时不把原文放过去():
    text = "```cpp\nint main() { return 0; }\n```"
    result = downgrade_to_hint(text)
    assert "main" not in result
    # 也得给一句话，不能返回空串
    assert result.strip()


def test_档位号与列表下标一致_不会错位():
    # 错位是最危险的一类：任何地方用 _POLICIES[level - 1] 取档，
    # 错位后每一档都在描述别人，而所有断言仍然通过。
    for index, policy in enumerate(all_policies()):
        assert policy.level == index + 1
        assert policy_for(index + 1).level == index + 1

def test_描述不为空_两端要显示同一句话():
    for policy in all_policies():
        assert policy.label.strip()
        assert policy.student_label.strip()
        assert policy.description.strip()

@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_每一档都能取出策略且不会异常(level: int):
    assert policy_for(level).level == level
