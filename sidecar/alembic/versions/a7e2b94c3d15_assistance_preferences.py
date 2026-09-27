"""assistance_preferences: a student's own level, under the teacher's ceiling

Revision ID: a7e2b94c3d15
Revises: f1c0d5a7e932
Create Date: 2026-09-26 19:55:00.000000

Why a new table rather than reusing ``assignments.assistance_level``
--------------------------------------------------------------------
``assignments.assistance_level`` is the teacher's **ceiling** — how much help
this piece of work permits, which is a property of the assessment. What this
table holds is a different thing: how much help the student **wants**, which
is a property of the person. The same assignment legitimately has one value
for each, and the effective level is ``min(teacher, student)``.

Conflating them would mean a student could raise the level by editing an
assignment, or that lowering it for one assignment silently changed it for
everyone in the class. Both are wrong, so the column is not reused.

Why the level is not stored pre-clamped
---------------------------------------
The row stores what the student asked for. If the teacher later *lowers* the
ceiling, the stored value stays put and the effective level drops — and if
the teacher raises it again, the student's earlier choice comes back without
them having to re-state it. Storing the already-clamped value would freeze
the student's intent at whatever the ceiling happened to be that day.

Uniqueness
----------
One row per (student, assignment). Two rows would make the effective level
depend on row order, a bug that only appears under concurrency and is
therefore worth preventing in the schema rather than in the query.

``assignment_id`` is nullable, matching the usage log: free practice happens
outside any assignment, and forcing a value would invent a fake "default
assignment" that then has to be filtered out everywhere. Note that PostgreSQL
treats ``NULL`` as distinct in a unique constraint, so the "one row per
free-practice preference" rule is enforced by the query layer for the NULL
case — stated here because the constraint alone does not cover it.
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7e2b94c3d15'
down_revision: str | Sequence[str] | None = 'f1c0d5a7e932'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'assistance_preferences',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'student_id',
            'assignment_id',
            name='uq_assistance_pref_student_assignment',
        ),
    )
    # The read path is always "this student's preference", so the index
    # follows the query rather than the key order.
    op.create_index(
        'ix_assistance_preferences_student_id',
        'assistance_preferences',
        ['student_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_assistance_preferences_student_id', table_name='assistance_preferences')
    op.drop_table('assistance_preferences')
