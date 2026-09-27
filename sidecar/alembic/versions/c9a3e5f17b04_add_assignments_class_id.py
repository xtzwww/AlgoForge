"""assignments.class_id: tie homework to a class (classrooms 关联)

Revision ID: c9a3e5f17b04
Revises: 2c32d0f1ae9b
Create Date: 2026-09-25 18:50:00.000000

Why nullable
------------
Pre-class assignments (set for nobody in particular) are a real thing:
``class_id IS NULL`` means "for everyone", so the column must be nullable
or the first assignment created before any classroom exists would fail.

Why the dev DB never had it
---------------------------
``app.models.assignment.Assignment`` has carried ``class_id`` since the
classroom feature landed, but the column was never added to the live
database (dev-mode ``create_all`` only creates missing *tables*, never
missing columns), so ``SELECT ... FROM assignments`` failed with
``UndefinedColumn: column assignments.class_id does not exist`` and the
teacher console's homework block surfaced it as a CORS-flavoured
"Network Error" (a 500 response carries no CORS header).

On this deployment the intermediate migrations between
``b7d24f8a1c35`` and ``2c32d0f1ae9b`` had already been applied through
``create_all``, so the version table was stamped to ``2c32d0f1ae9b``
before this file ran; only this column is missing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9a3e5f17b04'
down_revision: Union[str, Sequence[str], None] = '2c32d0f1ae9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assignments",
        sa.Column(
            "class_id",
            sa.Integer(),
            sa.ForeignKey("classrooms.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_assignments_class_id", "assignments", ["class_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_assignments_class_id", table_name="assignments")
    op.drop_column("assignments", "class_id")
