"""ORM models package.

Every model module must be imported somewhere before ``Base.metadata``
is used, or its table is invisible to ``create_all()`` and to Alembic's
autogenerate. Alembic's ``env.py`` imports them explicitly; this module
is the single list so the test suite does not have to repeat it (and
cannot silently forget a table when a new model is added).

That last claim is now checked rather than merely asserted: ``user`` and
``assignment`` were missing from the list below for a while and nothing
noticed, because every entry point that touched ``Base.metadata`` happened
to import them for some other reason. ``tests/test_model_registry.py``
walks the package on disk and fails if this list falls behind again.
"""

from app.models.assignment import Assignment
from app.models.assistance_preference import AssistancePreference
from app.models.assistance_usage import AssistanceUsage
from app.models.base import Base
from app.models.classroom import ClassMember, Classroom
from app.models.knowledge_stats import KnowledgeStat, compute_knowledge_stats
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_TIME_LIMIT,
    DIFFICULTIES,
    Problem,
)
from app.models.problem_set import (
    VISIBILITIES,
    VISIBILITY_CLASS,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    ProblemSet,
    ProblemSetItem,
)
from app.models.review import ReviewRecord
from app.models.submission import STATUSES, Submission
from app.models.test_case import TestCase
from app.models.user import User

__all__ = [
    "Base",
    "DEFAULT_MEMORY_LIMIT",
    "DEFAULT_TIME_LIMIT",
    "DIFFICULTIES",
    "STATUSES",
    "Assignment",
    "AssistancePreference",
    "AssistanceUsage",
    "ClassMember",
    "Classroom",
    "KnowledgeStat",
    "MistakeBookEntry",
    "Problem",
    "ProblemSet",
    "ProblemSetItem",
    "ReviewRecord",
    "Submission",
    "TestCase",
    "User",
    "VISIBILITIES",
    "VISIBILITY_CLASS",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "compute_knowledge_stats",
]
