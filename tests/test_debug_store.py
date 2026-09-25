"""Tests for the bounded debug-session store (PU-17).

``app/debug_store.py`` is what keeps a debug session from being a slow
disk leak: each one owns a scratch directory with a compiled binary in
it, so an uncapped dictionary is one binary per student who ever pressed
调试, held until the sidecar restarts. On this machine the disk is the
tightest resource there is, which is why the bound is enforced rather
than merely documented.

Three behaviours, each with a failure that is *invisible* in normal use:

* **LRU, not oldest-created.** A student debugging for an hour and
  stepping once more must keep their session; the one who opened 调试 and
  wandered off should lose it. Evicting by creation time looks identical
  until the first long session, at which point somebody's work vanishes
  mid-thought.
* **Idle TTL, not session lifetime.** Debugging is reading, not clicking.
  A hard lifetime would kill a session while its owner was thinking.
* **Eviction removes the directory.** A dropped *entry* that leaves its
  directory behind is the disk leak the cap was supposed to prevent.

⚠️ **``work_dir`` must be a real (and disposable) path.**
``close_session`` is ``shutil.rmtree(session.work_dir, ignore_errors=True)``.
An earlier version of this file passed ``Path(".")``, and because both
fixtures patched the *wrong* name (``app.debug_session.close_session``
rather than ``app.debug_store.close_session``) the real function ran
against the repository root. ``ignore_errors=True`` made it succeed
quietly, and it deleted ``.git`` plus most of the working tree before
anyone noticed.

Two consequences, both applied below:

1. Every fixture patches ``app.debug_store.close_session`` — the name the
   store actually resolves. Patching the defining module's name changes
   nothing here, because ``debug_store`` imported the function into its
   own namespace.
2. Sessions get :func:`tempfile.mkdtemp` directories and are cleaned up
   deliberately, so even if the patch ever fails the damage is bounded.

Run with ``pytest tests/test_debug_store.py -m "not eval_set"``.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app import debug_store
from app.debug_session import DebugSession


@pytest.fixture
def work_dir() -> Path:
    """A fresh disposable working directory for one session.

    Belt and braces on top of the ``close_session`` patch: a test that
    somehow bypasses the patch still only touches a temp directory, so
    the damage is bounded to something ``tempfile`` would clean up anyway.
    """
    path = Path(tempfile.mkdtemp(prefix="dbgtest_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def make_session(session_id: str, work_dir: Path) -> DebugSession:
    return DebugSession(session_id=session_id, code="int main() {}", work_dir=work_dir)


class _Clock:
    """A monotonic clock the test advances explicitly.

    ``monotonic()`` returns a distinct value on every call, so insertion
    order is a real ordering rather than a tie-break. That is what lets a
    test assert *which* session LRU evicts.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds


@pytest.fixture
def sessions(work_dir: Path):
    """A session factory; every session gets the same temp directory.

    Directory *uniqueness* is not what these tests are about — the store
    forgets sessions by id, and the ``close_session`` patch is asserted on
    rather than allowed to run. What matters is that the path handed to
    ``close_session`` is a real, disposable temp directory, so a failed
    patch is harmless.
    """
    return lambda session_id: make_session(session_id, work_dir)


@pytest.fixture(autouse=True)
def _patch_close_session():
    """Stop the store from touching the filesystem.

    Patches ``app.debug_store.close_session`` — the name **as the store
    resolves it**. ``debug_store`` does ``from app.debug_session import
    close_session``, so it holds its own reference; patching the defining
    module leaves the real, destructive function in place. That mistake
    deleted the repository's ``.git`` directory (see the module docstring).
    """
    with patch("app.debug_store.close_session") as close:
        yield close


@pytest.fixture(autouse=True)
def _clean_store(_patch_close_session):
    """Every test starts and ends with an empty store.

    The store is module-level state, so a leaked session would change the
    eviction order of every later test — and the failures would look like
    a broken eviction policy rather than a dirty fixture.
    """
    debug_store.clear()
    yield
    debug_store.clear()


# ------------------------------------------------------------------ basics


def test_put_then_get_returns_the_same_session(sessions) -> None:
    session = debug_store.put(sessions("a"))

    assert debug_store.get("a") is session


def test_get_unknown_id_returns_none() -> None:
    assert debug_store.get("nope") is None


def test_remove_reports_whether_it_existed(sessions) -> None:
    debug_store.put(sessions("a"))

    assert debug_store.remove("a") is True
    # Idempotent from the caller's point of view, which is what lets the
    # DELETE route answer `closed: false` instead of 404.
    assert debug_store.remove("a") is False


def test_remove_cleans_up_the_directory(sessions) -> None:
    """Dropping the entry without the directory is the leak itself."""
    debug_store.put(sessions("a"))

    debug_store.remove("a")

    debug_store.close_session.assert_called_once()


def test_clear_empties_the_store(sessions) -> None:
    """``clear`` must not raise on the dict it is iterating.

    A plain ``for key in _sessions`` while removing would; the guard is
    reading the keys into a list first.
    """
    for name in ("a", "b", "c"):
        debug_store.put(sessions(name))

    debug_store.clear()

    assert debug_store.count() == 0


