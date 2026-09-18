"""Tests for the edit-behaviour trace and the features derived from it (C1).

What is under test is the claim the project's title makes. The system
promises learning-**process** analysis, and this is the only place where
the process — as opposed to its result — is turned into data. If these
tests pass and the feature set is wrong, the title is wrong.

The two cases that motivate the whole module are pinned first:

* paste a body of code and submit it unchanged → flagged;
* paste the same body and rewrite much of it → **not** flagged.

The second is the one that matters. A signal that fires on "pasted a lot"
is not a learning-process signal at all, it is a "used a clipboard"
detector, and it would flag the student the feature exists to help.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.submission import Submission
from app.process_trace import (
    HIGH_PASTE_RATIO,
    LOW_REWRITE_RATIO,
    MIN_PASTED_CHARS,
    EditEvent,
    ProcessTrace,
    describe,
    from_trace,
    parse_process_trace,
)


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with the schema created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _trace(*events: EditEvent) -> ProcessTrace:
    return ProcessTrace(events=list(events))


def _paste(chars: int, at: int = 0, length: int | None = None) -> EditEvent:
    """A paste event; document length defaults to the pasted size."""
    return EditEvent(t=at, kind="paste", chars=chars, length=length or chars)


# --- The two motivating cases ------------------------------------------


def test_paste_and_submit_unchanged_is_flagged() -> None:
    """Paste a whole solution, change nothing, submit → flagged.

    This is the pattern that cannot be seen in the submitted text: the
    code is indistinguishable from hand-written code, and only the
    process says otherwise.
    """
    features = from_trace(
        _trace(_paste(1200, at=5_000, length=1200)), code="x" * 1200
    )
    signal = describe(features)

    assert signal.flagged is True
    assert features.paste_ratio == pytest.approx(1.0)
    assert features.rewrite_ratio == 0.0
    # The flag has to be explainable, not just boolean.
    assert signal.reasons
    assert signal.summary


def test_paste_then_rewrite_is_not_flagged() -> None:
    """Paste a solution and rewrite much of it → not flagged.

    The one that matters. Rewriting pasted code is what adapting a
    reference solution looks like, and flagging it would punish exactly
    the behaviour the system wants. Both students pasted 1,200
    characters; only the ratio separates them.
    """
    trace = _trace(
        _paste(1200, at=5_000, length=1200),
        EditEvent(t=60_000, kind="type", chars=400, length=1300),
        EditEvent(t=90_000, kind="delete", chars=100, length=1200),
        EditEvent(t=120_000, kind="run", length=1200),
    )
    features = from_trace(trace, code="x" * 1200)
    signal = describe(features)

    assert signal.flagged is False
    assert features.rewrite_ratio is not None
    assert features.rewrite_ratio > LOW_REWRITE_RATIO
    assert features.post_paste_edits == 2
    assert features.runs_before_submit == 1
    # The evidence still describes the paste — "not flagged" must not mean
    # "nothing to see".
    assert any("粘贴" in reason for reason in signal.reasons)


# --- Thresholds ---------------------------------------------------------


def test_a_small_paste_is_not_flagged() -> None:
    """A snippet-sized paste is not a body of work.

    Students paste single lines and small blocks constantly. Flagging
    those would make the flag meaningless, so there is a floor.
    """
    small = MIN_PASTED_CHARS - 1
    features = from_trace(
        _trace(_paste(small, length=small)), code="x" * small
    )
    signal = describe(features)

    assert features.paste_ratio == pytest.approx(1.0)
    assert features.rewrite_ratio == 0.0
    # Both conditions the main rule checks hold — only the floor stops it.
    assert signal.flagged is False


def test_paste_inside_hand_written_code_is_not_flagged() -> None:
    """A paste that is a minority of the code is not flagged.

    Pasting an algorithm into scaffolding the student wrote is an
    ordinary way to work, and the ratio is what distinguishes it from
    submitting someone else's whole file.
    """
    # 300 pasted into 1,500 total: 20%, well under the threshold.
    features = from_trace(
        _trace(
            EditEvent(t=1_000, kind="type", chars=1200, length=1200),
            EditEvent(t=5_000, kind="paste", chars=300, length=1500),
        ),
        code="x" * 1500,
    )
    signal = describe(features)

    assert features.paste_ratio < HIGH_PASTE_RATIO
    assert signal.flagged is False


def test_no_paste_at_all_is_not_flagged() -> None:
    """Hand-typed code has no process signal to raise."""
    features = from_trace(
        _trace(
            EditEvent(t=0, kind="open", length=0),
            EditEvent(t=1_000, kind="type", chars=50, length=50),
            EditEvent(t=2_000, kind="type", chars=50, length=100),
            EditEvent(t=3_000, kind="run", length=100),
        ),
        code="x" * 100,
    )
    signal = describe(features)

    assert signal.flagged is False
    assert features.paste_count == 0
    assert features.edit_count == 2
    assert signal.summary


def test_rewrite_ratio_is_none_without_a_paste() -> None:
    """A rewrite ratio over zero pasted characters is undefined, not zero.

    ``0.0`` would assert "none of the pasted text was rewritten" about a
    paste that never happened, and would then fail the main rule's
    "rewrote nothing" half — flagging a student who never pasted.
    """
    features = from_trace(
        _trace(EditEvent(t=0, kind="type", chars=100, length=100)), code="x" * 100
    )
    assert features.rewrite_ratio is None


# --- Absent traces ------------------------------------------------------


def test_a_missing_trace_yields_empty_features() -> None:
    """No trace means an empty feature set, never a ``None``.

    Callers would otherwise need a branch at every use. "Empty" is then
    reported by ``describe`` as *unobserved*, which is the honest answer.
    """
    features = from_trace(None, code="int main(){}")
    assert features.edit_count == 0
    assert features.paste_count == 0
    assert features.final_length == len("int main(){}")


def test_an_unobserved_process_is_worded_differently_from_a_clean_one() -> None:
    """'No data' and 'looked fine' are different statements.

    Collapsing them would make every submission made outside the editor
    — the REST route, an older client — look like a clean one, which is
    a claim the system has no evidence for.
    """
    unobserved = describe(from_trace(None, code="x" * 10))
    observed_clean = describe(
        from_trace(
            _trace(EditEvent(t=0, kind="type", chars=10, length=10)), code="x" * 10
        )
    )

    assert unobserved.flagged is False
    assert observed_clean.flagged is False
    assert unobserved.summary != observed_clean.summary
    assert "没有记录" in unobserved.summary


def test_an_empty_event_list_is_not_a_missing_trace() -> None:
    """An empty trace was sent, so it was observed — and says nothing.

    Distinguished from ``None`` because the difference is real: one
    client has no instrumentation, the other has instrumentation that
    saw nothing.
    """
    features = from_trace(_trace(), code="x" * 10)
    assert features.duration_ms == 0
    assert describe(features).flagged is False


# --- Derivation mechanics -----------------------------------------------


def test_events_are_sorted_before_after_the_last_paste_is_computed() -> None:
    """An out-of-order upload must not move the 'last paste' boundary.

    A client that batches or retries can deliver events slightly out of
    order, and computing "after the last paste" on the unsorted list
    would count the wrong events — in the worst case scoring a student's
    real work as if it had happened before the paste.
    """
    trace = _trace(
        # Delivered out of order: t=90_000 arrives before t=60_000.
        EditEvent(t=90_000, kind="type", chars=500, length=1700),
        EditEvent(t=5_000, kind="paste", chars=1200, length=1200),
        EditEvent(t=60_000, kind="type", chars=500, length=1700),
    )
    features = from_trace(trace, code="x" * 1700)

    assert features.post_paste_edits == 2
    assert features.post_paste_chars == 1000
    assert features.rewrite_ratio == pytest.approx(1000 / 1200)


def test_only_the_most_recent_paste_counts_for_the_rewrite_ratio() -> None:
    """Paste → rewrite → paste → stop is flagged.

    Averaging over both pastes would let the first paste's effort excuse
    the second one. The most recent paste is the only one that was not
    followed by anything, so it is the one that counts.
    """
    trace = _trace(
        EditEvent(t=1_000, kind="paste", chars=600, length=600),
        EditEvent(t=2_000, kind="type", chars=400, length=1000),
        EditEvent(t=3_000, kind="paste", chars=1000, length=2000),
    )
    features = from_trace(trace, code="x" * 2000)

    assert features.paste_count == 2
    assert features.pasted_chars == 1600
    assert features.post_paste_edits == 0
    # 1600/2000 is above the ratio threshold and nothing was rewritten.
    assert describe(features).flagged is True


def test_runs_are_counted_separately_from_edits() -> None:
    """Asking for a verdict is not an edit.

    ``run`` is the strongest cheap sign of iteration, so it is counted —
    but not as an edit, or a student who runs often would look like one
    who types a lot.
    """
    trace = _trace(
        EditEvent(t=0, kind="type", chars=10, length=10),
        EditEvent(t=1_000, kind="run", length=10),
        EditEvent(t=2_000, kind="run", length=10),
    )
    features = from_trace(trace, code="x" * 10)

    assert features.edit_count == 1
    assert features.runs_before_submit == 2


def test_duration_is_the_span_of_the_session() -> None:
    features = from_trace(
        _trace(
            EditEvent(t=4_000, kind="open", length=0),
            EditEvent(t=94_000, kind="type", chars=10, length=10),
        ),
        code="x" * 10,
    )
    assert features.duration_ms == 94_000


def test_final_length_falls_back_to_the_code() -> None:
    """A trace whose last event predates the final edit still measures.

    The editor records ``length`` per event; a client that stops
    recording before the last keystroke would otherwise divide by zero.
    """
    features = from_trace(
        _trace(EditEvent(t=0, kind="open", length=0)), code="int main(){}"
    )
    assert features.final_length == len("int main(){}")


# --- Parsing untrusted input --------------------------------------------


def test_parse_accepts_a_well_formed_trace() -> None:
    parsed = parse_process_trace(
        {"events": [{"t": 0, "kind": "paste", "chars": 10, "length": 10}]}
    )
    assert parsed is not None
    assert parsed.events[0].kind == "paste"


def test_parse_drops_a_malformed_trace_instead_of_rejecting_the_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad trace must not cost the student their verdict.

    The trace is telemetry; the request's job is to get code judged. A
    422 (REST) or a ``judge_error`` (WS) would turn an instrumentation
    bug into a failed submission, which is a far worse outcome than a
    missing process column.
    """
    with caplog.at_level("WARNING"):
        assert parse_process_trace({"events": "not a list"}) is None
        assert parse_process_trace({"events": [{"t": -1, "kind": "paste"}]}) is None
        assert parse_process_trace(42) is None

    # Dropped, but not silently: a client whose traces never validate has
    # to be findable, or the bug looks like "nobody uses this".
    assert caplog.records


