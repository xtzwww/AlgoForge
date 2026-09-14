"""End-to-end multi-language judging tests (PU-16).

PU-16's acceptance criteria are stated per language: one run that is
accepted, one that fails to build, and one that times out. These tests
drive all three through the real pipeline — descriptor → argv → compiler →
runner → verdict — so a descriptor that generates a wrong command, or a
filename that the toolchain rejects, fails here instead of in the product.

Toolchains that are not installed are skipped per language rather than
per module: a machine with only g++ should still get its C++ coverage
instead of a wall of errors. That is also how ``test_languages.py``
handles the same problem.

The ``ascii_tmp_path`` fixture is not needed here because ``judge`` owns
its own scratch directory (under ``C:\\Windows\\Temp`` on Windows).
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ws import _parse_request
from app.config import settings
from app.main import app
from app.models.base import Base
from app.models.problem import Problem
from app.models.test_case import TestCase
from app.sandbox import judge_input as judge_input_module
from app.sandbox.judge import JudgeStatus, judge
from app.sandbox.judge import TestCase as JudgeTestCase
from app.sandbox.languages import UnknownLanguageError


@contextmanager
def _memory_session() -> Iterator[Session]:
    """Yield an in-memory SQLite session with the schema created.

    ``StaticPool`` keeps every checkout on one connection so the schema
    and rows survive across the session's lifetime — the default pool
    would hand out a new, empty in-memory database per connection.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed_problem(
    session: Session,
    *,
    problem_id: int = 1,
    time_limit: float = 10.0,
    memory_limit: int = 256,
    cases: list[tuple[str, str, bool]] | None = None,
) -> Problem:
    """Insert a problem with ``(input, output, is_sample)`` cases."""
    problem = Problem(
        id=problem_id,
        title="Seeded",
        knowledge_tags=[],
        description="",
        samples=[],
        time_limit=time_limit,
        memory_limit=memory_limit,
    )
    session.add(problem)
    session.flush()
    for ordinal, (case_in, case_out, is_sample) in enumerate(cases or [], start=1):
        session.add(
            TestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                input=case_in,
                expected_output=case_out,
                is_sample=is_sample,
            )
        )
    session.commit()
    return problem


@contextmanager
def _patched_judge_session() -> Iterator[None]:
    """Point the resolver's default session at an empty in-memory DB.

    The REST route calls ``resolve`` without a session, so the only hook
    is the module-level ``get_session`` the resolver imported. Patching
    it here is what lets the 404 test drive the route without a live
    PostgreSQL. The database is intentionally empty — an unknown id is
    the whole point.
    """
    with _memory_session() as session:
        real = judge_input_module.get_session

        def fake() -> Session:
            return session

        judge_input_module.get_session = fake  # type: ignore[assignment]
        try:
            yield
        finally:
            judge_input_module.get_session = real  # type: ignore[assignment]

# ------------------------------------------------------------ C++ fixtures

_CPP_AC = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""

_CPP_CE = """\
#include <iostream>
int main() {
    std::cout << "missing semicolon" << std::endl
    return 0;
}
"""

_CPP_TLE = """\
int main() {
    while (true) {}
    return 0;
}
"""

# --------------------------------------------------------- Python fixtures

_PY_AC = """\
import sys


def main() -> None:
    a, b = map(int, sys.stdin.read().split())
    print(a + b)


main()
"""

# Unterminated string literal — a syntax error, which py_compile must
# report as CE rather than letting the interpreter fail at runtime.
_PY_CE = """\
print("unterminated
"""

_PY_TLE = """\
while True:
    pass
"""

# ----------------------------------------------------------- Java fixtures
#
# Java 8 syntax only: the only JDK on this machine is 1.8.0_291.

_JAVA_AC = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int a = sc.nextInt();
        int b = sc.nextInt();
        System.out.println(a + b);
    }
}
"""

_JAVA_CE = """\
public class Main {
    public static void main(String[] args) {
        System.out.println("missing semicolon")
    }
}
"""

_JAVA_TLE = """\
public class Main {
    public static void main(String[] args) {
        while (true) {
        }
    }
}
"""


# Every language's toolchain probe, plus the three programs. The probe is
# the compiler rather than the runtime, matching env-check's reasoning: a
# JRE ships `java` and still cannot judge anything.
_LANGUAGES: dict[str, dict[str, str]] = {
    "cpp": {
        "tool": settings.cpp_compiler_path,
        "ac": _CPP_AC,
        "ce": _CPP_CE,
        "tle": _CPP_TLE,
    },
    "python": {
        "tool": settings.resolved_python_interpreter,
        "ac": _PY_AC,
        "ce": _PY_CE,
        "tle": _PY_TLE,
    },
    "java": {
        "tool": settings.java_compiler_path,
        "ac": _JAVA_AC,
        "ce": _JAVA_CE,
        "tle": _JAVA_TLE,
    },
}


def _tool_available(command: str) -> bool:
    """True when *command* (bare name or path) can actually be located."""
    if os.sep in command or "/" in command:
        return Path(command).exists()
    return shutil.which(command) is not None


def _cases(kind: str) -> list[pytest.ParameterSet]:
    """One parametrized case per language, skipped when it cannot run."""
    params = []
    for language, programs in _LANGUAGES.items():
        params.append(
            pytest.param(
                language,
                programs[kind],
                id=f"{language}-{kind}",
                marks=pytest.mark.skipif(
                    not _tool_available(programs["tool"]),
                    reason=f"configured {language} toolchain is not available",
                ),
            )
        )
    return params


_SUM_CASES = [
    JudgeTestCase(input="3 5\n", expected_output="8\n"),
    JudgeTestCase(input="-5 5\n", expected_output="0\n"),
]


# ------------------------------------------------------------ accept path


@pytest.mark.parametrize("language,code", _cases("ac"))
def test_correct_solution_is_accepted(language: str, code: str) -> None:
    """Each language's happy path, through the real build+run pipeline."""
    result = judge(
        code,
        _SUM_CASES,
        time_limit=15.0,
        mem_limit_mb=256,
        language=language,
    )

    assert result.status is JudgeStatus.AC, (
        f"{language} verdict was {result.status.value}; "
        f"compile_output={result.compile_output!r}; "
        f"cases={[tc.to_dict() for tc in result.test_cases]}"
    )
    assert result.passed_count == len(_SUM_CASES)


