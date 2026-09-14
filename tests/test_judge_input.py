"""Tests for judge input resolution (PU-12).

Covers :mod:`app.sandbox.judge_input` — the shared resolver that both
``POST /api/v1/judge`` and ``WS /ws/run-log`` call. Testing it directly
rather than through each transport is what keeps the two from drifting:
the rules are asserted once, here.

The behavior worth guarding:

- exactly one of ``problem_id`` / ``test_cases`` is required — both and
  neither are errors, because either way the intent is ambiguous;
- ``problem_id`` mode reads the *hidden* cases too (that is the whole
  point — the client never receives them) and inherits the problem's
  own limits;
- a caller-supplied limit overrides the problem's, which is what makes
  "retry this TLE with a longer limit" possible;
- every failure is a :class:`JudgeInputError`, which is a ``ValueError``
  so the existing handlers keep working.

Sessions are injected rather than monkeypatched: ``resolve`` takes a
``session`` argument precisely so tests can pass in-memory SQLite.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.problem import Problem
from app.models.test_case import TestCase
from app.sandbox.judge_input import (
    JudgeInputError,
    ResolvedJudgeInput,
    parse_inline_cases,
    resolve,
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


def _seed_problem(
    session: Session,
    *,
    problem_id: int = 1,
    time_limit: float = 10.0,
    memory_limit: int = 256,
    cases: list[tuple[str, str, bool]] | None = None,
) -> Problem:
    """Insert a problem with ``(input, output, is_sample)`` cases."""
    problem = Problem(
        id=problem_id,
        title="Seeded",
        knowledge_tags=[],
        description="",
        samples=[],
        time_limit=time_limit,
        memory_limit=memory_limit,
    )
    session.add(problem)
    session.flush()
    for ordinal, (case_in, case_out, is_sample) in enumerate(cases or [], start=1):
        session.add(
            TestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                input=case_in,
                expected_output=case_out,
                is_sample=is_sample,
            )
        )
    session.commit()
    return problem


# --- parse_inline_cases ------------------------------------------------


def test_parse_inline_cases_converts_objects() -> None:
    """A well-formed list becomes judge TestCase objects."""
    cases = parse_inline_cases(
        [
            {"input": "1 2\n", "expected_output": "3\n"},
            {"input": "a", "expected_output": "b"},
        ]
    )
    assert [(c.input, c.expected_output) for c in cases] == [
        ("1 2\n", "3\n"),
        ("a", "b"),
    ]


def test_parse_inline_cases_defaults_missing_fields_to_empty() -> None:
    """An omitted ``input`` is empty stdin, not an error.

    A program that prints a constant has no input at all, so requiring
    the key would reject a legitimate case.
    """
    cases = parse_inline_cases([{"expected_output": "42"}])
    assert cases[0].input == ""
    assert cases[0].expected_output == "42"


def test_parse_inline_cases_coerces_null_fields_to_empty() -> None:
    """JSON ``null`` reads as "not supplied" rather than crashing."""
    cases = parse_inline_cases([{"input": None, "expected_output": None}])
    assert cases[0].input == ""
    assert cases[0].expected_output == ""


@pytest.mark.parametrize("bad", [[], "not a list", None, 42, {"input": "x"}])
def test_parse_inline_cases_rejects_non_list_or_empty(bad: object) -> None:
    """Anything that is not a non-empty list is a JudgeInputError."""
    with pytest.raises(JudgeInputError):
        parse_inline_cases(bad)


def test_parse_inline_cases_rejects_non_object_element() -> None:
    """A bare string where an object belongs names the offending index."""
    with pytest.raises(JudgeInputError, match=r"test_cases\[1\]"):
        parse_inline_cases([{"expected_output": "a"}, "oops"])


# --- mode selection ----------------------------------------------------


def test_resolve_requires_exactly_one_mode() -> None:
    """Neither mode supplied is an error, not a vacuous pass."""
    with pytest.raises(JudgeInputError, match="one of"):
        resolve()


def test_resolve_rejects_both_modes() -> None:
    """Both modes supplied is ambiguous, so it is refused outright."""
    with pytest.raises(JudgeInputError, match="mutually exclusive"):
        resolve(problem_id=1, test_cases=[{"expected_output": "x"}])


def test_judge_input_error_is_a_value_error() -> None:
    """The handlers catch ``ValueError``; subclasses must not break that."""
    assert issubclass(JudgeInputError, ValueError)


# --- inline mode -------------------------------------------------------


def test_resolve_inline_applies_defaults() -> None:
    """Omitted limits fall back to the REST defaults (10s / 256MB)."""
    result = resolve(test_cases=[{"expected_output": "3"}])
    assert isinstance(result, ResolvedJudgeInput)
    assert result.time_limit == 10.0
    assert result.mem_limit_mb == 256
    assert result.problem_id is None


def test_resolve_inline_honours_explicit_limits() -> None:
    """Explicit limits in inline mode are authoritative."""
    result = resolve(
        test_cases=[{"expected_output": "3"}], time_limit=2.5, mem_limit_mb=512
    )
    assert result.time_limit == 2.5
    assert result.mem_limit_mb == 512


def test_resolve_inline_does_not_touch_the_database(session: Session) -> None:
    """Inline mode works with no session and no problem rows at all."""
    result = resolve(test_cases=[{"expected_output": "3"}], session=None)
    assert len(result.test_cases) == 1
    assert session.query(Problem).count() == 0


# --- problem_id mode ---------------------------------------------------


def test_resolve_from_problem_returns_all_cases_including_hidden(
    session: Session,
) -> None:
    """**The point of this mode.** Hidden cases reach the judge.

    The client only ever receives the samples (PU-12's detail endpoint
    filters them); the judge reads the table directly, so the expected
    output of a hidden case never crosses the wire.
    """
    _seed_problem(
        session,
        cases=[
            ("1 2", "3", True),
            ("SECRET-IN", "SECRET-OUT", False),
        ],
    )

    result = resolve(problem_id=1, session=session)
    assert len(result.test_cases) == 2
    assert result.test_cases[0].input == "1 2"
    assert result.test_cases[1].input == "SECRET-IN"
    assert result.test_cases[1].expected_output == "SECRET-OUT"
    assert result.problem_id == 1


def test_resolve_from_problem_inherits_problem_limits(session: Session) -> None:
    """The problem's own limits apply when the caller sends none."""
    _seed_problem(session, time_limit=3.0, memory_limit=512, cases=[("a", "a", False)])

    result = resolve(problem_id=1, session=session)
    assert result.time_limit == 3.0
    assert result.mem_limit_mb == 512