def test_parse_returns_none_for_an_absent_trace() -> None:
    assert parse_process_trace(None) is None


def test_the_event_list_is_bounded() -> None:
    """An unbounded list would be an unbounded parse.

    The trace is uploaded with every submission, so it is attacker-shaped
    input in the same way the code is.
    """
    from app.process_trace import MAX_EVENTS

    huge = {"events": [{"t": i, "kind": "type"} for i in range(MAX_EVENTS + 1)]}
    assert parse_process_trace(huge) is None


# --- Persistence --------------------------------------------------------


def test_the_features_survive_a_round_trip(session: Session) -> None:
    """A row reloaded from the database describes itself the same way.

    The teacher's evidence panel reads rows that came out of a query,
    not rows that were just built, so the read path has to be exercised
    rather than only the write path.
    """
    features = from_trace(
        _trace(_paste(1200, at=5_000, length=1200)), code="x" * 1200
    )
    sub = Submission(problem_id=None, status="AC", code="x" * 1200)
    sub.apply_process(features, describe(features).flagged)
    session.add(sub)
    session.commit()

    reloaded = session.execute(select(Submission)).scalar_one()
    view = reloaded.process_view()

    assert view is not None
    assert view["flagged"] is True
    assert view["features"]["pasted_chars"] == 1200
    assert view["features"]["paste_ratio"] == pytest.approx(1.0)
    assert view["reasons"]


