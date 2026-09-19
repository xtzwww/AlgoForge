"""进程级鉴权 —— ``app/security.py`` 与它在 ``app/main.py`` 的安装。

这些测试关心的是「没凭证的请求进不来」，不是某个具体 token 的值。凭证值由
``conftest.TEST_SIDECAR_TOKEN`` 提供，并由那里的 autouse fixture 写进
``settings``，所以这里既能造出「对」的凭证也能造出「错」的。

守卫本身用一个最小的 app 来测：真实 ``app`` 上挂了十几个路由，任何一条都可能
因为数据库、编译器或 LLM 而 500，把「守卫拦住了它」和「它自己坏了」混在一起。
真实 app 只在最后一条里被检查 —— 检查的是**中间件装上了**，那是唯一可能悄悄
腐坏的部分（改 ``main.py`` 时漏掉一行）。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app as real_app
from app.security import (
    SIDECAR_TOKEN_HEADER,
    SidecarTokenMiddleware,
    credential_from_scope,
    token_matches,
    websocket_is_authorised,
)
from conftest import TEST_SIDECAR_HEADERS, TEST_SIDECAR_TOKEN


def _guarded_app() -> FastAPI:
    """A one-route app carrying nothing but the guard."""
    guarded = FastAPI()
    guarded.add_middleware(SidecarTokenMiddleware)

    @guarded.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    return guarded


def test_a_request_with_no_credential_is_refused() -> None:
    """The whole point: a bare request does not reach the route."""
    with TestClient(_guarded_app()) as client:
        assert client.get("/ping").status_code == 401


def test_a_wrong_credential_is_refused() -> None:
    client = TestClient(_guarded_app())
    wrong = client.get("/ping", headers={SIDECAR_TOKEN_HEADER: "not-it"})
    assert wrong.status_code == 401


def test_an_empty_credential_is_refused() -> None:
    """`""` is "no credential", not "a credential that happens to be empty"."""
    client = TestClient(_guarded_app())
    assert client.get("/ping", headers={SIDECAR_TOKEN_HEADER: ""}).status_code == 401


def test_the_right_credential_is_accepted() -> None:
    client = TestClient(_guarded_app())
    response = client.get("/ping", headers=TEST_SIDECAR_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_the_refusal_says_nothing_about_how_far_off_the_token_was() -> None:
    """One message for "absent" and "wrong" alike.

    A response that distinguished them would turn the endpoint into an
    oracle for probing the token one byte at a time.
    """
    client = TestClient(_guarded_app())
    absent = client.get("/ping").json()
    wrong = client.get("/ping", headers={SIDECAR_TOKEN_HEADER: "x" * 32}).json()
    assert absent == wrong


def test_preflight_is_not_refused() -> None:
    """CORS preflight carries no custom headers by construction.

    Refusing it would refuse every browser call, because the browser will
    not send the real request until the preflight succeeds. Whether the
    route itself answers OPTIONS is the router's business — what matters
    here is that the guard is not what stops it.
    """
    client = TestClient(_guarded_app())
    assert client.options("/ping").status_code != 401


def test_the_health_probe_stays_open() -> None:
    """The launcher polls /health before it can present anything."""
    with TestClient(real_app) as client:
        assert client.get("/health").status_code == 200


def test_the_real_app_refuses_a_bare_request_before_the_route_runs() -> None:
    """The refusal comes from the middleware, not from the route.

    ``require_user`` also answers 401, so the status code alone cannot
    tell the two apart — the message can. This is the one assertion that
    proves a real route on the real app is behind the guard.

    Only the *bare* request is made: a credentialed one would reach
    ``current_user``, which opens a session against the documented
    PostgreSQL DSN that is not running here.
    """
    with TestClient(real_app) as client:
        response = client.get("/api/v1/teacher/alerts")

    assert response.status_code == 401
    assert "sidecar token" in response.json()["detail"]


def test_the_real_app_carries_the_guard() -> None:
    """Pin the one thing that rots silently: that main.py installs it.

    Everything above tests the middleware against a toy app. If a future
    edit to ``main.py`` dropped the ``add_middleware`` call, all of those
    would still pass while every route in the product stood open.
    """
    assert any(
        middleware.cls is SidecarTokenMiddleware
        for middleware in real_app.user_middleware
    )


# --- the WebSocket, which cannot set a header ---------------------------


def test_a_websocket_handshake_without_the_credential_is_refused() -> None:
    assert websocket_is_authorised({"headers": []}) is False


def test_a_websocket_handshake_takes_the_credential_from_the_query() -> None:
    scope = {"headers": [], "query_string": f"token={TEST_SIDECAR_TOKEN}".encode()}
    assert websocket_is_authorised(scope) is True


def test_the_credential_is_also_read_from_the_header() -> None:
    """One function, two entry points, one rule."""
    scope = {"headers": [(SIDECAR_TOKEN_HEADER.encode(), TEST_SIDECAR_TOKEN.encode())]}
    assert credential_from_scope(scope) == TEST_SIDECAR_TOKEN
    assert websocket_is_authorised(scope) is True


def test_the_query_parameter_does_not_shadow_an_absent_header() -> None:
    """A handshake with a token in neither place is refused."""
    assert credential_from_scope({"headers": [], "query_string": b""}) is None


# --- the token itself ----------------------------------------------------


def test_a_generated_token_is_the_same_token_on_the_next_read(
    monkeypatch,
) -> None:
    """A token that is made up must be made up **once**.

    ``resolved_sidecar_token`` used to generate a fresh value on every
    call, which is fine for a launcher that reads it once but useless as
    something to compare against: two reads would disagree, so no request
    could ever match. This pins the write-back.
    """
    monkeypatch.setattr(settings, "sidecar_token", None)
    first = settings.resolved_sidecar_token
    assert first == settings.resolved_sidecar_token
    assert token_matches(first) is True
