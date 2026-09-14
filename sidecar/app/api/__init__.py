"""REST API route modules.

Also hosts :data:`LanguageId`, the request-field type every route that
accepts a language uses. It lives here rather than in each route module
because three of them need it and the validation rule is identical: the
id has to name a registered language, and a bad one must be a 4xx rather
than a 500.

No business logic here — the type delegates to
``app.sandbox.languages``, which owns the registry.
"""

from typing import Annotated

from pydantic import AfterValidator

from app.sandbox.languages import UnknownLanguageError, get_language


def validate_language_id(value: object) -> str:
    """Coerce a request-supplied language id to its canonical spelling.

    Accepts what :func:`get_language` accepts: a registered id in any
    case, or ``None``/blank meaning "the default language". A blank field
    is deliberately valid — it is how a client says "whatever the default
    is" without having to hardcode a language name.

    Args:
        value: Raw value from a request body. Typed ``object`` rather than
            ``str`` because the WebSocket route takes unvalidated JSON and
            a client can put a number or a list here; the registry reports
            those as unknown ids instead of raising ``TypeError``.

    Returns:
        The canonical id, e.g. ``"cpp"``.

    Raises:
        ValueError: The id is not registered. Raised as a ``ValueError``
            rather than the registry's own ``UnknownLanguageError`` so
            Pydantic converts it into a 422 and the WebSocket parser into
            a ``judge_error`` — a bare ``LookupError`` would escape both
            and become a 500. The message is the registry's, which lists
            every valid id.
    """
    try:
        return get_language(value).id
    except UnknownLanguageError as exc:
        raise ValueError(str(exc)) from None


#: Request field type for a language id. Use it as the annotation of a
#: Pydantic field to get validation and canonical spelling for free:
#: ``language: LanguageId = "cpp"``.
LanguageId = Annotated[str, AfterValidator(validate_language_id)]


__all__ = ["LanguageId", "validate_language_id"]
