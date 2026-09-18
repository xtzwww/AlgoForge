"""Submission ORM model.

A Submission records a single attempt by the user on a Problem. The
``status`` field carries the judge verdict (AC / WA / TLE / MLE / CE /
RE) from the sandbox. P1-17's knowledge-statistics aggregation joins
submissions → problems → knowledge_tags to compute per-tag accuracy.

PU-13 filled in the fields P1-9 reserved and added the write path. Before
this the table had no writer at all, while ``MistakeBookEntry`` required
a ``submission_id`` that had to already exist — so the mistake book's
normal path could not be reached.

Why ``problem_id`` is nullable
------------------------------
A free run — code the user is trying out with no problem selected — is a
legitimate thing to record, and it has no problem to point at. Those rows
simply do not participate in the knowledge-tag join in
:mod:`app.models.knowledge_stats`, which is the correct outcome: a
submission that is not about a problem should not move that problem's
accuracy. The alternative, dropping free runs entirely, would leave the
submission history with holes that the user cannot explain.

Why the judge output is stored denormalized
-------------------------------------------
``stdout`` / ``stderr`` are the *first failing* case's output, and
``exec_time`` / ``memory_used`` are the worst measurement across cases.
Storing the per-case detail would need a child table, and nothing reads
that yet; the aggregate is what the submission list shows.

Why the process features are scalar columns and not one JSON blob
-----------------------------------------------------------------
The edit-behaviour features (:mod:`app.process_trace`) are ten numbers,
and they are stored as ten nullable columns rather than as a single JSON
document. That is a deliberate choice against the obvious alternative:

* The teacher side filters on them — "which submissions need a look" is
  ``WHERE process_flagged``, and a threshold query wants
  ``process_paste_ratio``. Filtering inside a JSON document means
  expanding the array/object per dialect, which is exactly the class of
  bug :mod:`app.models.tag_values` exists because of: the project got it
  wrong twice, and every test passed both times because the suite runs on
  SQLite. Ten columns cannot have that failure mode.
* The words shown to a student or a teacher are **derived at read time**
  by :func:`app.process_trace.describe`, not stored. So the numbers are
  the single source of truth, and there is nothing to drift — which also
  means a threshold changed in code re-explains every historical row
  instead of leaving old rows speaking the old rule.

All ten are nullable because a submission without a trace is normal: the
REST judge route, an older client, and any submission made outside the
editor all have no editing session to describe. ``process_flagged`` is
the one exception — it is ``NOT NULL`` with a server default, because
"not flagged" and "unknown" are different claims and the teacher's query
must not have to write ``OR IS NULL``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.process_trace import ProcessFeatures, describe

#: Verdicts a submission can carry, matching
#: :class:`app.sandbox.judge.JudgeStatus`. Kept as a tuple here so the API
#: layer can validate a filter without importing the sandbox.
STATUSES: tuple[str, ...] = ("AC", "WA", "TLE", "MLE", "CE", "RE")

#: What a teacher can conclude about a flagged submission (阶段 5).
#:
#: Three states, not two. "nobody has looked" and "looked and found
#: nothing" have to be distinguishable: if a dismissed flag still
#: reported as unreviewed, the review queue could never shrink, and a
#: queue that never shrinks is a queue nobody opens.
REVIEW_CONFIRMED = "confirmed"
REVIEW_DISMISSED = "dismissed"
#: The teacher took their judgement back. Distinct from "never reviewed"
#: in the stored history even though both leave ``process_review`` NULL —
#: see :meth:`Submission.apply_review`.
REVIEW_CLEARED = "cleared"
REVIEWS: tuple[str, ...] = (REVIEW_CONFIRMED, REVIEW_DISMISSED, REVIEW_CLEARED)


class Submission(Base):
    """A single submission of code against a problem.

    Attributes:
        id: Primary key.
        problem_id: Foreign key to :class:`Problem`, or ``None`` for a
            free run with no problem selected.
        status: Judge verdict — one of :data:`STATUSES`.
        code: The source code that was submitted.
        language: Language id it was judged as, e.g. ``"cpp"``. Stored
            because code is not self-describing — the same text is a
            valid C++ program and an invalid Python one.
        stdout: Output from the first case that did not pass, or from
            the last case when everything passed. Empty for CE.
        stderr: Stderr from that same case, or the compiler diagnostics
            for CE.
        exec_time: Worst-case wall-clock seconds across all cases, or
            ``None`` when nothing was measured (e.g. CE ran no cases).
        memory_used: Peak RSS in megabytes across all cases, or ``None``
            when nothing was measured.
        created_at: Server-side timestamp of submission creation.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=True, index=True
    )
    #: Who submitted it (B1). Nullable on purpose: every row written
    #: before the user table existed has no owner, and a submission made
    #: with nobody logged in is still a real submission. Requiring one
    #: would mean either inventing an owner for old rows or refusing to
    #: record them, and losing history is the worse of the two.
    #:
    #: The teacher's per-student queries filter on this, so an unowned
    #: row is simply outside every student's view — which is honest, not
    #: a bug to paper over by lumping them under one account.
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)

    # --- PU-13: what was submitted and how it went ---
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(
        String(32), nullable=False, default="cpp", server_default="cpp"
    )
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Nullable rather than defaulted to 0: CE never runs a case, so there
    # is no execution to measure, and 0.0 seconds / 0 MB would read as a
    # real measurement of an instant, memory-free run.
    exec_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # --- Process features (C1): how the code came to be ---
    #
    # Derived from the editor's behaviour trace by
    # :func:`app.process_trace.from_trace` and written by the same call
    # that stores the verdict. All nullable; see the module docstring on
    # why these are columns and not a JSON document.
    #
    # ``None`` everywhere means "no trace was supplied", which is not the
    # same as "the process was unremarkable" — :func:`describe` words the
    # two differently, and that distinction is the honest part.
    process_duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    process_edit_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    process_paste_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    process_pasted_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    process_final_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    process_paste_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    process_post_paste_edits: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    process_post_paste_chars: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    process_rewrite_ratio: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    process_runs_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: The one queryable conclusion. Indexed because the teacher's
    #: "which submissions need a look" query filters on it, which is the
    #: whole reason it is a column rather than a derivation.
    # ``false()``, not ``func.false()``: the former is SQLAlchemy's
    # dialect-aware literal, rendering ``FALSE`` on PostgreSQL and ``0``
    # on SQLite. The latter emits a *call* to a function named ``false``,
    # which PostgreSQL has and SQLite does not — so it would work in
    # production and fail in every test, which is the worst of the two
    # possible directions.
    process_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # --- The teacher's answer to the flag (阶段 5) ---
    #
    # ``None`` means nobody has judged it yet. The other two are the
    # judgement. This is what makes the signal reviewable rather than
    # merely visible: the README promises 过程证据提示 + 教师复核, and a
    # mark with no way to disagree with it is a verdict, not a hint.
    #
    # Why dismissing does not clear ``process_flagged``
    # -------------------------------------------------
    # The flag is a *measurement* and the review is a *judgement about
    # the measurement*. Overwriting the first with the second would lose
    # exactly the data needed to improve the threshold later: "the flag
    # fired 40 times and the teacher agreed 9 times" is how the rule gets
    # tuned, and it is unanswerable once disagreements are erased. So a
    # dismissed row keeps its numbers and simply leaves the queue.
    process_review: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    #: Who judged it. Nullable for the same reason ``user_id`` is: rows
    #: written before this existed, and a review recorded by a caller
    #: that did not authenticate, still have to be storable.
    process_review_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    process_review_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    def apply_process(self, features: ProcessFeatures, flagged: bool) -> None:
        """Write a derived feature set onto this row.

        Split out from the caller that computes the features so the
        column names live in exactly one place — the model — and the
        derivation (:mod:`app.process_trace`) never has to know that a
        database exists.

        Args:
            features: The measured features.
            flagged: The conclusion, computed by the caller rather than
                here. Keeping the judgement out of the model is what
                lets the threshold change without touching schema or
                storage: old rows are re-described on read, not
                re-written.
        """
        self.process_duration_ms = features.duration_ms
        self.process_edit_count = features.edit_count
        self.process_paste_count = features.paste_count
        self.process_pasted_chars = features.pasted_chars
        self.process_final_length = features.final_length
        self.process_paste_ratio = features.paste_ratio
        self.process_post_paste_edits = features.post_paste_edits
        self.process_post_paste_chars = features.post_paste_chars
        self.process_rewrite_ratio = features.rewrite_ratio
        self.process_runs_before = features.runs_before_submit
        self.process_flagged = flagged

    def apply_review(self, verdict: str, reviewer_id: Optional[int] = None) -> None:
        """Record a teacher's judgement about this row's process flag.

        Args:
            verdict: One of :data:`REVIEW_CONFIRMED` /
                :data:`REVIEW_DISMISSED` / :data:`REVIEW_CLEARED`.
            reviewer_id: Who judged it, when the caller knows.

        Raises:
            ValueError: On anything else. Rejected here rather than only
                in the route's Pydantic model because a typo in a future
                caller is otherwise written to the database as a status
                no query will ever match.

        Note:
            Changing a verdict is allowed and is not an error: a teacher
            who confirmed by mistake and then dismisses has produced the
            right final state, and refusing the second write would leave
            the wrong one standing with no way to fix it.
        """
        if verdict not in REVIEWS:
            raise ValueError(f"unknown review verdict: {verdict!r}")
        cleared = verdict == REVIEW_CLEARED
        self.process_review = None if cleared else verdict
        self.process_review_by = None if cleared else reviewer_id
        self.process_review_at = None if cleared else datetime.now(UTC).replace(
            tzinfo=None
        )

    @property
    def is_open_flag(self) -> bool:
        """Flagged and not yet judged — i.e. still in the teacher's queue.

        The queue's whole predicate, in one place. Duplicating
        ``process_flagged and process_review != dismissed`` across the
        aggregations is how a dismissed row ends up counted in one place
        and not another.
        """
        return bool(self.process_flagged) and self.process_review != REVIEW_DISMISSED

    def review_view(self) -> dict:
        """The review state for the API.

        Always present, even for a row that was never flagged: the client
        renders one row per submission and a missing key would make it
        branch on ``undefined`` for the common case.
        """
        return {
            "verdict": self.process_review,
            "reviewed_by": self.process_review_by,
            "reviewed_at": (
                self.process_review_at.isoformat() if self.process_review_at else None
            ),
        }

    def process_features(self) -> Optional[ProcessFeatures]:
        """The stored process features, or ``None`` if none were stored.

        Rebuilt from the columns rather than read from a cached attribute
        so that a row loaded by a query — the normal case — describes
        itself the same way a freshly built one does.

        The sentinel is ``process_duration_ms is None``: it is the one
        field that is written for *every* recorded trace, including an
        empty one (whose duration is 0, not None), so "None" can only
        mean no trace was supplied at all.
        """
        if self.process_duration_ms is None:
            return None
        return ProcessFeatures(
            duration_ms=self.process_duration_ms,
            edit_count=self.process_edit_count or 0,
            paste_count=self.process_paste_count or 0,
            pasted_chars=self.process_pasted_chars or 0,
            final_length=self.process_final_length or 0,
            paste_ratio=self.process_paste_ratio or 0.0,
            post_paste_edits=self.process_post_paste_edits or 0,
            post_paste_chars=self.process_post_paste_chars or 0,
            rewrite_ratio=self.process_rewrite_ratio,
            runs_before_submit=self.process_runs_before or 0,
        )

    def process_view(self) -> Optional[dict]:
        """Features plus the signal they imply, for the API.

        ``None`` when no trace was supplied, so a client can tell "this
        submission has no process data" from "this submission's process
        was unremarkable" without a second field.

        The words are produced here, at read time, from the numbers —
        never stored. See the module docstring.
        """
        features = self.process_features()
        if features is None:
            return None
        return describe(features).to_dict(features)

    def to_dict(self, *, include_source: bool = True) -> dict:
        """Serialize for the API.

        Args:
            include_source: Include ``code`` / ``stdout`` / ``stderr``.
                False for a list view, which has no room for full source
                and would otherwise drag every submission's code across
                the wire.
        """
        data: dict = {
            "id": self.id,
            "problem_id": self.problem_id,
            "user_id": self.user_id,
            "status": self.status,
            "language": self.language,
            "exec_time": self.exec_time,
            "memory_used": self.memory_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "process": self.process_view(),
            "review": self.review_view(),
        }
        if include_source:
            data["code"] = self.code
            data["stdout"] = self.stdout
            data["stderr"] = self.stderr
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Submission id={self.id} problem_id={self.problem_id} "
            f"status={self.status!r}>"
        )
