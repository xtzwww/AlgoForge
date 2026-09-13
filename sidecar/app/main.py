"""AlgoForge Sidecar — FastAPI application entry point.

Minimal skeleton (Phase 1, slice P1-1). The app exposes a /health
endpoint. Business routes are registered via include_router so each
feature module owns its own router.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.health import router as health_router
from app.api.run_code import router as run_code_router
from app.api.judge import router as judge_router
from app.api.ws import router as ws_router
from app.api.mistake_book import router as mistake_book_router
from app.api.profile import router as profile_router
from app.api.review import router as review_router

app = FastAPI(
    title="AlgoForge Sidecar",
    version="0.1.0",
    description="AI Agent 驱动的算法竞赛错因分析后端服务",
)

# CORS: the Electron renderer calls the sidecar over localhost. In dev
# Vite serves the renderer from http://localhost:5173 (preview: 4173),
# so only those origins are allowed by default. Production loads the
# renderer from file:// / a custom scheme, which sends no Origin header
# and therefore needs no CORS entry.
#
# Previously this was allow_origins=["*"] with allow_credentials=True,
# which browsers reject and which exposed the sidecar to any web page
# the user happened to visit. Origins are now configurable via
# CORS_ALLOW_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.resolved_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(run_code_router)
app.include_router(judge_router)
app.include_router(ws_router)
app.include_router(mistake_book_router)
app.include_router(profile_router)
app.include_router(review_router)