def test_count_tracks_live_sessions(sessions) -> None:
    assert debug_store.count() == 0
    debug_store.put(sessions("a"))
    assert debug_store.count() == 1


# ---------------------------------------------------------------------- id


def test_ids_are_unguessable_and_unique() -> None:
    """The id *is* the capability — it is the only thing standing between
    one client's variables and another's.
    """
    ids = {debug_store.new_id() for _ in range(200)}

    assert len(ids) == 200
    # token_urlsafe(32) ⇒ 43 chars. Far beyond what a local tool needs,
    # which is the right side to err on.
    assert all(len(i) >= 40 for i in ids)


# ------------------------------------------------------------------ bounds


def test_the_store_never_exceeds_its_cap(sessions) -> None:
    for index in range(debug_store.MAX_SESSIONS + 5):
        debug_store.put(sessions(f"s{index}"))

    assert debug_store.count() == debug_store.MAX_SESSIONS


def test_the_evicted_session_is_the_least_recently_used(sessions) -> None:
    """LRU, not oldest-created.

    Constructed so the two policies disagree: ``s0`` is created first but
    touched last, so an LRU store keeps it and a creation-ordered one
    would drop it. A student debugging for an hour and stepping once more
    should keep their session.

    A synthetic clock drives this rather than real time. Thirty-two
    insertions in a tight loop can land inside one ``time.monotonic()``
    tick, in which case every ``touched_at`` is *identical* and ``min``
    picks whichever entry the dict happens to yield first — so the test
    would pass or fail on dict ordering rather than on the policy. Ticks
    make "least recently used" a fact the test states rather than a race
    it hopes to win.
    """
    clock = _Clock()
    with patch("app.debug_store.time") as fake:
        fake.monotonic.side_effect = clock

        for index in range(debug_store.MAX_SESSIONS):
            debug_store.put(sessions(f"s{index}"))

        # s0 is the oldest, but make it the most recently used.
        clock.advance()
        debug_store.get("s0")

        # One more session forces exactly one eviction.
        clock.advance()
        debug_store.put(sessions("fresh"))

        assert debug_store.get("s0") is not None, (
            "the recently-used session was evicted"
        )
        # s1 was the least recently used, so that is the one that should go.
        assert debug_store.get("s1") is None


def test_eviction_removes_the_evicted_directory(sessions) -> None:
    """Otherwise the cap bounds the dict and not the disk — the leak stays."""
    for index in range(debug_store.MAX_SESSIONS):
        debug_store.put(sessions(f"s{index}"))

    debug_store.close_session.reset_mock()
    debug_store.put(sessions("fresh"))

    debug_store.close_session.assert_called_once()


# --------------------------------------------------------------------- ttl


def test_an_idle_session_expires(sessions) -> None:
    """The TTL is an *idle* timeout, so it is measured from the last touch."""
    session = debug_store.put(sessions("a"))

    with patch("app.debug_store.time") as clock:
        clock.monotonic.return_value = (
            time.monotonic() + debug_store.SESSION_TTL_SECONDS + 1
        )
        assert debug_store.get("a") is None

    assert session is not None  # the object exists; the store forgot it


def test_get_touches_the_entry_so_the_ttl_is_idle_time(sessions) -> None:
    """Reading a session must reset its clock, or a long read kills it.

    Debugging is reading, not clicking: a student staring at the variables
    panel for an hour is exactly the case a hard lifetime would break.
    """
    debug_store.put(sessions("a"))
    start = time.monotonic()

    with patch("app.debug_store.time") as clock:
        # Just over the TTL since creation, but `get` was called at start.
        clock.monotonic.return_value = start + debug_store.SESSION_TTL_SECONDS - 1
        assert debug_store.get("a") is not None

        # ... and half a TTL later it is still alive, because the `get`
        # above moved its clock forward.
        clock.monotonic.return_value = start + debug_store.SESSION_TTL_SECONDS + 10
        assert debug_store.get("a") is not None


def test_an_expired_session_is_cleaned_up_not_just_hidden(sessions) -> None:
    """Expiry must close the directory, same as eviction."""
    debug_store.put(sessions("a"))
    debug_store.close_session.reset_mock()

    with patch("app.debug_store.time") as clock:
        clock.monotonic.return_value = (
            time.monotonic() + debug_store.SESSION_TTL_SECONDS + 1
        )
        assert debug_store.get("a") is None

    debug_store.close_session.assert_called_once()
    assert debug_store.count() == 0


def test_the_sweep_runs_before_a_put_and_reclaims_expired_sessions(sessions) -> None:
    """Expiry is enforced without a background task.

    A store that only expired on ``get`` would hold every abandoned
    session's directory until somebody happened to read it — which, for
    an abandoned session, never happens.
    """
    debug_store.put(sessions("a"))

    with patch("app.debug_store.time") as clock:
        clock.monotonic.return_value = (
            time.monotonic() + debug_store.SESSION_TTL_SECONDS + 1
        )
        debug_store.put(sessions("b"))

    assert debug_store.get("a") is None
    assert debug_store.get("b") is not None
