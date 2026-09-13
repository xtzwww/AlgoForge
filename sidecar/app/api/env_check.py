"""Environment self-check endpoint (PU-3).

GET /api/v1/env-check

Reports which language toolchains, database and sandbox paths are
actually usable on this machine, so the desktop UI can disable options
it cannot honour instead of failing later at judge time.

This route is deliberately thin — all probing lives in
``app/sandbox/env_probe.py`` (AGENTS.md section 4: ``api/`` holds routes,
not business logic).
"""

from fastapi import APIRouter

from app.sandbox.env_probe import probe_all

router = APIRouter(prefix="/api/v1", tags=["env-check"])


@router.get("/env-check")
async def env_check() -> dict:
    """Return a capability report for the current environment.

    ``probe_all()`` is synchronous and does subprocess + network work,
    but the whole thing is bounded (5s per version probe, 3s for the
    database connect) and runs once at startup, so it is called inline
    rather than dispatched to a thread.
    """
    return probe_all()
