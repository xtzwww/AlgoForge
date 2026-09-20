"""The suggestion column's write/parse contract — no database at all.

The three prose fields of an error analysis (``error_detail``, ``hint``,
``suggested_fix``) share one ``agent_suggestion`` column. :func:`_format_suggestion`
writes it, :func:`split_suggestion` reads it back, and :data:`_SUGGESTION_LABELS`
is the single source for the headings both sides use. Keeping the writer
and the parser in one file is the point: they are one contract, and a
change to one that does not reach the other is a bug that only shows up
as a mislabelled paragraph in the student's UI.

Split out of :mod:`app.records` (T13) as the other zero-risk half: this
module imports nothing from the project at all, so moving it cannot change
any session or monkeypatch semantics.
"""

from __future__ import annotations


def _format_suggestion(analysis: object) -> str:
    """Join the analysis prose into the single text column.

    The three prose fields share one column rather than three because
    they are written as a unit and were, until :func:`split_suggestion`
    existed, read as a unit too. Labelled with their Chinese headings so
    the stored string is still readable when dumped straight into a
    terminal or a bug report.

    The labels are load-bearing: :func:`split_suggestion` parses them
    back out, and the frontend uses the split to release the three
    parts one at a time instead of handing the student the fix up
    front. So the format here and the parser below are one contract —
    change one, change both, and :data:`_SUGGESTION_LABELS` is the
    single source for the label strings.
    """
    detail = getattr(analysis, "error_detail", "") or ""
    hint = getattr(analysis, "hint", "") or ""
    fix = getattr(analysis, "suggested_fix", "") or ""

    parts = []
    if detail:
        parts.append(f"{_SUGGESTION_LABELS['error_detail']}{detail}")
    if hint:
        parts.append(f"{_SUGGESTION_LABELS['hint']}{hint}")
    if fix:
        parts.append(f"{_SUGGESTION_LABELS['suggested_fix']}{fix}")
    return "\n".join(parts)


#: The Chinese headings ``_format_suggestion`` writes and
#: :func:`split_suggestion` reads back. Kept as data rather than as
#: literals in both functions so a reworded heading cannot desynchronise
#: the writer from the parser.
_SUGGESTION_LABELS: dict[str, str] = {
    "error_detail": "【错在哪】",
    "hint": "【提示】",
    "suggested_fix": "【怎么改】",
}


def split_suggestion(suggestion: str) -> dict[str, str]:
    """Split ``agent_suggestion`` back into its three prose fields.

    The inverse of :func:`_format_suggestion`. Exists so the API can
    serve the parts separately and the frontend can release them one at
    a time — the point of which is pedagogical: ``hint`` is a nudge and
    ``suggested_fix`` is the answer, and presenting them together means
    the student reads the answer and never uses the nudge. The storage
    format is unchanged; only the read path gained structure.

    Parsed leniently rather than strictly, for three reasons:

    * **Old rows.** ``agent_suggestion`` is free text that predates any
      contract. A row written before a label was renamed, or by a
      different prompt version, simply won't match — and must still
      render. Anything unmatched is returned as-is under
      ``error_detail``, which is the field the UI always shows first, so
      the worst case is "the whole text appears at once" (the old
      behaviour) rather than "the text disappears".
    * **Model-authored newlines.** The prose itself may contain the
      label of a *later* section, or contain no label at all. Splitting
      on the first occurrence of each known label in order is enough to
      survive that; a strict line-by-line grammar would not.
    * **Cost of being wrong.** A false split shows a student a
      mislabelled paragraph. A lost split shows nothing. The lenient
      path degrades to the previous behaviour, so this is the safe
      direction to fail in.

    Args:
        suggestion: The stored column value. May be empty, may be
            unlabelled free text, may hold any subset of the three
            sections.

    Returns:
        A dict with keys ``error_detail``, ``hint`` and ``suggested_fix``.
        Every key is present; a section that was not found is ``""``.
        When no label matches at all, the whole input is returned under
        ``error_detail``.
    """
    if not suggestion:
        return {"error_detail": "", "hint": "", "suggested_fix": ""}

    # Locate each label, keeping only the first occurrence of each. A
    # label appearing later inside another section's prose is ignored,
    # because by then we already have that section's start.
    found: list[tuple[int, str]] = []
    # `section`, not `field`: `field` is the dataclasses import above, and
    # shadowing an import inside a loop is the kind of thing that turns
    # into a real bug the moment the function grows a `@dataclass`.
    for section, label in _SUGGESTION_LABELS.items():
        at = suggestion.find(label)
        if at != -1:
            found.append((at, section))

    if not found:
        # Unlabelled free text: hand it back whole so the UI still has
        # something to show.
        return {
            "error_detail": suggestion,
            "hint": "",
            "suggested_fix": "",
        }

    found.sort()
    result = {"error_detail": "", "hint": "", "suggested_fix": ""}
    for index, (start, section) in enumerate(found):
        body_start = start + len(_SUGGESTION_LABELS[section])
        body_end = found[index + 1][0] if index + 1 < len(found) else len(suggestion)
        result[section] = suggestion[body_start:body_end].strip()
    return result
