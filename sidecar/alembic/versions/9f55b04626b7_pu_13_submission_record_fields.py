"""PU-13 submission record fields

Revision ID: 9f55b04626b7
Revises: 2c32d0f1ae9b
Create Date: 2026-09-14 21:14:08.040730

Fills in the fields P1-9 reserved on ``submissions`` — the code, the
language it was judged as, the first failing case's output, and the
measured time and memory — so judging can finally write a row. Until
this revision the table had no writer, while
``mistake_book.submission_id`` required a row that had to already exist.

Note:
    ``exec_time`` and ``memory_used`` stay nullable and get **no**
    backfill. A CE submission runs no case, so there is no execution to
    measure, and defaulting to 0.0 / 0 would state "ran instantly using
    no memory" — a different claim from "not measured".

Two deviations from what ``--autogenerate`` produced, both required for
the migration to run against a database that already has rows:

1. ``code`` / ``stdout`` / ``stderr`` are added in three steps
   (nullable add → UPDATE backfill → SET NOT NULL), because PostgreSQL
   rejects ``ADD COLUMN ... NOT NULL`` with no default on a populated
   table. ``submissions`` is populated in any existing deployment.
2. The ``problem_id`` loosening and the ``SET NOT NULL`` calls go
   through ``batch_alter_table`` so the same revision works on SQLite,
   which has no ``ALTER COLUMN``.

Known limitation: verified against SQLite only. No PostgreSQL instance
was available in this environment, so the real-server check that
this design calls for is still outstanding.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f55b04626b7'
down_revision: Union[str, Sequence[str], None] = '2c32d0f1ae9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Columns to add as NOT NULL, with the literal used to backfill existing
#: rows. The literals are written out rather than imported from the model
#: so this file keeps describing the schema as of this revision even if
#: the model's defaults change later.
_NOT_NULL_BACKFILL: tuple[tuple[str, sa.types.TypeEngine, str], ...] = (
    ("code", sa.Text(), "''"),
    ("stdout", sa.Text(), "''"),
    ("stderr", sa.Text(), "''"),
)


def upgrade() -> None:
    """Upgrade schema."""
    # ``language`` is the one NOT NULL column that needs a real default:
    # every pre-existing row was judged before multi-language support, so
    # C++ is the accurate value, not just a convenient one. The server
    # default also covers inserts that omit the column.
    op.add_column(
        'submissions',
        sa.Column('language', sa.String(length=32), server_default='cpp', nullable=False),
    )

    for name, coltype, backfill in _NOT_NULL_BACKFILL:
        op.add_column('submissions', sa.Column(name, coltype, nullable=True))
        op.execute(f"UPDATE submissions SET {name} = {backfill} WHERE {name} IS NULL")
        with op.batch_alter_table('submissions') as batch:
            batch.alter_column(name, nullable=False)

    # Nullable with no backfill: see the module docstring.
    op.add_column('submissions', sa.Column('exec_time', sa.Float(), nullable=True))
    op.add_column('submissions', sa.Column('memory_used', sa.Integer(), nullable=True))

    # Free runs have no problem, so the column has to accept NULL now.
    # batch_alter_table is what lets SQLite rebuild the table instead of
    # being handed the ALTER COLUMN it does not understand.
    with op.batch_alter_table('submissions') as batch:
        batch.alter_column(
            'problem_id', existing_type=sa.INTEGER(), nullable=True
        )

    op.create_index(
        op.f('ix_submissions_problem_id'), 'submissions', ['problem_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_submissions_problem_id'), table_name='submissions')

    # Back to NOT NULL. Free-run rows would violate this, so they are
    # removed first: the alternative is a downgrade that fails on data
    # the schema can no longer represent, which is worse than dropping
    # rows the up-migration deliberately made possible.
    op.execute("DELETE FROM submissions WHERE problem_id IS NULL")
    with op.batch_alter_table('submissions') as batch:
        batch.alter_column(
            'problem_id', existing_type=sa.INTEGER(), nullable=False
        )

    op.drop_column('submissions', 'memory_used')
    op.drop_column('submissions', 'exec_time')
    op.drop_column('submissions', 'stderr')
    op.drop_column('submissions', 'stdout')
    op.drop_column('submissions', 'language')
    op.drop_column('submissions', 'code')
