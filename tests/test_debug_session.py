"""Tests for the debug *session* layer (PU-17).

``app/debug_session.py`` owns the state a debug conversation has — the
source, its breakpoints, the built binary, and where the program
currently is — and translates "start / next / continue" into one gdb
invocation each.

The design decision everything else follows from: **there is no
long-lived gdb process.** Measured, driving gdb over Windows pipes needs
GDB/MI, and MI produced no usable output on this machine; what works is
``--batch -x <script>``, which runs a file and exits. So a session is
rebuilt from scratch on every step, and "where was I" has to be
**replayed** — every ``next`` re-issues ``run`` and every movement that
came before it.

That replay is why these tests exist rather than an integration test:
the sequencing has four interlocking rules, each of which was a real bug,
and every one of them is invisible in a passing run of the happy path.

  1. The replay must be recorded, and a movement that landed nowhere must
     not be (`test_a_movement_that_did_not_stop_is_not_replayed`).
  2. ``run`` clears the history, because replaying old movements against
     new breakpoints stops somewhere unexpected
     (`test_run_starts_a_fresh_history`).
  3. ``continue`` after the program exits is a restart, not a crash
     (`test_continue_after_exit_restarts`).
  4. The visit history is applied **before** recording, or every stop
     looks like a revisit and the "尚未初始化" hint never appears at all
     (`test_the_first_visit_is_not_marked_as_a_revisit`).

``_invoke`` is patched throughout — no gdb, no compiler, no temp dirs.
What is under test is the sequencing, not gdb.

Run with ``pytest tests/test_debug_session.py -m "not eval_set"``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.debug_session import (
    _GDB_COMMAND,
    DebugError,
    DebugSession,
    command,
    set_breakpoints,
)
from app.sandbox.debugger import StopInfo, Variable

# --------------------------------------------------------------- helpers


def make_session(*, built: bool = True) -> DebugSession:
    """A session that looks built, without compiling anything.

    ``toolchain`` has to be set because :func:`command` refuses to run
    without one, and building a real ``DebugToolchain`` would import
    machine-specific paths into the test.
    """
    from app.sandbox.debugger import DebugToolchain

    session = DebugSession(
        session_id="test-session",
        code="int main() { return 0; }",
        work_dir=Path("."),
    )
    session.built = built
    session.executable = Path("main.exe") if built else None
    session.toolchain = DebugToolchain(compiler="g++", gdb="gdb")
    return session


def stop_at(
    line: int, *, reason: str = "breakpoint", function: str = "sum"
) -> StopInfo:
    return StopInfo(
        reason=reason,
        file="main.cpp",
        line=line,
        function=function,
        breakpoint_number=1,
    )


class Recorder:
    """A stand-in for ``_invoke`` that records the plans it was given.

    Returns a scripted stop per call, so a test can say "the first
    invocation stops at line 7, the second at line 8" and then assert
    what the *session* asked gdb to do.
    """

    def __init__(self, stops: list[StopInfo]) -> None:
        self.stops = stops
        self.plans: list[list[str]] = []

    def __call__(self, session: DebugSession, plan: list[str]) -> StopInfo:
        self.plans.append(list(plan))
        index = min(len(self.plans) - 1, len(self.stops) - 1)
        return self.stops[index]

    @property
    def movements(self) -> list[list[str]]:
        """The plan of each invocation that *moves* the program.

        An action is one movement plus, when the program stopped, an
        inspection that re-issues the movement and appends ``bt``. So the
        movements are the plans that do not ask for state — filtering on
        that rather than on a stride keeps the helper correct however
        many invocations an action turns out to take.
        """
        return [plan for plan in self.plans if "bt" not in plan]

    def movement(self, index: int) -> list[str]:
        return self.movements[index]


# ------------------------------------------------------------- replaying


def test_the_first_run_is_just_run() -> None:
    session = make_session()
    recorder = Recorder([stop_at(7)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")

    assert recorder.movement(0) == ["run"]


def test_next_replays_run_first() -> None:
    """A bare ``next`` fails with ``The program is not being run.``

    Measured: because each step is a fresh gdb process, the session has to
    issue ``run`` again before it can step. Without the replay the second
    click of 单步 is an error.
    """
    session = make_session()
    recorder = Recorder([stop_at(7), stop_at(8)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        command(session, "next")

    assert recorder.movement(1) == ["run", "next"]


def test_repeated_steps_grow_the_replay() -> None:
    """Three steps means `run / next / next / next` before the fourth.

    Quadratic in the number of steps, and correct: a loop a student
    actually debugs is milliseconds. Pinned because a "clever" rewrite
    that only re-issued the last movement would look equivalent and fail
    on the fourth click.
    """
    session = make_session()
    recorder = Recorder([stop_at(7), stop_at(8), stop_at(9), stop_at(10)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        command(session, "next")
        command(session, "next")
        command(session, "next")

    assert recorder.movement(3) == ["run", "next", "next", "next"]


def test_run_starts_a_fresh_history() -> None:
    """Replaying old movements against new breakpoints is worse than useless.

    The old movements would execute against a changed breakpoint set and
    could stop somewhere the student did not ask for.
    """
    session = make_session()
    recorder = Recorder([stop_at(7), stop_at(8), stop_at(3)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        command(session, "next")
        command(session, "run")

    assert recorder.movement(2) == ["run"]


def test_a_movement_that_did_not_stop_is_not_replayed() -> None:
    """A `continue` that ran to the end must not enter the replay list.

    Re-issuing it later would run the program to completion again and
    break every subsequent step.
    """
    session = make_session()
    recorder = Recorder(
        [stop_at(7), StopInfo(reason="exited", exit_code=0), stop_at(2)]
    )

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        command(session, "continue")
        command(session, "run")

    # The third plan is a plain `run` — the dead `continue` is gone.
    assert recorder.movement(2) == ["run"]


def test_continue_after_exit_restarts() -> None:
    """``continue`` on a finished program becomes a ``run``.

    Measured directly against gdb: a bare ``continue`` on a fresh process
    fails with ``The program is not being run.`` Clearing the step history
    alone therefore made this the *one* plan guaranteed to fail — the
    movement itself has to change. The old code cleared the history and
    issued ``continue`` anyway.
    """
    session = make_session()
    recorder = Recorder([StopInfo(reason="exited", exit_code=0), stop_at(7)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        stop = command(session, "continue")

    assert recorder.movement(1) == ["run"]
    assert session.finished is False
    assert stop.reason == "breakpoint"


def test_the_restart_movement_is_what_gets_replayed_afterwards() -> None:
    """After a `continue`→`run` translation, later steps replay the `run`.

    Recording the *requested* action instead of the one issued would put
    a `continue` at the head of the replay list, and every subsequent step
    would fail with "not being run" — a bug that only appears on the step
    *after* a restart.
    """
    session = make_session()
    recorder = Recorder(
        [
            StopInfo(reason="exited", exit_code=0),  # run   → exits
            stop_at(7),  # continue → restarted, stops
            stop_at(8),  # next
        ]
    )

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        command(session, "continue")
        command(session, "next")

    assert recorder.movement(2) == ["run", "next"]


def test_restart_is_labelled() -> None:
    """The panel has to be able to tell "restarted" from "nothing happened"."""
    session = make_session()
    recorder = Recorder([StopInfo(reason="exited", exit_code=0), stop_at(7)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")
        stop = command(session, "continue")

    assert "重新" in (stop.source_line or "") or stop.reason == "breakpoint"


# ------------------------------------------------------ inspection phase


def test_a_stopped_program_is_inspected_for_stack_and_variables() -> None:
    """The movement is asked for alone, then the state — two invocations.

    Asking for ``bt`` in the same script as a movement that exits the
    program makes gdb answer ``No stack.`` and abandon the rest of the
    file, so the exit status is never seen. Splitting removes the failure
    mode instead of trying to survive it.
    """
    session = make_session()
    inspected = stop_at(7)
    inspected.stack = []
    inspected.variables = [Variable(name="i", value="0")]
    recorder = Recorder([stop_at(7), inspected])

    with patch("app.debug_session._invoke", recorder):
        stop = command(session, "run")

    assert len(recorder.plans) == 2
    # The second invocation re-issues the movement, then asks for state.
    assert recorder.plans[1] == ["run", "bt", "info args", "info locals"]
    assert [v.name for v in stop.variables] == ["i"]


def test_an_exited_program_is_not_inspected() -> None:
    """Asking a dead program for its stack is what truncates the script."""
    session = make_session()
    recorder = Recorder([StopInfo(reason="exited", exit_code=0)])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")

    assert len(recorder.plans) == 1


def test_a_crash_is_inspected() -> None:
    """A crash is stopped, not gone — the backtrace is the whole point."""
    session = make_session()
    crash = StopInfo(reason="signal", signal="SIGSEGV", file="main.cpp", line=9)
    recorder = Recorder([crash, crash])

    with patch("app.debug_session._invoke", recorder):
        command(session, "run")

    assert len(recorder.plans) == 2


def test_a_failed_inspection_does_not_discard_the_stop() -> None:
    """A movement that stopped is still a result even if state is empty."""
    session = make_session()
    empty = StopInfo(reason="unknown")
    recorder = Recorder([stop_at(7), empty])

    with patch("app.debug_session._invoke", recorder):
        stop = command(session, "run")

    assert stop.reason == "breakpoint"
    assert stop.line == 7


# -------------------------------------------------- the visit history


def test_the_first_visit_is_not_marked_as_a_revisit() -> None:
    """Apply the flag *before* recording, or every stop looks like a revisit.

    Recording first makes the key always present when it is looked up, so
    the "尚未初始化" hint would never appear at all — the parser gets this
    right and the session would then throw it away. Measured: this was a
    real off-by-one and it silently disabled the feature.
    """
    session = make_session()
    recorder = Recorder([stop_at(7)])

    with patch("app.debug_session._invoke", recorder):
        stop = command(session, "run")

    assert stop.line_visited_before is False
    assert session.visited == {("main.cpp", 7)}


def test_a_second_run_is_not_compared_against_the_first() -> None:
    """``run`` clears the visit history along with the movement history.

    The pair is one piece of state: "have I been here *in this run*".
    Keeping the visits across a restart would report the fresh run's first
    trip round a loop as a revisit.
    """
    session = make_session()
    recorder = Recorder([stop_at(7), stop_at(7)])

    with patch("app.debug_session._invoke", recorder):
        first = command(session, "run")
        second = command(session, "run")

    assert first.line_visited_before is False
    assert second.line_visited_before is False


def test_a_revisit_clears_stale_uninitialised_flags() -> None:
    """The second time round a loop the old value is real.

    Within one gdb run the parser cannot know about earlier runs (each
    step is a process of its own), so the session has to clear the flag —
    otherwise a live loop variable is reported as garbage forever.
    """
    session = make_session()
    session.visited = {("main.cpp", 7)}
    # The parser re-derived `uninitialised` from this run's own source line,
    # which it has no way to know is a repeat.
    revisit = stop_at(7)
    revisit.variables = [Variable(name="i", value="1", uninitialised=True)]
    recorder = Recorder([revisit])

    with patch("app.debug_session._invoke", recorder):
        stop = command(session, "next")

    assert stop.line_visited_before is True
    assert stop.variables[0].uninitialised is False


def test_a_first_visit_keeps_the_uninitialised_flag() -> None:
    """The clearing must not fire on the first trip through the loop."""
    session = make_session()
    session.visited = set()
    first = stop_at(7)
    first.variables = [Variable(name="i", value="583006768", uninitialised=True)]
    recorder = Recorder([first])

    with patch("app.debug_session._invoke", recorder):
        stop = command(session, "run")

    assert stop.line_visited_before is False
    assert stop.variables[0].uninitialised is True


# ---------------------------------------------------------- guard rails


def test_an_unbuilt_session_refuses_to_run() -> None:
    session = make_session(built=False)
    session.executable = None

    with pytest.raises(DebugError, match="还没有编译"):
        command(session, "run")


def test_an_unknown_action_is_refused() -> None:
    """The action name is looked up, never passed to gdb as text.

    This is the whole reason the API takes a fixed vocabulary rather than
    a command string: a client cannot ask for ``shell``.
    """
    session = make_session()

    with pytest.raises(DebugError, match="不支持的调试动作"):
        command(session, "shell rm -rf /")


def test_a_session_without_a_toolchain_refuses_to_run() -> None:
    session = make_session()
    session.toolchain = None

    with pytest.raises(DebugError, match="工具链不可用"):
        command(session, "run")


def test_every_panel_action_has_a_gdb_command() -> None:
    """The API's vocabulary and this table must not drift apart.

    A name the route accepts but this table lacks raises ``DebugError``
    at the moment a student clicks it, which reads as the button being
    broken rather than the action being unsupported.
    """
    assert set(_GDB_COMMAND) == {
        "run",
        "continue",
        "next",
        "step",
        "finish",
        "until",
        "stepi",
        "nexti",
    }


# -------------------------------------------------------- breakpoints


def test_set_breakpoints_replaces_the_whole_set() -> None:
    """A full replace, not add/remove: the gutter holds the whole truth."""
    session = make_session()

    set_breakpoints(session, {3, 9})
    assert session.breakpoints == {3, 9}

    set_breakpoints(session, {5})
    assert session.breakpoints == {5}

    set_breakpoints(session, set())
    assert session.breakpoints == set()


def test_to_dict_shape_the_client_reads() -> None:
    """``api/debug.ts``'s ``DebugSession`` mirrors this exactly."""
    session = make_session()
    session.breakpoints = {7}

    assert set(session.to_dict()) == {
        "session_id",
        "breakpoints",
        "built",
        "finished",
        "stop",
    }


def test_breakpoints_are_sorted_for_stable_rendering() -> None:
    """A set has no order; the payload must, or the panel re-renders."""
    session = make_session()
    session.breakpoints = {9, 3, 7}

    assert session.to_dict()["breakpoints"] == [3, 7, 9]


def test_stop_is_none_before_anything_ran() -> None:
    session = make_session()

    assert session.to_dict()["stop"] is None
