"""Analysis status on the mistake book

Revision ID: c4d17a9e3b28
Revises: 8b41d6e07c52
Create Date: 2026-09-18 16:40:00.000000

Adds ``analysis_status``, which answers a question the schema could not
answer before: *why* an entry has no analysis.

Until now the only signal was ``agent_error_type IS NULL``, and that
collapses two very different situations — the analysis has not run yet,
and the analysis ran and failed. P2-4's failure path only wrote a log
line (see ``app/records.py``), so a network blip left an entry looking
exactly like one that had never been analysed: nothing to show in the
UI, no reason for the user to press anything, and no way to tell the
cases apart afterwards.

Values, and why three rather than two
-------------------------------------
* ``pending`` — no analysis has landed. Covers both "queued" and "never
  attempted", which is honest: from the user's side those are the same
  state (no result, and retrying is the reasonable next action).
* ``done`` — an analysis landed, so ``agent_error_type`` is set.
* ``failed`` — an analysis ran and produced nothing. This is the state
  the retry endpoint exists for, and the one that used to be
  indistinguishable from ``pending``.

A fourth value (``running``) was considered and rejected. The analysis
runs in a background task, so the transition would have to be written by
whoever schedules it, and a crash between the two writes would leave
rows stuck in ``running`` forever with nothing able to notice.
``pending`` plus a retry button degrades better: a stuck entry is
retryable, while a stuck ``running`` entry looks like work in progress
and is not.

Backfill
--------
Existing rows are not all ``pending``. ``agent_error_type IS NOT NULL``
is proof an analysis landed, so those become ``done``. Everything else
becomes ``pending`` — including rows whose analysis failed silently
before this migration existed. That is deliberate, and it is the point:
they *are* retryable now, whereas marking them ``failed`` would mean
inventing a fact the old schema never recorded.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d17a9e3b28'
down_revision: Union[str, Sequence[str], None] = '8b41d6e07c52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required rather than cosmetic, same reason as in
    # 8b41d6e07c52: the table already has rows, and a NOT NULL column
    # added without a default is rejected outright on SQLite and would
    # need a two-step backfill on Postgres. The model's Python-side
    # `default=` does not help existing rows at all.
    op.add_column(
        'mistake_book',
        sa.Column(
            'analysis_status',
            sa.String(length=16),
            nullable=False,
            server_default='pending',
        ),
    )
    # Backfilled here rather than left to a separate script: the value is
    # derivable from a column sitting in the same row, and a second step
    # is one more thing that can be forgotten on deploy.
    op.execute(
        "UPDATE mistake_book SET analysis_status = 'done' "
        "WHERE agent_error_type IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mistake_book', 'analysis_status')