def test_language_is_case_insensitive_on_the_request() -> None:
    """Handlers pass the id straight through; the registry canonicalises it."""
    result = judge(
        _CPP_AC,
        [JudgeTestCase(input="1 2\n", expected_output="3\n")],
        time_limit=15.0,
        mem_limit_mb=256,
        language="  Cpp ",
    )
    assert result.status is JudgeStatus.AC


# ------------------------------------------------------- compilation error


@pytest.mark.parametrize("language,code", _cases("ce"))
def test_broken_source_yields_ce(language: str, code: str) -> None:
    """A build failure short-circuits before any case runs.

    Python is included deliberately: it has no build phase, but it goes
    through ``py_compile`` so a syntax error is reported as CE instead of
    as a confusing runtime failure.
    """
    result = judge(
        code,
        [JudgeTestCase(input="", expected_output="")],
        time_limit=15.0,
        mem_limit_mb=256,
        language=language,
    )

    assert result.status is JudgeStatus.CE
    assert result.test_cases == []
    assert result.compile_output.strip(), "CE must carry toolchain diagnostics"


# ------------------------------------------------------------- time limit


@pytest.mark.parametrize("language,code", _cases("tle"))
def test_infinite_loop_yields_tle(language: str, code: str) -> None:
    result = judge(
        code,
        [JudgeTestCase(input="", expected_output="anything\n")],
        time_limit=1.0,
        mem_limit_mb=256,
        language=language,
    )

    assert result.status is JudgeStatus.TLE
    assert result.test_cases[0].status is JudgeStatus.TLE


# --------------------------------------------------------------- encoding


@pytest.mark.skipif(
    not _tool_available(settings.resolved_python_interpreter),
    reason="configured Python interpreter is not available",
)
def test_python_non_ascii_output_is_not_mangled() -> None:
    """Guards ``PYTHONIOENCODING=utf-8`` in the sandbox environment.

    Python's stdio otherwise inherits the locale encoding — GBK on this
    machine's Chinese Windows — so the interpreter would emit GBK bytes
    that the runner decodes as UTF-8. A correct answer would then come
    back as mojibake and be judged WA, which is a failure mode with no
    visible cause from the user's side.
    """
    code = 'print("你好，世界")\n'

    result = judge(
        code,
        [JudgeTestCase(input="", expected_output="你好，世界\n")],
        time_limit=15.0,
        mem_limit_mb=256,
        language="python",
    )

    assert result.status is JudgeStatus.AC, (
        f"actual_output={result.test_cases[0].actual_output!r}, "
        f"stderr={result.test_cases[0].stderr!r}"
    )


# ------------------------------------------------------- request plumbing


def test_unknown_language_is_rejected_before_any_work() -> None:
    """The failure must be legible, not a KeyError from deep in the sandbox."""
    with pytest.raises(UnknownLanguageError) as excinfo:
        judge(
            "int main() {}",
            [JudgeTestCase(input="", expected_output="")],
            language="cppp",
        )

    assert "cppp" in str(excinfo.value)


def test_ws_language_defaults_to_cpp_when_omitted() -> None:
    """P1-15 clients send no language field and must keep working."""
    payload = {"code": "x", "test_cases": [{"input": "", "expected_output": ""}]}

    assert _parse_request(payload)[-1] == "cpp"


def test_ws_language_is_canonicalised() -> None:
    payload = {
        "code": "x",
        "language": " JAVA ",
        "test_cases": [{"input": "", "expected_output": ""}],
    }

    assert _parse_request(payload)[-1] == "java"


def test_ws_rejects_an_unknown_language_as_a_judge_error() -> None:
    """A bad id is a client error, not a crash in the worker thread."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": _CPP_AC,
                "language": "cppp",
                "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
            }
        )
        msg = ws.receive_json()

    assert msg["type"] == "judge_error"
    assert "cppp" in msg["error"]


def test_rest_judge_rejects_an_unknown_language_with_a_422() -> None:
    """The REST twin validates at the edge too, before spawning anything."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_AC,
            "language": "cppp",
            "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
        },
    )

    assert response.status_code == 422


