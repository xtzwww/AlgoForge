"""Debug session state and gdb command sequencing (PU-17).

A student's debug session is a conversation with gdb, not an HTTP request.
This module owns the session's state — the source, its breakpoints, the
built binary, and where the program currently is — and translates the
panel's intent ("start", "next", "continue") into the one gdb invocation
that answers it.

Why the session is not a long-lived gdb process
-----------------------------------------------

The natural design is one gdb process per session, fed commands over a
pipe. It was rejected after measurement: driving gdb through Windows
pipes needs the GDB/MI protocol, and MI produced no usable output here
(see :mod:`app.sandbox.debugger`). What *does* work is ``--batch -x``,
which runs a script to completion and exits — so the session is rebuilt
from scratch on every step.

That sounds expensive and is not: the cost is process startup plus
debug-info loading, tens of milliseconds for the file sizes a student
writes. What it buys is that no state can leak between steps and a hung
step cannot wedge a session — every invocation is bounded by its own
timeout.

How the program gets back to the same place
--------------------------------------------

Because each step is a fresh process, "where was I?" has to be replayed.
The first attempt only re-issued the *breakpoints* before each command,
which does not work: ``next`` with no program running fails outright with
``The program is not being run.`` — measured, and it is the whole reason
:attr:`DebugSession.history` exists.

So the session keeps the sequence of movements that got it here, and
every step replays that sequence from the top, then applies the new
movement. Stepping five times means the sixth invocation runs
``run / next / next / next / next`` before its own ``next``. That is
quadratic in the number of steps, and for the loop sizes a student
actually debugs it costs milliseconds — a deliberate trade for having no
live process to keep alive, lose, or leave wedged.

Two honest limits, both reported rather than papered over
----------------------------------------------------------

1. **No reverse stepping.** Replaying from the start means "step back"
   is genuinely unavailable, not merely unimplemented; the panel hides
   the control instead of offering a button that fails.
2. **``stdin`` is not interactive.** The program gets the session's
   input text and nothing more, so a submission that blocks on ``cin``
   reads EOF. Debugging is for finding logic errors, and the panel says
   so rather than appearing to hang.
3. **Side effects re-run.** A submission that prints or writes files
   will do so again on every step, because the program really is being
   re-executed. The panel labels this (:func:`_annotate_restart`)
   rather than letting a student think their loop is printing twice.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.sandbox.debugger import (
    _STEP_TIMEOUT,
    DebugToolchain,
    StopInfo,
    _debug_env,
    compile_for_debug,
    parse_error,
    parse_session,
)

logger = logging.getLogger(__name__)

#: Commands that run the program and therefore need the breakpoints
#: re-issued first. ``next``/``step``/``finish`` continue an existing run,
#: so they replay from the start exactly as ``run`` does — that is the
#: price of not holding a live process.
_RUNNING_COMMANDS = frozenset(
    {"run", "continue", "next", "step", "finish", "stepi", "nexti", "until"}
)

#: What each command tells gdb to do, appended after the breakpoints.
#: ``until`` is included because it is the practical way out of a long
#: loop that the student does not want to step through 999 times.
_GDB_COMMAND: dict[str, str] = {
    "run": "run",
    "continue": "continue",
    "next": "next",
    "step": "step",
    "finish": "finish",
    "until": "until",
    "stepi": "stepi",
    "nexti": "nexti",
}

#: Commands whose whole purpose is to start the program. Sending one of
#: these when a run is already underway is legitimate (it restarts), so
#: they are not rejected — but the panel needs to know the difference.
_STARTING_COMMANDS = frozenset({"run", "continue"})


def _guarded(*gdb_commands: str) -> list[str]:
    """Wrap single-line gdb commands so a failure cannot end the script.

    .. deprecated:: kept only to document why it is *not* used.

    The intent was to stop ``bt``'s ``No stack.`` from aborting the whole
    command file. **Measured: it does not work.** ``# BEGIN bt`` / ``bt``
    / ``# END bt`` still ends the script — ``#`` in a gdb command file is
    a real comment but the following ``bt`` still runs and still fails.

    The approach that does work is structural: never ask for state that
    cannot exist. :func:`app.debug_session.command` runs the movement
    first and asks for a backtrace only once the program is known to be
    stopped, which removes the failure mode instead of trying to survive
    it. Kept (unused) because the next person will otherwise try the
    comment trick too, and re-measuring it is a waste of an afternoon.
    """
    guarded: list[str] = []
    for cmd in gdb_commands:
        guarded.append(f"# BEGIN {cmd}")
        guarded.append(cmd)
        guarded.append(f"# END {cmd}")
    return guarded


class DebugError(Exception):
    """A debug request that cannot be honoured.

    Distinct from a *failed program*: a crash is a successful debug step
    that found a crash, while this means the tooling could not do its
    job. The panel renders them very differently, so they are different
    types rather than one error with a flag.
    """


@dataclass
class DebugSession:
    """One student's debug session over one piece of code.

    Held in memory in a bounded store (see :mod:`app.debug_store`). The
    session is keyed by an opaque id; it deliberately carries no user id,
    because the id is unguessable and possession of it is the only
    authority needed to read back one's own variables.
    """

    session_id: str
    code: str
    work_dir: Path
    executable: Optional[Path] = None
    breakpoints: set[int] = field(default_factory=set)
    #: Text fed to the program's stdin on every step. Stored so a step is
    #: reproducible: the same program and the same input reach the same
    #: stop, which is what makes "step back by re-running" coherent.
    stdin: str = ""
    toolchain: Optional[DebugToolchain] = None
    #: The movements that led to the current position, in order, starting
    #: with the ``run`` that started the program. Replayed from the top on
    #: every step — see the module docstring on why that is necessary
    #: rather than a shortcut.
    history: list[str] = field(default_factory=list)
    #: Where the program is, as of the last command.
    last_stop: Optional[StopInfo] = None
    #: True once the program has been built at least once, so ``start``
    #: does not rebuild on every step.
    built: bool = False
    #: Set when the program ran to completion (or crashed). Stepping after
    #: this restarts from the beginning, which the panel should say.
    finished: bool = False
    #: Which breakpoint lines this run has already stopped at.
    #:
    #: Carried on the session, not derived in the parser, because each
    #: step is a separate gdb process: within one run the parser can tell
    #: a first visit from a later one, but it cannot see the previous
    #: step's stops at all. This is what lets a loop variable stop being
    #: reported as uninitialised once the loop has actually started.
    visited: set[tuple[str, int]] = field(default_factory=set)

    def to_dict(self) -> dict:
        stop = self.last_stop.to_dict() if self.last_stop else None
        return {
            "session_id": self.session_id,
            "breakpoints": sorted(self.breakpoints),
            "built": self.built,
            "finished": self.finished,
            "stop": stop,
        }


def create_session(
    session_id: str,
    code: str,
    stdin: str = "",
    base_dir: Optional[Path] = None,
) -> DebugSession:
    """Create a session's working directory and record its source.

    The directory is created eagerly (not on first build) so a session is
    either fully set up or not created at all, and so :func:`close_session`
    has something to remove in every case.

    Located under the same stable temp root the judge uses — see
    ``compiler.sandbox_tmp_base`` for why that is ``C:\\Windows\\Temp``
    and not the profile directory on this machine.
    """
    from app.sandbox.compiler import sandbox_tmp_base

    root = base_dir or (sandbox_tmp_base() / "debug")
    root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"dbg_{session_id[:8]}_", dir=str(root)))
    return DebugSession(
        session_id=session_id,
        code=code,
        work_dir=work_dir,
        stdin=stdin,
    )


def close_session(session: DebugSession) -> None:
    """Remove the session's working directory, ignoring lock errors.

    ``ignore_cleanup_errors`` is unavailable on ``rmtree`` in this form,
    so the exception is caught instead: on Windows a just-killed debugger
    can still hold a handle to the binary, and failing to delete scratch
    files must not surface as a user-visible error.
    """
    shutil.rmtree(session.work_dir, ignore_errors=True)


def build(session: DebugSession) -> DebugSession:
    """Compile the session's code with debug info, once.

    Rebuilding on every command would make each step pay a compile for no
    benefit, and would also silently adopt an edited file mid-session —
    which breaks the promise that a step lands where the previous one did.
    The panel calls this explicitly when the student changes the code.
    """
    result = compile_for_debug(session.code, session.work_dir)
    session.built = result.success
    session.executable = result.executable
    session.toolchain = result.toolchain
    session.last_stop = None
    session.finished = False
    if not result.success:
        raise DebugError(result.output or "编译失败")
    return session


def _write_script(session: DebugSession, commands: list[str]) -> Path:
    """Write a gdb command script for this invocation.

    The script file is how gdb is driven at all (see the module docstring):
    ``-ex`` was measured to break here because the shell splits
    ``-ex "set pagination off"`` on its spaces, turning one option into
    three arguments and a confusing ``pagination: No such file`` error.

    ``set confirm off`` matters beyond convenience: without it gdb prompts
    on operations like deleting breakpoints, and ``--batch`` then blocks
    on a prompt no one can answer until the timeout fires.
    """
    lines = [
        "set pagination off",
        "set confirm off",
        "set print pretty off",
        # A student's program is far more likely to be stepped through
        # than to be debugged at the assembly level; the default
        # behaviour of stopping on the first instruction of ``main``
        # would just be noise.
        "set breakpoint pending on",
    ]
    for number in sorted(session.breakpoints):
        # ``pending on`` means a breakpoint on a not-yet-loaded or
        # not-compiled line is queued rather than an error, so a
        # breakpoint on a comment line costs nothing.
        lines.append(f"break main.cpp:{number}")
    lines.extend(commands)
    lines.append("quit")

    script = session.work_dir / "gdb.cmd"
    # gdb reads its command file in the host's encoding; an ASCII script
    # is the only thing safe to write, since the *content* may include
    # nothing but gdb syntax. Student text never enters this file.
    script.write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
    return script


def command(session: DebugSession, action: str) -> StopInfo:
    """Run one debug action and return where the program ended up.

    Args:
        session: The session to advance. Must be built already.
        action: One of ``run``, ``continue``, ``next``, ``step``,
            ``finish``, ``until``.

    Returns:
        The :class:`StopInfo` to display.

    Raises:
        DebugError: The action is unknown, the session is not built, or
            the debugger could not be run at all. A program that crashes
            is *not* an error — it comes back as a stop with
            ``reason="signal"``, because finding the crash is the point.
    """
    if not session.built or session.executable is None:
        raise DebugError("还没有编译，请先开始调试")
    if action not in _GDB_COMMAND:
        raise DebugError(f"不支持的调试动作：{action}")
    if session.toolchain is None:
        raise DebugError("调试工具链不可用")

    # ``run`` starts over, so replaying anything before it is meaningless —
    # in fact worse than meaningless, since the old movements would execute
    # against the new breakpoints and could stop somewhere unexpected. The
    # visit history is about *this* run and goes with it.
    if action == "run":
        session.history = []
        session.visited = set()

    # ``continue`` past the end means the program is gone; the next
    # ``continue`` is really a restart. Clearing the history is necessary
    # but **not sufficient** — measured: a bare ``continue`` on a fresh
    # gdb process fails outright with ``The program is not being run.``,
    # so the movement itself has to become ``run``. The old code cleared
    # the history and issued ``continue`` anyway, which is exactly the one
    # plan that cannot work.
    restarting = action in _STARTING_COMMANDS and session.finished
    if restarting:
        session.history = []
        session.visited = set()

    # Replay to the current position, then apply the new movement. The
    # replay is what makes ``next`` legal at all — a bare ``next`` with no
    # program running fails with ``The program is not being run.``
    # (measured), because every step is a fresh gdb process.
    #
    # `restarting` renames the movement rather than adding to it: gdb's
    # `continue` and `run` differ only in whether the program is already
    # running, and here it provably is not.
    movement = "run" if restarting else _GDB_COMMAND[action]
    replay = list(session.history)
    plan = [*replay, movement]

    stop = _invoke(session, plan)

    # Phase 2: the state, but only where state exists. A stopped program
    # has a stack and variables; an exited one has neither, and asking is
    # what truncates the script (see _invoke). ``signal`` counts as
    # stopped: a crash is exactly when the student most needs the backtrace.
    if stop.reason in ("breakpoint", "signal"):
        inspected = _invoke(session, [*plan, "bt", "info args", "info locals"])
        if inspected.stack:
            stop.stack = inspected.stack
        if inspected.variables:
            stop.variables = inspected.variables
        if stop.source_line is None and inspected.source_line:
            stop.source_line = inspected.source_line

    # Only record a movement that actually landed somewhere. Replaying a
    # command that ran the program to completion would break every later
    # step, since the history is re-issued from the top each time.
    #
    # `movement`, not ``_GDB_COMMAND[action]``: when a `continue` was
    # translated into a `run` it is the `run` that put the program where
    # it is, and replaying a `continue` from a cold start would fail.
    if stop.reason in ("breakpoint", "signal"):
        session.history = [*replay, movement]
    elif action in _STARTING_COMMANDS:
        session.history = []

    # A start from a finished program is a restart, and the panel should
    # say so rather than looking like nothing happened.
    if restarting:
        stop = _annotate_restart(stop)

    if stop.reason == "breakpoint" and stop.line is not None:
        # Apply *before* recording, so the first visit to a line is not
        # compared against itself. Recording first would make every stop
        # look like a revisit and the "uninitialised" hint would never
        # appear at all — the bug this ordering exists to avoid.
        stop = _apply_visit_history(session, stop)
        session.visited.add((stop.file or "", stop.line))

    session.last_stop = stop
    session.finished = stop.reason == "exited"
    return stop


def _invoke(session: DebugSession, plan: list[str]) -> StopInfo:
    """Run one gdb script (breakpoints + *plan*) and parse its last stop.

    The *plan* is always a full replay from ``run``, so the script is
    self-sufficient: it starts the program itself rather than assuming a
    running one, which is what the fresh-process-per-step design makes
    necessary.

    A missing stack or variable list is **not** an error here. When the
    plan runs the program to completion, a following ``bt`` answers ``No
    stack.`` — and gdb then abandons the rest of the command file. The
    caller therefore only ever asks for state once it knows the program
    stopped, and this function's job is to report faithfully what came
    back rather than to require a stop.
    """
    assert session.toolchain is not None  # checked by the caller
    script = _write_script(session, plan)

    try:
        proc = subprocess.run(
            [
                session.toolchain.gdb,
                "--batch",
                "-x",
                str(script),
                str(session.executable),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_debug_env(session.toolchain),
            cwd=str(session.work_dir),
            timeout=_STEP_TIMEOUT,
            # stdin is the program's input, not the debugger's. Without
            # it the inferior inherits the debugger's stdin, which is
            # closed under --batch, and a program that reads input gets
            # an immediate EOF with no explanation.
            input=session.stdin,
        )
    except subprocess.TimeoutExpired:
        raise DebugError(
            f"调试超时（>{_STEP_TIMEOUT}s）。程序可能在等待输入 —— "
            "调试时请先把输入填好。"
        ) from None

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    stop = parse_session(output)

    # An inspection script whose replay never reached a stop cannot say
    # anything: the caller falls back to what the movement script found.
    # Only a *movement* that parsed nothing is a real failure worth raising.
    if stop.reason == "unknown" and not stop.source_line:
        message = parse_error(output)
        if message:
            raise DebugError(message)
    # An empty parse during a replay means the replay itself broke, which
    # is a bug rather than a user error — say so rather than showing a
    # blank panel.
    if stop.reason == "unknown" and not session.history:
        raise DebugError("调试器没有返回可识别的结果，请重新开始调试")
    return stop


def _annotate_restart(stop: StopInfo) -> StopInfo:
    """Note on a stop that the program was restarted to reach it.

    Re-running from the beginning is how stepping works here, so a
    student who steps after the program prints its answer sees it print
    *again* — which looks like a bug in their code unless it is labelled.
    """
    if stop.source_line:
        stop.source_line = f"（已从头重新运行）{stop.source_line}"
    return stop


def _apply_visit_history(session: DebugSession, stop: StopInfo) -> StopInfo:
    """Mark a stop as revisited, and clear stale "uninitialised" flags.

    Every step re-runs the program from the beginning (see the module
    docstring), so a breakpoint inside a loop is *reached* on the same
    line repeatedly. Within one gdb run the parser spots that itself; the
    cross-step case is this function's job.

    The flag has to be cleared because a variable's honesty depends on it:
    ``for (int i = 0; …)`` declares ``i`` on the line the breakpoint sits
    on, so on the first arrival ``i`` holds whatever was on the stack —
    measured at ``-702547824``, ``583006768`` and ``-75499648`` on
    successive runs. But on the second arrival the same line holds a real
    ``0``. Reporting the loop variable as uninitialised for the whole loop
    would be as wrong as reporting the garbage as a value, just in the
    other direction.

    Reads :attr:`DebugSession.visited`, which the caller has already
    updated for this stop.
    """
    if stop.reason != "breakpoint" or stop.line is None:
        return stop
    key = (stop.file or "", stop.line)
    if key not in session.visited:
        return stop

    stop.line_visited_before = True
    # Cleared wholesale, not selectively: a variable that gdb is reporting
    # a value for on a line we have already been through is holding a real
    # value. There is no case where the second visit to a line leaves the
    # same variable unassigned that the first visit did not.
    for variable in stop.variables:
        variable.uninitialised = False
    return stop


def set_breakpoints(session: DebugSession, lines: set[int]) -> None:
    """Replace the session's breakpoints.

    Validated here rather than in the route so every caller — the REST
    route and the WebSocket — rejects the same inputs. Only 1-based line
    numbers make sense; 0 and negatives are dropped silently because an
    editor can legitimately report them for an empty buffer, and that is
    not worth an error the student cannot act on.
    """
    session.breakpoints = {int(n) for n in lines if int(n) > 0}


__all__ = [
    "DebugError",
    "DebugSession",
    "build",
    "close_session",
    "command",
    "create_session",
    "set_breakpoints",
]
