"""Judge API endpoint.

POST /api/v1/judge
  Body: {
    "code": "<source code>",
    "language": "cpp",       // optional, default "cpp"
    "test_cases": [{"input": "...", "expected_output": "..."}],
    "time_limit": 10.0,      // optional, seconds, default 10
    "mem_limit_mb": 256      // optional, MB, default 256
  }
  Response: {
    "status": "AC" | "WA" | "TLE" | "MLE" | "CE" | "RE",
    "compile_output": "...",
    "passed": 2,
    "total": 3,
    "test_cases": [{ "status", "input", "expected_output", "actual_output", ... }]
  }

The REST twin of ``WS /ws/run-log``. It shares the same pipeline but
returns one finished response instead of streaming progress, which is
what tests and simple clients want.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api import LanguageId
from app.sandbox.judge import TestCase, judge

router = APIRouter(prefix="/api/v1", tags=["judge"])


class TestCaseIn(BaseModel):
    input: str = ""
    expected_output: str


class JudgeRequest(BaseModel):
    code: str
    #: Language the code is written in. A bad id is a 422, not a 500.
    language: LanguageId = "cpp"
    test_cases: list[TestCaseIn]
    # Per-case wall-clock timeout in seconds. Defaults to 10s.
    time_limit: float = Field(default=10.0, ge=0.1, le=60.0)
    # Per-case memory ceiling in MB. Defaults to 256 MB. The language's
    # own allowance is added on top before the runner's check fires.
    mem_limit_mb: int = Field(default=256, ge=1, le=2048)


class TestCaseOut(BaseModel):
    status: str
    input: str
    expected_output: str
    actual_output: str
    stderr: str
    exit_code: int


class JudgeResponse(BaseModel):
    status: str
    compile_output: str
    passed: int
    total: int
    test_cases: list[TestCaseOut]


@router.post("/judge", response_model=JudgeResponse)
async def judge_code(req: JudgeRequest) -> JudgeResponse:
    """Build and judge the submitted code against the test cases.

    The program is built once (P1-11) and reused for every test case
    (P1-12 ``run_executable`` enforces timeout + memory ceiling).
    Per-case verdicts are aggregated into an overall verdict with
    priority CE > TLE > MLE > RE > WA > AC.
    """
    test_cases = [
        TestCase(input=tc.input, expected_output=tc.expected_output)
        for tc in req.test_cases
    ]
    result = judge(
        req.code,
        test_cases,
        time_limit=req.time_limit,
        mem_limit_mb=req.mem_limit_mb,
        language=req.language,
    )
    return JudgeResponse(**result.to_dict())
