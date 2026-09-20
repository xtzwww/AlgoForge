"""The one place the write path's session factory is bound.

Five lines of module for one reason: **a monkeypatched session must reach every
module at once.** `tests/test_mistake_analysis_retry.py` replaces `get_session`
with one backed by SQLite so no test in that file can reach the real
PostgreSQL URL — and the failure mode when it misses a module is not a red
test, it is a **hang** on a connection timeout.

If each module did `from app.db import get_session` it would own a *separate*
binding, and replacing one would leave the others pointing at the real DSN.
Binding it here and reaching it as `records_session.get_session()` everywhere
means there is exactly one thing to replace.
"""

from app.db import get_session

__all__ = ["get_session"]
