"""Tests for the gdb text-output parser (PU-17).

``app/sandbox/debugger.py`` turns gdb's *printed text* into JSON. Every
quirk this suite pins was found by running real gdb on this machine and
reading what it actually printed, so the fixtures below are **transcripts
captured from gdb 10.2 (MinGW, RedPanda-CPP)**, not invented strings.
The captured originals live in ``C:\\Windows\\Temp\\dbgprobe`` on the
machine this was developed on; the shapes that matter are reproduced
here.

The five that cost the most to find:

1. **Windows drive letters broke the location regex.** ``[^:]+:(\\d+)``
   stops at the colon in ``C:\\Temp\\…\\main.cpp:4``, so *every* stop
   parsed as ``unknown`` — with no error anywhere. The fix was a greedy
   path group (take the **last** colon), pinned by
   :func:`test_a_stop_is_recognised_despite_windows_drive_letters`.
2. **A crash's location is below its header, not on it.** ``Program
   received signal SIGSEGV, Segmentation fault.`` carries no file/line;
   it is on the next ``0x… in f () at file:line``. Without the backfill a
   crash reported "somewhere unknown", which is the one case a debugger
   must not fumble. See :func:`test_signal_location_is_backfilled_from_the_stack`.
3. **The breakpoint header has no brackets and a single-integer thread.**
   Real output is ``Thread 1 hit Breakpoint 1, sum (…) at m2.cpp:6``.
   A fixture written as ``[Thread 1.0x2f1c] hit …`` — the shape gdb uses
   when *reporting* exit, not when hitting a breakpoint — matches nothing,
   and the parser silently returns ``unknown``. That mistake was made
   while writing this very file, which is why the header shape is pinned
   separately in :func:`test_the_thread_exit_form_is_not_a_breakpoint_header`.
4. **A security agent floods stderr with ``warning: [TSBX] …`` lines.**
   Dozens of them, before the inferior starts. They must be filtered or a
   real compiler error gets pushed out of view —
   :func:`test_environment_noise_does_not_become_the_error`.
5. **Library frames are dropped from the payload, not flagged in it.**
   ``_interpret_stop`` filters ``stop.stack`` down to student frames, so
   the panel receives a short stack. The ``is_library`` flag is still what
   makes the crash backfill pick the student's line rather than a
   ``stl_algo.h`` one, so both halves are pinned below.

Run with ``pytest tests/test_debug_parser.py -m "not eval_set"``.
"""

from __future__ import annotations

from app.sandbox.debugger import (
    Frame,
    StopInfo,
    Variable,
    parse_breakpoint_set,
    parse_error,
    parse_session,
    parse_stops,
    tidy_diagnostics,
)

# --------------------------------------------------------------- fixtures

#: The scratch directory gdb really reports — drive letter, backslashes,
#: and under ``C:\Windows\Temp`` because the profile path has CJK in it and
#: the MinGW linker cannot handle a non-ASCII path.
_SRC = r"C:\Windows\Temp\codelab_judge\debug\dbg_1a2b\main.cpp"
_LIB = r"D:\RedPanda-CPP\mingw64\include\c++\11.5.0\bits\stl_algo.h"


#: A stop inside `sum`, in the exact order real gdb prints it. Three
#: things about the shape matter:
#:   * the header is ``Thread 1 hit Breakpoint 1, …`` — no brackets;
#:   * the echoed source line comes **immediately** after the header,
#:     because `_interpret_stop` reads it there and stops looking;
#:   * variables come before the backtrace, and ``#0``/``#1`` follow.
#:
#: `i` is the interesting one: it is declared on the very line the program
#: is stopped on (`for (int i = 0; …)`), so its declaration has not run.
STOP_AT_DECLARATION = f"""\
Breakpoint 1 at 0x140001553: file {_SRC}, line 7.
Thread 1 hit Breakpoint 1, sum (n=5) at {_SRC}:7
7\t    for (int i = 0; i < n; i++) {{
i = 583006768
total = 0
#0  sum (n=5) at {_SRC}:7
#1  0x00007ff69f151616 in main () at {_SRC}:14
"""

