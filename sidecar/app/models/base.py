"""SQLAlchemy declarative base for all AlgoForge ORM models.

P1-17 introduces the models layer. The base class is defined here so
every model file imports from a single place, keeping metadata
centralized for ``Base.metadata.create_all()`` (used in tests) and
for Alembic migrations (P1-10, when wired up).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common declarative base for all ORM models in the project."""
