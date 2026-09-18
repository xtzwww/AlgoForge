"""The sandbox's build pool: off the loop, and at most N at once.

Two properties, and the second is the one that was missing. ``asyncio.to_thread``
already put builds on a worker thread, which fixed "one compile freezes the
sidecar"; it did nothing about "eight compiles at once", because the default
executor it submits to is sized ``min(32, cpu + 4)``.

The cap is tested by observation rather than by inspecting the pool: what
matters is that N+ builds never run N+ at a time, and counting how many
are actually inside the callable is the only way to say that. A test that
asserted ``max_workers == 4`` would pass on a pool nothing used.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.sandbox import pool as pool_module
from app.sandbox.pool import CONCURRENCY, run_in_sandbox, shutdown_pool


@pytest.fixture(autouse=True)
def _clean_pool():
    """Give each test a pool nobody else's jobs are sitting in."""
    shutdown_pool()
    yield
    shutdown_pool()


def test_the_callable_runs_off_the_event_loop_thread() -> None:
    """The property ``to_thread`` already gave us, and must keep giving."""
    seen: dict[str, threading.Thread] = {}

    def build() -> str:
        seen["build"] = threading.current_thread()
        return "ok"

    async def main() -> None:
        seen["loop"] = threading.current_thread()
        assert await run_in_sandbox(build) == "ok"

    asyncio.run(main())
    assert seen["build"] is not seen["loop"]


def test_the_pool_is_named_after_the_sandbox() -> None:
    """So a stack trace during a build says which pool it came from."""

    def build() -> str:
        return threading.current_thread().name

    name = asyncio.run(run_in_sandbox(build))
    assert name.startswith("sandbox")


def test_at_most_n_builds_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The property that was missing.

    More jobs than slots, each holding its slot long enough to overlap.
    Without a cap the peak would climb towards the number of jobs; with
    one it stops at ``CONCURRENCY``.
    """
    monkeypatch.setattr(pool_module, "CONCURRENCY", 3)
    shutdown_pool()

    inside = 0
    peak = 0
    lock = threading.Lock()

    def build() -> None:
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.05)
        with lock:
            inside -= 1

    async def main() -> None:
        await asyncio.gather(*(run_in_sandbox(build) for _ in range(12)))

    asyncio.run(main())

    assert peak > 1, "the jobs never overlapped, so the cap was not exercised"
    assert peak <= 3, f"{peak} builds ran at once with a ceiling of 3"


def test_the_shipped_ceiling_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The right number depends on the machine, so it is not hardcoded."""
    monkeypatch.setenv("SANDBOX_CONCURRENCY", "2")
    assert pool_module._configured_concurrency() == 2

    monkeypatch.setenv("SANDBOX_CONCURRENCY", "not-a-number")
    # A typo must not pick an arbitrary ceiling or crash the boot.
    assert pool_module._configured_concurrency() == pool_module.DEFAULT_CONCURRENCY

    monkeypatch.setenv("SANDBOX_CONCURRENCY", "0")
    # Zero would deadlock every submission that was ever made.
    assert pool_module._configured_concurrency() == 1


def test_the_default_ceiling_is_sane() -> None:
    assert CONCURRENCY >= 1


def test_a_failing_build_propagates_its_exception() -> None:
    """The caller's error handling must not have to know a pool exists."""

    def build() -> None:
        raise RuntimeError("compiler blew up")

    async def main() -> None:
        with pytest.raises(RuntimeError, match="compiler blew up"):
            await run_in_sandbox(build)

    asyncio.run(main())


def test_arguments_reach_the_callable() -> None:
    """``partial`` forwarding, which is the easy thing to get wrong."""

    def build(code: str, *, language: str) -> str:
        return f"{language}:{len(code)}"

    got = asyncio.run(run_in_sandbox(build, "int main(){}", language="cpp"))
    assert got == "cpp:12"
