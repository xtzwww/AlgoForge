"""The /run-code route must not compile on the event loop.

Why this file exists
--------------------
``compile_and_run`` ends in ``subprocess.run`` — a blocking call that holds
its thread for as long as the compiler and the program take — and the route
is ``async def``. Calling it directly, which is what the route did, ran
that blocking work *on the event loop*, so a single submission being
compiled froze the whole sidecar: the judge WebSocket stream, the problem
list, every other request.

``app/api/judge.py`` has always done this correctly with ``await
asyncio.to_thread(...)``; this route was the one place that did not. It
also had **no test at all**, which is how it survived — nothing ever
exercised it concurrently.

The assertion is about the *thread*, not the clock. A timing assertion
("the loop kept ticking during the build") would be flaky and would still
be measuring the machine; "which thread did the build run on" is exactly
the property that was wrong, and it is deterministic.

The route is called directly rather than through ``TestClient``, because
under ``TestClient`` the whole app already runs off the main thread — so
"not the main thread" would pass either way and prove nothing.
"""

from __future__ import annotations

import asyncio
import threading

from app.api import run_code as run_code_module


class _StubCompileResult:
    """Just the shape ``RunCodeResponse(**result.to_dict())`` needs."""

    def to_dict(self) -> dict:
        return {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "compile_output": "",
        }


def test_the_build_runs_off_the_event_loop_thread(monkeypatch):
    """The compile must not share a thread with the loop that awaited it.

    Before the fix these were the same thread and this fails; with
    ``asyncio.to_thread`` the build lands on an executor thread.
    """
    threads: dict[str, threading.Thread] = {}

    def stub_compile_and_run(code: str, *, language: str):
        threads["build"] = threading.current_thread()
        return _StubCompileResult()

    monkeypatch.setattr(run_code_module, "compile_and_run", stub_compile_and_run)

    async def main() -> None:
        threads["loop"] = threading.current_thread()
        await run_code_module.run_code(
            run_code_module.RunCodeRequest(code="int main(){}")
        )

    asyncio.run(main())

    assert threads["build"] is not threads["loop"], (
        "the build ran on the event loop thread, so one slow compile would "
        "block every other request in the sidecar"
    )


def test_the_response_is_built_from_the_compile_result(monkeypatch):
    """The route still returns what the compiler reported."""
    monkeypatch.setattr(
        run_code_module,
        "compile_and_run",
        lambda code, **kwargs: _StubCompileResult(),
    )

    response = asyncio.run(
        run_code_module.run_code(run_code_module.RunCodeRequest(code="int main(){}"))
    )

    assert response.success is True
    assert response.exit_code == 0
    assert response.stdout == ""