@pytest.mark.skipif(
    not _tool_available(settings.resolved_python_interpreter),
    reason="configured Python interpreter is not available",
)
def test_ws_streams_a_verdict_for_a_non_cpp_language() -> None:
    """Proves the language reaches the worker thread, not just the parser."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": _PY_AC,
                "language": "python",
                "test_cases": [{"input": "3 5\n", "expected_output": "8\n"}],
                "time_limit": 15.0,
                "mem_limit_mb": 256,
            }
        )
        msgs = []
        while True:
            msg = ws.receive_json()
            msgs.append(msg)
            if msg.get("type") in ("judge_done", "judge_error"):
                break

    assert msgs[-1]["type"] == "judge_done", msgs[-1]
    assert msgs[-1]["status"] == "AC"


# ==========================================================================
# PU-12: judging by problem_id
#
# These cover the transport layer only — the resolution rules themselves
# are asserted in ``test_judge_input.py``. What is checked here is that
# both entry points actually route through the resolver: the WS parser
# returns the resolved cases, and the REST route 404s on an unknown
# problem instead of leaking a 500.
# ==========================================================================


def test_ws_parse_request_resolves_a_problem_id() -> None:
    """``problem_id`` is accepted in place of ``test_cases``.

    Uses an injected in-memory session: the production default would
    open an engine against the documented PostgreSQL DSN, which is not
    running here. ``_parse_request`` takes a session for exactly this
    reason.
    """
    with _memory_session() as session:
        _seed_problem(session, problem_id=1, cases=[("1 2\n", "3\n", False)])

        code, resolved, language = _parse_request(
            {"code": "x", "problem_id": 1, "time_limit": 2.0, "mem_limit_mb": 128},
            session=session,
        )

    assert code == "x"
    assert language == "cpp"
    assert resolved.problem_id == 1
    assert resolved.time_limit == 2.0
    assert resolved.mem_limit_mb == 128
    # The hidden case reached the judge — that is the point of this mode.
    assert [c.input for c in resolved.test_cases] == ["1 2\n"]


def test_ws_parse_request_rejects_unknown_problem_id() -> None:
    """An unknown id raises rather than judging against nothing."""
    with _memory_session() as session, pytest.raises(ValueError, match="not found"):
        _parse_request({"code": "x", "problem_id": 7}, session=session)


def test_ws_parse_request_rejects_both_input_modes() -> None:
    """``problem_id`` + ``test_cases`` is refused, not resolved.

    Precedence guessed here would be a silent behavior difference
    between a client that sends both and one that sends one.
    """
    with pytest.raises(ValueError, match="mutually exclusive"):
        _parse_request(
            {
                "code": "x",
                "problem_id": 1,
                "test_cases": [{"input": "", "expected_output": ""}],
            }
        )


def test_ws_parse_request_requires_an_input_mode() -> None:
    """Neither mode supplied is an error."""
    with pytest.raises(ValueError, match="one of"):
        _parse_request({"code": "x"})


def test_rest_judge_404s_on_unknown_problem_id() -> None:
    """A missing problem is a 404, not a 422 or a 500.

    The distinction matters to a client: 404 means "that id does not
    exist", 422 means "your body is malformed".

    Patches the resolver's view of ``get_session`` so the route's own
    database call lands on an empty in-memory database. Seeding nothing
    is what makes the id unknown.
    """
    with _patched_judge_session():
        # No rows seeded, so any id is unknown.
        client = TestClient(app)
        response = client.post(
            "/api/v1/judge",
            json={"code": _CPP_AC, "problem_id": 987654321},
        )

    assert response.status_code == 404
    assert "987654321" in response.json()["detail"]


def test_rest_judge_422s_when_no_input_mode_supplied() -> None:
    """Omitting both ``problem_id`` and ``test_cases`` is a 422."""
    client = TestClient(app)
    response = client.post("/api/v1/judge", json={"code": _CPP_AC})
    assert response.status_code == 422


def test_rest_judge_422s_when_both_input_modes_supplied() -> None:
    """Supplying both is a 422 rather than a silent precedence choice."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_AC,
            "problem_id": 1,
            "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
        },
    )
    assert response.status_code == 422
    assert "mutually exclusive" in response.json()["detail"]


def test_rest_judge_inline_mode_still_works() -> None:
    """The self-test path is unchanged by the new mode."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_AC,
            "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "AC"
    assert body["problem_id"] is None


def test_rest_judge_response_type_is_still_bean_shaped() -> None:
    """``problem_id`` is additive, and the old keys are all present.

    A response model that *dropped* a field would be a breaking change
    for the existing frontend; this pins the shape.
    """
    client = TestClient(app)
    body = client.post(
        "/api/v1/judge",
        json={
            "code": _CPP_AC,
            "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
        },
    ).json()

    for key in ("status", "compile_output", "passed", "total", "test_cases"):
        assert key in body, key
    assert "problem_id" in body
    assert body["total"] == 1