def test_resolve_from_problem_honours_caller_override(session: Session) -> None:
    """A caller-supplied limit overrides the problem's.

    This is what lets a user retry a TLE submission with a longer limit
    without editing the problem for everyone.
    """
    _seed_problem(session, time_limit=1.0, memory_limit=256, cases=[("a", "a", False)])

    result = resolve(problem_id=1, time_limit=20.0, mem_limit_mb=1024, session=session)
    assert result.time_limit == 20.0
    assert result.mem_limit_mb == 1024


def test_resolve_from_problem_orders_cases_by_ordinal(session: Session) -> None:
    """Cases come back in authored order, not insertion or id order."""
    _seed_problem(session, cases=[])
    for ordinal, label in ((3, "third"), (1, "first"), (2, "second")):
        session.add(
            TestCase(
                problem_id=1,
                ordinal=ordinal,
                input=label,
                expected_output=label,
                is_sample=False,
            )
        )
    session.commit()

    result = resolve(problem_id=1, session=session)
    assert [c.input for c in result.test_cases] == ["first", "second", "third"]


def test_resolve_from_problem_404s_on_unknown_id(session: Session) -> None:
    """An unknown problem names the id in the message.

    The REST route keys off "not found" to pick 404 over 422, so the
    wording is part of the contract rather than incidental.
    """
    with pytest.raises(JudgeInputError, match="problem 7 not found"):
        resolve(problem_id=7, session=session)


def test_resolve_from_problem_rejects_problem_without_cases(session: Session) -> None:
    """A problem with no cases cannot be judged.

    Silently returning an empty case list would make every submission
    AC — the most dangerous possible degradation, since it reads as
    success.
    """
    _seed_problem(session, cases=[])

    with pytest.raises(JudgeInputError, match="no test cases"):
        resolve(problem_id=1, session=session)


# --- limit validation --------------------------------------------------


