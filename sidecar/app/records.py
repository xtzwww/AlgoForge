"""The submission write path — a facade over four modules.

Everything that used to live in this one file is now split by job:

* :mod:`app.records_support` — pure helpers that never open a session.
* :mod:`app.records_suggestion` — the suggestion column's write/parse contract.
* :mod:`app.records_analyse` — what went wrong, and annotating the entry.
* :mod:`app.records_write` — that a row and a mistake-book entry exist.

This module keeps the old import surface so callers and tests read
`from app.records import record_submission` exactly as before. It holds no
logic of its own, and that is the point: **the split is about where the code
lives, not about what the rest of the app has to know.**

One deliberate omission: `get_session` is *not* re-exported here. It is bound in
:mod:`app.records_session` and reached as `records_session.get_session()` by the
modules that need it, so replacing it once replaces it everywhere — see that
module's docstring. Re-exporting it here would create a second binding that
looks patchable but is not, which is worse than not having it at all.
"""

from app.records_analyse import (
    _analyse_and_annotate,
    _find_recurrence,
    _mark_analysis_failed,
    analyse_submission,
    reanalyse_entry,
    will_analyse,
)
from app.records_suggestion import (
    _SUGGESTION_LABELS,
    _format_suggestion,
    split_suggestion,
)
from app.records_support import (
    _ARCHIVABLE,
    SubmissionContext,
    _assemble_statement,
    _code_fingerprint,
    _find_open_duplicate,
    _first_failing_case,
    _has_open_duplicate,
    _pick_output,
    _worst_measurements,
)
from app.records_write import (
    _archive_mistake,
    _should_archive,
    _write,
    record_submission,
    record_submission_quietly,
)

# Listed explicitly because Ruff reads __all__ as "used": these names are
# re-exports, not imports this module consumes.
__all__ = [
    "SubmissionContext",
    "_ARCHIVABLE",
    "_SUGGESTION_LABELS",
    "_analyse_and_annotate",
    "_archive_mistake",
    "_assemble_statement",
    "_code_fingerprint",
    "_find_open_duplicate",
    "_find_recurrence",
    "_first_failing_case",
    "_format_suggestion",
    "_has_open_duplicate",
    "_mark_analysis_failed",
    "_pick_output",
    "_should_archive",
    "_worst_measurements",
    "_write",
    "analyse_submission",
    "record_submission",
    "record_submission_quietly",
    "reanalyse_entry",
    "split_suggestion",
    "will_analyse",
]