def test_a_row_without_a_trace_reports_none_not_zero(session: Session) -> None:
    """'Not observed' stays NULL; it must not read back as a clean run.

    This is the property the whole nullable-column decision rests on. If
    an untraced submission came back as zeros, every submission made
    outside the editor would testify that its author never pasted
    anything — a fact nobody established.
    """
    sub = Submission(problem_id=None, status="AC", code="int main(){}")
    session.add(sub)
    session.commit()

    reloaded = session.execute(select(Submission)).scalar_one()
    assert reloaded.process_view() is None
    assert reloaded.process_features() is None
    assert reloaded.process_flagged is False
    assert reloaded.to_dict()["process"] is None


def test_to_dict_carries_the_process_view(session: Session) -> None:
    """The API shape has the evidence, not just the flag.

    A boolean on its own is not reviewable, and the README promises a
    teacher can check the evidence behind a mark.
    """
    features = from_trace(
        _trace(_paste(1200, at=5_000, length=1200)), code="x" * 1200
    )
    sub = Submission(problem_id=None, status="AC", code="x" * 1200)
    sub.apply_process(features, describe(features).flagged)
    session.add(sub)
    session.commit()

    data = sub.to_dict()
    assert data["process"]["flagged"] is True
    assert data["process"]["summary"]
    # The list shape keeps it: it is small, and the teacher's list wants
    # to show which rows need a look.
    assert sub.to_dict(include_source=False)["process"]["flagged"] is True


