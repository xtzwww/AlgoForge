"""Health check endpoint for liveness/readiness probes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return service health status.

    Returns:
        JSON object with status "ok" when the service is alive.
    """
    return {"status": "ok"}