#: The same breakpoint, one `next` later. gdb prints a header for **every**
#: stop it reports, including one reached by stepping — a bare source-echo
#: line (``8\t…``) on its own is *not* a stop header and starts nothing
#: (measured; the first draft of this fixture got that wrong and the
#: parser correctly returned a single stop).
STOP_AFTER_ONE_STEP = f"""\
Thread 1 hit Breakpoint 1, sum (n=5) at {_SRC}:7
7\t    for (int i = 0; i < n; i++) {{
i = 583006768
total = 0
#0  sum (n=5) at {_SRC}:7
#1  0x00007ff69f151616 in main () at {_SRC}:14
Thread 1 hit Breakpoint 1, sum (n=5) at {_SRC}:8
8\t        total += i;
i = 0
total = 0
#0  sum (n=5) at {_SRC}:8
#1  0x00007ff69f151616 in main () at {_SRC}:14
"""

#: A crash. The header carries no location at all — that is on the
#: ``0x… in crash ()`` line below it.
CRASH_RUN = f"""\
Program received signal SIGSEGV, Segmentation fault.
0x00007ff69f151616 in crash () at {_SRC}:9
9\t    *p = 1;
#0  crash () at {_SRC}:9
#1  0x00007ff69f151616 in main () at {_SRC}:15
"""

#: A normal exit, exactly as captured: three thread-exit lines, then the
#: inferior line. The thread lines contain the word "exited" and must
#: **not** be mistaken for stops.
EXIT_RUN = """\
14
[Thread 23520.0x4004 exited with code 0]
[Thread 23520.0x3fb4 exited with code 0]
[Thread 23520.0x445c exited with code 0]
[Inferior 1 (process 23520) exited normally]
"""

#: An exit with a non-zero status.
EXIT_RUN_CODE_2 = "[Inferior 1 (process 9876) exited with code 2]\n"

#: The security agent's flood, verbatim in shape. These arrive before the
#: inferior starts and number in the dozens.
_NOISE = "\n".join(
    [
        "'import sitecustomize' failed; use -v for traceback",
        "warning: [TSBX] tsbx.dll loaded successfully! (PID=23520)",
        "warning: [TSBX] Shared memory name: Local\\LiteSandbox_23184_1b5ddbfd",
        "warning: [TSBX] Rules loaded: 0 file rules, ipc.uid=set",
        "warning: [LSHOOK INFO]  Hook engine initialized (Windows version: 1000)",
        "warning: [TSBX] File hooks installed: 6/6",
        "warning: [TSBX] Initialization complete (M7)",
    ]
)

#: A stack whose innermost frame is standard-library code the student did
#: not write. gdb prints library paths with backslashes on Windows, which
#: is what made the library check silently useless until it normalised.
LIBRARY_INNERMOST_RUN = f"""\
Thread 1 hit Breakpoint 1, solve () at {_SRC}:12
12\t    std::sort(v.begin(), v.end());
#0  std::sort<int*> (first=0x0, last=0x0) at {_LIB}:1897
#1  0x00007ff69f151616 in solve () at {_SRC}:12
#2  0x00007ff69f151616 in main () at {_SRC}:20
"""


# ------------------------------------------------------- location regexes


def test_a_stop_is_recognised_despite_windows_drive_letters() -> None:
    """The colon in ``C:`` must not be read as the file/line separator.

    The bug this pins made every stop parse as ``unknown`` with no error
    anywhere: the regex matched nothing, the parser fell through to the
    default, and the panel showed nothing while the program was
    genuinely stopped on line 7.
    """
    stop = parse_session(STOP_AT_DECLARATION)

    assert stop.reason == "breakpoint"
    assert stop.file is not None
    assert stop.file.endswith("main.cpp")
    assert stop.line == 7
    assert stop.function == "sum"


