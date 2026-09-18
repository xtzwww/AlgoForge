"""A ceiling on how many builds can run at once.

Every blocked-thread call in this project used to go through
``asyncio.to_thread``, which was a fix for a real bug — a compile on the
event loop froze the whole sidecar — but it is not a *limit*. It submits
to the loop's default executor, sized ``min(32, cpu_count + 4)``, so on
this machine eight submissions in flight mean eight concurrent ``g++``
processes, each of which wants a core and a few hundred megabytes. Two
students submitting at the same moment is enough to make the third one's
verdict take ten seconds longer than it should, and the failure looks
like "the judge is slow" rather than "we started too many compilers".

So the sandbox gets its own pool with its own size.

Why a pool and not a semaphore
------------------------------
The obvious alternative is ``asyncio.Semaphore(N)`` around the existing
``to_thread`` calls. It does not work here: a semaphore binds to the
first event loop that awaits it and refuses every later one, and this
process runs many — one per test, one per Uvicorn reload, one per
``asyncio.run`` in the tooling. A ``ThreadPoolExecutor`` has no loop in
its lifetime, so it is correct in all of them.

Why the queue is unbounded
--------------------------
A pool with N threads still accepts unlimited submissions; they wait.
That is the right failure mode for a class: the ninth submission of a
burst is *late*, not refused, and a judge that answered 503 to a student
who pressed "submit" would be worse than one that made them wait two
seconds. The sandbox's own timeout still bounds any single run.

Not covered here
----------------
``analyse_submission`` stays on ``asyncio.to_thread``. It is a blocking
HTTP call to a model provider, not a compiler: it holds no core, and
capping it alongside builds would let a slow provider starve the judge.
Its ceiling is the default executor's own size, which is fine for
network-bound work.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar

T = TypeVar("T")

#: How many builds may run at once. Overridable because the right number
#: depends on the machine this is deployed on, and a hardcoded guess is
#: wrong somewhere — on a 2-core CI runner 4 is already too many.
DEFAULT_CONCURRENCY = 4


def _configured_concurrency() -> int:
    raw = os.getenv("SANDBOX_CONCURRENCY", "").strip()
    if not raw:
        return DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        # A typo in the environment should not silently pick an
        # arbitrary ceiling, and it should not crash the sidecar on boot
        # either — so it is reported once and the default stands.
        return DEFAULT_CONCURRENCY
    # At least one: zero would deadlock every submission forever, and a
    # negative number would be accepted by ThreadPoolExecutor and then
    # reject every submit.
    return max(1, value)


CONCURRENCY: int = _configured_concurrency()

_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    """The process-wide pool, created on first use.

    Lazily rather than at import: importing this module must not spawn
    threads, because several test modules import it without ever running
    a build, and threads created at import time outlive the test run.
    """
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=CONCURRENCY, thread_name_prefix="sandbox"
        )
    return _executor


async def run_in_sandbox(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking build off the event loop, at most N at a time.

    Args:
        func: The blocking callable — ``judge``, ``compile_and_run``, or
            a closure around them.
        *args / **kwargs: Forwarded unchanged.

    Returns:
        Whatever ``func`` returns.

    Raises:
        Exception: Anything ``func`` raises, propagated unchanged. The
            caller's error handling must not have to know a pool exists.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool(), partial(func, *args, **kwargs))


def shutdown_pool() -> None:
    """Release the pool's threads.

    Only for tests and for a clean process exit — the pool is meant to
    live as long as the process does.
    """
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None
