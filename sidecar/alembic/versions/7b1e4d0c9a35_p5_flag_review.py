"""A teacher's verdict on a process flag

Revision ID: 7b1e4d0c9a35
Revises: a1f3c9e62b40
Create Date: 2026-09-19 05:10:00.000000

Adds ``submissions.process_review`` / ``process_review_by`` /
``process_review_at``.

Why the flag is not overwritten
-------------------------------
The obvious design is one column — the flag becomes ``false`` when a
teacher disagrees with it. That loses the thing the flag was for. The
flag is a measurement of how the code was produced; the review is a
person's judgement about that measurement. Collapsing them into one
column makes the question "how often is the rule wrong?" unanswerable,
and that question is the only way the thresholds in
:mod:`app.process_trace` ever get improved with real data instead of
guesswork.

So the row keeps its flag and its ten numbers, and gains its verdict.
A dismissed submission leaves the teacher's queue without having its
evidence rewritten.

Why three values and not a boolean
----------------------------------
``NULL`` / ``confirmed`` / ``dismissed``. A boolean would force
"not reviewed" to share a value with one of the two judgements, and the
queue — "show me the ones nobody has ruled on" — cannot be expressed
against a column that cannot say "nobody has ruled".

``cleared`` in the API un-sets the column rather than storing a fourth
value; see :meth:`app.models.submission.Submission.apply_review`.

Backfill
--------
None needed. Every existing row is simply unreviewed, which is true.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b1e4d0c9a35'
down_revision: Union[str, Sequence[str], None] = 'a1f3c9e62b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ``batch_alter_table`` for the same reason as a1f3c9e62b40: SQLite
    # cannot ALTER a foreign key onto a live table.
    with op.batch_alter_table("submissions") as batch:
        batch.add_column(
            sa.Column("process_review", sa.String(length=16), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "process_review_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("process_review_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("submissions") as batch:
        batch.drop_column("process_review_at")
        batch.drop_column("process_review_by")
        batch.drop_column("process_review")