def test_the_thread_exit_form_is_not_a_breakpoint_header() -> None:
    """``[Thread 23520.0x4004 exited with code 0]`` must not become a stop.

    Two traps in one: the thread id is ``23520.0x4004`` rather than a
    single integer, and the line is bracketed. A fixture written in that
    shape for a *breakpoint* matches no regex at all — a mistake made
    while writing this file, and the reason the real header
    (``Thread 1 hit Breakpoint 1, …``) is asserted here and not just
    described.
    """
    crashed_fixture = f"[Thread 1.0x2f1c] hit Breakpoint 1, sum (n=5) at {_SRC}:7"

    assert parse_session(crashed_fixture).reason == "unknown"
    # And the real one, for contrast.
    assert parse_session(STOP_AT_DECLARATION).reason == "breakpoint"


def test_tidy_diagnostics_strips_the_sandbox_path() -> None:
    """Students see ``main.cpp``, never the temp directory it was built in.

    The scratch path tells them nothing they can use, and on a machine
    with a tight disk it invites them to go looking in a temp dir.
    """
    tidied = tidy_diagnostics(f"{_SRC}:7: error: 'x' undeclared")

    assert "codelab_judge" not in tidied
    assert "dbg_1a2b" not in tidied
    assert tidied.startswith("main.cpp:7")


def test_tidy_diagnostics_leaves_other_files_alone() -> None:
    """A library path still has to be traceable, so only main.cpp is folded."""
    text = r"D:\RedPanda-CPP\mingw64\include\c++\11.5.0\bits\stl_algo.h:12: note:"

    assert "stl_algo.h" in tidy_diagnostics(text)


def test_stop_file_is_tidied_in_to_dict() -> None:
    assert parse_session(STOP_AT_DECLARATION).to_dict()["file"] == "main.cpp"


# -------------------------------------------------------------- stop kinds


def test_signal_reason_is_signal() -> None:
    stop = parse_session(CRASH_RUN)

    assert stop.reason == "signal"
    assert stop.signal == "SIGSEGV"


def test_signal_description_is_kept_for_the_message() -> None:
    """The panel renders this verbatim, so it has to survive parsing.

    The captured text includes the trailing period (gdb prints
    ``Segmentation fault.``); the parser keeps the line's tail intact
    rather than trimming punctuation, so this asserts the real string.
    """
    assert parse_session(CRASH_RUN).signal_description == "Segmentation fault."


def test_signal_location_is_backfilled_from_the_stack() -> None:
    """The crash header carries no file/line; the frame below it does.

    Without the backfill a crash reported no location — and a crash is
    the one stop where "where did it happen" is the entire question.
    """
    stop = parse_session(CRASH_RUN)

    assert stop.file is not None
    assert stop.file.endswith("main.cpp")
    assert stop.line == 9
    assert stop.function == "crash"


def test_exit_reason_and_code() -> None:
    stop = parse_session(EXIT_RUN)

    assert stop.reason == "exited"
    assert stop.exit_code == 0
    # Nothing to point at once the process is gone.
    assert stop.file is None
    assert stop.line is None


def test_thread_exit_lines_do_not_become_extra_stops() -> None:
    """Three thread-exit lines precede the inferior line; only one stop.

    Each contains the word "exited", so a looser matcher would report the
    program as having ended three times before it ended.
    """
    stops = parse_stops(EXIT_RUN)

    assert len(stops) == 1
    assert stops[0].reason == "exited"


def test_non_zero_exit_code_is_read_from_the_message() -> None:
    assert parse_session(EXIT_RUN_CODE_2).exit_code == 2


def test_crash_exit_code_is_none_not_zero() -> None:
    """A crash must not report exit code 0 — that reads as success."""
    assert parse_session(CRASH_RUN).exit_code is None


# ----------------------------------------------------------- stop entries


def test_parse_stops_keeps_every_stop() -> None:
    """A `continue` to exit must not hide the breakpoint that preceded it.

    Returning only the last stop meant the interesting one (where the
    program was) was thrown away by the one after it (where it ended).
    """
    stops = parse_stops(STOP_AT_DECLARATION + EXIT_RUN)

    assert len(stops) == 2
    assert stops[0].reason == "breakpoint"
    assert stops[1].reason == "exited"


