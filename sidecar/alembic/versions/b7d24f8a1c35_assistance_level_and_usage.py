"""Assistance level on assignments, and a table for its usage

Revision ID: b7d24f8a1c35
Revises: e3a71c58b2d4
Create Date: 2026-09-22 11:50:00.000000

Two changes, and they belong in one migration because they are one feature:

* ``assignments.assistance_level`` (1–5) — how much AI help the teacher
  permits while a student works on this assignment.
* ``assistance_usage`` — one row per AI action a student actually took.

Why the level lives on the assignment
-------------------------------------
The same problem is practice on Monday and an assessment on Friday. A
level stored on the problem would force the teacher to duplicate the
problem to change that; stored here, "no AI today" is a property of the
task they set, which is what they are actually deciding.

Why usage gets its own table rather than a counter column
---------------------------------------------------------
A single ``usage_count`` on the assignment could answer "how many times
was the AI used" and nothing else. The questions a teacher actually asks
are all of the shape "**who** used **what** on **which problem**" — the
per-student breakdown, and whether hints were used on the one problem
everybody failed. Those need rows. A counter would have to be replaced by
this table the first time somebody asked one of them, so it is not built.

``student_id`` and ``assignment_id`` are both nullable, deliberately:
this app allows solving while logged out, and free practice happens
outside any assignment. A NOT NULL column would silently discard exactly
the records that matter most — a log that refuses to record an action is
worse than no log, because the teacher reads its emptiness as "nothing
happened".

No foreign keys, matching the rest of the schema
------------------------------------------------
Other tables in this project store ids from related tables without FK
constraints (``submissions.problem_id`` is the same). Following the
existing convention rather than introducing a single constrained table:
mixing the two styles is how a later bulk delete starts failing in one
place and not another. An orphaned usage row is also harmless — it is a
historical record, not a reference that must resolve.

The server default on ``assistance_level`` is 3
-----------------------------------------------
Existing assignments predate the column, and 3 (hints-only) is the
middle of the scale. Defaulting to 1 would retroactively turn every past
assignment into a no-tools exam; defaulting to 5 would let the AI write
answers for work already handed out. The middle is the only default that
does not silently change what an old assignment meant.

Alembic, not a hand-written DDL — ``AGENTS.md`` §二.3.
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7d24f8a1c35'
down_revision: str | Sequence[str] | None = 'e3a71c58b2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'assignments',
        sa.Column(
            'assistance_level',
            sa.Integer(),
            nullable=False,
            server_default='3',
        ),
    )
    op.create_table(
        'assistance_usage',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=True),
        sa.Column('assignment_id', sa.Integer(), nullable=True),
        sa.Column('problem_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('assistance_usage')
    op.drop_column('assignments', 'assistance_level')
