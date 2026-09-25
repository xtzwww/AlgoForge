"""Open self-registration policy (2026-09-25).

Why this file exists at all
---------------------------
Until now the project had **no** registration path, and that was a
documented decision rather than an omission: accounts were created by a
teacher through ``POST /api/v1/teacher/students``, and
:mod:`app.api.auth` carried a note explaining that "an open registration
endpoint on a process that compiles arbitrary code is not something to
add before it is needed".

It is now needed — the product decision (2026-09-25) is that a student
can sign up without a teacher first creating their account. This module
is where the consequences of that decision are handled, so they are not
scattered through the route:

1. **Role is not a parameter.** :func:`registration_role` takes no
   argument and returns ``ROLE_STUDENT``. There is deliberately no way
   for a request to influence it. Self-registration that could produce a
   teacher account would hand the whole teacher console — every class's
   roster, every student's submissions — to anyone who could reach the
   endpoint. This is the one line that must never grow a parameter, and
   ``tests/test_registration.py`` pins it.

2. **The door has a switch.** ``ALLOW_OPEN_REGISTRATION`` defaults to
   on, because that is the product decision. A deployment that wants the
   old behaviour sets it to ``false`` and gets 403 from the endpoint,
   without a code change.

3. **There is a floor on credentials.** The teacher-created path
   validates nothing (its own default password is ``123456``), which is
   defensible when a teacher is typing one row per student. It is not
   defensible for an endpoint anyone can call in a loop: an empty
   password or a one-character username turns account creation into
   something a script can do thousands of times. The floors here are
   deliberately *low* — 2 characters of username, 6 of password — so
   that they match the accounts the teacher path already produces
   instead of inventing a second, stricter password culture that would
   confuse the same students.

4. **Attempts are rate limited.** Per client, in-process. See
   :class:`RegistrationThrottle` for why in-process is the right scope
   here rather than a database table.

What is still *not* here
------------------------
No email, no verification, no password reset, no captcha. Each of those
is a real feature with its own storage and failure modes, and none of
them is what "a student can sign up" requires. Adding an unverified
email field would look like a security control while providing none.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

from app.config import settings
from app.models.user import ROLE_STUDENT

logger = logging.getLogger(__name__)

#: Shortest and longest accepted login name. The floor is 2 rather than 3
#: because a teacher's imported roster may legitimately contain two-letter
#: names, and rejecting at sign-up a name the teacher path would accept is
#: an inconsistency a student would experience as a bug.
USERNAME_MIN = 2
USERNAME_MAX = 32

#: Punctuation allowed in a username. Deliberately tiny: enough for
#: ``zhang_san``, ``li-wei`` and ``2025021120058``, not enough to build
#: something that looks like another person's name or contains markup.
#: ``·`` is included because it appears in some Chinese ID conventions.
_USERNAME_PUNCTUATION = frozenset("_-·.")

#: Password floor, in characters. See the module docstring for why this is
#: 6 and not 12 — it matches the project's own default password.
PASSWORD_MIN = 6
PASSWORD_MAX = 128


class RegistrationError(ValueError):
    """A registration request was refused.

    Carries an HTTP status so the route does not have to map message text
    to a status code — a mapping that would break silently the moment a
    message was reworded. ``message`` is written for the person filling
    in the form, in Chinese, matching how the rest of the auth surface
    answers.
    """

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def registration_role() -> str:
    """The role a self-registered account gets. Always a student.

    Exists as a function rather than a literal at the call site so the
    constraint has one place to be asserted, and so a future reader who
    is tempted to add a ``role`` parameter sees this docstring first.
    """
    return ROLE_STUDENT


def open_registration_enabled() -> bool:
    """Whether the endpoint accepts new accounts at all."""
    return bool(settings.allow_open_registration)


def validate_username(raw: str) -> str:
    """Return the cleaned username, or raise :class:`RegistrationError`.

    Unicode letters and digits are accepted, so a student may sign up as
    ``张三`` — the project's users are Chinese-speaking and the teacher
    path has never restricted this. What is rejected is anything that is
    not alphanumeric or one of the few allowed separators, which keeps
    out whitespace, emoji, markup and the lookalike characters that make
    one account impersonate another.

    **All-digit names are allowed on purpose.** The first version of this
    function rejected them, on the theory that a bare ``7`` is
    indistinguishable from an internal id. That was wrong for this
    domain: the school exports rosters whose username column *is* the
    student number (``2025021120058``), and the CSV import exists for
    exactly those files. Rejecting them here would mean a name the
    teacher's own import accepts cannot be self-registered — an
    inconsistency a student experiences as a bug, and one that would hit
    the primary use case rather than an edge case. Uniqueness is what
    prevents collisions; "looks like an id" is not a reason to refuse.
    """
    username = (raw or "").strip()
    if not username:
        raise RegistrationError("请填写用户名")
    if len(username) < USERNAME_MIN or len(username) > USERNAME_MAX:
        raise RegistrationError(
            f"用户名长度需在 {USERNAME_MIN}–{USERNAME_MAX} 个字符之间"
        )
    for char in username:
        if char.isalnum() or char in _USERNAME_PUNCTUATION:
            continue
        raise RegistrationError("用户名只能包含字母、数字、下划线、连字符和点")
    return username


def validate_password(raw: str) -> str:
    """Return the password unchanged, or raise :class:`RegistrationError`.

    Not stripped: leading and trailing spaces are legitimate characters
    in a password, and silently trimming them would mean the password
    stored differs from the one typed — a login failure nobody can
    explain.
    """
    password = raw or ""
    if not password:
        raise RegistrationError("请填写密码")
    if len(password) < PASSWORD_MIN:
        raise RegistrationError(f"密码至少 {PASSWORD_MIN} 位")
    if len(password) > PASSWORD_MAX:
        raise RegistrationError(f"密码最多 {PASSWORD_MAX} 位")
    return password


def validate_display_name(raw: str) -> str:
    """Return the display name, falling back to ``''``.

    Blank is allowed and means "use the username" — see
    :func:`app.auth.create_user`, which already does that. The only
    check is length, because this value is rendered in the teacher
    console and a 10,000-character name would break the roster layout.
    """
    name = (raw or "").strip()
    if len(name) > 64:
        raise RegistrationError("姓名最多 64 个字符")
    return name


class RegistrationThrottle:
    """A per-client sliding window on successful registrations.

    **Why in-process rather than a table.** The same reasoning as
    :mod:`app.model_catalog`: this is a single-process sidecar for one
    class on one machine, so a dict is the whole distributed system.
    A table would need a migration and a cleanup job to answer a
    question whose window is one hour.

    **Why it counts successes rather than attempts.** Counting attempts
    would let a student who mistyped their password five times be locked
    out of creating their own account, which is the wrong person to
    punish. Counting successes bounds the thing actually being defended
    against — how many accounts one client can create — and leaves the
    honest user's retries free.

    **Why the key is the client address.** It is the only thing
    available before the account exists, and it is the right granularity
    for a lab: thirty students behind one NAT share a key, which is why
    the limit is generous rather than tight. This stops a script, not a
    determined attacker with a proxy pool, and it is documented that way
    instead of being presented as a security boundary.
    """

    def __init__(self, limit: int, window_seconds: float = 3600.0) -> None:
        self._limit = max(1, limit)
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Raise :class:`RegistrationError` (429) when *key* is over budget."""
        now = time.monotonic()
        bucket = self._hits[key]
        self._prune(bucket, now)
        if len(bucket) >= self._limit:
            retry_after = max(1, int(self._window - (now - bucket[0])))
            raise RegistrationError(
                f"注册过于频繁，请 {retry_after} 秒后再试", status_code=429
            )

    def record(self, key: str) -> None:
        """Count one successful registration against *key*."""
        now = time.monotonic()
        bucket = self._hits[key]
        self._prune(bucket, now)
        bucket.append(now)

    def _prune(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def reset(self) -> None:
        """Forget every window. Test hook and process-restart behaviour."""
        self._hits.clear()


def throttle_for_settings() -> RegistrationThrottle:
    """Build a throttle from the current settings."""
    return RegistrationThrottle(limit=settings.registration_rate_limit)


#: Process-wide throttle. Built lazily so a test that changes the setting
#: can rebuild it through :func:`reset_throttle`.
_throttle: Optional[RegistrationThrottle] = None


def get_throttle() -> RegistrationThrottle:
    """The process-wide throttle, built on first use."""
    global _throttle
    if _throttle is None:
        _throttle = throttle_for_settings()
    return _throttle


def reset_throttle() -> None:
    """Drop the process-wide throttle. Test hook."""
    global _throttle
    _throttle = None
