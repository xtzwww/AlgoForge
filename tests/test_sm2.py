"""Unit tests for the pure SM-2 algorithm (P1-19a).

VIBECODING P1-19 验证：pytest 验证不同质量分下的下次复习间隔。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.scheduler.sm2 import (
    SrsState,
    is_due,
    schedule,
    _next_interval,
    _update_easiness,
)


NOW = datetime(2026, 9, 7, 12, 0, 0)


def test_fresh_state_defaults() -> None:
    """A brand-new SRS state has SM-2 defaults: 0 reps, EF 2.5, no due."""
    s = SrsState()
    assert s.repetitions == 0
    assert s.easiness_factor == 2.5
    assert s.interval_days == 0
    assert s.last_reviewed_at is None
    assert s.due_at is None


def test_first_review_quality_5_interval_1_day() -> None:
    """First successful review (q=5): reps=1, interval=1 day."""
    new = schedule(SrsState(), 5, now=NOW)
    assert new.repetitions == 1
    assert new.interval_days == 1
    assert new.last_reviewed_at == NOW
    assert new.due_at == NOW + timedelta(days=1)


def test_second_review_quality_5_interval_6_days() -> None:
    """Second successful review: reps=2, interval=6 days."""
    first = schedule(SrsState(), 5, now=NOW)
    second = schedule(first, 5, now=NOW + timedelta(days=1))
    assert second.repetitions == 2
    assert second.interval_days == 6
    assert second.due_at == NOW + timedelta(days=1) + timedelta(days=6)


def test_third_review_quality_5_uses_ef() -> None:
    """Third review: interval = prev_interval * latest EF.

    After three q=5 reviews EF climbs 2.5 → 2.6 → 2.7 → 2.8,
    so the third interval = round(6 * 2.8) = 17.
    """
    first = schedule(SrsState(), 5, now=NOW)
    second = schedule(first, 5, now=NOW + timedelta(days=1))
    third = schedule(second, 5, now=NOW + timedelta(days=7))
    assert third.repetitions == 3
    assert third.interval_days == 17


def test_failed_review_resets_repetitions() -> None:
    """q < 3 resets reps to 0 and schedules for tomorrow."""
    first = schedule(SrsState(), 5, now=NOW)  # reps=1, interval=1
    # Forget the next day → reset
    failed = schedule(first, 2, now=NOW + timedelta(days=1))
    assert failed.repetitions == 0
    assert failed.interval_days == 1
    assert failed.due_at == NOW + timedelta(days=1) + timedelta(days=1)


def test_quality_3_decreases_ef() -> None:
    """q=3 (struggled but correct) should drop EF."""
    ef_new = _update_easiness(2.5, 3)
    assert ef_new < 2.5
    # EF update formula: 2.5 + (0.1 - 2 * (0.08 + 2 * 0.02)) = 2.5 - 0.14 = 2.36
    assert ef_new == pytest.approx(2.36)


def test_quality_0_drops_ef_more() -> None:
    """q=0 (blackout) drops EF by the maximum amount.

    EF update formula: EF += 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    For q=0: 0.1 - 5 * (0.08 + 5 * 0.02) = 0.1 - 5 * 0.18 = 0.1 - 0.9 = -0.8
    So EF = 2.5 - 0.8 = 1.7.
    """
    ef_new = _update_easiness(2.5, 0)
    assert ef_new < _update_easiness(2.5, 3)
    assert ef_new == pytest.approx(1.7)


def test_ef_clamped_at_1_3() -> None:
    """EF never drops below 1.3."""
    ef_new = _update_easiness(1.3, 0)
    assert ef_new == 1.3
    # Even if math would lower it further
    ef_low = _update_easiness(1.31, 0)
    assert ef_low >= 1.3


def test_quality_clamped_to_range() -> None:
    """Quality values outside [0, 5] are clamped."""
    high = schedule(SrsState(), 100, now=NOW)
    assert high.repetitions == 1
    low = schedule(SrsState(), -10, now=NOW)
    assert low.repetitions == 0


def test_quality_4_keeps_ef_high() -> None:
    """q=4 (correct after hesitation) keeps EF close to 2.5."""
    ef_new = _update_easiness(2.5, 4)
    # 2.5 + (0.1 - 1 * (0.08 + 1 * 0.02)) = 2.5 + 0 = 2.5
    assert ef_new == 2.5


def test_quality_5_increases_ef() -> None:
    """q=5 (perfect) increases EF above 2.5."""
    ef_new = _update_easiness(2.5, 5)
    # 2.5 + (0.1 - 0 * ...) = 2.6
    assert ef_new == pytest.approx(2.6)


def test_to_dict_round_trip() -> None:
    """to_dict() produces JSON-safe dict with all keys."""
    s = schedule(SrsState(), 5, now=NOW)
    d = s.to_dict()
    assert d["repetitions"] == 1
    assert d["easiness_factor"] == 2.6
    assert d["interval_days"] == 1
    assert d["last_reviewed_at"] == NOW.isoformat()
    assert d["due_at"] == (NOW + timedelta(days=1)).isoformat()


# --- is_due -----------------------------------------------------------


def test_is_due_never_reviewed_is_due() -> None:
    """An item with no due_at is always due (needs first review)."""
    assert is_due(SrsState(), now=NOW) is True


def test_is_due_future_not_due() -> None:
    """An item due in the future is not due."""
    future = NOW + timedelta(days=2)
    state = SrsState(due_at=future)
    assert is_due(state, now=NOW) is False


def test_is_due_past_is_due() -> None:
    """An item whose due_at has passed is due."""
    past = NOW - timedelta(hours=1)
    state = SrsState(due_at=past)
    assert is_due(state, now=NOW) is True


def test_is_due_now_is_due() -> None:
    """Boundary: due_at == now counts as due (≥)."""
    state = SrsState(due_at=NOW)
    assert is_due(state, now=NOW) is True
