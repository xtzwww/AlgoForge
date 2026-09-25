"""自助注册（2026-09-25）。

这个文件的存在理由只有一个：**注册是唯一一个「陌生人能直接调」的写接口**。
其他所有写路径要么需要老师的 403 令牌，要么需要已经存在的账号。所以这里的
用例按「漏掉之后后果多严重」排序，而不是按代码顺序。

**第一条，也是唯一不能出错的一条：注册永远只能造出学生账号。**

在这之前，账号只能由老师创建（``POST /api/v1/teacher/students``），而那条
路径的角色是写死的。开放注册把「谁能拥有一个账号」从一个受控动作变成了
一个公开动作，于是「谁能成为老师」必须仍然是一个受控动作。如果它失守，
泄露的不是某个班的花名册，而是**每个班的花名册和每个人的提交记录** ——
因为教师控制台的全部内容就是这些。

所以这条断言写得比「发个 role 字段被忽略」更狠：直接把 ``role`` 塞进请求体
（包括各种大小写、以及一个额外注入的字段名），然后回数据库里读那一行，
确认它是 ``student``。请求体被 pydantic 丢掉只是**其中一种**正确结果，
真正要钉的是最终落到库里的那个值。

**第二条：门有一个开关。** ``ALLOW_OPEN_REGISTRATION=false`` 时端点必须
403，而不是「还开着但悄悄少建点号」。

**第三条：限流只算成功次数。** 把密码打错五次的学生不该被挡在自己的账号
外面。这条容易写成「限制请求数」——那样测起来也是绿的，但惩罚错了人。

**第四条：凭据有下限。** 老师那条路径什么都不校验（它自己的默认密码就是
``123456``），那在「老师一行一行敲」的前提下是合理的；在一个任何人都能
循环调用的端点上不是。下限故意压得很低（用户名 2 位、密码 6 位），是为了
和老师路径已经产生的账号保持一致，而不是另立一套更严的密码文化。

**第五条：密码不被 strip。** 前后空格是密码的合法字符。悄悄裁掉它，就等于
存的密码和敲进去的不是一个东西 —— 一次没人解释得清的登录失败。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import registration
from app.api import auth as auth_api
from app.api import teacher as teacher_api
from app.auth import create_user
from app.config import settings
from app.main import app
from app.models.base import Base
from app.models.user import ROLE_STUDENT, ROLE_TEACHER, User
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


@pytest.fixture(autouse=True)
def fresh_throttle() -> Iterator[None]:
    """Every test starts with an empty rate-limit window.

    Autouse because the throttle is process-wide: without this, the test
    that deliberately fills the budget would leave every later test in
    the file answering 429, and the failures would point at whichever
    test happened to run after it.
    """
    registration.reset_throttle()
    yield
    registration.reset_throttle()


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose auth *and teacher* routes share one engine.

    The teacher routes are redirected too, and that is not optional: one
    test asks whether an account created by self-registration shows up in
    the teacher's roster, and if only ``auth_api`` were patched the roster
    would be read from the **real** database — so the test would pass or
    fail depending on what happens to be in it, which is exactly how it
    failed the first time (it saw a seeded ``smokestu`` and not the
    account it had just created).
    """
    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(teacher_api, "get_session", _test_session)

    with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
        yield c


def _register(client: TestClient, **body):
    payload = {"username": "zhangsan", "password": "secret123"}
    payload.update(body)
    return client.post("/api/v1/auth/register", json=payload)


# --- 1. 角色不可协商 ----------------------------------------------------


def test_注册永远只能创建学生账号_即使请求里塞了_role(client, session):
    """把 role 塞进请求体，落到库里的仍必须是 student。

    测的是数据库里那一行，不是响应体。响应体可能因为各种原因看起来正确
    （比如字段被 pydantic 丢掉），而真正要紧的是那一行是什么。
    """
    resp = _register(client, username="sneaky", role="teacher")
    assert resp.status_code == 201, resp.text

    row = session.execute(
        select(User).where(User.username == "sneaky")
    ).scalar_one()
    assert row.role == ROLE_STUDENT
    # 顺带确认响应里报的也是学生，避免「库里对、界面错」。
    assert resp.json()["user"]["role"] == ROLE_STUDENT


@pytest.mark.parametrize("spelling", ["teacher", "TEACHER", "Teacher", " teacher "])
def test_角色的各种写法都不能提权(client, session, spelling):
    resp = _register(client, username="sneaky2", role=spelling)
    assert resp.status_code == 201, resp.text
    row = session.execute(select(User).where(User.username == "sneaky2")).scalar_one()
    assert row.role == ROLE_STUDENT


