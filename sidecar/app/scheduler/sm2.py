"""SM-2 spaced-repetition algorithm (P1-19).

Pure-Python implementation of the SuperMemo 2 algorithm. Given a
learner's previous SRS state and the quality of their latest recall
(in [0, 5]), returns the next state: interval (days), repetitions,
and easiness factor.

The algorithm is implemented as a pure function over a small dataclass
so it can be unit-tested in isolation (VIBECODING: "pytest 验证不同
质量分下的下次复习间隔") and reused by the review API without a DB
round-trip.

References:
    P. Wozniak, "SuperMemo 2: Optimization of repetition scheduling"
    (https://www.supermemo.com/en/blog/application-of-a-spaced-repetition-algorithm-in-learning)

Algorithm summary:
    quality q ∈ [0, 5]
        5 = perfect, 4 = correct after hesitation, 3 = correct after
        serious struggle, 2 = incorrect but easy to recall after,
        1 = incorrect, recognition after seeing, 0 = blackout.

    If q < 3: reset repetitions to 0, interval = 1 day.
    Else:
        repetitions += 1
        interval = 1 if reps == 1
                 = 6 if reps == 2
                 = round(prev_interval * EF) otherwise
        EF = max(1.3, EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))

    The next review time is "now + interval days".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class SrsState:
    """The persistent spaced-repetition state for one item.

    Attributes:
        repetitions: Number of consecutive successful recalls.
            Reset to 0 when quality < 3 (forgot).
        easiness_factor: The multiplier (≥1.3) controlling how fast
            intervals grow. Starts at 2.5.
        interval_days: Current scheduled interval in days. 0 before
            the first review.
        last_reviewed_at: When the most recent review happened. None
            if never reviewed.
        due_at: When the next review is due. None if never scheduled.
    """

    repetitions: int = 0
    easiness_factor: float = 2.5
    interval_days: int = 0
    last_reviewed_at: Optional[datetime] = None
    due_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "repetitions": self.repetitions,
            "easiness_factor": round(self.easiness_factor, 4),
            "interval_days": self.interval_days,
            "last_reviewed_at": (
                self.last_reviewed_at.isoformat()
                if self.last_reviewed_at is not None
                else None
            ),
            "due_at": self.due_at.isoformat() if self.due_at is not None else None,
        }


def _update_easiness(ef: float, quality: int) -> float:
    """Compute the new easiness factor.

    EF += 0.1 - (5 - q)(0.08 + (5 - q) * 0.02)
    Clamped to a floor of 1.3 so the schedule never collapses.
    """
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    new_ef = ef + delta
    return max(1.3, new_ef)


def _next_interval(repetitions: int, prev_interval: int, ef: float) -> int:
    """Compute the next interval in days based on repetition count."""
    if repetitions <= 0:
        # Just failed → review tomorrow.
        return 1
    if repetitions == 1:
        return 1
    if repetitions == 2:
        return 6
    return max(1, round(prev_interval * ef))


def schedule(
    state: SrsState, quality: int, *, now: Optional[datetime] = None
) -> SrsState:
    """Advance SRS state after a review with the given ``quality``.

    Args:
        state: The previous SRS state.
        quality: Recall quality in [0, 5]. 0 = blackout,
            5 = perfect. Values outside [0, 5] are clamped.
        now: Optional review timestamp. Defaults to UTC now. Used
            both as ``last_reviewed_at`` and as the base for computing
            ``due_at`` (now + interval days).

    Returns:
        The new :class:`SrsState` with updated repetitions, EF,
        interval, last_reviewed_at, and due_at.
    """
    # Clamp quality to [0, 5].
    q = max(0, min(5, int(quality)))

    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)

    ef_new = _update_easiness(state.easiness_factor, q)

    if q < 3:
        # Failed recall: reset streak, schedule for tomorrow.
        reps_new = 0
        interval_new = 1
    else:
        reps_new = state.repetitions + 1
        interval_new = _next_interval(reps_new, state.interval_days, ef_new)

    due = now + timedelta(days=interval_new)
    return SrsState(
        repetitions=reps_new,
        easiness_factor=ef_new,
        interval_days=interval_new,
        last_reviewed_at=now,
        due_at=due,
    )


def is_due(state: SrsState, *, now: Optional[datetime] = None) -> bool:
    """Whether the item is due for review at ``now``.

    An item is due if its ``due_at`` has passed or it has never been
    reviewed (no due_at)."""
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)
    if state.due_at is None:
        return True
    return now >= state.due_at
