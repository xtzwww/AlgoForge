"""P2-22：端到端集成测试 —— 一道题从写完到排进复习队列。

``VIBECODING.md`` 的 P2-22 要求把这条链路整条走一遍：

    写代码 → 运行 → WA → Agent 分析 → 错因入库 → 错题本可见 → 画像更新 → 复习调度

在此之前，每一段都被单独测过（``test_judge_integration.py`` 测判题、
``test_agent_analysis_recording.py`` 测分析落库、``test_review.py`` 测调度），
但**没有一条穿过整条链**。这留下了分段测试抓不到的一类缺陷：每一段都对，
而它们接起来的地方是断的——例如分析写进去了、错题本接口却读不出来，或者
提交记了、画像的数字却没动。两次假绿都出在这个缝里。

所以这里的断言不是「没报错」，而是**每一环都要拿出一个值**：
判题给出 WA、提交落了一行、错因标签写着具体字符串、错题本**序列化**之后
有内容、画像里那个知识点的 total 从无到 1、复习记录被排到了明天。

⚠️ 两个刻意的取舍
------------------
1. **不走 HTTP。** 判题路由走 ``app.db.get_session``（async + asyncpg），
   而套件跑在 SQLite 上、本机没装 ``aiosqlite``，异步 SQLite 方言不存在。
   所以穿的是**服务层**边界而不是网络边界：HTTP 那一层由
   ``tests/test_pg_business_flow.py``（真实 PostgreSQL）和各 API 测试覆盖。
2. **“错题本可见”断言的是序列化结果**，不是 ORM 行。查到行只能证明数据在库
   里；``_entry_to_out`` 是接口真正用来组装响应体的函数，断言它的输出才等于
   断言「学生打开页面能看到什么」——字段名写错、描述没查出来，都会在这里现形。

⚠️ 与所有测试一样，真实编译由沙箱完成，模型调用是脚本化的
----------------------------------------------------------
编译是真的（``judge`` 真的调 g++），所以这条测试受机器负载影响，时间上限用
``EXEC_TIME_LIMIT`` 而不是 5 秒——本机起一个进程就要 5 秒以上（见 conftest）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.mistake_book import _entry_to_out
from app.llm.gateway import Completion
from app.models.base import Base
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.review import ReviewRecord
from app.models.submission import Submission
from app.records import SubmissionContext, record_submission
from app.sandbox.judge import JudgeStatus, TestCase, judge
from app.scheduler.sm2 import SrsState, is_due, schedule
from conftest import EXEC_TIME_LIMIT

# 题目是「两数之和」，学生写成了减法 —— 编译得过、跑得出、答案错。
_CPP_WA = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a - b << std::endl;
    return 0;
}
"""

_CPP_AC = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""

_CASES = [TestCase(input="3 5\n", expected_output="8\n")]

#: 一个合法的 P2-2 分析载荷。写成字面量，不依赖 analyzer 自己的 fixture。
_SCRIPTED_ANALYSIS = (
    '{"error_type":"misread_problem",'
    '"error_detail":"题目要求求和，你的程序做的是减法",'
    '"hint":"看看输出那一行用的是哪个运算符",'
    '"suggested_fix":"把 a - b 改成 a + b"}'
)

#: 复习调度用的固定「现在」，避免跨午夜的偶发。
_NOW = datetime(2026, 9, 21, 12, 0, 0)


def _scripted(*_args: object, **_kwargs: object) -> Completion:
    return Completion(content=_SCRIPTED_ANALYSIS, finish_reason="stop")


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite，StaticPool 共享一条连接。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def problem(session: Session) -> Problem:
    row = Problem(
        id=1,
        title="两数之和",
        description="读入两个整数，输出它们的和。",
        input_format="两个整数，空格分隔。",
        output_format="一个整数。",
        knowledge_tags=["模拟", "数学"],
        samples=[],
    )
    session.add(row)
    session.commit()
    return row


def _tags(session: Session) -> dict[str, int]:
    """当前画像：知识点 → 提交次数。"""
    return {s.tag: s.total for s in compute_knowledge_stats(session)}