def test_flagged_defaults_to_false_for_an_untraced_row(session: Session) -> None:
    """The teacher's query must not need ``OR IS NULL``.

    If an untraced row came back ``NULL``, "which submissions need a
    look" would have to spell out the null case or silently drop rows.
    """
    sub = Submission(problem_id=None, status="AC", code="int main(){}")
    session.add(sub)
    session.commit()
    session.refresh(sub)

    assert sub.process_flagged is False


# --- The write path -----------------------------------------------------


def _record(session: Session, trace: ProcessTrace | None) -> int:
    """Record one accepted submission, with or without a trace.

    ``problem_id`` is left ``None`` so nothing is filed in the mistake
    book — archiving would reach the error-analysis agent, and this
    module's tests must not touch a provider.
    """
    from app.records import SubmissionContext, record_submission
    from app.sandbox.judge import JudgeResult, JudgeStatus

    context = SubmissionContext(
        code="x" * 1200,
        language="cpp",
        problem_id=None,
        trace=trace,
    )
    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.AC), context, session=session
    )
    assert submission_id is not None
    return submission_id


def test_recording_persists_the_process_features(session: Session) -> None:
    """The trace handed to the judge route reaches the row.

    The whole point of C1 is that the process is recorded at the moment
    the verdict is; a feature computed anywhere else — in the client, or
    in a nightly job — would not be describing this submission.
    """
    submission_id = _record(session, _trace(_paste(1200, at=5_000, length=1200)))

    row = session.get(Submission, submission_id)
    assert row is not None
    assert row.process_flagged is True
    assert row.process_pasted_chars == 1200
    assert row.process_paste_ratio == pytest.approx(1.0)
    assert row.process_duration_ms == 5_000


def test_recording_without_a_trace_leaves_the_columns_null(session: Session) -> None:
    """An untraced submission stays untraced through the write path.

    Not zeros. The REST route accepts submissions with no trace, and
    those rows must not testify to a process nobody observed.
    """
    submission_id = _record(session, None)

    row = session.get(Submission, submission_id)
    assert row is not None
    assert row.process_duration_ms is None
    assert row.process_pasted_chars is None
    assert row.process_flagged is False
    assert row.process_view() is None
