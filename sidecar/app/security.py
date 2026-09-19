"""进程级鉴权 —— 证明「这个请求来自本应用」，而不是来自任何能连到端口的东西。

为什么不是 ``Authorization``
---------------------------
``Authorization`` 已经被**人**的会话凭证占用了：``app.api.auth.current_user``
读它，而前端 ``api/session.ts`` 在有人登录时把同一个头**替换**成用户会话
token（一个头只能带一个 bearer 凭证，两个头就是同一个概念的两个名字，
任何读错的那条路由都会把教师当成「没登录」）。所以进程凭证走独立的头。

为什么留了两个入口
------------------
``X-Sidecar-Token`` 头给 HTTP；``?token=`` 查询参数给 WebSocket —— 浏览器
的 WebSocket API 不允许设置请求头，握手只能带 URL。两者都走同一个比较函数，
不存在「某条路径忘了校验」。

为什么放行 ``/health`` 与 ``OPTIONS``
------------------------------------
- ``/health`` 是启动器的存活探针，它在拿到任何东西之前就要用，且不泄露任何
  信息（固定返回 ``{"status": "ok"}``）。
- ``OPTIONS`` 是 CORS 预检，浏览器发它的时候**不会**带自定义头。拦掉预检等于
  拦掉全部浏览器调用，而预检本身不执行任何业务逻辑。

为什么用 ``compare_digest``
--------------------------
普通 ``==`` 在第一个不同的字节就返回，比较耗时随匹配前缀长度变化，可被逐字节
爆破。``hmac.compare_digest`` 是定长的。token 本身是 32 字节 URL-safe 随机串，
暴力不可行，但定长比较是免费的，不做没有理由。

拦截范围
--------
除上面两个例外，**所有**路径都要凭证，包括 ``/docs`` 与 ``/openapi.json``。
这不是过度设计：本进程能编译并执行提交上来的代码，对外暴露时不该顺手附赠一张
它的代码执行路由地图。
"""

from hmac import compare_digest
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import settings

#: HTTP 头上进程凭证的名字。
SIDECAR_TOKEN_HEADER = "x-sidecar-token"

#: WebSocket 握手用同一个凭证，走 URL 查询参数。
SIDECAR_TOKEN_PARAM = "token"

#: 不需要凭证的路径。见模块文档字符串。
OPEN_PATHS: frozenset[str] = frozenset({"/health"})

#: 拒绝时返回的 body。刻意不说明「差在哪」——对没凭证的调用者，正确的信息量是
#: 「你需要一个凭证」，而不是「你的 token 第 7 个字节错了」。
_UNAUTHORIZED = {"detail": "missing or invalid sidecar token"}


def expected_token() -> str:
    """The process credential this sidecar is currently using."""
    return settings.resolved_sidecar_token


def token_matches(candidate: Optional[str]) -> bool:
    """Whether *candidate* is the sidecar credential.

    ``None`` (header absent) and ``""`` are both "no credential", and are
    answered the same way as a wrong one so the response cannot be used to
    probe which is which.
    """
    if not candidate:
        return False
    return compare_digest(str(candidate), expected_token())


def credential_from_scope(scope: dict) -> Optional[str]:
    """Pull the process credential out of an ASGI/Starlette scope.

    Checks the header first and the query parameter second, so a WebSocket
    (which can only use the parameter) and an HTTP call (which should use
    the header) are both covered by one function and one rule.
    """
    headers = scope.get("headers") or ()
    for name, value in headers:
        if name.decode("latin-1").lower() == SIDECAR_TOKEN_HEADER:
            return value.decode("latin-1")

    query = scope.get("query_string")
    if query:
        parsed = parse_qs(query.decode("latin-1"))
        values = parsed.get(SIDECAR_TOKEN_PARAM)
        if values:
            return values[0]
    return None


class SidecarTokenMiddleware(BaseHTTPMiddleware):
    """Reject HTTP requests that do not carry the sidecar credential."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        # CORS preflight carries no custom headers by construction.
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        if not token_matches(request.headers.get(SIDECAR_TOKEN_HEADER)):
            return JSONResponse(status_code=401, content=_UNAUTHORIZED)
        return await call_next(request)


def websocket_is_authorised(scope: dict) -> bool:
    """Whether a WebSocket handshake presented the credential.

    Checked **before** :meth:`WebSocket.accept`. Accepting first and then
    refusing would mean the socket was already open — the client would see
    a connected socket that immediately dies, which is indistinguishable
    from a server bug.
    """
    return token_matches(credential_from_scope(scope))