def test_full_chain_from_wa_to_review_schedule(
    session: Session, problem: Problem, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写代码 → WA → 分析 → 入库 → 错题本可见 → 画像更新 → 复习调度。"""
    # --- 1–2. 写代码，运行，判 WA（真实编译） --------------------------------
    result = judge(
        _CPP_WA,
        _CASES,
        time_limit=EXEC_TIME_LIMIT,
        mem_limit_mb=256,
        language="cpp",
    )
    assert result.status is JudgeStatus.WA, f"判题没给出 WA：{result.status}"
    # 失败用例不能是空的：没有用例的 WA 会被判成「没东西可分析」，错因标签
    # 永远写不进去，而后面每一环都吊在它身上。
    assert result.test_cases, "WA 却没有任何失败用例"

    # --- 3. 画像：提交之前，这个知识点还没有出现过 --------------------------
    assert "模拟" not in _tags(session), "开局的画像不该已经有这个知识点"

    # --- 4–5. Agent 分析并随提交一起落库 ------------------------------------
    # 两处都换：默认编排走 analyzer，graph / team 编排走工具循环里的
    # complete。只换一处的话，换条 pipeline 这条测试就静默变成在测别的。
    monkeypatch.setattr("app.agent.analyzer.chat_completion", _scripted)
    monkeypatch.setattr("app.agent.tool_caller.complete", _scripted)

    submission_id = record_submission(
        result,
        SubmissionContext(code=_CPP_WA, language="cpp", problem_id=problem.id),
        session=session,
    )
    assert submission_id is not None

    submission = session.get(Submission, submission_id)
    assert submission is not None
    assert submission.status == "WA", f"落库的判定是 {submission.status}"

    # --- 6. 错因入库 --------------------------------------------------------
    entry = session.execute(select(MistakeBookEntry)).scalars().one()
    assert str(entry.agent_error_type) == "misread_problem"

    # --- 7. 错题本可见：走接口用来组装响应体的那个函数 ----------------------
    # 查到行只证明数据在库里；这里断言的是学生打开页面会看到什么。
    visible = _entry_to_out(entry)
    assert visible.error_type_description, "错因标签没有中文描述，学生看不懂机器标签"
    assert "减法" in visible.error_detail
    assert "运算符" in visible.hint

    # --- 8. 画像更新：同一个知识点现在有一次提交了 --------------------------
    after = _tags(session)
    assert after.get("模拟") == 1, f"画像没动：{after}"
    assert after.get("数学") == 1, f"第二个知识点也没进画像：{after}"
    # 这次是错的，所以通过数是 0 —— 画像要能区分「做过」和「做对」。
    passed = {s.tag: s.passed for s in compute_knowledge_stats(session)}
    assert passed["模拟"] == 0

    # --- 9. 复习调度：新条目立刻到期，复习一次后排到明天 --------------------
    record = ReviewRecord(entry_id=entry.id)
    record.apply_state(SrsState())
    session.add(record)
    session.commit()
    assert is_due(record.to_state(), now=_NOW), "没复习过的条目应当立刻到期"

    record.apply_state(schedule(record.to_state(), quality=4, now=_NOW))
    session.commit()

    assert record.repetitions == 1
    assert record.interval_days == 1
    assert record.due_at == _NOW + timedelta(days=1)
    assert not is_due(
        record.to_state(), now=_NOW + timedelta(hours=1)
    ), "刚复习完不该马上又到期"


def test_a_correct_submission_does_not_reach_the_mistake_book(
    session: Session, problem: Problem, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一条链路走一遍 AC：错因不该凭空出现。

    这条存在的理由：上一条断言「错题本里有一条」，而一条**无条件**写入的
    错题本也能让它通过。要让「链路接通」这个说法有意义，就得同时证明它对
    通过的提交是安静的——否则「可见」断言测的是写入，不是判题。
    """
    result = judge(
        _CPP_AC,
        _CASES,
        time_limit=EXEC_TIME_LIMIT,
        mem_limit_mb=256,
        language="cpp",
    )
    assert result.status is JudgeStatus.AC, f"判题没给出 AC：{result.status}"

    monkeypatch.setattr("app.agent.analyzer.chat_completion", _scripted)
    monkeypatch.setattr("app.agent.tool_caller.complete", _scripted)

    record_submission(
        result,
        SubmissionContext(code=_CPP_AC, language="cpp", problem_id=problem.id),
        session=session,
    )

    assert session.execute(select(MistakeBookEntry)).scalars().all() == []
    # 但提交还是记下来了，画像也算进去了 —— 安静的是错题本，不是记录。
    assert _tags(session).get("模拟") == 1


def test_a_forgotten_review_resets_the_schedule(
    session: Session, problem: Problem
) -> None:
    """复习调度这一段自己也要有负向断言：答不出来要回到明天、连击清零。

    挂在一道真实存在的错题上，而不是拿 ``entry_id=1`` 顶上：SQLite 不强制
    外键，写个不存在的 id 也能过，但那测的就不是产品里的约束了。
    """
    entry = MistakeBookEntry(submission_id=1, problem_id=problem.id)
    session.add(entry)
    session.commit()

    record = ReviewRecord(entry_id=entry.id)
    record.apply_state(SrsState(repetitions=3, interval_days=6, easiness_factor=2.5))
    session.add(record)
    session.commit()

    record.apply_state(schedule(record.to_state(), quality=1, now=_NOW))
    session.commit()

    assert record.repetitions == 0, "忘了就该清零，不能接着算连击"
    assert record.interval_days == 1
    assert is_due(record.to_state(), now=_NOW + timedelta(days=1))


def test_the_scripted_reply_is_actually_used(
    session: Session, problem: Problem, monkeypatch: pytest.MonkeyPatch
) -> None:
    """防「脚本没接上、分析其实失败了，而其余断言照样过」。

    这条是本模块的自检：如果哪天分析路径换了调用点（比如新增一层编排），
    ``_scripted`` 会落在一个没人读的名字上，分析静默失败——上面的用例里
    “错题本可见”那几条会立刻红，但红的原因是“没内容”，看不出是脚本没接上
    还是链路断了。这里直接断言那次调用**发生过**。
    """
    result = judge(
        _CPP_WA,
        _CASES,
        time_limit=EXEC_TIME_LIMIT,
        mem_limit_mb=256,
        language="cpp",
    )
    assert result.status is JudgeStatus.WA

    with patch("app.agent.tool_caller.complete", side_effect=[_scripted()]) as mock:
        monkeypatch.setattr("app.agent.analyzer.chat_completion", _scripted)
        record_submission(
            result,
            SubmissionContext(code=_CPP_WA, language="cpp", problem_id=problem.id),
            session=session,
        )

    assert mock.call_count >= 1, "脚本化的回复一次都没被调用：分析根本没跑到模型"
