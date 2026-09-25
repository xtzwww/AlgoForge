"""Bounded in-memory store of debug sessions (PU-17).

A debug session is inherently stateful — it owns a compiled binary and a
position in a running program — so it cannot be a stateless request
handler. It also must not be persisted: a session's working directory
holds an executable, and keeping those across a restart would accumulate
binaries on a disk that is already tight on this machine.

Hence a bounded dictionary with eviction, which is the smallest thing
that behaves correctly:

* **Capped.** Every session owns a scratch directory with a binary in it.
  An uncapped dict is a slow disk leak — one entry per student who ever
  pressed 调试, held until the sidecar restarts.
* **Expiring.** A session idle for longer than the TTL is abandoned in
  practice; keeping it only holds its directory. The TTL is generous
  because debugging involves reading, not clicking, and evicting a
  session mid-thought is much worse than holding a few megabytes.
* **Not thread-safe by lock, but safe by usage.** Every mutation happens
  inside ``run_in_sandbox`` (the sandbox thread pool), which serialises
  access; the store's own operations are dict reads/writes, which are
  atomic under the GIL. Documented rather than defended with a lock,
  because a lock here would imply a concurrency requirement that the
  sandbox pool already removes.
* **Keyed by an unguessable id.** The id *is* the capability: anyone
  holding it can read the session's variables. That is acceptable because
  the id is only ever handed to the authenticated client that created it,
  and because the sidecar itself already requires the process token on
  every request (see ``app/security.py``).

Sessions are never shared between users, and the store deliberately does
not try to enforce that: it stores no user id, so it cannot leak one. The
id is generated with :func:`secrets.token_urlsafe`, and a client can only
learn its own.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from app.debug_session import DebugSession, close_session

logger = logging.getLogger(__name__)

#: How many sessions may exist at once. Chosen to be comfortably above a
#: classroom's concurrent debuggers while bounding disk use: each session
#: is one scratch directory with a ~130 KB binary.
MAX_SESSIONS = 32

#: Seconds a session may sit idle before it is evicted. An hour is far
#: longer than any single debugging attempt, and short enough that an
#: abandoned session does not outlive the lecture it belonged to.
SESSION_TTL_SECONDS = 60 * 60


@dataclass
class _Entry:
    """A session plus the bookkeeping the eviction policy needs."""

    session: DebugSession
    created_at: float = 0.0
    touched_at: float = 0.0

    def __post_init__(self) -> None:
        # Reads ``time.monotonic`` at *call* time rather than binding the
        # function object in a ``default_factory``. A default_factory is
        # evaluated when the class body runs, so it would capture the
        # original function and ignore any later substitution of the
        # clock — making the two timestamps in this module come from
        # different sources. One clock source, read late.
        if not self.created_at:
            self.created_at = time.monotonic()
        if not self.touched_at:
            self.touched_at = time.monotonic()

    def touch(self) -> None:
        self.touched_at = time.monotonic()


_sessions: dict[str, _Entry] = {}


def _sweep() -> None:
    """Drop expired sessions and their directories.

    Called before every create so the store never grows past its bounds
    without a background task. Cheap even when nothing expires — one pass
    over at most :data:`MAX_SESSIONS` entries.
    """
    cutoff = time.monotonic() - SESSION_TTL_SECONDS
    expired = [key for key, entry in _sessions.items() if entry.touched_at < cutoff]
    for key in expired:
        close_session(_sessions.pop(key).session)


def _evict_oldest() -> None:
    """Drop the least-recently-used session to make room.

    LRU rather than oldest-created: a student who has been debugging for
    an hour and steps once more should keep their session, while the one
    who opened 调试 and wandered off should not.

    The evicted session's directory is removed, and the next request
    against that id gets a clean 404 — the client is expected to start a
    new session, which is the correct recovery.
    """
    if not _sessions:
        return
    oldest = min(_sessions.items(), key=lambda item: item[1].touched_at)
    logger.info("evicting debug session %s to make room", oldest[0])
    close_session(_sessions.pop(oldest[0]).session)


def new_id() -> str:
    """An unguessable session id.

    ``token_urlsafe`` rather than a counter: the id is the only thing
    standing between one client's variables and another's, so it has to
    be unpredictable. 32 bytes is far beyond what a local tool needs,
    which is the right side to err on.
    """
    return secrets.token_urlsafe(32)


def put(session: DebugSession) -> DebugSession:
    """Register a session, making room if the store is full."""
    _sweep()
    while len(_sessions) >= MAX_SESSIONS:
        _evict_oldest()
    _sessions[session.session_id] = _Entry(session=session)
    return session


def get(session_id: str) -> Optional[DebugSession]:
    """The session with this id, or ``None`` if unknown or expired.

    Touches the entry on success, which is what makes the TTL an *idle*
    timeout rather than a hard session lifetime.
    """
    entry = _sessions.get(session_id)
    if entry is None:
        return None
    if entry.touched_at < time.monotonic() - SESSION_TTL_SECONDS:
        close_session(_sessions.pop(session_id).session)
        return None
    entry.touch()
    return entry.session


def remove(session_id: str) -> bool:
    """Drop a session and its directory. True if it existed."""
    entry = _sessions.pop(session_id, None)
    if entry is None:
        return False
    close_session(entry.session)
    return True


def clear() -> None:
    """Drop every session. For tests and for shutdown.

    Reading the keys into a list first because ``remove`` mutates the dict
    being iterated — a plain ``for key in _sessions`` would raise.
    """
    for key in list(_sessions):
        remove(key)


def count() -> int:
    """How many sessions are live. For tests and diagnostics."""
    return len(_sessions)


__all__ = [
    "MAX_SESSIONS",
    "SESSION_TTL_SECONDS",
    "clear",
    "count",
    "get",
    "new_id",
    "put",
    "remove",
]
