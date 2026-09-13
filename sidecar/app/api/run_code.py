"""Run-code API endpoint.

POST /api/v1/run-code
  Body: {"code": "<C++ source code>"}
  Response: {"success": bool, "stdout": str, "stderr": str,
             "exit_code": int, "compile_output": str}
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.sandbox.compiler import compile_and_run

router = APIRouter(prefix="/api/v1", tags=["run-code"])


class RunCodeRequest(BaseModel):
    code: str


class RunCodeResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    compile_output: str


@router.post("/run-code", response_model=RunCodeResponse)
async def run_code(req: RunCodeRequest) -> RunCodeResponse:
    """Compile and run the submitted C++ code, returning the output."""
    result = compile_and_run(req.code)
    return RunCodeResponse(**result.to_dict())