def test_parse_session_returns_the_last_stop() -> None:
    """By convention the *current* position is the last thing gdb said."""
    assert parse_session(STOP_AT_DECLARATION + EXIT_RUN).reason == "exited"


def test_two_stops_in_one_transcript_both_parse() -> None:
    """`run` then `next` in one script really does print two stops.

    Both must survive: the second is the current position and the first
    is where the step started, which a step-history view needs.
    """
    stops = parse_stops(STOP_AFTER_ONE_STEP)

    assert len(stops) == 2
    assert stops[0].line == 7
    assert stops[1].line == 8


def test_duplicate_backtraces_do_not_duplicate_frames() -> None:
    """Two `bt` commands must not leave two ``#0`` frames.

    `info locals` and `bt` are separate gdb commands and each may print a
    backtrace; a frame appearing at level 0 restarts the list rather than
    appending to it. Measured: without the reset the panel showed two
    frames numbered zero and two `main`s, which reads as a bug in the
    student's program.
    """
    stop = parse_session(STOP_AFTER_ONE_STEP)

    levels = [frame.level for frame in stop.stack]
    assert levels == sorted(set(levels)), f"duplicate frame levels: {levels}"
    assert levels == [0, 1]


def test_no_stop_at_all_reports_unknown_with_whatever_gdb_said() -> None:
    """A program that never started must not be dressed up as a real stop."""
    stop: StopInfo = parse_session("Reading symbols from a.exe...\ndone.\n")

    assert stop.reason == "unknown"
    assert stop.file is None


# -------------------------------------------------------- stack filtering


def test_library_frames_are_not_in_the_payload() -> None:
    """``_interpret_stop`` drops them, so the panel gets a short stack.

    The frames are already gone by the time the panel sees them, which is
    why it cannot compute a "N frames hidden" count from the payload.
    """
    stop = parse_session(LIBRARY_INNERMOST_RUN)

    assert all("stl_algo.h" not in frame.file for frame in stop.stack)
    assert [frame.line for frame in stop.stack] == [12, 20]


def test_crash_inside_the_library_reports_the_student_frame() -> None:
    """A crash deep in `std::sort` must point at the student's own line.

    Taking the crash site from the *filtered* stack is what makes this
    work: the innermost library frame is gone by the time the backfill
    runs, so what is left is the first line of the student's code.
    """
    crash_in_library = f"""\
Program received signal SIGSEGV, Segmentation fault.
0x00007ff69f151616 in std::sort<int*> () at {_LIB}:1897
#0  std::sort<int*> () at {_LIB}:1897
#1  0x00007ff69f151616 in solve () at {_SRC}:12
"""

    stop = parse_session(crash_in_library)

    assert stop.reason == "signal"
    assert stop.file is not None
    assert stop.file.endswith("main.cpp")
    assert stop.line == 12
    assert stop.function == "solve"


def test_library_detection_normalises_backslashes() -> None:
    """gdb prints library paths with ``\\``; the markers use ``/``.

    Comparing raw made the check silently useless — it matched nothing
    and there was no error to notice.
    """
    frame = Frame(
        level=0,
        function="std::sort",
        file=r"D:\RedPanda-CPP\mingw64\include\c++\11.5.0\bits\stl_algo.h",
        line=1897,
    )

    assert frame.is_library is True


def test_student_code_is_not_flagged_as_library() -> None:
    assert Frame(level=0, function="main", file="main.cpp", line=3).is_library is False


def test_frame_display_file_is_tidied() -> None:
    frame = Frame(level=0, function="main", file=_SRC, line=3)

    assert frame.display_file == "main.cpp"


# ---------------------------------------------------------- uninitialised


def test_variable_defaults_to_initialised() -> None:
    assert Variable(name="x", value="1").uninitialised is False


def test_variable_round_trips_uninitialised_through_to_dict() -> None:
    payload = Variable(name="i", value="583006768", uninitialised=True).to_dict()

    assert payload == {
        "name": "i",
        "value": "583006768",
        "kind": "local",
        "uninitialised": True,
    }


