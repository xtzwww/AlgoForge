"""Expand ``problems.knowledge_tags`` into one row per tag, per dialect.

Why this module exists
----------------------
``knowledge_tags`` is a JSON array column, and two features need to see it
one-element-per-row: the profile page's per-tag accuracy aggregation
(:mod:`app.models.knowledge_stats`) and the problem catalogue's tag filter
(:mod:`app.api.problems`).

SQLite and PostgreSQL spell that differently, and **there is no portable
spelling**:

==========  =================================
SQLite      ``json_each(col)``
PostgreSQL  ``json_array_elements_text(col)``
==========  =================================

PostgreSQL has no ``json_each`` at all (it is SQLite's name), and SQLite
has no ``json_array_elements_text``. Both return a single column named
``value``, which is what lets everything around this expression stay
identical — including the comma-join form, which is valid on both:
PostgreSQL documents that ``LATERAL`` before a function-call FROM item is
"a noise word, because the function expression can refer to earlier FROM
items in any case".

Why it is a shared helper rather than a local decision
------------------------------------------------------
This was got wrong **twice, in exactly the same way**. Both call sites
used ``func.json_each(...)`` unconditionally and both carried a comment
asserting that PostgreSQL supported it too. Every test in the suite runs
on SQLite (``conftest`` monkeypatches ``get_session``), so both spellings
worked everywhere they were ever executed, and neither had ever been run
against the database ``AGENTS.md`` actually mandates. The failure would
have been a hard ``function json_each(json) does not exist`` — on the
profile page and on the problem list's tag filter, i.e. on two of the
pages a demonstration shows.

One helper means a third call site cannot repeat it, and
``tests/test_dialect_portability.py`` pins the rendered SQL for both
dialects so the mistake cannot come back silently.

What is *not* solved here
-------------------------
The PostgreSQL branch is still never **executed** by the test suite; only
its SQL is compiled and checked. Running it for real needs a PostgreSQL
server, which this machine does not have. The structural fix is a CI job
backed by a real server.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.problem import Problem

#: Dialects this module knows how to spell the expansion for.
_SQLITE = "sqlite"
_POSTGRESQL = "postgresql"


def knowledge_tag_values(session: Session):
    """A table-valued expression yielding one ``value`` row per tag.

    Args:
        session: The session the expression will be executed through. Its
            dialect decides the spelling — the expression itself is
            dialect-specific, so this cannot be decided at import time.

    Returns:
        A SQLAlchemy table-valued construct with a single ``value``
        column, suitable for ``select(...)``, ``group_by`` and a
        membership comparison.

    Raises:
        NotImplementedError: The session's dialect is neither SQLite nor
            PostgreSQL. Raised deliberately instead of defaulting to one
            spelling: silently emitting ``json_each`` for an unknown
            dialect is precisely the bug this module was created to fix,
            and it fails at the server with a message that names a
            function rather than the actual problem.
    """
    dialect = session.get_bind().dialect.name

    if dialect == _POSTGRESQL:
        # `json_array_elements_text`, not `json_array_elements`: the tags
        # are strings and the caller does `str(row.tag)`, so text is what
        # is wanted. (For a `jsonb` column the `jsonb_` variant would be
        # the analogue; `knowledge_tags` is declared as plain `JSON`.)
        return func.json_array_elements_text(Problem.knowledge_tags).table_valued(
            "value", joins_implicitly=True
        )

    if dialect == _SQLITE:
        return func.json_each(Problem.knowledge_tags).table_valued(
            "value", joins_implicitly=True
        )

    raise NotImplementedError(
        f"no knowledge_tags expansion for dialect {dialect!r}; "
        f"only {_SQLITE!r} and {_POSTGRESQL!r} are supported"
    )
