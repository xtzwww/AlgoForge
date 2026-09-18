"""Homework: a set of problems with a deadline (阶段 4)

Revision ID: f2c8b61d40a7
Revises: 7b1e4d0c9a35
Create Date: 2026-09-19 05:40:00.000000

Adds ``assignments``.

Why there is no per-student state table
---------------------------------------
"Who has done this" is derivable: the roster minus the people who have a
submission on one of the problem ids. Storing it would mean a row per
student per assignment that has to be written by every submission path
— the judge REST route, the WebSocket route, and the direct recorder —
and one path that forgets produces a student marked as absent for work
they did. Subtraction cannot get out of step.

Why ``problem_ids`` is JSON
---------------------------
Nothing queries inside it; the column is always read whole. See the
docstring in :mod:`app.models.assignment` — the day a query needs to
filter on a single problem id, this needs a child table.

``due_at`` is nullable
----------------------
An assignment with no deadline is a real thing, and a far-future
sentinel would have to be compared against somewhere.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8b61d40a7'
down_revision: Union[str, Sequence[str], None] = '7b1e4d0c9a35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("problem_ids", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("assignments")
