"""Style consistency columns on submissions (C2)

Revision ID: b9e0f31a6c42
Revises: a7e2b94c3d15
Create Date: 2026-09-27 22:20:00.000000

Adds the second reading of AI-use detection: how the *code itself*
compares against the student's own history for the same language
(:mod:`app.style_trace`).

Why two columns and why this shape
---------------------------------
C1 (``process_*``) is derived from the editor's behaviour trace and is
stored as one column per feature. C2 is different: its features are
three tiny statistics plus the words that explain them, and nothing
about it is queried except the boolean. So it gets a boolean column (the
teacher's queue filters on it, same as ``process_flagged``) and one JSON
column for the evidence — the feature numbers and the Chinese reasons,
which the review panel renders verbatim.

The review machinery (``process_review*``) is deliberately shared: a
review is a teacher's verdict on *this submission's* flag, whichever
reading raised it. Splitting reviews per reading would give one
submission two verdicts that could contradict each other.

``style_flagged`` defaults to false like ``process_flagged``; the same
``false()`` idiom is used, for the same reason (dialect-aware literal —
see the model's comment).
"""

import sqlalchemy as sa
from alembic import op

revision = "b9e0f31a6c42"
down_revision = "a7e2b94c3d15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("style_flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "submissions",
        sa.Column("style_evidence", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submissions", "style_evidence")
    op.drop_column("submissions", "style_flagged")
