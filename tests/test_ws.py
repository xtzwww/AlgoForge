"""Tests for the WebSocket judge-progress stream (P1-15).

Exercises the ``WS /ws/run-log`` endpoint through FastAPI's
TestClient WebSocket support:

- A correct AC submission streams compile + case events, ending in
  judge_done with status AC.
- A CE submission short-circuits: compile_start → compile_done
  (success=false) → judge_done (CE).
- A bad request (missing code / empty cases) yields judge_error and the
  connection closes.
- The event sequence for AC respects the documented order
  (compile_start before compile_done before any case_start, etc.).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import app

# A correct sum solution: reads a b, prints a+b.
_SUM_AC_CODE = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""

# Missing semicolon — guaranteed CE.
_CE_CODE = """\
#include <iostream>
int main() {
    std::cout << "missing semicolon" << std::endl
    return 0;
}
"""


def _receive_until_done(
    client: TestClient, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Drain WS messages until a terminal event (judge_done / judge_error)."""
    msgs: list[dict[str, Any]] = []
    while True:
        msg = client.receive_json()
        msgs.append(msg)
        if msg.get("type") in ("judge_done", "judge_error"):
            break
    return msgs


def test_ws_ac_streams_full_progress_sequence() -> None:
    """A correct submission streams compile + case events → judge_done AC."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": _SUM_AC_CODE,
                "test_cases": [
                    {"input": "1 2\n", "expected_output": "3\n"},
                    {"input": "10 20\n", "expected_output": "30\n"},
                ],
                "time_limit": 5.0,
                "mem_limit_mb": 256,
            }
        )
        msgs = _receive_until_done(ws)

    # Must end with judge_done AC.
    last = msgs[-1]
    assert last["type"] == "judge_done"
    assert last["status"] == "AC"
    assert last["passed"] == 2
    assert last["total"] == 2

    # Verify the event ordering: compile_start before compile_done before
    # any case_start; each case_start before its case_done; judge_done last.
    types = [m["type"] for m in msgs]
    assert types[0] == "compile_start"
    assert "compile_done" in types
    assert types.index("compile_done") < types.index("case_start")
    assert types[-1] == "judge_done"

    # compile_done should report success.
    compile_done = next(m for m in msgs if m["type"] == "compile_done")
    assert compile_done["success"] is True

    # Two case_start / two case_done in order.
    case_starts = [m for m in msgs if m["type"] == "case_start"]
    case_dones = [m for m in msgs if m["type"] == "case_done"]
    assert len(case_starts) == 2
    assert len(case_dones) == 2
    assert case_starts[0]["index"] == 0
    assert case_starts[1]["index"] == 1
    assert case_dones[0]["index"] == 0
    assert case_dones[1]["index"] == 1
    assert all(cd["status"] == "AC" for cd in case_dones)


def test_ws_ce_short_circuits_to_judge_done_ce() -> None:
    """A CE short-circuits: compile_start → compile_done(failed) → judge_done."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": _CE_CODE,
                "test_cases": [{"input": "", "expected_output": ""}],
                "time_limit": 5.0,
                "mem_limit_mb": 256,
            }
        )
        msgs = _receive_until_done(ws)

    last = msgs[-1]
    assert last["type"] == "judge_done"
    assert last["status"] == "CE"
    assert last["total"] == 0

    # No case events should be emitted on CE.
    assert not any(m["type"] == "case_start" for m in msgs)
    assert not any(m["type"] == "case_done" for m in msgs)

    # compile_done must report failure with diagnostics.
    compile_done = next(m for m in msgs if m["type"] == "compile_done")
    assert compile_done["success"] is False
    assert compile_done["output"], "Expected non-empty compile diagnostics"


def test_ws_missing_code_returns_judge_error() -> None:
    """A request without 'code' yields judge_error and closes."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json({"test_cases": [{"input": "", "expected_output": ""}]})
        msgs = _receive_until_done(ws)

    assert len(msgs) == 1
    assert msgs[0]["type"] == "judge_error"
    assert "code" in msgs[0]["error"].lower()


def test_ws_empty_test_cases_returns_judge_error() -> None:
    """A request with no test cases yields judge_error."""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json({"code": _SUM_AC_CODE, "test_cases": []})
        msgs = _receive_until_done(ws)

    assert len(msgs) == 1
    assert msgs[0]["type"] == "judge_error"
    assert "test_cases" in msgs[0]["error"].lower()


def test_ws_wa_submission_streams_case_done_wa() -> None:
    """A wrong answer streams case_done with status WA and ends judge_done WA."""
    # Reads a b, but prints a*b instead of a+b — wrong answer.
    wa_code = """\
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a * b << std::endl;
    return 0;
}
"""
    client = TestClient(app)
    with client.websocket_connect("/ws/run-log") as ws:
        ws.send_json(
            {
                "code": wa_code,
                "test_cases": [{"input": "1 2\n", "expected_output": "3\n"}],
                "time_limit": 5.0,
                "mem_limit_mb": 256,
            }
        )
        msgs = _receive_until_done(ws)

    assert msgs[-1]["type"] == "judge_done"
    assert msgs[-1]["status"] == "WA"
    case_done = next(m for m in msgs if m["type"] == "case_done")
    assert case_done["status"] == "WA"