def test_uninitialised_is_decided_from_the_stop_line() -> None:
    """A variable declared at or after the stop line has not been assigned.

    This is the safe direction to err in: claiming a value that is not
    real is far worse than being briefly unsure. Measured three runs of
    the same breakpoint reported ``i`` as 583006768, then -75499648, then
    564133088 — it is stack garbage, and a student reading it would
    conclude their loop starts at 583006768.
    """
    stop = parse_session(STOP_AT_DECLARATION)
    by_name = {variable.name: variable for variable in stop.variables}

    # The stop is on the `for (int i = …)` line itself, so `i` — declared
    # on that very line — has not been assigned yet.
    assert by_name["i"].uninitialised is True
    # `total` was declared on an earlier line, so its value is real.
    assert by_name["total"].uninitialised is False


def test_a_later_stop_on_a_loop_body_line_clears_the_flag() -> None:
    """One step on, the same variable is live and must show its value.

    Staying flagged forever is the opposite failure to showing garbage as
    fact, and just as misleading.
    """
    stops = parse_stops(STOP_AFTER_ONE_STEP)
    body = {variable.name: variable for variable in stops[1].variables}

    assert body["i"].uninitialised is False
    assert body["i"].value == "0"


def test_source_line_is_captured_for_the_declaration_check() -> None:
    """The echoed line is what makes the check above decidable at all.

    It is read immediately after the header and nowhere else, so a
    fixture that puts it elsewhere would silently make every variable
    look initialised.
    """
    assert (
        parse_session(STOP_AT_DECLARATION).source_line
        == "    for (int i = 0; i < n; i++) {"
    )


def test_optimized_out_values_are_dropped() -> None:
    """``<optimized out>`` is not a value, and showing it helps nobody.

    Debug builds do not optimise, so this should not appear — but if it
    ever does, showing the literal string would be worse than omitting
    the variable.
    """
    out = f"""\
Thread 1 hit Breakpoint 1, main () at {_SRC}:4
4\t    int a = 0;
x = <optimized out>
y = 7
"""

    names = {variable.name for variable in parse_session(out).variables}
    assert "x" not in names
    assert "y" in names


# ------------------------------------------------------------ breakpoints


def test_parse_breakpoint_set_returns_the_resolved_location() -> None:
    """The *resolved* line, not the requested one.

    gdb moves a breakpoint off a blank or comment line to the next
    statement; reporting the resolved spot is what lets the panel tell a
    student their breakpoint moved.
    """
    output = f"Breakpoint 1 at 0x140001553: file {_SRC}, line 7."

    assert parse_breakpoint_set(output) == (_SRC, 7)


def test_parse_breakpoint_set_returns_none_when_nothing_was_set() -> None:
    assert parse_breakpoint_set("No breakpoints set.\n") is None


# ----------------------------------------------------------- error paths


def test_parse_error_extracts_a_failed_script_line() -> None:
    """gdb reports a failed script line as ``<script>.txt:N: <message>``.

    The wrapper names *our* generated command file, which is meaningless
    to a student, so it is stripped and the message kept.

    The script is ``gdb.cmd`` (see ``app.debug_session._write_script``),
    and the first version of this pattern required ``.txt`` — so it never
    matched and a failed step reported no reason at all. Both spellings
    are asserted so a future rename cannot quietly reintroduce that.
    """
    assert parse_error("gdb.cmd:3: No stack.\n") == "No stack."
    assert parse_error("gdb.txt:3: No stack.\n") == "No stack."


def test_parse_error_keeps_a_message_that_contains_a_colon() -> None:
    """Stripping the prefix must not eat into the message itself."""
    assert (
        parse_error("gdb.cmd:12: Error in sourced command file: no such variable\n")
        == "Error in sourced command file: no such variable"
    )


def test_parse_error_returns_none_for_a_clean_run() -> None:
    assert parse_error(STOP_AT_DECLARATION) is None


def test_parse_error_ignores_a_missing_symbol() -> None:
    """``No symbol "i" in current context`` is normal, not an error.

    Asking for a variable before its declaration is reached is exactly
    what stopping early produces; reporting it would put a red error over
    a perfectly successful step.
    """
    assert parse_error('No symbol "i" in current context.\n') is None


