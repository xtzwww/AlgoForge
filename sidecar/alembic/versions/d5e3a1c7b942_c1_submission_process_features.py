"""Process features on submissions

Revision ID: d5e3a1c7b942
Revises: c4d17a9e3b28
Create Date: 2026-09-19 02:20:00.000000

Adds the ten ``process_*`` columns that record *how* a submission's code
came to be, derived from the editor's behaviour trace by
:mod:`app.process_trace`.

Why this migration exists at all
--------------------------------
The project's title promises learning-**process** analysis, and until now
the schema could only describe the *result* of a submission. Everything
that happened before the submit button was pressed was never written
down, so the system could not tell apart two students who hand in the
same bytes but got there completely differently. These columns are what
makes the title true of the data, not just of the prose.

Why ten columns and not one JSON document
-----------------------------------------
The teacher side has to *filter* on these — "which submissions need a
look" is ``WHERE process_flagged``. Filtering inside a JSON document
needs a per-dialect expansion, and that is precisely the bug class
``app/models/tag_values.py`` was written to contain: it was got wrong
twice, in two places, and both times every test passed because the suite
runs on SQLite while production is PostgreSQL. Plain columns cannot have
that failure mode at all.

The words shown to a student or teacher are **not** stored. They are
derived at read time by ``describe()`` from these numbers, so a
threshold changed later re-explains every historical row rather than
leaving old rows speaking the old rule.

Nullability
-----------
All ten measurements are nullable because "no trace was supplied" is a normal
state, not an error: the REST judge route, an older client, and any
submission made outside the editor have no editing session to describe.
``process_flagged`` is the exception — ``NOT NULL`` with a server
default, because "not flagged" and "unknown" are different claims and a
teacher's query must not have to spell out ``OR process_flagged IS
NULL`` to get the right answer.

Backfill
--------
There is none, and there must not be. Every existing row predates the
editor's instrumentation, so it has no trace; the honest value for all
ten nullable columns is ``NULL`` ("not observed"), not ``0`` ("observed
and unremarkable"). Backfilling zeros would invent a measurement that
never happened and make old submissions look like clean ones.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e3a1c7b942'
down_revision: Union[str, Sequence[str], None] = 'c4d17a9e3b28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (column name, type) for the nine nullable measurements. Adding them in
#: a loop rather than nine near-identical ``add_column`` calls keeps the
#: list readable — and this list *is* the documentation of what is
#: recorded, so it wants to be readable.
_MEASUREMENTS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("process_duration_ms", sa.Integer()),
    ("process_edit_count", sa.Integer()),
    ("process_paste_count", sa.Integer()),
    ("process_pasted_chars", sa.Integer()),
    ("process_final_length", sa.Integer()),
    ("process_paste_ratio", sa.Float()),
    ("process_post_paste_edits", sa.Integer()),
    ("process_post_paste_chars", sa.Integer()),
    ("process_rewrite_ratio", sa.Float()),
    ("process_runs_before", sa.Integer()),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, type_ in _MEASUREMENTS:
        op.add_column("submissions", sa.Column(name, type_, nullable=True))

    # server_default is required, not cosmetic: the table already has
    # rows, and a NOT NULL column added without a default is rejected
    # outright on SQLite and needs a two-step backfill on PostgreSQL. The
    # model's Python-side ``default=`` cannot help rows that already
    # exist.
    #
    # ``sa.false()``, not ``sa.func.false()`` — the latter emits a call
    # to a function named ``false``, which PostgreSQL has and SQLite does
    # not, so it would work in production and fail in every test.
    op.add_column(
        "submissions",
        sa.Column(
            "process_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        op.f("ix_submissions_process_flagged"), "submissions", ["process_flagged"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_submissions_process_flagged"), table_name="submissions")
    op.drop_column("submissions", "process_flagged")
    for name, _ in reversed(_MEASUREMENTS):
        op.drop_column("submissions", name)