@pytest.mark.parametrize("bad_time", [0, 0.05, 61, 1e9])
def test_resolve_rejects_out_of_range_time_limit(
    bad_time: float, session: Session
) -> None:
    """Out-of-range time limits are refused in both modes."""
    _seed_problem(session, cases=[("a", "a", False)])

    with pytest.raises(JudgeInputError, match="time_limit"):
        resolve(problem_id=1, time_limit=bad_time, session=session)
    with pytest.raises(JudgeInputError, match="time_limit"):
        resolve(test_cases=[{"expected_output": "a"}], time_limit=bad_time)


@pytest.mark.parametrize("bad_mem", [0, -1, 2049])
def test_resolve_rejects_out_of_range_mem_limit(
    bad_mem: int, session: Session
) -> None:
    """Out-of-range memory limits are refused in both modes."""
    _seed_problem(session, cases=[("a", "a", False)])

    with pytest.raises(JudgeInputError, match="mem_limit_mb"):
        resolve(problem_id=1, mem_limit_mb=bad_mem, session=session)
    with pytest.raises(JudgeInputError, match="mem_limit_mb"):
        resolve(test_cases=[{"expected_output": "a"}], mem_limit_mb=bad_mem)


@pytest.mark.parametrize("bad", ["soon", [1], {"a": 1}])
def test_resolve_rejects_non_numeric_time_limit(bad: object, session: Session) -> None:
    """A non-numeric limit is a client error, not a 500.

    The WS route takes raw JSON, so ``"time_limit": "soon"`` is reachable
    by a real client. It has to come back as a judge_error event.

    ``None`` is deliberately absent from this list: the WS parser passes
    ``payload.get("time_limit")`` straight through, so ``null`` and
    "absent" are the same thing by design and both mean "use the
    default". Treating ``None`` as malformed would make an explicit
    ``"time_limit": null`` a hard error while omitting the key worked.
    """
    with pytest.raises(JudgeInputError, match="time_limit"):
        resolve(test_cases=[{"expected_output": "a"}], time_limit=bad)  # type: ignore[arg-type]


def test_resolve_treats_null_time_limit_as_unset() -> None:
    """``None`` means "not supplied", so the default applies."""
    result = resolve(test_cases=[{"expected_output": "a"}], time_limit=None)
    assert result.time_limit == 10.0


def test_resolve_treats_null_mem_limit_as_unset() -> None:
    """Same for the memory limit."""
    result = resolve(test_cases=[{"expected_output": "a"}], mem_limit_mb=None)
    assert result.mem_limit_mb == 256


def test_resolve_rejects_non_numeric_mem_limit(session: Session) -> None:
    """Same for the memory limit."""
    with pytest.raises(JudgeInputError, match="mem_limit_mb"):
        resolve(
            test_cases=[{"expected_output": "a"}],
            mem_limit_mb="lots",  # type: ignore[arg-type]
        )


def test_resolve_accepts_boundary_limits(session: Session) -> None:
    """The documented endpoints are inclusive."""
    low = resolve(test_cases=[{"expected_output": "a"}], time_limit=0.1, mem_limit_mb=1)
    assert low.time_limit == 0.1
    assert low.mem_limit_mb == 1

    high = resolve(
        test_cases=[{"expected_output": "a"}], time_limit=60.0, mem_limit_mb=2048
    )
    assert high.time_limit == 60.0
    assert high.mem_limit_mb == 2048


def test_resolve_coerces_numeric_strings() -> None:
    """Raw JSON may carry "5" instead of 5; accepting it is friendlier.

    Both transports should behave the same here, and the WS one has no
    schema to coerce for it.
    """
    result = resolve(
        test_cases=[{"expected_output": "a"}],
        time_limit="5",  # type: ignore[arg-type]
        mem_limit_mb="512",  # type: ignore[arg-type]
    )
    assert result.time_limit == 5.0
    assert result.mem_limit_mb == 512
    assert isinstance(result.mem_limit_mb, int)


def test_resolve_rejects_fractional_mem_limit() -> None:
    """``int(2.7)`` truncates silently; the resolver must not.

    A memory limit of 2.7 MB is meaningless, and silently treating it as
    2 would be worse than saying so.
    """
    with pytest.raises(JudgeInputError, match="mem_limit_mb"):
        resolve(
            test_cases=[{"expected_output": "a"}],
            mem_limit_mb=2.7,  # type: ignore[arg-type]
        )