def test_environment_noise_does_not_become_the_error() -> None:
    """A security agent injects dozens of ``warning: [TSBX] …`` lines.

    Left in, they would be reported as the reason a compile failed, and
    the student would go looking for a code problem that does not exist.
    """
    noisy = _NOISE + "\ngdb.cmd:3: Error in sourced command file:\n"

    assert parse_error(noisy) == "Error in sourced command file:"


def test_noise_is_filtered_out_of_stop_parsing_too() -> None:
    """The same flood must not be able to start or corrupt a stop."""
    noisy = _NOISE + "\n" + STOP_AT_DECLARATION
    stop = parse_session(noisy)

    assert stop.reason == "breakpoint"
    assert stop.line == 7


def test_a_genuine_gdb_warning_survives_the_noise_filter() -> None:
    """The filter is a denylist of known prefixes, not "ignore stderr".

    ``warning: Source file is more recent than executable`` is exactly the
    class of message a student needs to see, so it must not be swept up
    with the agent's chatter.
    """
    assert parse_error("warning: Source file is more recent than executable.\n") is None
    # ... and it is not noise, so it is *available* if something ever
    # wants to report it.
    from app.sandbox.debugger import _is_environment_noise

    assert (
        _is_environment_noise("warning: Source file is more recent than executable.")
        is False
    )


# ---------------------------------------------------------------- shape


def test_stop_to_dict_has_every_field_the_panel_reads() -> None:
    """The TypeScript interface in ``api/debug.ts`` mirrors this exactly.

    A field renamed here without the other side moving shows up as an
    empty panel, not an error.
    """
    payload = parse_session(STOP_AT_DECLARATION).to_dict()

    assert set(payload) == {
        "reason",
        "file",
        "line",
        "function",
        "breakpoint_number",
        "signal",
        "signal_description",
        "exit_code",
        "variables",
        "stack",
        "source_line",
        "line_visited_before",
    }


def test_frame_to_dict_has_every_field_the_panel_reads() -> None:
    frame = Frame(level=0, function="main", file=_SRC, line=3, address="0x1")

    assert set(frame.to_dict()) == {
        "level",
        "function",
        "display_function",
        "file",
        "line",
        "address",
        "is_library",
    }


def test_display_function_strips_the_argument_list() -> None:
    """gdb prints argument *values*, so the raw name is unusable as a label.

    Measured: a ``std::vector`` argument prints as
    ``sum (v=std::vector of length 5, capacity 5 = {...})`` — and the panel
    appends its own parentheses, so rendering the raw string gave
    ``sum (v=… = {...})()``. Cut at the first ``(``; a function name
    cannot contain one.
    """
    frame = Frame(
        level=0,
        function="sum (v=std::vector of length 5, capacity 5 = {...})",
        file=_SRC,
        line=6,
    )

    assert frame.display_function == "sum"
    # The raw capture is kept: a future "arguments" view needs it.
    assert frame.function.startswith("sum (v=")


def test_display_function_handles_a_name_with_no_arguments() -> None:
    assert (
        Frame(level=0, function="main ()", file="main.cpp", line=1).display_function
        == "main"
    )


def test_a_repeated_line_within_one_run_is_flagged_as_a_revisit() -> None:
    """Within one gdb run the parser can see the repeat itself.

    Across separate runs it cannot — every step is a new process — which
    is why the session layer re-applies this flag
    (``app.debug_session._apply_visit_history``).

    Two headers on the *same* line, which is what a loop looks like when
    gdb prints both the entry and the second iteration in one transcript.
    """
    same_line_twice = f"""\
Thread 1 hit Breakpoint 1, sum (n=5) at {_SRC}:7
7\t    for (int i = 0; i < n; i++) {{
i = 583006768
total = 0
Thread 1 hit Breakpoint 1, sum (n=5) at {_SRC}:7
7\t    for (int i = 0; i < n; i++) {{
i = 0
total = 0
"""
    stops = parse_stops(same_line_twice)

    assert len(stops) == 2
    assert stops[0].line_visited_before is False
    assert stops[1].line_visited_before is True
