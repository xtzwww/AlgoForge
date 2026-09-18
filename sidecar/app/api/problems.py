"""Problem catalogue API (PU-12).

- GET  /api/v1/problems
    Paged catalogue listing. Returns *no* statement prose and *no* test
    cases — just what a list view renders.
- GET  /api/v1/problems/{problem_id}
    Full detail: statement, display samples, and the **visible** test
    cases only.
- GET  /api/v1/problems/{problem_id}/test-cases
    Every case, including the hidden ones. This is the judging input.
- POST /api/v1/problems
    Create a problem together with its cases in one transaction.

The hidden/visible boundary (read this before changing anything)
---------------------------------------------------------------
`TestCase.is_sample` is what stops a user from reading the expected
output of a judging case and hardcoding it. The model enforces nothing on
its own — the guarantee lives entirely in which query each route runs:

* :func:`get_problem` filters ``is_sample.is_(True)``.
* :func:`list_problems` does not query cases at all.
* :func:`get_problem_test_cases` deliberately returns everything, and is
  the **only** endpoint that does.

So a regression here is not a cosmetic bug — it silently disables the
distinction between practicing and cheating. ``tests/test_problems_api.py``
asserts the hidden case's expected output does not appear in the detail
response body, at the raw-text level, so a future refactor that leaks it
through a different field still fails the test.

Security boundary: this design assumes single-user local deployment
------------------------------------------------------------------
``GET /problems/{id}/test-cases`` hands the hidden cases to whoever asks.
That is acceptable here because the user is the problem author and the
server is on their own machine, and it is called out in the response
docstring so the assumption is visible rather than implicit.

Judging is nonetheless designed the
multi-user-safe way *now*: the judge accepts a ``problem_id`` and reads
the cases server-side (see ``app/api/judge.py`` and ``app/api/ws.py``), so
the client never has to receive them. Turning this into a real web service
means removing this one endpoint's exposure, not rewriting the judge.

Session handling mirrors P1-18's mistake-book API (sync session, imported
from ``app.db``). Per AGENTS.md section 4 this module holds routes only —
selection and serialization logic lives in ``app/models/problem.py`` and
``app/models/test_case.py``.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.problem import DIFFICULTIES, Problem
from app.models.tag_values import knowledge_tag_values
from app.models.test_case import TestCase

router = APIRouter(prefix="/api/v1/problems", tags=["problems"])


# --- Constants ---------------------------------------------------------

#: Hard ceiling on ``page_size``. Without one, ``?page_size=1000000`` lets
#: an unauthenticated caller turn the catalogue into a memory amplifier.
MAX_PAGE_SIZE = 100


# --- Request schemas ---------------------------------------------------


class SampleIn(BaseModel):
    """One display sample: a stdin/stdout pair shown to the user."""

    input: str = ""
    output: str = ""


class TestCaseIn(BaseModel):
    """One judging case.

    ``is_sample`` defaults to False — hidden unless asked for, matching
    the model's fail-closed default. A caller importing a catalogue must
    opt in to exposing a case, never opt out of hiding one.
    """

    input: str = ""
    expected_output: str = ""
    is_sample: bool = False


class ProblemCreate(BaseModel):
    """Request body for POST /api/v1/problems.

    Cases are created in the same transaction as the problem, because a
    problem with no cases cannot be judged and a caller that has to make
    two round trips can leave that half-finished state behind.
    """

    title: str = Field(min_length=1, max_length=255)
    knowledge_tags: list[str] = Field(default_factory=list)
    description: str = ""
    input_format: Optional[str] = None
    output_format: Optional[str] = None
    samples: list[SampleIn] = Field(default_factory=list)
    time_limit: float = Field(default=10.0, gt=0, le=60.0)
    memory_limit: int = Field(default=256, ge=1, le=2048)
    difficulty: Optional[Literal["easy", "medium", "hard"]] = None
    source: Optional[str] = Field(default=None, max_length=255)
    test_cases: list[TestCaseIn] = Field(default_factory=list)


# --- Response schemas --------------------------------------------------


class TestCaseOut(BaseModel):
    """A judging case as returned to a client."""

    id: int
    ordinal: int
    input: str
    expected_output: str
    is_sample: bool


class ProblemSummaryOut(BaseModel):
    """One catalogue row — no statement, no cases.

    Omitting ``description`` is not just payload economy: a list view has
    no use for it, and including it would make every catalogue page drag
    the full text of every problem across the wire.
    """

    id: int
    title: str
    knowledge_tags: list[str]
    difficulty: Optional[str] = None
    source: Optional[str] = None
    time_limit: float
    memory_limit: int
    is_active: bool


class ProblemListOut(BaseModel):
    """A page of the catalogue."""

    items: list[ProblemSummaryOut]
    total: int
    page: int
    page_size: int


class ProblemDetailOut(BaseModel):
    """Full problem detail, with **visible** cases only.

    ``test_cases`` here is filtered to ``is_sample = True``. The complete
    set is available from ``GET /problems/{id}/test-cases``. Keeping both
    a filtered and an unfiltered view means "show the problem" and "judge
    it" are separate, explicit operations rather than one query whose
    safety depends on the caller remembering to filter.
    """

    id: int
    title: str
    knowledge_tags: list[str]
    description: str
    input_format: Optional[str] = None
    output_format: Optional[str] = None
    samples: list[dict]
    time_limit: float
    memory_limit: int
    difficulty: Optional[str] = None
    source: Optional[str] = None
    is_active: bool
    test_cases: list[TestCaseOut]


class TestCaseListOut(BaseModel):
    """The complete case set for judging (hidden cases included)."""

    problem_id: int
    total: int
    test_cases: list[TestCaseOut]


# --- Helpers -----------------------------------------------------------


def _case_to_out(case: TestCase) -> TestCaseOut:
    return TestCaseOut(
        id=case.id,
        ordinal=case.ordinal,
        input=case.input,
        expected_output=case.expected_output,
        is_sample=case.is_sample,
    )


def _get_problem_or_404(session: Session, problem_id: int) -> Problem:
    """Fetch a problem by id, or raise 404.

    Inactive problems are returned here on purpose: a caller who already
    holds a submission or mistake-book entry for a retired problem must
    still be able to resolve it. Filtering inactive problems is the list
    endpoint's job, not this helper's.
    """
    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(
            status_code=404, detail=f"problem {problem_id} not found"
        )
    return problem


def _ordered_cases(
    session: Session, problem_id: int, *, samples_only: bool = False
) -> list[TestCase]:
    """Return a problem's cases in authored order.

    Ordered by ``ordinal`` rather than ``id`` so the numbering the user
    sees ("case 2 of 5") is stable and author-controlled — see the
    TestCase module docstring.
    """
    stmt = select(TestCase).where(TestCase.problem_id == problem_id)
    if samples_only:
        stmt = stmt.where(TestCase.is_sample.is_(True))
    stmt = stmt.order_by(TestCase.ordinal)
    return list(session.execute(stmt).scalars().all())


# --- Routes ------------------------------------------------------------


@router.get("", response_model=ProblemListOut)
def list_problems(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    knowledge_tag: Optional[str] = Query(
        default=None, description="Only problems carrying this exact tag"
    ),
    difficulty: Optional[str] = Query(
        default=None, description="easy | medium | hard"
    ),
    include_inactive: bool = Query(
        default=False,
        description="Include soft-deleted problems (default: active only)",
    ),
) -> ProblemListOut:
    """Return one page of the problem catalogue.

    Filtering by knowledge tag is done in SQL rather than by the caller,
    because ``knowledge_tags`` is a JSON array whose membership test has
    no portable spelling (``json_each`` on SQLite,
    ``json_array_elements_text`` on PostgreSQL — see
    ``app.models.tag_values``). Keeping it here means there is one
    implementation to fix rather than one per client.

    Note:
        ``knowledge_tag`` matches an exact tag, not a substring. The
        frontend's tag filter (PU-14) offers the tags that actually exist,
        so exact matching is what its UI implies.
    """
    if difficulty is not None and difficulty not in DIFFICULTIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"difficulty must be one of {', '.join(DIFFICULTIES)}; "
                f"got {difficulty!r}"
            ),
        )

    with get_session() as session:
        filters = []
        if not include_inactive:
            filters.append(Problem.is_active.is_(True))
        if difficulty is not None:
            filters.append(Problem.difficulty == difficulty)
        if knowledge_tag:
            # JSON array membership. The expansion is dialect-specific and
            # shared with the profile page's aggregation — see
            # ``app.models.tag_values``. The comment here used to name both
            # dialects and then call ``json_each`` anyway.
            tag_value = knowledge_tag_values(session)
            filters.append(tag_value.c.value == knowledge_tag)

        total = session.execute(
            select(func.count()).select_from(Problem).where(*filters)
        ).scalar_one()

        rows = (
            session.execute(
                select(Problem)
                .where(*filters)
                .order_by(Problem.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )

        return ProblemListOut(
            items=[
                ProblemSummaryOut(
                    id=p.id,
                    title=p.title,
                    knowledge_tags=list(p.knowledge_tags or []),
                    difficulty=p.difficulty,
                    source=p.source,
                    time_limit=p.time_limit,
                    memory_limit=p.memory_limit,
                    is_active=p.is_active,
                )
                for p in rows
            ],
            total=int(total),
            page=page,
            page_size=page_size,
        )


@router.get("/{problem_id}", response_model=ProblemDetailOut)
def get_problem(problem_id: int) -> ProblemDetailOut:
    """Return a problem's detail plus its **visible** test cases.

    Hidden cases are filtered out here — see the module docstring for why
    that filter is load-bearing rather than cosmetic.
    """
    with get_session() as session:
        problem = _get_problem_or_404(session, problem_id)
        visible = _ordered_cases(session, problem_id, samples_only=True)
        return ProblemDetailOut(
            id=problem.id,
            title=problem.title,
            knowledge_tags=list(problem.knowledge_tags or []),
            description=problem.description,
            input_format=problem.input_format,
            output_format=problem.output_format,
            samples=list(problem.samples or []),
            time_limit=problem.time_limit,
            memory_limit=problem.memory_limit,
            difficulty=problem.difficulty,
            source=problem.source,
            is_active=problem.is_active,
            test_cases=[_case_to_out(c) for c in visible],
        )


@router.get("/{problem_id}/test-cases", response_model=TestCaseListOut)
def get_problem_test_cases(problem_id: int) -> TestCaseListOut:
    """Return **every** case for a problem, hidden ones included.

    This is the judging input, and the single endpoint that intentionally
    exposes hidden cases. Its documented boundary is single-user local
    deployment: the caller is the problem's author running the server on
    their own machine. A future multi-user deployment must close this
    route (or gate it behind ownership) and rely on the judge's
    ``problem_id`` mode instead — see the module docstring.
    """
    with get_session() as session:
        _get_problem_or_404(session, problem_id)
        cases = _ordered_cases(session, problem_id)
        return TestCaseListOut(
            problem_id=problem_id,
            total=len(cases),
            test_cases=[_case_to_out(c) for c in cases],
        )


@router.post("", response_model=ProblemDetailOut, status_code=201)
def create_problem(req: ProblemCreate) -> ProblemDetailOut:
    """Create a problem and its cases in one transaction.

    Ordinals are assigned from the request order, 1-based, so the caller
    does not have to supply them and cannot supply a duplicate by mistake.
    """
    with get_session() as session:
        problem = Problem(
            title=req.title,
            knowledge_tags=list(req.knowledge_tags),
            description=req.description,
            input_format=req.input_format,
            output_format=req.output_format,
            samples=[s.model_dump() for s in req.samples],
            time_limit=req.time_limit,
            memory_limit=req.memory_limit,
            difficulty=req.difficulty,
            source=req.source,
            is_active=True,
        )
        session.add(problem)
        session.flush()  # assign problem.id for the cases below

        for ordinal, case in enumerate(req.test_cases, start=1):
            session.add(
                TestCase(
                    problem_id=problem.id,
                    ordinal=ordinal,
                    input=case.input,
                    expected_output=case.expected_output,
                    is_sample=case.is_sample,
                )
            )

        session.commit()
        session.refresh(problem)
        visible = _ordered_cases(session, problem.id, samples_only=True)
        return ProblemDetailOut(
            id=problem.id,
            title=problem.title,
            knowledge_tags=list(problem.knowledge_tags or []),
            description=problem.description,
            input_format=problem.input_format,
            output_format=problem.output_format,
            samples=list(problem.samples or []),
            time_limit=problem.time_limit,
            memory_limit=problem.memory_limit,
            difficulty=problem.difficulty,
            source=problem.source,
            is_active=problem.is_active,
            # The response is the same shape as GET detail, so it inherits
            # the same filter: a caller that posts a hidden case must not
            # read it back out of the creation response.
            test_cases=[_case_to_out(c) for c in visible],
        )
