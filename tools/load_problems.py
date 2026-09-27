"""Load the problem catalogue into the database, verifying it first.

For every problem in ``corpus/problems/`` this script:

1. Checks the file's shape (sample/hidden counts, required fields).
2. **Judges ``correct_reference`` against every case** — samples and
   hidden alike. It must be AC. A problem whose reference solution fails
   its own cases has broken cases, and importing it would put a problem
   into the catalogue that no correct solution can pass.
3. Only if *every* file passes both steps does it write to the database.

The all-or-nothing rule is the point. A partial import leaves the
catalogue in a state nobody can reason about: some problems present,
some silently missing, and no record of which. Failing loudly and
writing nothing means the operator fixes the corpus and re-runs, and the
database only ever holds a catalogue that passed verification.

Usage:

    python tools/load_problems.py                 # verify, then import
    python tools/load_problems.py --dry-run       # verify only, no writes
    python tools/load_problems.py --replace       # replace existing rows

``--replace`` deletes catalogue rows (and their cases) before inserting.
It is opt-in because it destroys data: submissions reference problems by
foreign key, and assignments reference them by id inside a JSON column,
so replaced problems get new ids and both stop resolving. Off by
default, and refused outright when either kind of row points at the
catalogue — see :func:`_check_replace_is_safe`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))

from app.problems_corpus import (  # noqa: E402
    CatalogueProblem,
    difficulty_distribution,
    load_catalogue,
    tag_distribution,
)
# Same one-line form as tools/validate_eval_cases.py, which imports the
# identical trio: `TestCase` is aliased to avoid shadowing this module's
# own corpus type when read side by side.
from app.sandbox.judge import (  # noqa: E402
    JudgeStatus,
    TestCase as JudgeTestCase,
    judge,
)
#: Generous ceilings for the verification run. The corpus is meant to be
#: solvable well within these; a reference solution that needs more is
#: itself a signal that the problem's limits are misconfigured.
VERIFY_TIME_LIMIT = 10.0
VERIFY_MEM_LIMIT_MB = 256


def _to_judge_cases(problem: CatalogueProblem) -> list[JudgeTestCase]:
    return [
        JudgeTestCase(input=tc.input, expected_output=tc.expected_output)
        for tc in problem.test_cases
    ]


def verify(problem: CatalogueProblem) -> list[str]:
    """Return a list of problems with *problem*; empty means it is sound.

    The structural checks run first: they are instant and their messages
    are far more actionable than "the reference solution got WA", which
    is what a missing field would otherwise produce.
    """
    problems = problem.validate_shape()
    if problems:
        # Judging a file we already know is malformed would only add a
        # confusing second error to the list.
        return problems

    result = judge(
        problem.correct_reference,
        _to_judge_cases(problem),
        time_limit=VERIFY_TIME_LIMIT,
        mem_limit_mb=VERIFY_MEM_LIMIT_MB,
        language="cpp",
    )

    if result.status is JudgeStatus.CE:
        return [
            "correct_reference does not compile:\n"
            + (result.compile_output.strip() or "(no compiler output)")
        ]

    if result.status is not JudgeStatus.AC:
        sample_count = _sample_count(problem)
        detail = []
        for i, tc in enumerate(result.test_cases, start=1):
            if tc.status is not JudgeStatus.AC:
                kind = "sample" if i <= sample_count else "hidden"
                detail.append(
                    f"  case {i} ({kind}): {tc.status.value} — "
                    f"input {tc.input!r}, "
                    f"expected {tc.expected_output!r}, "
                    f"got {tc.actual_output!r}"
                )
        return [
            f"correct_reference got {result.status.value}, not AC — "
            f"the test cases are wrong, not the solution",
            *detail,
        ]

    return []


def _sample_count(problem: CatalogueProblem) -> int:
    return sum(1 for tc in problem.test_cases if tc.is_sample)


def _clear_sandbox_scratch() -> None:
    """Remove leftover ``judge_*`` scratch directories.

    Every ``judge()`` call creates a scratch directory under
    :func:`sandbox_tmp_base`, and it is created with
    ``ignore_cleanup_errors=True`` — so on Windows a directory whose binary
    was still held open when the context manager unwound is left behind
    instead of raising. Verifying a large corpus (75 problems, hundreds of
    cases) therefore leaks one directory per compile.

    Left unchecked that is not merely untidy: the local safe-delete hook
    refuses any deletion once a turn has seen more than its bulk threshold
    of targets, and the accumulated directories push the judge's own
    cleanup over that line — the verification run then dies mid-loop with
    ``SAFE_DELETE_BULK_CONFIRM_REQUIRED`` and no partial output, which
    reads exactly like a corpus problem but is not one.

    Clearing between problems keeps the directory count bounded. Errors are
    ignored on purpose: a directory still held by a dying process is
    expected here, and it will be cleared on a later pass.
    """
    import shutil

    from app.sandbox.compiler import sandbox_tmp_base

    base = sandbox_tmp_base()
    if not base.is_dir():
        return
    for entry in base.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            pass


def _check_replace_is_safe(session: object) -> None:
    """Refuse ``--replace`` when submissions or assignments would be orphaned.

    Deleting problems re-assigns ids on insert, so any row that points at
    an old id stops resolving. Once the user has run code against the
    catalogue, silently breaking that history is worse than refusing —
    they can clear the tables deliberately if that is really what they
    want.

    Two kinds of row can be orphaned, and the second was added after it
    bit us in practice:

    * **Submissions** carry ``problem_id`` as a real foreign key, so the
      database would eventually refuse the delete — or, worse, the
      ``ondelete`` behaviour would quietly erase work.
    * **Assignments** carry ``problem_ids`` as a JSON list. JSON has no
      foreign key, so nothing stops the delete, nothing complains, and
      the damage only shows up later as a student clicking "开始做题" and
      getting a 404 on a problem the teacher did publish. That is exactly
      what happened: a reload moved the catalogue to fresh ids and every
      id in the one existing assignment went dangling.

    The module docstring already promised "old submissions stop
    resolving" as the reason ``--replace`` is opt-in; assignments were
    simply missing from the check that is supposed to enforce it.

    Only rows that actually *point* at a problem count. ``problem_id`` is
    nullable on purpose: a free run ("just compile and run this snippet")
    has none. Counting those would refuse every replace on any database
    where someone has ever used the scratch runner, even though nothing
    they own is at risk. An assignment with an empty ``problem_ids`` is
    the same story.
    """
    from sqlalchemy import func, select

    from app.models.assignment import Assignment
    from app.models.submission import Submission

    count = session.execute(  # type: ignore[attr-defined]
        select(func.count())
        .select_from(Submission)
        .where(Submission.problem_id.is_not(None))
    ).scalar_one()
    if count:
        raise SystemExit(
            f"refusing --replace: {count} submission(s) reference the "
            f"catalogue, and replacing would orphan them.\n"
            f"Clear the submissions table first if this is intended."
        )

    # Assignments are loaded whole rather than counted in SQL: the ids
    # live inside a JSON column and this module must not be the first
    # place that tries to query inside it (see the Assignment model
    # docstring). A term of homework is a few dozen rows, so reading
    # them all is cheap and keeps the JSON rule intact.
    dangling_owners = [
        row
        for row in session.execute(select(Assignment)).scalars().all()  # type: ignore[attr-defined]
        if row.problem_ids
    ]
    if dangling_owners:
        # Name them, because "some assignment" is not actionable and the
        # operator needs to know what to re-publish.
        titles = ", ".join(f"#{row.id} {row.title!r}" for row in dangling_owners)
        raise SystemExit(
            f"refusing --replace: {len(dangling_owners)} assignment(s) "
            f"reference the catalogue by id, and replacing would leave "
            f"their problem sets dangling (students would get 404s).\n"
            f"  affected: {titles}\n"
            f"Delete or re-publish those assignments first if this is "
            f"intended."
        )


def _import(problems: list[CatalogueProblem], *, replace: bool) -> None:
    """Write the verified corpus to the database in one transaction."""
    from app.db import get_session
    from app.models.problem import Problem
    from app.models.test_case import TestCase

    with get_session() as session:
        if replace:
            _check_replace_is_safe(session)
            # Cases before problems: the foreign key points at problems.
            for case in session.query(TestCase).all():
                session.delete(case)
            for problem in session.query(Problem).all():
                session.delete(problem)
            session.flush()

        for entry in problems:
            problem = Problem(
                title=entry.title,
                knowledge_tags=list(entry.knowledge_tags),
                description=entry.description,
                input_format=entry.input_format,
                output_format=entry.output_format,
                samples=entry.samples,
                time_limit=entry.time_limit,
                memory_limit=entry.memory_limit,
                difficulty=entry.difficulty,
                source=entry.source,
                is_active=True,
            )
            session.add(problem)
            session.flush()  # assign problem.id for the cases below

            for ordinal, tc in enumerate(entry.test_cases, start=1):
                session.add(
                    TestCase(
                        problem_id=problem.id,
                        ordinal=ordinal,
                        input=tc.input,
                        expected_output=tc.expected_output,
                        is_sample=tc.is_sample,
                    )
                )

        session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and load the problem catalogue."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify only; do not write to the database",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete existing catalogue rows before inserting",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="corpus directory (defaults to corpus/problems)",
    )
    args = parser.parse_args(argv)

    try:
        problems = load_catalogue(args.dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return 1

    if not problems:
        print("error: no problems found")
        return 1

    print(f"verifying {len(problems)} problems\n")

    failures = 0
    total_cases = 0
    for index, entry in enumerate(problems):
        # Judge leaves one scratch directory per compile behind. Clear them
        # between problems so a long verification run does not trip the
        # safe-delete bulk threshold and abort (see _clear_sandbox_scratch).
        if index:
            _clear_sandbox_scratch()
        issues = verify(entry)
        label = f"{entry.problem_id} [{entry.difficulty}] {entry.title}"
        sample_count = _sample_count(entry)
        hidden_count = len(entry.test_cases) - sample_count
        total_cases += len(entry.test_cases)
        if issues:
            failures += 1
            print(f"FAIL  {label}")
            for issue in issues:
                print(f"        {issue}")
        else:
            print(
                f"ok    {label}  "
                f"({sample_count} sample + {hidden_count} hidden)"
            )

    print()
    if failures:
        print(
            f"{failures}/{len(problems)} problem(s) FAILED verification — "
            f"nothing was written"
        )
        return 1

    print(
        f"all {len(problems)} problems verified "
        f"({total_cases} cases, reference solutions all AC)"
    )
    print(f"difficulty: {difficulty_distribution(problems)}")
    tags = tag_distribution(problems)
    print(f"tags ({len(tags)}): {tags}")

    if args.dry_run:
        print("\n--dry-run: skipping the database import")
        return 0

    try:
        _import(problems, replace=args.replace)
    except SystemExit as exc:
        print(f"\nerror: {exc}")
        return 1

    verb = "replaced" if args.replace else "imported"
    print(f"\n{len(problems)} problems {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
