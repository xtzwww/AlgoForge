"""Users, sessions, and the owner of a submission

Revision ID: a1f3c9e62b40
Revises: d5e3a1c7b942
Create Date: 2026-09-19 03:05:00.000000

Adds ``users`` and ``user_sessions``, and gives ``submissions`` a
``user_id``.

Why first, when the roadmap lists it later
------------------------------------------
The teacher console is entirely about other people's learning — class
mastery, who is struggling, which mistakes are common — and none of
those questions can be asked of a schema where every row belongs to one
unnamed person. The roadmap put the user system in a later phase because
it was written before the teacher side was designed; that ordering was
wrong and this revision corrects it.

Why ``submissions.user_id`` is nullable
---------------------------------------
Every row already in the table was written before accounts existed, so
there is no owner to put in it. The alternatives are both worse:
backfilling a synthetic "legacy" user invents a person who never
submitted anything, and refusing to record unowned submissions would
break the no-login path the desktop client uses today.

Unowned rows fall outside every student's view, which is the honest
answer — a teacher looking at 张三's history should not see a stranger's
attempts mixed in.

Sessions rather than signed tokens
----------------------------------
----------------------------------
Sessions are rows because a signed token needs a signing secret that
survives a restart, and the only configured secret in this system
(``SIDECAR_TOKEN``) is generated randomly on every start — so stateless
tokens would log every user out every time the sidecar restarted, in
the middle of a lesson. A row needs no secret, is revocable, and makes
the expiry a real column.

Backfill
--------
None. See nullability above: there is no owner to assign.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c9e62b40'
down_revision: Union[str, Sequence[str], None] = 'd5e3a1c7b942'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False,
                  server_default=""),
        sa.Column("role", sa.String(length=16), nullable=False,
                  server_default="student"),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("password_salt", sa.String(length=64), nullable=False),
        # server_default because the column is NOT NULL and the table is
        # not the only thing that writes these rows.
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        # No server_default: an expiry is not something a database should
        # be guessing at, and a default here would silently make every
        # session immortal if a caller forgot to set it.
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_sessions_token"), "user_sessions", ["token"],
                    unique=True)
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])

    # ``batch_alter_table``, matching 9f55b04626b7: SQLite cannot ALTER a
    # constraint onto an existing table, so the batch wrapper is what
    # lets this revision run on both dialects. The FK *is* declared —
    # a NULL owner does not violate it, so the unowned rows this
    # revision leaves behind are legal — and leaving it out would let
    # the migration and the model describe the same column differently,
    # which is the drift this codebase has already been bitten by.
    with op.batch_alter_table("submissions") as batch:
        batch.add_column(
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                      nullable=True)
        )
        batch.create_index(batch.f("ix_submissions_user_id"), ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_submissions_user_id"), table_name="submissions")
    op.drop_column("submissions", "user_id")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_token"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
