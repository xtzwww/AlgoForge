"""真实 PostgreSQL 下的完整业务链路冒烟（队列 T26）。

为什么有这个文件
----------------
README §九 长期写着「未验：真 PostgreSQL 下的完整业务链路」，而 §四 有六行功能
标着「⚠️ 未目视验证」（分层释放 / 历史联动 / 编辑埋点 / 教师端看板 / 作业 /
过程标记复核）。在这之前，这些功能的验证只到「接口返回 200」——那既证明不了
字段名对不对，也证明不了数据真的落进了 ``AGENTS.md`` 指定的那个数据库。

本文件把那六项**按接口逐个调一遍并断言返回体里的关键字段**，全程跑在真实
PostgreSQL 上，走的是**没被打过补丁的**会话路径（``app.db.get_session`` →
``settings.database_url``）。

⚠️ 它会**清空所有表**再自己造数据
--------------------------------
和 ``tests/test_postgres_aggregation.py`` 同一个约定：指向的必须是一个
**临时库**。``tools/pg_business_smoke.sh`` 会专门建一个 ``algoforge_smoke``，
不碰应用在用的 ``algoforge``。

⚠️ 没有 ``ALGOFORGE_PG_TEST_URL`` 时**跳过，并且跳过理由要说出「没测」**
--------------------------------------------------------------------
理由文案照 ``pytest.ini`` 对 ``eval_set`` 那条规矩写：一个读起来像「通过」的
skip，就是一次被悄悄跳过的测量。要手动跑：

    ALGOFORGE_PG_TEST_URL=postgresql+psycopg://u:p@host:5432/db \\
        pytest tests/test_pg_business_flow.py -q

或者直接用脚本（起库 → 建库 → 迁移 → 跑本文件 → 停库，全在一次调用内）：

    bash tools/pg_business_smoke.sh

本文件补的两个缺口（都是别处测不到的）
--------------------------------------
1. **``recurrence`` 只走 WebSocket。** 它是「历史联动」的全部内容，而后端只有
   ``records_analyse.py`` 把它放进 ``agent_done`` 事件里——错题本的 HTTP 响应
   里没有它。所以这一项**必须**通过 WS 驱动，不能只调 REST。
2. **写入路径不能被 conftest 关掉。** ``conftest._no_real_submission_writes``
   会把判题路由的记录钩子换成空操作（否则整套测试会去连真 PG 而挂住）。本模块
   要的正是真实写入，所以在自己的 fixture 里把它换回来——这也是为什么它必须
   在真实 PG 上跑，而不是像其他模块那样把 ``get_session`` 换成 SQLite。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from app.auth import create_user, issue_session
from app.llm.gateway import Completion
from app.main import app
from app.models.assignment import Assignment
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.models.user import ROLE_TEACHER
from app.process_trace import ProcessTrace, describe, from_trace
from app.records import SubmissionContext, record_submission
from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult
from conftest import TEST_SIDECAR_HEADERS, TEST_SIDECAR_TOKEN

PG_URL_ENV = "ALGOFORGE_PG_TEST_URL"

#: 脚本化的分析回复。三个段落齐全，且 ``error_type`` 是一个已知标签——
#: 「历史联动」按标签跨题统计，用一个 schema 里不存在的标签会让那条断言
#: 因为「没人犯过」而通过，而不是因为历史真的被算出来了。
SCRIPTED_ANALYSIS = (
    '{"error_type":"overflow","error_detail":"a * b 在 int 内先溢出",'
    '"hint":"看看乘法的操作数是什么类型","suggested_fix":"让运算前就是 64 位"}'
)

#: ``ProcessFeatures`` 的十个标量。写死一份，才挡得住「悄悄少存一个特征」——
#: 少一个不会报错，只会让教师端的证据面板少一行。
FEATURE_KEYS = {
    "duration_ms",
    "edit_count",
    "paste_count",
    "pasted_chars",
    "final_length",
    "paste_ratio",
    "post_paste_edits",
    "post_paste_chars",
    "rewrite_ratio",
    "runs_before_submit",
}

#: 两个题目的测试用例：读两个整数、求和。
_SUM_CASES = [("3 5\n", "8\n"), ("-5 5\n", "0\n")]

#: 一个**算错**的 C++ 程序：输出 a-b，所以两个用例都 WA。
_WA_CPP = (
    "#include <iostream>\n"
    "int main(){int a,b;std::cin>>a>>b;std::cout<<a-b<<std::endl;return 0;}\n"
)

#: 一次「大段粘贴」的编辑轨迹：1200 字符在 5 秒处一次性进来。
#: 阈值是 ≥120 字符且 ≥80% 占比，所以它会被标记（见 ``app/process_trace``）。
_PASTE_TRACE = {
    "events": [
        {"t": 0, "kind": "open", "chars": 0, "length": 0},
        {"t": 5_000, "kind": "paste", "chars": 1200, "length": 1200},
    ]
}


@dataclass
class Smoke:
    """一次冒烟运行用到的东西：一个客户端、一套账号、两个题目。"""

    client: TestClient
    teacher_id: int
    teacher_token: str
    student_id: int
    student_token: str
    other_student_id: int
    problem_ids: tuple[int, int]
    session: Session

    def auth(self, token: str) -> dict[str, str]:
        """侧车凭证 + 用户会话，两者都要带。"""
        return {**TEST_SIDECAR_HEADERS, "Authorization": f"Bearer {token}"}


def _scripted_completion() -> Completion:
    return Completion(content=SCRIPTED_ANALYSIS, finish_reason="stop")


def _wa_result() -> JudgeResult:
    """一个「算错了」的判定结果，**带一个失败用例**。

    用例不能省。没有用例的 WA 会在分析入口被判成「没东西可分析」直接标
    ``failed``（``records_analyse`` 里那条 ``no failing case to analyse``
    日志），于是错因标签永远写不进去——而「历史联动」正是按标签查的。
    第一次写这个模块时就是这么踩的：种子数据看着有提交、有错题本条目，
    但一条标签都没有，于是 ``recurrence.count`` 恒为 0。
    """
    return JudgeResult(
        status=JudgeStatus.WA,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.WA,
                input="3 5\n",
                expected_output="8\n",
                actual_output="-2\n",
            )
        ],
    )


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch) -> Iterator[Smoke]:
    """把应用接到真实 PostgreSQL 上，造一份最小数据，再交给用例。

    三件事必须一起做，少一件这个模块就变成在测别的东西：

    1. ``settings.database_url`` 指向 PG + ``reset_engine()``——``app.db`` 把
       引擎缓存在模块级，不重置的话第一次 ``get_session`` 还会用旧的（可能
       指向没在跑的本机 DSN，表现是**挂住**而不是报错）。
    2. 把 conftest 关掉的写入钩子换回来（理由见模块 docstring）。
    3. 把网关调用脚本化——不脚本化的话 conftest 的 ``_refuse`` 会让分析失败，
       而「分析失败」正是这些用例要断言其**没有**发生的事。
    """
    url = os.environ.get(PG_URL_ENV)
    if not url:
        pytest.skip(
            f"{PG_URL_ENV} is not set, so the six business flows have NOT been "
            f"run against a real PostgreSQL in this run"
        )

    from app import db as db_module
    from app.agent import analyzer, tool_caller
    from app.api import judge as judge_api
    from app.api import ws as ws_api
    from app.config import settings
    from app.records import record_submission_quietly

    monkeypatch.setattr(settings, "database_url", url)
    db_module.reset_engine()

    # 真实写入：conftest 把它们换成空操作是为了让别的模块不连真库，这里正相反。
    monkeypatch.setattr(judge_api, "record_judge_outcome", record_submission_quietly)
    monkeypatch.setattr(ws_api, "record_judge_outcome", record_submission_quietly)

    # 脚本化模型：文本路径与工具循环路径各一处，两处都换才不会漏。
    def _reply(*_args: Any, **_kwargs: Any) -> Completion:
        return _scripted_completion()

    monkeypatch.setattr(analyzer, "chat_completion", _reply)
    monkeypatch.setattr(tool_caller, "complete", _reply)

    engine = create_engine(url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    session = factory()

    # 从空开始。指向的必须是临时库——见模块 docstring。
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.commit()

    try:
        seeded = _seed(session)
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as client:
            yield Smoke(client=client, session=session, **seeded)
    finally:
        session.close()
        engine.dispose()
        # 先丢引擎再让 monkeypatch 还原 URL：顺序反了的话，下一个模块会拿到
        # 一个指向 PG 的缓存引擎，而它以为自己在用 SQLite。
        db_module.reset_engine()


def _seed(session: Session) -> dict[str, Any]:
    """一个教师、两个学生、两个题目、以及学生 A 在题目 A 上的一次旧错。"""
    teacher = create_user(
        session, username="t_liu", password="pw-teacher", role=ROLE_TEACHER,
        display_name="刘老师",
    )
    student = create_user(
        session, username="s_zhang", password="pw-student", display_name="张三"
    )
    other = create_user(
        session, username="s_li", password="pw-student", display_name="李四"
    )

    problem_ids: list[int] = []
    for index in range(2):
        problem = Problem(
            title=f"求和 {index + 1}",
            description="读两个整数，输出它们的和。",
            knowledge_tags=["io-and-basic-syntax", "loops-and-conditions"],
            samples=[],
        )
        session.add(problem)
        session.flush()
        for ordinal, (stdin, expected) in enumerate(_SUM_CASES):
            session.add(
                TestCase(
                    problem_id=problem.id,
                    ordinal=ordinal,
                    input=stdin,
                    expected_output=expected,
                    is_sample=ordinal == 0,
                )
            )
        problem_ids.append(problem.id)
    session.commit()

    first, second = problem_ids

    # 「历史联动」的历史：同一个标签、**另一道题**上犯过。
    # 同题重试不算——`find_recurrence` 会把当前题排除掉，所以这条必须是别的题。
    record_submission(
        _wa_result(),
        SubmissionContext(
            code="int main(){/* 第一题上错的那次 */}",
            language="cpp",
            problem_id=first,
            user_id=student.id,
        ),
        session=session,
    )
    session.commit()

    return {
        "teacher_id": teacher.id,
        "teacher_token": issue_session(session, teacher),
        "student_id": student.id,
        "student_token": issue_session(session, student),
        "other_student_id": other.id,
        "problem_ids": (first, second),
    }


def _ws_run(
    smoke: Smoke,
    *,
    code: str,
    problem_id: int,
    token: str,
    process_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """跑一次 WS 判题，返回收到的全部消息（按顺序）。

    读到 ``agent_done`` / ``judge_error`` 就停——那正是服务端的收尾条件
    （见 ``app/api/ws.py`` 的读循环），所以这里不设超时也不会挂。
    """
    messages: list[dict[str, Any]] = []
    url = f"/ws/run-log?token={TEST_SIDECAR_TOKEN}"
    with smoke.client.websocket_connect(url) as ws:
        request: dict[str, Any] = {
            "code": code,
            "language": "cpp",
            "problem_id": problem_id,
            "user_token": token,
            "record": True,
        }
        if process_trace is not None:
            request["process_trace"] = process_trace
        ws.send_json(request)

        while True:
            message = ws.receive_json()
            messages.append(message)
            if message.get("type") in {"agent_done", "judge_error"}:
                break
    return messages


def _by_type(messages: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    found = [m for m in messages if m.get("type") == kind]
    assert found, f"没有收到 {kind} 事件，收到的是：{[m.get('type') for m in messages]}"
    return found[-1]


# --- 1 + 2 + 3：WS 一次跑通「分层释放 / 历史联动 / 编辑埋点」 -------------


def test_ws_run_carries_sections_history_and_process_view(smoke: Smoke):
    """一次 WA 提交，把三项反馈一次验完。

    合并成一个用例是有意的：它们出自**同一次** WS 往返，拆开会跑三遍真编译，
    而三者之间的一致性（同一个 ``submission_id`` 上的三段文案、历史、过程
    特征）恰恰是分开测就会丢掉的东西。
    """
    _, second = smoke.problem_ids

    messages = _ws_run(
        smoke,
        code=_WA_CPP,
        problem_id=second,
        token=smoke.student_token,
        process_trace=_PASTE_TRACE,
    )

    # --- 判定本身 ---
    verdict = _by_type(messages, "judge_done")
    assert verdict["status"] == "WA", "用例程序是错的，判定必须是 WA"
    assert verdict["analysing"] is True, "带 problem_id 的 WA 必须触发分析"

    # --- 功能 3：编辑埋点 → 10 个标量特征 + 一句话 ---
    process = verdict["process"]
    assert process is not None, "请求带了 process_trace，回包就必须有 process"
    assert set(process["features"]) == FEATURE_KEYS
    assert process["features"]["paste_count"] == 1
    assert process["features"]["pasted_chars"] == 1200
    assert isinstance(process["flagged"], bool)
    assert process["summary"], "有粘贴就必须给出一句人话，否则教师端只有数字"

    # --- 功能 1：分层释放的三段（顺序即释放顺序） ---
    done = _by_type(messages, "agent_done")
    assert done["ok"] is True, f"分析失败了：{done.get('error')}"
    assert done["error_detail"] == "a * b 在 int 内先溢出"
    assert done["hint"] == "看看乘法的操作数是什么类型"
    assert done["suggested_fix"] == "让运算前就是 64 位"
    assert done["error_type"] == "overflow"
    assert done["error_type_description"], "已知标签必须带上中文说法"

    # --- 功能 2：历史联动。旧错在**另一道题**上，所以 count 是 1 ---
    recurrence = done["recurrence"]
    assert recurrence["label"] == "overflow"
    assert recurrence["count"] == 1, f"跨题历史没算出来：{recurrence}"
    assert recurrence["problem_ids"] == [smoke.problem_ids[0]]
    assert "不是第一次了" in recurrence["summary"]


def test_the_mistake_book_serves_the_three_sections_over_http(smoke: Smoke):
    """功能 1 的读侧：错题本接口真的把三段分开返回，而不是只给拼接后的全文。

    这是「分层释放」能不能做的前提——客户端要把三段**分次**放出来，就得分得开。
    与上一条的区别：那条验的是 WS 推送，这条验的是**落库之后**再读出来还是
    三段齐全，即写入与读出没有各写一套格式。
    """
    _, second = smoke.problem_ids
    _ws_run(
        smoke, code=_WA_CPP, problem_id=second, token=smoke.student_token
    )

    listed = smoke.client.get(
        "/api/v1/mistake-book", headers=smoke.auth(smoke.student_token)
    )
    assert listed.status_code == 200, listed.text
    entries = listed.json()
    assert entries, "判了 WA 就该在错题本里有一条"

    entry = max(entries, key=lambda e: e["id"])
    assert entry["analysis_status"] == "done"
    assert entry["error_detail"] == "a * b 在 int 内先溢出"
    assert entry["hint"] == "看看乘法的操作数是什么类型"
    assert entry["suggested_fix"] == "让运算前就是 64 位"
    # 拼接版仍在，供需要原始列的调用方使用；三段是它的拆分，不是替代。
    assert entry["agent_error_type"] == "overflow"


# --- 4：教师端控制台 -----------------------------------------------------


def test_teacher_console_aggregations_over_http(smoke: Smoke):
    """功能 4：班级概览 / 预警 / 错因分布 / 知识点 / 学生清单 / 存疑提交。

    每一项都断言**返回体里的关键字段**，而不只是 200——一个把键名写错的
    ``/overview`` 照样是 200，前端拿到的是 ``undefined``。
    """
    _, second = smoke.problem_ids
    _ws_run(smoke, code=_WA_CPP, problem_id=second, token=smoke.student_token)

    auth = smoke.auth(smoke.teacher_token)

    overview = smoke.client.get("/api/v1/teacher/overview", headers=auth)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    for key in (
        "students",
        "submissions",
        "accepted",
        "accuracy",
        "open_mistakes",
        "analysed_mistakes",
        "flagged_submissions",
        "open_flags",
    ):
        assert key in body, f"/overview 少了 {key}"
    assert body["students"] == 2
    assert body["submissions"] >= 1

    students = smoke.client.get("/api/v1/teacher/students", headers=auth)
    assert students.status_code == 200, students.text
    roster = students.json()
    assert len(roster) == 2
    row = next(s for s in roster if s["user_id"] == smoke.student_id)
    assert row["display_name"] == "张三"
    assert row["submissions"] >= 1
    assert row["mistakes"] >= 1
    # 「最弱知识点」是教师端唯一一处替学生下结论的字段，必须真的算出来。
    assert row["weakest_tag"] == "io-and-basic-syntax"

    distribution = smoke.client.get("/api/v1/teacher/error-distribution", headers=auth)
    assert distribution.status_code == 200, distribution.text
    counts = {row["label"]: row for row in distribution.json()}
    assert "overflow" in counts, f"分析写进去的标签没出现在错因分布里：{counts}"
    assert counts["overflow"]["count"] >= 1
    assert counts["overflow"]["description"]

    stats = smoke.client.get("/api/v1/teacher/knowledge-stats", headers=auth)
    assert stats.status_code == 200, stats.text
    tags = {row["tag"] for row in stats.json()}
    assert "io-and-basic-syntax" in tags
    first_tag = next(r for r in stats.json() if r["tag"] == "io-and-basic-syntax")
    assert set(first_tag) == {"tag", "total", "passed", "accuracy"}

    alerts = smoke.client.get("/api/v1/teacher/alerts", headers=auth)
    assert alerts.status_code == 200, alerts.text
    for alert in alerts.json():
        # 预警必须带依据，否则教师没法行动。
        assert alert["reason"]

    timeline = smoke.client.get(
        f"/api/v1/teacher/students/{smoke.student_id}/timeline", headers=auth
    )
    assert timeline.status_code == 200, timeline.text
    assert timeline.json(), "学生有提交，时间线就不该是空的"
    assert timeline.json()[0]["problem_id"] in smoke.problem_ids


def test_a_student_is_locked_out_of_the_console(smoke: Smoke):
    """功能 4 的另一半：边界在**服务端**，不在前端有没有那个链接。

    学生拿到的是 403（他确实是登录用户，只是不许进），而不是 401——两者
    混同会把「去登录」和「你没权限」变成同一个提示。
    """
    response = smoke.client.get(
        "/api/v1/teacher/overview", headers=smoke.auth(smoke.student_token)
    )
    assert response.status_code == 403, response.text


# --- 5：作业 -------------------------------------------------------------


def test_assignment_publish_stats_and_student_list(smoke: Smoke):
    """功能 5：教师发布 → 统计（含「谁还没交」）→ 学生能看到。

    「谁还没交」是**临时算出来的**（名册减掉有提交的人），不存状态列——
    所以它必须真的跟着提交动，否则一个「已交」的学生会一直挂在缺交名单里。
    """
    _, second = smoke.problem_ids
    auth = smoke.auth(smoke.teacher_token)

    # 作业**只留第二题**。fixture 在第一题上造了一条旧错，它不在作业里——
    # 「谁还没交」最容易搞错的就是这里：它按「作业里的题有没有提交」算，
    # 不是按「这个学生有没有任何提交」算。把旧错排除在外，这条断言才有意义。
    created = smoke.client.post(
        "/api/v1/teacher/assignments",
        headers=auth,
        json={
            "title": "第一次作业",
            "description": "只留了第二题",
            "problem_ids": [second],
        },
    )
    assert created.status_code == 201, created.text
    assignment = created.json()
    assert assignment["problem_ids"] == [second]
    assert assignment["created_by"] == smoke.teacher_id
    assert assignment["overdue"] is False

    listed = smoke.client.get("/api/v1/teacher/assignments", headers=auth)
    assert listed.status_code == 200, listed.text
    assert any(a["id"] == assignment["id"] for a in listed.json())

    # 还没有人交：两个学生都在缺交名单里——张三在第一题上的旧错不算数。
    before = smoke.client.get(
        f"/api/v1/teacher/assignments/{assignment['id']}/stats", headers=auth
    )
    assert before.status_code == 200, before.text
    stats = before.json()
    assert stats["students_who_submitted"] == 0
    assert sorted(stats["students_missing"]) == ["张三", "李四"]
    assert len(stats["problems"]) == 1
    assert stats["problems"][0]["problem_id"] == second
    assert set(stats["problems"][0]) == {
        "problem_id",
        "submissions",
        "accepted",
        "accuracy",
        "students",
    }

    # 张三交一次（哪怕判 WA，也算「交过」）。
    _ws_run(smoke, code=_WA_CPP, problem_id=second, token=smoke.student_token)

    after = smoke.client.get(
        f"/api/v1/teacher/assignments/{assignment['id']}/stats", headers=auth
    )
    assert after.status_code == 200, after.text
    stats = after.json()
    assert stats["students_who_submitted"] == 1, "交了之后统计没跟着动"
    assert stats["students_missing"] == ["李四"]

    # 学生侧不需要登录也能看到留了什么作业。
    student_view = smoke.client.get("/api/v1/assignments")
    assert student_view.status_code == 200, student_view.text
    assert any(a["id"] == assignment["id"] for a in student_view.json())


# --- 6：过程标记的复核 ---------------------------------------------------


def _flagged_submission(smoke: Smoke, *, problem_id: int) -> int:
    """造一条被标记的提交，并返回它的 id。

    直接写特征而不是跑一次判题：本用例关心的是**复核**这条链路，而标记是它的
    输入。走判题只会多花一次编译，却不会让断言更强。
    """
    submission_id = record_submission(
        _wa_result(),
        SubmissionContext(
            code="int main(){/* 粘贴来的 */}",
            language="cpp",
            problem_id=problem_id,
            user_id=smoke.student_id,
        ),
        session=smoke.session,
    )
    assert submission_id is not None

    row = smoke.session.get(Submission, submission_id)
    assert row is not None
    features = from_trace(ProcessTrace(events=_PASTE_TRACE["events"]), row.code)
    row.apply_process(features, describe(features).flagged)
    smoke.session.commit()
    assert row.process_flagged is True, "这条轨迹本该被标记，标记规则变了？"
    return submission_id


def test_process_flag_review_confirm_dismiss_and_undo(smoke: Smoke):
    """功能 6：确认 / 驳回 / 撤销三种裁定，以及它们对队列的影响。

    这里最要紧的一条是**驳回不清数据**：驳回是「判为误报」，它让这条退出待看
    队列，但全部特征必须留着——否则「这条标记是不是误报」这个判断就再也无法
    被复核，而那正是唯一能告诉我们规则有没有滥报的证据。
    """
    _, second = smoke.problem_ids
    submission_id = _flagged_submission(smoke, problem_id=second)
    auth = smoke.auth(smoke.teacher_token)

    pending = smoke.client.get("/api/v1/teacher/submissions/flagged", headers=auth)
    assert pending.status_code == 200, pending.text
    items = pending.json()["items"]
    mine = next(i for i in items if i["id"] == submission_id)
    assert mine["student"] == "张三"
    assert mine["process"] is not None
    assert set(mine["process"]["features"]) == FEATURE_KEYS
    assert mine["review"] == {"verdict": None, "reviewed_by": None, "reviewed_at": None}

    # 确认：留在列表里备查。
    confirmed = smoke.client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        headers=auth,
        json={"verdict": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["review"]["verdict"] == "confirmed"
    assert confirmed.json()["review"]["reviewed_by"] == smoke.teacher_id

    still_there = smoke.client.get(
        "/api/v1/teacher/submissions/flagged", headers=auth
    ).json()["items"]
    assert any(i["id"] == submission_id for i in still_there), "确认过的应留在列表里"

    # 驳回：退出待看队列，但特征与标记本身都还在。
    dismissed = smoke.client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        headers=auth,
        json={"verdict": "dismissed"},
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["review"]["verdict"] == "dismissed"

    queue = smoke.client.get(
        "/api/v1/teacher/submissions/flagged", headers=auth
    ).json()["items"]
    assert all(i["id"] != submission_id for i in queue), "驳回后不该再出现在待看队列"

    with_reviewed = smoke.client.get(
        "/api/v1/teacher/submissions/flagged?include_reviewed=true", headers=auth
    ).json()["items"]
    kept = next(i for i in with_reviewed if i["id"] == submission_id)
    assert kept["process"]["features"]["pasted_chars"] == 1200, "驳回把特征删了"
    assert kept["review"]["verdict"] == "dismissed"

    # 撤销：回到待看队列。
    cleared = smoke.client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        headers=auth,
        json={"verdict": "cleared"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["review"]["verdict"] is None
    queue = smoke.client.get(
        "/api/v1/teacher/submissions/flagged", headers=auth
    ).json()["items"]
    assert any(i["id"] == submission_id for i in queue), "撤销后应回到待看队列"


def test_reviewing_an_unflagged_submission_is_rejected(smoke: Smoke):
    """没标记的提交不能有裁定。

    409 而不是静默接受：在一个没有标记的行上存一条「老师不同意」，会凭空造出
    一次从未发生的分歧，并污染那个唯一能说明规则有没有滥报的数字。
    """
    first, _ = smoke.problem_ids
    plain = record_submission(
        _wa_result(),
        SubmissionContext(
            code="int main(){/* 自己写的 */}",
            language="cpp",
            problem_id=first,
            user_id=smoke.student_id,
        ),
        session=smoke.session,
    )
    assert plain is not None

    response = smoke.client.post(
        f"/api/v1/teacher/submissions/{plain}/review",
        headers=smoke.auth(smoke.teacher_token),
        json={"verdict": "confirmed"},
    )
    assert response.status_code == 409, response.text


def test_the_rows_are_really_in_postgresql(smoke: Smoke):
    """收尾的一条元验证：上面那些数据确实在 PostgreSQL 里，不是某个内存替身。

    前几条用例全是 HTTP 断言，理论上在 SQLite 上也能过。这一条直接问数据库
    自己「你是谁」，把「跑在真 PG 上」这件事本身变成一条断言——否则整个文件
    可能因为接线错误而在别处跑，却一路绿灯。
    """
    dialect = smoke.session.bind.dialect.name  # type: ignore[union-attr]
    assert dialect == "postgresql", (
        f"这个模块必须在真 PostgreSQL 上跑，实际是 {dialect}"
    )

    rows = smoke.session.query(Assignment).count()
    assert rows >= 0  # 只是证明这条连接能查到表
    entries = smoke.session.query(MistakeBookEntry).count()
    assert entries >= 1, "错题本一行都没有，前面的写入没落到 PG 上"
