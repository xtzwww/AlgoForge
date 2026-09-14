"""ORM models package.

Every model module must be imported somewhere before ``Base.metadata``
is used, or its table is invisible to ``create_all()`` and to Alembic's
autogenerate. Alembic's ``env.py`` imports them explicitly; this module
is the single list so the test suite does not have to repeat it (and
cannot silently forget a table when a new model is added).
"""

from app.models.base import Base
from app.models.knowledge_stats import KnowledgeStat, compute_knowledge_stats
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_TIME_LIMIT,
    DIFFICULTIES,
    Problem,
)
from app.models.review import ReviewRecord
from app.models.submission import STATUSES, Submission
from app.models.test_case import TestCase

__all__ = [
    "Base",
    "DEFAULT_MEMORY_LIMIT",
    "DEFAULT_TIME_LIMIT",
    "DIFFICULTIES",
    "STATUSES",
    "KnowledgeStat",
    "MistakeBookEntry",
    "Problem",
    "ReviewRecord",
    "Submission",
    "TestCase",
    "compute_knowledge_stats",
]
