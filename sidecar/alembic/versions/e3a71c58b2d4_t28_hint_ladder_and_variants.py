"""S7's hint ladder and variant problem get somewhere to live

Revision ID: e3a71c58b2d4
Revises: f2c8b61d40a7
Create Date: 2026-09-21 12:10:00.000000

Adds two nullable JSON columns to ``mistake_book``:

* ``agent_hint_ladder`` — S7's 提示 agent output: three graded rungs plus
  the self-check questions.
* ``agent_generated_variants`` — S7's 变式题 agent output.

Why this migration exists at all
--------------------------------
``ANALYSIS_PIPELINE=team`` runs three agents and costs roughly three times
what ``direct`` costs. Until now only the diagnosis survived the trip:
``AnalysisOutcome`` had no field for the other two and the mistake book
had no column for them, so both were written to a log line and dropped.
That is the one place in the project where money was demonstrably spent
and nothing was kept, so the columns are the point of the change, not an
implementation detail of it.

Nullable, with no backfill, and that is honest
----------------------------------------------
There is nothing to backfill. Every existing row was written by a pipeline
that could not produce these values — either ``direct`` (no such agents)
or ``team`` (the values were discarded before the row was touched). NULL
therefore means "this analysis did not produce one", which is also what it
will mean for every future ``direct`` row.

Why a list for the variants
---------------------------
The column name was already promised by ``app/models/mistake_book.py``
("``agent_generated_variants``: optional variant problems (S7)"), and the
plural is the shape that does not need a second migration if a later phase
asks for more than one practice problem per mistake. Today's team produces
exactly one, so the array holds 0 or 1 elements; the API serves it as a
list either way.

Both columns are JSON rather than Text-with-JSON-inside so that the
dialect is the one doing the encoding, matching ``knowledge_tags``. No
index is created: neither column is filtered or sorted on, they are read
whole by the mistake-book detail view.

Alembic, not a hand-written ALTER TABLE — ``AGENTS.md`` §二.3.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a71c58b2d4'
down_revision: Union[str, Sequence[str], None] = 'f2c8b61d40a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'mistake_book',
        sa.Column('agent_hint_ladder', sa.JSON(), nullable=True),
    )
    op.add_column(
        'mistake_book',
        sa.Column('agent_generated_variants', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mistake_book', 'agent_generated_variants')
    op.drop_column('mistake_book', 'agent_hint_ladder')
