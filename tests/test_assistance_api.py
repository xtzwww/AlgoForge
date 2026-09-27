"""AI 辅助接口的两条安全边界（P0-2）。

P0-2 修的是同一个根因的两种表现：

* ``PUT /assistance/models`` 曾经**不设权限**，且写的是**进程级全局变量**
  （``app.model_catalog._current_provider``）。于是师生共用同一 sidecar 时，
  任意一个学生把模型切走，其他人的下一次提问就跟着变 —— "换模型"实际是
  "给所有人换"。这条路由现在要求教师身份。
* 学生**本来就应该能**换个连得上的模型再问一次，所以另一条路是请求体里的
  逐请求 ``provider`` 字段：它只影响自己、不需要任何权限，也绝不碰那个全局
  变量。

这里钉的是"两条路分得开"：教师能改默认（且确实改了全局），学生改不了全局
（401/403），但学生用 per-request provider 照样能换、且换完全局没动。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.assistance import DEFAULT_ASSISTANCE_LEVEL, policy_for, policy_to_dict
from app.auth import create_user, issue_session
from app.main import app
from app.models.base import Base
from app.models.user import ROLE_STUDENT, ROLE_TEACHER
from conftest import TEST_SIDECAR_HEADERS


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """客户端：把鉴权与会话都接上测试用的内存库。

    辅助接口既走 ``auth_api.get_session``（鉴权），也走
    ``app.records_session.get_session``（``api/assistance.py`` 里
    ``from app import records_session`` 后调用）。两处都得换掉，否则会去连
    ``.env`` 里的 PostgreSQL。
    """
    import app.records_session as records_session_mod

    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(records_session_mod, "get_session", _test_session)

    with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
        yield c


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> dict:
    """把网关的 chat_completion 换成一个记录参数的假实现。

    ``assistance_service`` 在调用点 `from app.llm.gateway import
    chat_completion`，直接拿模块属性，所以 patch 模块属性就能截到它
    （conftest 只挡了 analyzer/team 那几层，没挡这一层）。用它来断言
    per-request 的 provider 确实透传了下去，且全程不联网、不花钱。
    """
    captured: dict = {}

    def _fake(messages, *, provider=None, temperature=None, max_tokens=None, **kwargs):
        captured["provider"] = provider
        captured["messages"] = messages
        return "fake reply"

    monkeypatch.setattr("app.llm.gateway.chat_completion", _fake)
    return captured


def _login(client: TestClient, session: Session, name: str, role: str) -> str:
    create_user(session, username=name, password="pw", role=role)
    return client.post(
        "/api/v1/auth/login", json={"username": name, "password": "pw"}
    ).json()["token"]


def test_切换模型对匿名用户返回401(client: TestClient, session: Session) -> None:
    # 没带 Authorization 头：require_user 先挡，401。
    resp = client.put("/api/v1/assistance/models", json={"model_id": "ollama"})
    assert resp.status_code == 401


def test_切换模型对学生返回403(client: TestClient, session: Session) -> None:
    # 这是 P0-2 的核心：学生过去能改全局默认，现在必须被拒。
    token = _login(client, session, "stu", ROLE_STUDENT)
    resp = client.put(
        "/api/v1/assistance/models",
        json={"model_id": "ollama"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_教师可以改进程默认模型(client: TestClient, session: Session) -> None:
    from app import model_catalog

    try:
        token = _login(client, session, "tea", ROLE_TEACHER)
        resp = client.put(
            "/api/v1/assistance/models",
            json={"model_id": "ollama"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # 真改了全局默认值（教师端该有的能力）。
        assert model_catalog.active_provider() == "ollama"
        body = resp.json()
        assert body["active"] == "ollama"
    finally:
        # 这变量是进程级的，清掉避免影响后续用例。
        model_catalog.reset()


def test_逐请求provider对学生可用且不动全局(
    fake_llm: dict, client: TestClient, session: Session
) -> None:
    """学生换模型的"正确姿势"：请求体里带 provider，只影响自己这次。

    这条路不需要教师身份，也从不去碰进程级默认值。
    """
    from app import model_catalog

    before = model_catalog.active_provider()
    token = _login(client, session, "stu", ROLE_STUDENT)

    resp = client.post(
        "/api/v1/assistance/complete",
        json={"prefix": "int mai", "provider": "ollama"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    # provider 确实透传到了网关调用 —— 这才是"换模型生效"的证据。
    assert fake_llm["provider"] == "ollama"
    # 但进程级默认值纹丝未动（P0-2 的根因：换自己不该牵动别人）。
    assert model_catalog.active_provider() == before


# ---------------------------------------------------------------------------
# GET / PUT /assistance/level（阶段 5 补）
#
# 读档位要能匿名用：学生在没登录时翻题目也该看到老师设的强度，否则面板会显示
# 一个错的值 —— 那正是"老师设 5 却显示 3"这个 bug 的形态。写档位反过来必须
# 是登录用户，否则任何人都能改别人的意愿。
# ---------------------------------------------------------------------------

def _make_assignment(session: Session, level: int, title: str = "t") -> int:
    from app.models.assignment import Assignment

    row = Assignment(title=title, description="d", assistance_level=level)
    session.add(row)
    session.commit()
    return row.id


def test_匿名也能读档位且拿到老师设的上限(client: TestClient, session: Session) -> None:
    asg = _make_assignment(session, 5)
    resp = client.get(f"/api/v1/assistance/level?assignment_id={asg}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ceiling"] == 5
    assert body["level"] == 5  # 没登录就没有个人意愿，等于上限
    assert body["preference"] is None
    assert body["has_assignment"] is True


def test_没有作业id时回落到默认档且标记为自由练习(
    client: TestClient, session: Session
) -> None:
    resp = client.get("/api/v1/assistance/level")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_assignment"] is False
    assert body["level"] == DEFAULT_ASSISTANCE_LEVEL
    assert body["ceiling"] == DEFAULT_ASSISTANCE_LEVEL


def test_匿名写档位返回401(client: TestClient, session: Session) -> None:
    asg = _make_assignment(session, 5)
    resp = client.put("/api/v1/assistance/level", json={"assignment_id": asg, "level": 2})
    assert resp.status_code == 401


def test_学生写档位后读到的生效值就是它(client: TestClient, session: Session) -> None:
    asg = _make_assignment(session, 5)
    token = _login(client, session, "stu", ROLE_STUDENT)
    auth = {"Authorization": f"Bearer {token}"}

    resp = client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": 2},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == 2
    assert resp.json()["preference"] == 2
    assert resp.json()["ceiling"] == 5

    # 再读一次（另一个请求），确认是落库了而不是只改了响应。
    again = client.get(f"/api/v1/assistance/level?assignment_id={asg}", headers=auth)
    assert again.json()["level"] == 2


def test_学生写超过上限的值会被压到上限(client: TestClient, session: Session) -> None:
    """安全不变量在 HTTP 层的表现。压不到就等于学生给自己提权。"""
    asg = _make_assignment(session, 2)
    token = _login(client, session, "stu", ROLE_STUDENT)
    auth = {"Authorization": f"Bearer {token}"}

    resp = client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": 5},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["ceiling"] == 2
    assert resp.json()["level"] == 2
    assert resp.json()["preference"] == 2


def test_学生清除意愿后回到上限(client: TestClient, session: Session) -> None:
    asg = _make_assignment(session, 5)
    token = _login(client, session, "stu", ROLE_STUDENT)
    auth = {"Authorization": f"Bearer {token}"}

    client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": 1},
        headers=auth,
    )
    resp = client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": None},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["preference"] is None
    assert resp.json()["level"] == 5


def test_两个学生的意愿互不干扰(client: TestClient, session: Session) -> None:
    """共用一张表，串味的后果是 A 把 B 的强度改了，而两人都不会察觉。"""
    asg = _make_assignment(session, 5)
    t1 = _login(client, session, "s1", ROLE_STUDENT)
    t2 = _login(client, session, "s2", ROLE_STUDENT)

    client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": 1},
        headers={"Authorization": f"Bearer {t1}"},
    )
    client.put(
        "/api/v1/assistance/level",
        json={"assignment_id": asg, "level": 4},
        headers={"Authorization": f"Bearer {t2}"},
    )

    r1 = client.get(
        f"/api/v1/assistance/level?assignment_id={asg}",
        headers={"Authorization": f"Bearer {t1}"},
    )
    r2 = client.get(
        f"/api/v1/assistance/level?assignment_id={asg}",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r1.json()["level"] == 1
    assert r2.json()["level"] == 4


def test_读档位返回的策略内容与档位号自洽(client: TestClient, session: Session) -> None:
    """前端拿 ``policy`` 直接渲染文案，错位会让界面描述别的档位。"""
    asg = _make_assignment(session, 3)
    body = client.get(f"/api/v1/assistance/level?assignment_id={asg}").json()
    assert body["policy"]["level"] == 3
    assert policy_to_dict(policy_for(3)) == body["policy"]
