"""Run-code API endpoint.

POST /api/v1/run-code
  Body: {"code": "<source code>", "language": "cpp"}
  Response: {"success": bool, "stdout": str, "stderr": str,
             "exit_code": int, "compile_output": str}

``language`` is optional and defaults to C++ (see
``app.sandbox.languages.DEFAULT_LANGUAGE``), so P1-8's callers keep
working unchanged. It is a bare code run with no test cases and no
memory ceiling: use ``POST /api/v1/judge`` for a verdict.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api import LanguageId
from app.sandbox.compiler import compile_and_run

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
    """Build and run the submitted code, returning the output."""
    result = compile_and_run(req.code, language=req.language)
    return RunCodeResponse(**result.to_dict())