def test_注册出来的账号拿不到教师接口(client, session):
    """真正要防的是这个：拿到令牌之后能不能读到教师控制台。

    上一条测「库里的角色字段」，这条测「那个角色实际能做什么」。两者都要，
    因为一个把 role 存对了但授权判断读错字段的实现，只会被这条抓到。
    """
    resp = _register(client, username="notateacher")
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]

    guarded = client.get(
        "/api/v1/teacher/students", headers={"Authorization": f"Bearer {token}"}
    )
    assert guarded.status_code == 403, guarded.text


def test_registration_role_不接受任何参数():
    """这个函数必须一直是无参的。

    加一个参数就等于把「能不能成为老师」变成一句校验而不是一个不存在的能力。
    这条用例的价值在于：哪天有人给它加了参数，这里会红，而加参数的人会被
    引到 registration.py 的模块注释里读到为什么。
    """
    import inspect

    params = inspect.signature(registration.registration_role).parameters
    assert list(params) == []
    assert registration.registration_role() == ROLE_STUDENT


# --- 2. 开关 ------------------------------------------------------------


def test_关掉开关后注册返回_403(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_open_registration", False)
    resp = _register(client)
    assert resp.status_code == 403
    assert "关闭" in resp.json()["detail"]


def test_关掉开关不影响登录(client, session, monkeypatch):
    """关的是「建号」，不是「进站」。

    一个容易被顺手写坏的实现是把开关放在依赖链上，于是老账号也进不来了 ——
    而这两种行为在测试里都表现为「403」，必须分开断言。
    """
    create_user(session, username="olduser", password="secret123")
    session.commit()

    monkeypatch.setattr(settings, "allow_open_registration", False)
    resp = client.post(
        "/api/v1/auth/login", json={"username": "olduser", "password": "secret123"}
    )
    assert resp.status_code == 200, resp.text


# --- 3. 限流 ------------------------------------------------------------


def test_超过配额后返回_429(client, monkeypatch):
    monkeypatch.setattr(settings, "registration_rate_limit", 3)
    registration.reset_throttle()

    for i in range(3):
        assert _register(client, username=f"user{i}").status_code == 201

    blocked = _register(client, username="user9")
    assert blocked.status_code == 429
    # 消息里要有等待时间，否则表单只能说「失败」。
    assert "秒" in blocked.json()["detail"]


def test_限流只算成功次数_密码打错不消耗配额(client, session, monkeypatch):
    """失败不计数。

    这是本文件里最容易写错的一条：把 check 和 record 放在一起（或只写
    check）都会让「请求数」变成配额，于是一个连输五次密码的学生被挡在
    自己的账号外面 —— 而两种实现在「超限返回 429」这个用例上都是绿的。
    """
    monkeypatch.setattr(settings, "registration_rate_limit", 2)
    registration.reset_throttle()

    # 六次失败：密码太短、用户名非法、用户名全是数字。
    for _ in range(6):
        assert _register(client, username="ab", password="x").status_code == 400

    # 配额应该一点没动：两次成功仍然可以。
    assert _register(client, username="ok1").status_code == 201
    assert _register(client, username="ok2").status_code == 201
    assert _register(client, username="ok3").status_code == 429


def test_用户名被占用不消耗配额(client, session, monkeypatch):
    """409 是「换个名字再来」，不是「你被限流了」。"""
    monkeypatch.setattr(settings, "registration_rate_limit", 2)
    registration.reset_throttle()

    assert _register(client, username="taken").status_code == 201
    for _ in range(4):
        assert _register(client, username="taken").status_code == 409

    # 只消耗了一次（第一次成功），所以还剩一次。
    assert _register(client, username="fresh").status_code == 201
    assert _register(client, username="fresh2").status_code == 429


# --- 4. 凭据下限 --------------------------------------------------------


@pytest.mark.parametrize(
    "username",
    [
        "",
        " ",
        "a",  # 1 位
        "x" * 33,  # 超长
        "zhang san",  # 空格
        "zhang@san",  # 不允许的符号
        "zhang/san",
        "<script>",
    ],
)
def test_非法用户名返回_400(client, username):
    assert _register(client, username=username).status_code == 400


@pytest.mark.parametrize("password", ["", "12345", "x" * 129])
def test_非法密码返回_400(client, password):
    assert _register(client, password=password).status_code == 400


@pytest.mark.parametrize(
    "username",
    [
        "张三",
        "李四-2025",
        "wang_wu",
        # 学号是本校花名册里最可能的用户名，老师那条 CSV 导入路径也认它。
        # 第一版实现按「全是数字像内部 id」把它拒了 —— 那会挡住主要用法。
        "2025021120058",
    ],
)
def test_合法用户名可以通过(client, username):
    """中文、连字符、下划线、学号都要能过。

    老师那条路径从不限制用户名，所以注册这里把中文挡掉的话，学生会遇到
    「老师能建的名字我自己建不了」—— 一个看起来像 bug 的不一致。
    """
    resp = _register(client, username=username)
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["username"] == username


def test_密码不被_strip_且能用原样登录(client, session):
    """前后空格是密码的一部分。

    悄悄 strip 掉，存的密码就和敲进去的不是一个东西，症状是一次没人
    解释得清的登录失败。所以这里注册时带空格、登录时也带空格，必须成功；
    去掉空格登录则必须失败。
    """
    resp = _register(client, username="spacey", password="  padded  ")
    assert resp.status_code == 201, resp.text

    ok = client.post(
        "/api/v1/auth/login", json={"username": "spacey", "password": "  padded  "}
    )
    assert ok.status_code == 200, ok.text

    trimmed = client.post(
        "/api/v1/auth/login", json={"username": "spacey", "password": "padded"}
    )
    assert trimmed.status_code == 401


# --- 5. 重名 ------------------------------------------------------------


def test_用户名被占用返回_409(client, session):
    """409 而不是 400：客户端能自己解决（换个名字），表单可以据此高亮用户名框。"""
    create_user(session, username="dup", password="secret123")
    session.commit()

    resp = _register(client, username="dup")
    assert resp.status_code == 409


def test_重名判断忽略首尾空格(client, session):
    """``" dup "`` 和 ``"dup"`` 是同一个人，不能各建一个号。

    老师那条路径也是 strip 之后再比的，两边必须一致 —— 否则会出现两个
    只差空格的账号，而登录时 strip 会让其中一个永远进不去。
    """
    create_user(session, username="dup2", password="secret123")
    session.commit()
    assert _register(client, username="  dup2  ").status_code == 409


# --- 6. 注册即登录 ------------------------------------------------------


def test_注册直接返回可用的会话令牌(client):
    """省掉「注册成功，请再登录一次」这一轮。

    那一步既多一次往返，又多一次让密码经过前端的机会。
    """
    resp = _register(client, username="autologin")
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["user"]["username"] == "autologin"


def test_不填姓名时用用户名兜底(client):
    resp = _register(client, username="noname")
    assert resp.json()["user"]["display_name"] == "noname"

    named = _register(client, username="withname", display_name="张三")
    assert named.json()["user"]["display_name"] == "张三"


def test_姓名全空格时也退回用户名(client):
    """``display_name="   "`` 不能变成一个空白的花名册条目。"""
    resp = _register(client, username="blankname", display_name="   ")
    assert resp.json()["user"]["display_name"] == "blankname"


def test_响应里没有密码字段(client):
    """响应体和老师建号那条路径一样，永远不带凭据。"""
    resp = _register(client, username="nocreds")
    body = resp.json()
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "password_salt" not in body["user"]
    assert "password" not in body


# --- 7. 与老师建号路径共存 ----------------------------------------------


def test_老师建的号能登录_注册的号也能被老师看到(client, session):
    """两条创建路径产出的是同一种东西。

    如果注册出来的行少了某个字段（或者角色写成了别的值），教师控制台
    的花名册会缺人 —— 而那种缺失表现为「这个学生没数据」，不会报错。
    """
    teacher = create_user(
        session, username="teacher1", password="secret123", role=ROLE_TEACHER
    )
    session.commit()
    assert teacher.role == ROLE_TEACHER

    assert _register(client, username="selfmade").status_code == 201

    # 老师能看见两个学生。
    login = client.post(
        "/api/v1/auth/login", json={"username": "teacher1", "password": "secret123"}
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]

    roster = client.get(
        "/api/v1/teacher/students", headers={"Authorization": f"Bearer {token}"}
    )
    assert roster.status_code == 200, roster.text
    usernames = {row["username"] for row in roster.json()}
    assert "selfmade" in usernames


def test_roles_常量仍然是两个():
    """注册引入的第三个角色名会是一个要设计界面的新东西。"""
    from app.models.user import ROLES

    assert ROLES == (ROLE_STUDENT, ROLE_TEACHER)
