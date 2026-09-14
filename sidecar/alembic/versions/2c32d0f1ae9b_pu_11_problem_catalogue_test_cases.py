"""PU-11 problem catalogue + test cases

Revision ID: 2c32d0f1ae9b
Revises: 7298601ba4c7
Create Date: 2026-09-14 20:12:16.429216

Extends ``problems`` with the statement, display samples and judge limits
that P1-8 reserved, and adds ``test_cases`` — the server-side case store
that makes hidden test cases possible.

Why this file is not a verbatim autogenerate output
---------------------------------------------------
``alembic revision --autogenerate`` emitted ``add_column(..., nullable=False)``
for ``description``, ``samples``, ``time_limit``, ``memory_limit`` and
``is_active``. That is only valid against an **empty** table. PostgreSQL
rejects ``ALTER TABLE ... ADD COLUMN ... NOT NULL`` without a default when
rows already exist, and a development database almost certainly has rows:
``submissions`` and ``mistake_book`` reference ``problems``, so anyone who
has practiced once has a populated table. Migrating such a database would
have aborted partway through.

Each new NOT NULL column is therefore added in three steps:

1. add it nullable,
2. ``UPDATE`` existing rows to the model's default,
3. enforce NOT NULL.

The defaults are stated literally (``0`` / ``[]`` / ``10.0`` / ``256`` /
``true``) rather than imported from ``app.models.problem``, because a
migration must describe the schema *at this revision* — importing the
model would make this file's meaning change every time the model does.

Why step 3 goes through ``batch_alter_table``
---------------------------------------------
``op.alter_column(..., nullable=False)`` renders
``ALTER TABLE problems ALTER COLUMN description SET NOT NULL``, which is
PostgreSQL syntax. SQLite has no ``ALTER COLUMN`` at all and fails with
``near "ALTER": syntax error``. ``batch_alter_table`` is Alembic's
dialect-aware path: on PostgreSQL it emits the same ``ALTER COLUMN``, and
on SQLite it recreates the table with the new definition and copies the
rows across. Using it means one migration file works on both, which
matters because the test suite runs SQLite while the documented
production target is PostgreSQL.

Server defaults are kept on the boolean columns (``false`` / ``true``) so
that a row inserted by something that bypasses the ORM still gets a
sensible value; the non-boolean columns rely on the ORM/loader defaults,
matching how ``problems.knowledge_tags`` already behaves.

Cross-dialect note
------------------
``sa.JSON()`` renders as ``JSON`` on PostgreSQL and SQLite but as
``LONGTEXT`` on MySQL. The project targets PostgreSQL only (see
AGENTS.md), so the portable ``sa.JSON()`` is correct here.

Known limitation
----------------
Per docs/PHASE-UX.md PU-11 this migration is required to be verified on a
real PostgreSQL instance, because JSON column DDL can differ from
SQLite's. On the development machine PostgreSQL was not running (nothing
listening on 5432), so verification covered SQLite only: upgrade over a
populated ``problems`` table, downgrade, and upgrade again. Running
``alembic upgrade head`` once against PostgreSQL is still owed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c32d0f1ae9b'
down_revision: Union[str, Sequence[str], None] = '7298601ba4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Columns that must end up NOT NULL, with the literal used to backfill
#: any pre-existing row. Driving the loop from data keeps the three steps
#: per column in one place instead of nine near-identical code blocks.
_NOT_NULL_BACKFILL: tuple[tuple[str, sa.types.TypeEngine, str], ...] = (
    ("description", sa.Text(), "''"),
    ("samples", sa.JSON(), "'[]'"),
    ("time_limit", sa.Float(), "10.0"),
    ("memory_limit", sa.Integer(), "256"),
    ("is_active", sa.Boolean(), "true"),
)


def upgrade() -> None:
    """Upgrade schema."""
    # --- test_cases: a brand-new table, so NOT NULL is safe here ----------
    op.create_table(
        'test_cases',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('input', sa.Text(), nullable=False),
        sa.Column('expected_output', sa.Text(), nullable=False),
        sa.Column('is_sample', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('problem_id', 'ordinal', name='uq_test_cases_problem_ordinal'),
    )
    op.create_index(
        op.f('ix_test_cases_problem_id'), 'test_cases', ['problem_id'], unique=False
    )

    # --- problems: backfill-then-constrain for every NOT NULL column ------
    # ``is_active`` is the one column whose server_default is set here as
    # well: it is kept afterwards so the default survives the migration,
    # whereas the other four rely on the ORM.
    for name, coltype, backfill in _NOT_NULL_BACKFILL:
        server_default = sa.true() if name == "is_active" else None
        op.add_column(
            'problems',
            sa.Column(name, coltype, server_default=server_default, nullable=True),
        )
        op.execute(f"UPDATE problems SET {name} = {backfill} WHERE {name} IS NULL")
        with op.batch_alter_table('problems') as batch:
            batch.alter_column(name, nullable=False)

    # --- problems: genuinely optional columns ------------------------------
    # Nullable on purpose: "unclassified" and "no known source" are real
    # states, and backfilling them with a guess would be a lie.
    op.add_column('problems', sa.Column('input_format', sa.Text(), nullable=True))
    op.add_column('problems', sa.Column('output_format', sa.Text(), nullable=True))
    op.add_column('problems', sa.Column('difficulty', sa.String(length=16), nullable=True))
    op.add_column('problems', sa.Column('source', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the columns discards the statements and cases, which is
    # inherently irreversible — the data has nowhere else to live. That is
    # acceptable for a generated catalogue that can be re-imported from
    # corpus/problems/ once PU-14 lands, but not for anything hand-edited
    # through the API. Take a database backup before downgrading.
    op.drop_column('problems', 'source')
    op.drop_column('problems', 'difficulty')
    op.drop_column('problems', 'output_format')
    op.drop_column('problems', 'input_format')
    op.drop_column('problems', 'is_active')
    op.drop_column('problems', 'memory_limit')
    op.drop_column('problems', 'time_limit')
    op.drop_column('problems', 'samples')
    op.drop_column('problems', 'description')
    op.drop_index(op.f('ix_test_cases_problem_id'), table_name='test_cases')
    op.drop_table('test_cases')
