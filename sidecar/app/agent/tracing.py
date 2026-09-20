"""LangSmith tracing for the analysis path (阶段 10 · `AGENTS.md` S9).

Why this module exists rather than "just set the env var"
---------------------------------------------------------
LangSmith's *automatic* tracing instruments objects it already knows —
LangChain runnables, chat models, LangGraph graphs. **None of those are on
this project's LLM path.** :mod:`app.llm.gateway` talks to an
OpenAI-compatible endpoint over plain httpx, deliberately: ``AGENTS.md``
§二 pins the gateway to one protocol so switching provider is a config
change and not a code change. A LangChain chat model would put something
else in front of that endpoint.

So ``LANGSMITH_API_KEY`` alone buys nothing. Set it, run a submission, and
the LangSmith project stays empty — and nothing warns you, because from
the outside "tracing configured" and "tracing silently captured nothing"
look identical. The traces have to be attached to *our* functions, which
is what :func:`traceable_if_enabled` does.

What is traced
--------------
Three levels, because a flat list of spans is not worth much:

* :func:`app.agent.pipeline.analyse` — one span per submission, whatever
  the orchestrator.
* :func:`app.agent.graph.run_analysis_graph` — the graph's own span,
  with the four nodes nested inside it.
* :func:`app.agent.analyzer.analyze_error` — the innermost, and the one
  that shows the prompt, the reply and how many attempts it took.

Each wraps the next, so a run shows up as a tree rather than three
unrelated rows.

Off by default, and decided per call
------------------------------------
Tracing costs a background HTTP request per span, sent from the student's
machine while they wait for ``judge_done``. With no key configured none of
that happens — the wrapper calls the original function straight through.

The switch is read **at call time**, not once at import. Two reasons, and
the second is the one that matters:

1. importing LangSmith and building the wrapper is work a process that
   never traces should not pay;
2. a decision frozen at import time cannot be tested without reloading
   modules, and cannot be changed without a restart. Reading it per call
   costs one attribute lookup against a function that is about to make a
   network round trip — which is not a cost worth optimising.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from typing import Any, Optional, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

#: Set once, by :func:`configure_tracing`, so the startup log line appears
#: once per process rather than once per traced call.
_configured = False


def tracing_enabled() -> bool:
    """Whether this process sends traces.

    The key is the only switch. There is no separate "tracing on/off"
    setting, because the failure mode of a second switch is a user who
    turns tracing on, sees nothing, and has two places to check.
    """
    return bool((settings.langsmith_api_key or "").strip())


def configure_tracing() -> bool:
    """Export the environment LangSmith reads. Call once, at startup.

    LangSmith is configured entirely through environment variables —
    there is no Python API for "use this key" — so a ``settings`` value
    has to be translated into the process environment for the tracer to
    find it. Doing it here rather than inline in ``main.py`` keeps the
    four variable names together and keeps the import of this module free
    of side effects.

    Returns:
        Whether tracing is on, so the caller can log it once.
    """
    global _configured

    enabled = tracing_enabled()
    if not _configured:
        _configured = True
        if enabled:
            logger.info(
                "LangSmith tracing on (project %s)",
                settings.langsmith_project or "algoforge",
            )
        else:
            logger.info(
                "LangSmith tracing off: LANGSMITH_API_KEY is not set. "
                "Set it to send analysis traces; the analysis itself is "
                "unaffected."
            )

    if not enabled:
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project or "algoforge"
    # The endpoint is deliberately left unset: LangSmith infers it from the
    # key's prefix, and guessing it wrong sends traces nowhere silently.
    # Only a self-hosted deployment needs LANGCHAIN_ENDPOINT.
    return True


def load_traceable() -> Optional[Callable[..., Any]]:
    """Import LangSmith's decorator, or ``None`` when it is unavailable.

    Broken out — rather than inlined in the wrapper — so a test can
    substitute a recorder and assert the traced path is taken without
    sending anything to smith.langchain.com.
    """
    try:
        from langsmith import traceable
    except ImportError:
        # langsmith arrives with langchain, which arrives with langgraph,
        # so this is belt-and-braces. Tracing is enrichment: a missing
        # tracer must not stop the analysis from running.
        logger.warning("langsmith is not importable; tracing stays off")
        return None
    return traceable


def traceable_if_enabled(name: str) -> Callable[[F], F]:
    """A decorator that wraps *func* for LangSmith when tracing is on.

    Args:
        name: The span name. Passed explicitly rather than taken from
            ``func.__name__`` so renaming the function does not silently
            rename the span — traces are looked up by name, and a rename
            that quietly moves them is worse than one that breaks loudly.

    Returns:
        A wrapper that calls *func* directly while tracing is off, and
        through LangSmith's ``traceable`` once it is on.
    """

    def _decorate(func: F) -> F:
        traced: Optional[Callable[..., Any]] = None
        resolved = False

        @functools.wraps(func)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            if not tracing_enabled():
                return func(*args, **kwargs)

            nonlocal traced, resolved
            if not resolved:
                decorator = load_traceable()
                traced = decorator(name=name)(func) if decorator else func
                resolved = True
            return traced(*args, **kwargs)

        return _wrapper  # type: ignore[return-value]

    return _decorate


def reset_for_tests() -> None:
    """Forget that :func:`configure_tracing` already ran.

    It logs once per process on purpose, and that cache is exactly what a
    test asserting the log line needs to clear.
    """
    global _configured
    _configured = False
