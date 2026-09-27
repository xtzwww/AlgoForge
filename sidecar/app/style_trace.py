"""Style consistency features and the signal derived from them (C2).

Why this module exists
----------------------
C1 (:mod:`app.process_trace`) watches *how* the code got into the editor.
It says nothing about the code itself. A submission that arrived by paste
through a freshly-opened editor looks nothing like the student's other
work — but so does a submission the student wrote by hand for a problem
that happens to need longer names than their usual ones. C2 is the second
independent reading: it compares the *code's own statistics* against the
same student's own history for the same language, and only raises a flag
when several features move together and far.

Why the two readings are separate
---------------------------------
Each is cheap to explain on its own, and a teacher who disagrees with one
can still use the other. Merging them into a single "AI suspicion" score
would make the evidence unanswerable — the whole point of the flagged
queue is that every mark carries the observations it came from (see
``models/alerts.py``). C1 and C2 stay independent columns and the queue
shows whichever fired.

The signal is conservative, more so than C1
-------------------------------------------
C1 fires on a narrow, reproducible pattern. C2's input is a handful of
text statistics, and each of those can move for innocent reasons: a
student taking a course on algorithm design suddenly writes longer
identifier names; a problem about string parsing makes the code denser.
So C2 demands **two or more** features moving **well past** the student's
own history before it says anything, and it says nothing at all until the
student has at least four prior submissions in that language — with fewer
samples the "history" is not a history. The module docstring of
``process_trace.py`` explains why the two error directions are not
symmetric; this module leans the same way, harder.

What is measured
----------------
Three language-neutral statistics, each computable with a regex pass:

* ``identifier_avg_len`` — mean length of identifier tokens (variable /
  function names). AI-generated code tends to use long, descriptive names
  with little variance; a beginner's names are shorter and more uneven.
* ``blank_ratio`` — blank lines / total lines. Hand-written code usually
  carries more air; generated code is denser.
* ``long_line_ratio`` — share of lines longer than 100 characters. Human
  code drifts into long lines (a chain of conditionals); generated code
  rarely exceeds a comfortable width.

None of the three is a verdict by itself. ``describe`` only raises the
flag when at least two of them sit beyond a generous band around the
student's own mean — and every reason it emits is the concrete number
pair ("your last 6 submissions averaged 4.1; this one is 12.6"), never
"AI", never a probability.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Identifier token: a letter/underscore followed by word characters.
#: `\w` also matches CJK, which would pollute length statistics, so the
#: body is restricted to ASCII word chars.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Lines longer than this count as "long". 100 is comfortably past the
#: 80-column convention most of these languages use.
LONG_LINE_CHARS = 100

#: Fewer historical submissions than this and there is no "history" to
#: compare against. With 4+ rows the mean is still wobbly, which is why
#: the band below is wide and the trigger needs two features.
MIN_HISTORY_SAMPLES = 4

#: How far from the student's own mean a feature must sit to count as a
#: deviation. A ratio band rather than a z-score: the features are
#: positive and skew, and a 1.5x / 0.6x band is directly sayable ("half
#: again as long").
HIGH_RATIO = 1.5
LOW_RATIO = 0.6

#: How many features must deviate before the flag may fire. 2 of 3 keeps
#: every single-feature explanation (a problem that needs long names, a
#: compact input-formatting task) from triggering on its own.
REQUIRED_DEVIATIONS = 2

#: How many of the student's most recent submissions are read as the
#: history. 6 is enough for a stable mean without making the query heavy.
HISTORY_WINDOW = 6

#: Language keyword sets. A keyword is not an identifier: ``int`` and
#: ``return`` carry no style signal. Only the three supported languages
#: are filtered; an unknown language still gets statistics, just with no
#: keyword exclusions — the features are computed either way, and a
#: missing set must not silently disable the reading for a new language.
KEYWORDS: dict[str, frozenset[str]] = {
    "cpp": frozenset(
        {
            "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand",
            "bitor", "bool", "break", "case", "catch", "char", "class",
            "compl", "const", "constexpr", "continue", "decltype",
            "default", "delete", "do", "double", "dynamic_cast", "else",
            "enum", "explicit", "export", "extern", "false", "float",
            "for", "friend", "goto", "if", "inline", "int", "long",
            "mutable", "namespace", "new", "noexcept", "not", "not_eq",
            "nullptr", "operator", "or", "or_eq", "private", "protected",
            "public", "register", "reinterpret_cast", "return", "short",
            "signed", "sizeof", "static", "static_assert", "static_cast",
            "struct", "switch", "template", "this", "thread_local",
            "throw", "true", "try", "typedef", "typeid", "typename",
            "union", "unsigned", "using", "virtual", "void", "volatile",
            "wchar_t", "while", "xor", "xor_eq",
        }
    ),
    "python": frozenset(
        {
            "and", "as", "assert", "async", "await", "break", "class",
            "continue", "def", "del", "elif", "else", "except", "False",
            "finally", "for", "from", "global", "if", "import", "in",
            "is", "lambda", "None", "nonlocal", "not", "or", "pass",
            "raise", "return", "True", "try", "while", "with", "yield",
        }
    ),
    "java": frozenset(
        {
            "abstract", "assert", "boolean", "break", "byte", "case",
            "catch", "char", "class", "const", "continue", "default",
            "do", "double", "else", "enum", "extends", "final", "finally",
            "float", "for", "goto", "if", "implements", "import",
            "instanceof", "int", "interface", "long", "native", "new",
            "package", "private", "protected", "public", "return", "short",
            "static", "strictfp", "super", "switch", "synchronized",
            "this", "throw", "throws", "transient", "try", "void",
            "volatile", "while",
        }
    ),
}


@dataclass(frozen=True)
class StyleFeatures:
    """Three language-neutral statistics of one piece of source code.

    All values are derived by :func:`from_code`; the fields are frozen so
    a computed feature set cannot be half-mutated somewhere.
    """

    identifier_avg_len: float
    blank_ratio: float
    long_line_ratio: float

    def to_dict(self) -> dict[str, float]:
        return {
            "identifier_avg_len": round(self.identifier_avg_len, 2),
            "blank_ratio": round(self.blank_ratio, 4),
            "long_line_ratio": round(self.long_line_ratio, 4),
        }


@dataclass(frozen=True)
class StyleSignal:
    """The reading: whether the code drifted from the student's own style.

    ``flagged`` is the conservative verdict described in the module
    docstring. ``reasons`` are always populated — a teacher who disagrees
    with a flag, or wonders why one was *not* raised, reads the same
    numbers. ``summary`` is the one-sentence form.
    """

    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self, features: StyleFeatures) -> dict[str, Any]:
        """The API shape: the numbers and the words together."""
        return {
            "features": features.to_dict(),
            "flagged": self.flagged,
            "reasons": self.reasons,
            "summary": self.summary,
        }


def from_code(code: str, language: str = "cpp") -> StyleFeatures:
    """Compute the three style statistics for one source text.

    Empty or whitespace-only code yields a zero feature set, not one that
    pretends the body is "all blank lines" — a submission with no
    measurable body is not worth comparing, and the caller skips it.
    """
    if not code.strip():
        return StyleFeatures(identifier_avg_len=0.0, blank_ratio=0.0, long_line_ratio=0.0)

    identifiers: list[int] = []
    for match in _IDENTIFIER_RE.finditer(code):
        token = match.group(0)
        if token in KEYWORDS.get(language, frozenset()):
            continue
        identifiers.append(len(token))

    lines = code.splitlines()
    total = len(lines)
    if total == 0:
        return StyleFeatures(identifier_avg_len=0.0, blank_ratio=0.0, long_line_ratio=0.0)

    blanks = sum(1 for line in lines if not line.strip())
    longs = sum(1 for line in lines if len(line) > LONG_LINE_CHARS)
    avg_len = sum(identifiers) / len(identifiers) if identifiers else 0.0

    return StyleFeatures(
        identifier_avg_len=avg_len,
        blank_ratio=blanks / total,
        long_line_ratio=longs / total,
    )


def compare_against_history(
    current: StyleFeatures, history: list[StyleFeatures]
) -> StyleSignal:
    """Compare one submission's style against the student's own history.

    The rule, stated so it can be defended out loud: **a submission is
    flagged only when it deviates from the student's own mean on at least
    two of the three style features, and only when the history has enough
    rows for the mean to be worth comparing against.**

    Args:
        current: Features of the submission under review.
        history: Features of the student's earlier submissions (same
            language), oldest-independent order — only the statistics are
            used.

    Returns:
        The signal. ``flagged`` is conservative by design; see the module
        docstring on why.
    """
    if len(history) < MIN_HISTORY_SAMPLES:
        return StyleSignal(
            flagged=False,
            reasons=[
                f"历史提交不足 {MIN_HISTORY_SAMPLES} 条（现有 {len(history)} 条），"
                f"无法形成可靠的个人风格基线，本次不作风格对比。"
            ],
            summary="历史样本不足，未做风格一致性判断。",
        )

    def mean(values: list[float]) -> float:
        return sum(values) / len(values)

    pairs: list[tuple[str, str, float, float]] = [
        (
            "平均标识符长度",
            "identifier_avg_len",
            current.identifier_avg_len,
            mean([h.identifier_avg_len for h in history]),
        ),
        (
            "空行占比",
            "blank_ratio",
            current.blank_ratio,
            mean([h.blank_ratio for h in history]),
        ),
        (
            "超长行占比",
            "long_line_ratio",
            current.long_line_ratio,
            mean([h.long_line_ratio for h in history]),
        ),
    ]

    deviations: list[str] = []
    for label, _key, value, base in pairs:
        if base <= 0:
            # No history to compare this feature against (e.g. the student
            # never had a long line); a nonzero current value is a
            # deviation only in company with another clear one.
            if value > 0:
                deviations.append(
                    f"{label}为 {value:.2f}，而历史提交中该项始终为 0。"
                )
            continue
        ratio = value / base
        if ratio >= HIGH_RATIO or ratio <= LOW_RATIO:
            deviations.append(
                f"{label}为 {value:.2f}，你最近 {len(history)} 次提交平均 {base:.2f}"
                f"（{ratio:.1f} 倍）。"
            )

    if len(deviations) < REQUIRED_DEVIATIONS:
        return StyleSignal(
            flagged=False,
            reasons=deviations if deviations else ["代码风格与你最近几次提交一致。"],
            summary="代码风格与你最近的提交无明显差异。",
        )

    reasons = [
        f"与最近 {len(history)} 次提交对比，本提交在 {len(deviations)} 个风格维度上"
        f"偏离明显：",
        *deviations,
        "多个维度同时偏离，说明这段代码的书写方式与你的历史风格差异较大。",
    ]
    return StyleSignal(
        flagged=True,
        reasons=reasons,
        summary="这次提交的代码风格和你以前的写法差别较大，建议回顾一下是否独立完成了这道题。",
    )
