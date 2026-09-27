"""Merge the two migration branches back into one line

Revision ID: f1c0d5a7e932
Revises: a4d81e2c6f90, b7d24f8a1c35
Create Date: 2026-09-26 19:40:00.000000

Why this exists
---------------
The migration graph had drifted into **two unmerged heads**::

    base -> 24a8e7bdf81d -> 7298601ba4c7 -> 2c32d0f1ae9b   (branchpoint)
                                                |
                            +-------------------+-------------------+
                            |                                       |
                    9f55b04626b7 -> ... -> e3a71c58b2d4     c9a3e5f17b04
                            |                    |                  |
                            |                    v                  v
                            |            b7d24f8a1c35       a4d81e2c6f90
                            |              (head)                (head)
                            +-------------------+
                            (branchpoint continues left, into the
                             9f55 -> ... -> e3a71 chain)

Both branches had already been applied to the live database, because this
project ran against a dev-mode database where the ORM's ``create_all``
covers missing **tables** (it never covers missing **columns**). So the
schema was correct and only the bookkeeping was ambiguous: ``alembic
upgrade head`` cannot resolve when ``head`` names two revisions, and any new
migration has to pick a parent — silently orphaning the branch it did not
pick.

This revision does nothing to the schema. It exists purely to give the two
lines a single convergence point, so ``head`` means one thing again.

Why not re-parent one of the branches instead
---------------------------------------------
Re-parenting is what created the earlier confusion: a migration that has
already run carries a recorded id, and changing its ``down_revision``
rewrites history that the ``alembic_version`` row can no longer corroborate.
A merge revision is the mechanism Alembic provides for exactly this
situation — it is additive, it does not alter any applied migration, and
``downgrade`` can simply split back.

The empty ``upgrade`` is deliberate and is the documented Alembic idiom for
a merge point; it is not an accidentally forgotten implementation.
"""
from typing import Sequence

# revision identifiers, used by Alembic.
revision: str = 'f1c0d5a7e932'
down_revision: str | Sequence[str] | None = ('a4d81e2c6f90', 'b7d24f8a1c35')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge point — no schema change.

    Both branches are already applied; there is nothing left to do. This
    function is intentionally empty so that ``upgrade head`` on a database
    at either old head simply records convergence.
    """


def downgrade() -> None:
    """Split back into the two branches.

    Also a no-op for the same reason: nothing was changed, so nothing is
    undone. Dropping rows from ``alembic_version`` is Alembic's job when it
    walks back past a merge point.
    """
