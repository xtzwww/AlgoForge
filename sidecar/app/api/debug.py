"""Debug session API (PU-17).

``GET  /api/v1/debug/availability``  — can this machine debug at all?
``POST /api/v1/debug/sessions``      — open a session (compiles the code)
``PUT  /api/v1/debug/sessions/{id}/breakpoints`` — set breakpoints
``POST /api/v1/debug/sessions/{id}/step``        — move the program
``DELETE /api/v1/debug/sessions/{id}``           — close and clean up

Why a session and not one stateless call per action
---------------------------------------------------

Debugging is a conversation with state: the same breakpoints, the same
binary, the same position in the program. Modelling it as one call per
action would mean re-sending the code, the breakpoints and the whole
step history on every click, and the client would have to own the
authoritative copy of all three. The server owning it means the editor's
gutter is a view, so a breakpoint the student sees is one gdb will
actually honour.

Why the endpoints are not role-gated
------------------------------------

Any logged-in user may debug. This is a *learning* tool, not a teaching
setting: a teacher gains nothing from controlling it, and students are
exactly who it is for. It is not unauthenticated — the sidecar's process
token still gates every route (see ``app/security.py``), and the store
hands out ids that are unguessable — but there is no
``require_teacher`` here, deliberately, for the same reason
``/assistance/models`` has none.

The one write that *does* need care is ``code``. A session compiles
student code with debug flags at ``-O0``, which is a different build from
the judge's. It runs in the sandbox thread pool with the same concurrency
ceiling as judging, so a classroom cannot start one compiler per click.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import debug_store
from app.api.auth import require_user
from app.debug_session import (
    DebugError,
    build,
    command,
    create_session,
    set_breakpoints,
)
from app.models.user import User
from app.sandbox.debugger import DEBUG_TOOLCHAIN, debug_available
from app.sandbox.pool import run_in_sandbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["debug"])

#: Debug actions the client may request. Deliberately excludes ``run``
#: with arguments, ``shell``, and anything that could execute a command
#: of the client's choosing — the action name is looked up against this
#: set and never passed to gdb as text.
STEPS = frozenset({"run", "continue", "next", "step", "finish", "until"})


class AvailabilityOut(BaseModel):
    """Whether 调试 is usable, and why not when it is not."""

    available: bool
    compiler: Optional[str] = None
    gdb: Optional[str] = None
    #: Human-readable reason when ``available`` is false. Names the
    #: expected paths, because "调试不可用" alone gives a student nothing
    #: to act on.
    reason: Optional[str] = None


class CreateSessionRequest(BaseModel):
    code: str
    #: Input fed to the program on every step. Debugging is for logic
    #: errors; interactive stdin is out of scope and a submission that
    #: blocks on input reads EOF (see ``app.debug_session``).
    stdin: str = ""
    breakpoints: list[int] = Field(default_factory=list)


class SetBreakpointsRequest(BaseModel):
    lines: list[int] = Field(default_factory=list)


class StepRequest(BaseModel):
    action: str


def _require_session(session_id: str):
    """Look up a live session or raise 404.

    404 rather than 410 for an expired session: the client cannot act on
    the difference and should start a new one either way.
    """
    session = debug_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="调试会话不存在或已过期")
    return session


@router.get("/debug/availability", response_model=AvailabilityOut)
async def availability() -> AvailabilityOut:
    """Report whether a debug toolchain exists on this machine.

    The panel calls this before offering the 调试 tab, so a machine
    without the toolchain shows an explanation instead of a button that
    fails. The probe is a file-existence check, not a launch (see
    ``debugger.probe_debug_toolchain``), so this is cheap enough to call
    on every page load.
    """
    if debug_available():
        from app.sandbox.debugger import probe_debug_toolchain

        toolchain = probe_debug_toolchain()
        return AvailabilityOut(
            available=True,
            compiler=toolchain.compiler if toolchain else None,
            gdb=toolchain.gdb if toolchain else None,
        )
    expected = "、".join(DEBUG_TOOLCHAIN[0]) if DEBUG_TOOLCHAIN else ""
    return AvailabilityOut(
        available=False,
        reason=f"没有找到配套的调试工具链，调试功能需要以下两个文件：{expected}",
    )


@router.post("/debug/sessions")
async def create(
    req: CreateSessionRequest,
    _user: User = Depends(require_user),
) -> dict:
    """Open a session: compile the code with debug info and return an id.

    Compiling here rather than on the first step is what makes the
    response honest — the student learns their code does not compile
    *before* the panel switches into debugging, at the moment they expect
    a compile result, rather than as a confusing failure of the first
    step.

    Runs in the sandbox pool because compiling is blocking and a
    template-heavy file takes real time.
    """
    session = create_session(
        debug_store.new_id(),
        req.code,
        stdin=req.stdin,
    )
    debug_store.put(session)

    try:
        await run_in_sandbox(build, session)
    except DebugError as exc:
        # The session is unusable, so it is closed rather than left for
        # the client to clean up — a compile failure should not consume a
        # slot in the store.
        debug_store.remove(session.session_id)
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if req.breakpoints:
        set_breakpoints(session, set(req.breakpoints))

    result = session.to_dict()
    # The id is returned separately from the session dict because the
    # dict's copy is for convenience; this is the value the client must
    # send back, and having one obvious field for it avoids the client
    # reading it out of a nested structure.
    result["session_id"] = session.session_id
    return result


@router.put("/debug/sessions/{session_id}/breakpoints")
async def put_breakpoints(
    session_id: str,
    req: SetBreakpointsRequest,
    _user: User = Depends(require_user),
) -> dict:
    """Replace the session's breakpoints.

    A full replace rather than add/remove deltas: the editor's gutter
    already holds the whole truth for the current file, and reconciling
    deltas on the server invites drift between the two. The breakpoints
    take effect on the next step, which is stated in the response so the
    client can tell the student if they expected otherwise.
    """
    session = _require_session(session_id)
    set_breakpoints(session, set(req.lines))
    return {
        "session_id": session.session_id,
        "breakpoints": sorted(session.breakpoints),
        # Sending the taken effect means the panel can say "已保存，下次
        # 单步生效" rather than implying the program will re-stop now —
        # it cannot, because it is not running between steps.
        "takes_effect": "next_step",
    }


@router.post("/debug/sessions/{session_id}/step")
async def step(
    session_id: str,
    req: StepRequest,
    _user: User = Depends(require_user),
) -> dict:
    """Move the program and return where it ended up.

    ``run`` is not special-cased here even though it restarts: the
    session decides what a restart means (see ``app.debug_session``), and
    duplicating that judgement in the route would give two places to keep
    in step.
    """
    session = _require_session(session_id)
    if req.action not in STEPS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的调试动作：{req.action}；可用：{'、'.join(sorted(STEPS))}",
        )

    try:
        await run_in_sandbox(command, session, req.action)
    except DebugError as exc:
        # A *failed step* is not an unusable session — a timeout or a
        # missing binary is worth reporting, but the session may still be
        # good, so it stays. 400 rather than 500: this is the request
        # that could not be honoured, not the server breaking.
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return session.to_dict()


@router.delete("/debug/sessions/{session_id}")
async def close(
    session_id: str,
    _user: User = Depends(require_user),
) -> dict:
    """Close a session and delete its working directory.

    Idempotent: closing an unknown or already-closed session reports
    ``closed: false`` rather than 404, because the client's intent —
    "make sure this is gone" — is satisfied either way, and a 404 would
    make the panel show an error for a successful cleanup.
    """
    return {"session_id": session_id, "closed": debug_store.remove(session_id)}


__all__ = ["router"]
