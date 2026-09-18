"""Run-code API endpoint.

POST /api/v1/run-code
  Body: {"code": "<source code>", "language": "cpp"}
  Response: {"success": bool, "stdout": str, "stderr": str,
             "exit_code": int, "compile_output": str}

``language`` is optional and defaults to C++ (see
``app.sandbox.languages.DEFAULT_LANGUAGE``), so P1-8's callers keep
working unchanged. It is a bare code run with no test cases and no
memory ceiling: use ``POST /api/v1/judge`` for a verdict.

Why the build runs in a worker thread
-------------------------------------
``compile_and_run`` ends in ``subprocess.run`` — a blocking call that
holds its thread for as long as the compiler and the program take. This
route is ``async def``, so calling it directly ran that blocking work *on
the event loop*: one submission being compiled froze the whole sidecar,
including the judge stream and every other request.

``app/api/judge.py`` has always done this correctly with
``await asyncio.to_thread(...)``; this route was the one place that did
not, which is also why it had no test — nothing exercised it concurrently.

Both now go through :mod:`app.sandbox.pool`, which is the same idea with
a ceiling attached: being off the loop stops one compile from freezing
the sidecar, but it does not stop eight of them from running at once.

On a single user with one request at a time this is invisible, which is
how it survived. It stops being invisible the moment two things happen at
once, which is the point of the multi-user direction.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api import LanguageId
from app.sandbox.compiler import compile_and_run
from app.sandbox.pool import run_in_sandbox

router = APIRouter(prefix="/api/v1", tags=["run-code"])


class RunCodeRequest(BaseModel):
    code: str
    language: LanguageId = "cpp"


class RunCodeResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    compile_output: str


@router.post("/run-code", response_model=RunCodeResponse)
async def run_code(req: RunCodeRequest) -> RunCodeResponse:
    """Build and run the submitted code, returning the output.

    The build happens off the event loop; see the module docstring for
    why that is not optional.
    """
    # ``run_in_sandbox`` rather than ``asyncio.to_thread``: same thread
    # off the loop, but the pool behind it is capped — see
    # :mod:`app.sandbox.pool` for why the default executor's size is not
    # a limit.
    result = await run_in_sandbox(compile_and_run, req.code, language=req.language)
    return RunCodeResponse(**result.to_dict())
