"""assignments.source_set_id: provenance for problem-set publications

Revision ID: a4d81e2c6f90
Revises: c9a3e5f17b04
Create Date: 2026-09-25 21:40:00.000000

The ``Assignment`` model gained ``source_set_id`` (题单改造) but the live
database never received the column — dev-mode ``create_all`` only creates
missing *tables*, never missing columns — so every ``SELECT`` against
``assignments`` failed with ``UndefinedColumn`` and the student homework
list surfaced as a 500 (which the frontend renders as a connection-style
error when the response carries no usable detail).

Why nullable
------------
Assignments published before the field existed (or published from no set
at all) legitimately have no source; ``NULL`` means "not published from a
problem set". ``ondelete="SET NULL"`` because deleting a set must not
take the assignments that cite it down with it — same rule as
``class_id``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d81e2c6f90'
down_revision: Union[str, Sequence[str], None] = 'c9a3e5f17b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assignments",
        sa.Column(
            "source_set_id",
            sa.Integer(),
            sa.ForeignKey("problem_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_assignments_source_set_id", "assignments", ["source_set_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_assignments_source_set_id", table_name="assignments")
    op.drop_column("assignments", "source_set_id")
