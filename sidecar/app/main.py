"""基于 AI Agent 的编程课程学习过程分析与反馈系统 — FastAPI 后端入口。

Minimal skeleton (Phase 1, slice P1-1). The app exposes a /health
endpoint. Business routes are registered via include_router so each
feature module owns its own router.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.assignments import router as assignments_router
from app.api.auth import router as auth_router
from app.api.env_check import router as env_check_router
from app.api.health import router as health_router
from app.api.judge import router as judge_router
from app.api.mistake_book import router as mistake_book_router
from app.api.problems import router as problems_router
from app.api.profile import router as profile_router
from app.api.review import router as review_router
from app.api.run_code import router as run_code_router
from app.api.submissions import router as submissions_router
from app.api.teacher import router as teacher_router
from app.api.ws import router as ws_router
from app.config import settings
from app.security import SidecarTokenMiddleware

app = FastAPI(
    title="基于 AI Agent 的编程课程学习过程分析与反馈系统",
    version="0.1.0",
    description="智能体驱动的编程学习过程分析与分层反馈后端服务",
)

# CORS: the Electron renderer calls the sidecar over localhost. In dev
# Vite serves the renderer from http://localhost:5173 (preview: 4173),
# so only those origins are allowed by default. Production loads the
# renderer from file:// / a custom scheme, which sends no Origin header
# and therefore needs no CORS entry.
#
# The web target (PU-5, `npm run dev:web`) reuses the 5173 entry: unlike
# Electron it is a real page on a real origin, so its requests do carry
# an Origin header. A LAN deployment must add that origin explicitly —
# CORS does not support wildcard hosts.
#
# Previously this was allow_origins=["*"] with allow_credentials=True,
# which browsers reject and which exposed the sidecar to any web page
# the user happened to visit. Origins are now configurable via
# CORS_ALLOW_ORIGINS.
#
# Process-level auth. Every request must carry `SIDECAR_TOKEN`
# (`X-Sidecar-Token`, or `?token=` for the WebSocket); `/health` and CORS
# preflight are exempt. See app/security.py for the exemptions and for why
# this is not the `Authorization` header.
#
# Added *before* CORS on purpose. `add_middleware` wraps what is already
# there, so the last one added is the outermost — adding CORS last puts it
# outside the token check, which is what makes the 401 carry CORS headers
# (an unannotated 401 is indistinguishable from a network error in a
# browser) and what lets preflight be answered without a credential.
app.add_middleware(SidecarTokenMiddleware)

# CORS: the Electron renderer calls the sidecar over localhost. In dev
# Vite serves the renderer from http://localhost:5173 (preview: 4173),
# so only those origins are allowed by default. Production loads the
# renderer from file:// / a custom scheme, which sends no Origin header
# and therefore needs no CORS entry.
#
# The web target (PU-5, `npm run dev:web`) reuses the 5173 entry: unlike
# Electron it is a real page on a real origin, so its requests do carry
# an Origin header. A LAN deployment must add that origin explicitly —
# CORS does not support wildcard hosts.
#
# Previously this was allow_origins=["*"] with allow_credentials=True,
# which browsers reject and which exposed the sidecar to any web page
# the user happened to visit. Origins are now configurable via
# CORS_ALLOW_ORIGINS.
#
# CORS is a *browser* rule: it stops a web page the user happens to visit
# from reading the response, but it does nothing about a script, a curl,
# or another process on the same machine. That is what the credential
# above is for. Both are needed — neither replaces the other.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.resolved_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(env_check_router)
app.include_router(run_code_router)
app.include_router(judge_router)
app.include_router(problems_router)
app.include_router(submissions_router)
app.include_router(ws_router)
app.include_router(mistake_book_router)
app.include_router(profile_router)
app.include_router(review_router)
app.include_router(teacher_router)
app.include_router(assignments_router)
