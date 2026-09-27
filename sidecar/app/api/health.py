"""Health check endpoint for liveness/readiness probes."""

from fastapi import APIRouter

from app.code_stamp import current_code_stamp

router = APIRouter()

#: Fingerprint of the sources *this process* loaded, captured at import.
#:
#: Snapshotting it here rather than computing it per request is the whole
#: point: the value has to describe the revision the process started with,
#: not the files as they are now — the two only differ once the code has
#: been edited under a running backend, which is precisely the case
#: ``start-web.bat`` needs to detect. See :mod:`app.code_stamp`.
_LOADED_CODE_STAMP: str | None = current_code_stamp()


@router.get("/health")
async def health_check() -> dict[str, str | None]:
    """Return service health status.

    Returns:
        JSON object with status "ok" when the service is alive, plus
        ``code_stamp``: the fingerprint of the sources this process loaded.
        A launcher compares it against a fresh hash of ``sidecar/app`` to
        tell a current backend from one that predates the code on disk.
    """
    return {"status": "ok", "code_stamp": _LOADED_CODE_STAMP}
