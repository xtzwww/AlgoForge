"""Edit-behaviour traces and the process features derived from them.

Why this module exists
----------------------
The project's title promises *learning-process* analysis, but until now
the database only ever saw the **result** of a submission: the code, the
verdict, the timings. Everything that happened *before* the submit button
was pressed — how the code got there — was never recorded and therefore
never existed as far as the system was concerned.

That gap is not cosmetic. Two students can hand in byte-identical code
whose histories are opposites:

* one pastes 80 lines and submits them unchanged, and
* one pastes 80 lines, rewrites half of them, runs the program four
  times, and submits the fifth attempt.

On the submitted text the two are indistinguishable. On the edit trace
they are trivially separable. This module turns the trace into the small
set of numbers that makes them separable, and into a signal a human can
read.

What is stored, and what is not
-------------------------------
The raw event list is **not** persisted — only the derived features are.
Three reasons, in descending order of importance:

1. Explainability. A teacher reviewing a flag needs *"pasted 1,180
   characters, changed 12 of them afterwards"*, not 400 timestamped
   keypresses. The features are the evidence; the trace is the raw
   material they were computed from.
2. Size. A long session produces thousands of events. Storing them per
   submission would make ``submissions`` the largest table in the schema
   by an order of magnitude.
3. Privacy. Behaviour traces are the most identifying data the system
   touches. Keeping the derived summary means a data-protection question
   has a short answer.

The signal is categorical
-------------------------
There is deliberately **no** "AI probability" anywhere in this module,
and there must never be one. The signal is either *worth a look* or it
is not, and it is always accompanied by the observations it came from.

A single score would be the wrong shape for the claim being made. Falsely
flagging a student who wrote their own code costs their trust and the
teacher's attention, and it is unrecoverable — a "sorry, 87% was
actually fine" does not undo the accusation. Missing a student who did
use a tool costs a nudge that was never going to be enforced anyway.
Those two errors are not symmetric, so the threshold is set to favour the
student: only the specific, reproducible pattern of *"text arrived from
outside and was not worked on afterwards"* raises the flag.

Rewriting after a paste is treated as **evidence of learning**, not as a
way of hiding anything. A student who pastes a reference solution and
then spends twenty minutes adapting it is doing exactly what a reference
solution is for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

#: Event kinds the editor reports.
#:
#: ``type`` / ``delete`` / ``paste`` are the three ways text enters or
#: leaves the buffer. ``run`` is the student asking for a verdict, and is
#: counted separately: it is the strongest cheap indicator of iteration,
#: because pressing it means "I want to know whether this works".
#: ``open`` marks the start of the session and carries no edit.
EVENT_KINDS: tuple[str, ...] = ("open", "type", "delete", "paste", "run")

#: Events that change the buffer.
_EDIT_KINDS = frozenset({"type", "delete", "paste"})

#: Upper bound on the events one submission will accept.
#:
#: A trace is uploaded with every submission, so it is attacker-controlled
#: input in the same way the code is — an unbounded list is an unbounded
#: parse. 2,000 events is far more than a session needs to characterise
#: it (the features are counts and ratios; they saturate long before
#: that), and it keeps the request body in the tens of kilobytes.
MAX_EVENTS = 2000

# --- Thresholds -------------------------------------------------------
#
# Every one of these is a judgement call, so each carries the reasoning
# rather than just a number. They live here, not in config, because they
# are part of the definition of the signal: changing one changes what the
# system claims, which is a code change, not a deployment setting.

#: Below this many pasted characters a paste is a snippet, not a body of
#: work. 120 characters is roughly four lines of C++ — the size of a
#: single loop or an ``#include`` block. Students legitimately paste
#: snippets constantly, and flagging them would bury the real signal.
MIN_PASTED_CHARS = 120

#: Share of the final code that has to come from paste before the
#: submission is "mostly pasted". 0.8 leaves room for a student who wrote
#: the surrounding scaffolding (``main``, the I/O) by hand and pasted the
#: algorithm into it — a normal and unremarkable way to work.
HIGH_PASTE_RATIO = 0.8

#: Share of the pasted text that has to be *left alone* afterwards before
#: the submission counts as "not worked on". This is the threshold that
#: does the actual work: it is what separates the two students in the
#: module docstring. 0.15 means at least roughly one character in seven
#: was retyped or deleted after the paste landed.
LOW_REWRITE_RATIO = 0.15


class EditEvent(BaseModel):
    """One thing the student did in the editor.

    Attributes:
        t: Milliseconds since the editing session started. Relative, not
            absolute: an absolute timestamp would leak the student's
            timezone and working hours for no analytical gain.
        kind: One of :data:`EVENT_KINDS`.
        chars: Characters involved. For ``paste`` this is the size of the
            pasted text; for ``type`` and ``delete`` the size of the
            change. ``0`` for ``open`` and ``run``.
        length: Length of the document *after* the event. Redundant with
            the event list, but it is what makes a trace checkable by eye
            and it is the denominator of the paste ratio.
    """

    t: int = Field(ge=0)
    kind: str
    chars: int = Field(default=0, ge=0)
    length: int = Field(default=0, ge=0)


class ProcessTrace(BaseModel):
    """The editing session that led up to one submission.

    Attributes:
        events: The recorded events, in the order the editor saw them.
            Sorted defensively on use — a client that batches or retries
            can deliver them slightly out of order, and an unsorted trace
            would compute "after the last paste" against the wrong event.
    """

    events: list[EditEvent] = Field(default_factory=list, max_length=MAX_EVENTS)


def parse_process_trace(raw: Any) -> Optional[ProcessTrace]:
    """Validate a client-supplied trace, or drop it.

    Never raises. The trace is **telemetry** — it describes how the code
    was written, and the request's actual job is to have that code
    judged. A malformed trace must not cost the student their verdict,
    which a 422 or a ``judge_error`` would do, and it must not cost the
    teacher the submission either.

    The rejection is logged at WARNING rather than being silent: a client
    whose traces never validate is a bug that would otherwise show up
    only as "the process column is empty for everyone", which reads like
    a feature nobody uses.

    Both entry points (REST and WebSocket) route through here, so a
    malformed trace behaves identically on both instead of being a 422
    on one and an ignored field on the other.

    Args:
        raw: The decoded JSON, usually a dict with an ``events`` list.

    Returns:
        The trace, or ``None`` when it is absent or unusable.
    """
    if raw is None:
        return None
    try:
        return ProcessTrace.model_validate(raw)
    except ValidationError as exc:
        logger.warning("dropping a malformed process_trace: %s", exc)
        return None


@dataclass
class ProcessFeatures:
    """The numbers derived from one :class:`ProcessTrace`.

    Every field is a count or a ratio, so every one of them can be shown
    to a student or a teacher without interpretation. Nothing here is
    "anomaly" or "score": that judgement is :func:`describe`'s, and it is
    kept separate so the evidence stays readable when the signal is
    disagreed with — which is the whole point of not emitting a number.

    Attributes:
        duration_ms: Time from the session's first event to its last.
        edit_count: Events that changed the buffer.
        paste_count: Paste events.
        pasted_chars: Total characters pasted.
        final_length: Length of the code as submitted.
        paste_ratio: ``pasted_chars / final_length``.
        post_paste_edits: Edits made *after* the last paste.
        post_paste_chars: Characters changed after the last paste.
        rewrite_ratio: ``post_paste_chars / pasted_chars``, or ``None``
            when nothing was pasted — a ratio over a zero denominator is
            not zero, it is undefined, and ``0.0`` would read as "no
            rewriting happened" when in fact there was nothing to
            rewrite.
        runs_before_submit: How many times a verdict was requested during
            the session.
    """

    duration_ms: int = 0
    edit_count: int = 0
    paste_count: int = 0
    pasted_chars: int = 0
    final_length: int = 0
    paste_ratio: float = 0.0
    post_paste_edits: int = 0
    post_paste_chars: int = 0
    rewrite_ratio: Optional[float] = None
    runs_before_submit: int = 0

    def to_dict(self) -> dict:
        """Serialize for the API and for storage.

        Keys are the field names verbatim rather than a hand-written
        subset: this dict is what the teacher's evidence panel renders,
        and a field that is computed but not exported is a field that
        silently disappears from every explanation of a flag.
        """
        return {
            "duration_ms": self.duration_ms,
            "edit_count": self.edit_count,
            "paste_count": self.paste_count,
            "pasted_chars": self.pasted_chars,
            "final_length": self.final_length,
            "paste_ratio": round(self.paste_ratio, 4),
            "post_paste_edits": self.post_paste_edits,
            "post_paste_chars": self.post_paste_chars,
            "rewrite_ratio": (
                None
                if self.rewrite_ratio is None
                else round(self.rewrite_ratio, 4)
            ),
            "runs_before_submit": self.runs_before_submit,
        }


@dataclass
class ProcessSignal:
    """What the features mean, in words a person can check.

    Attributes:
        flagged: Whether the submission shows the pattern worth a look.
        reasons: The observations behind the flag, in Chinese, ordered
            most-specific first. Always populated, even when ``flagged``
            is false — a teacher who disagrees with a flag, or wants to
            know why one was *not* raised, reads the same panel.
        summary: One sentence for the student-facing form.
    """

    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self, features: ProcessFeatures) -> dict:
        """The API shape: the numbers and the words together.

        Takes the features as an argument rather than holding them, so
        the judgement and the measurements stay separable — a caller can
        describe one set of features under a threshold that was not the
        one that produced them, which is exactly what happens when the
        rule changes and old rows are re-read.
        """
        return {
            "features": features.to_dict(),
            "flagged": self.flagged,
            "reasons": self.reasons,
            "summary": self.summary,
        }


def view_for(trace: Optional[ProcessTrace], code: str = "") -> Optional[dict]:
    """Features plus signal for a trace, or ``None`` when there is no trace.

    The one definition behind both the write path (which needs only the
    boolean) and the read path (which returns the whole thing), so a
    response and a stored row cannot describe the same submission
    differently.
    """
    if trace is None:
        return None
    features = from_trace(trace, code)
    return describe(features).to_dict(features)


def from_trace(trace: Optional[ProcessTrace], code: str = "") -> ProcessFeatures:
    """Derive features from a trace.

    Args:
        trace: The client's trace, or ``None`` when the client sent none.
            ``None`` is a normal outcome: an older client, a submission
            made through the REST API, or a page that never mounted the
            editor all legitimately have no trace.
        code: The submitted source, used as the fallback for
            ``final_length`` when the trace carries no length — which is
            the case for a session whose last recorded event predates the
            final edit.

    Returns:
        The features. ``None`` in, all-zero out: an absent trace yields
        an empty feature set rather than a ``None`` that every caller
        would have to branch on. :func:`describe` then reports "no trace"
        as the reason, which is honest — the system cannot say anything
        about a process it did not observe.
    """
    if trace is None or not trace.events:
        return ProcessFeatures(final_length=len(code))

    # Sorted by t, stable: two events with the same timestamp keep the
    # order the client recorded them in. Not sorting would make "after
    # the last paste" depend on upload ordering.
    events = sorted(trace.events, key=lambda e: e.t)

    features = ProcessFeatures()
    features.duration_ms = max(e.t for e in events)
    features.final_length = events[-1].length or len(code)

    features.paste_count = sum(1 for e in events if e.kind == "paste")
    features.pasted_chars = sum(e.chars for e in events if e.kind == "paste")
    features.runs_before_submit = sum(1 for e in events if e.kind == "run")
    features.edit_count = sum(1 for e in events if e.kind in _EDIT_KINDS)

    if features.final_length:
        features.paste_ratio = features.pasted_chars / features.final_length

    # "After the last paste" rather than "after any paste": a student who
    # pastes, rewrites, pastes again, and stops has not worked on the
    # second paste, and averaging over both would reward the first
    # paste's effort. The most recent paste is the one that was not
    # followed by anything.
    last_paste = -1
    for index, event in enumerate(events):
        if event.kind == "paste":
            last_paste = index

    if last_paste >= 0:
        after = events[last_paste + 1 :]
        features.post_paste_edits = sum(1 for e in after if e.kind in _EDIT_KINDS)
        features.post_paste_chars = sum(e.chars for e in after if e.kind in _EDIT_KINDS)
        if features.pasted_chars:
            features.rewrite_ratio = (
                features.post_paste_chars / features.pasted_chars
            )

    return features


def describe(features: ProcessFeatures) -> ProcessSignal:
    """Turn features into a signal plus the evidence behind it.

    The rule, stated in one sentence so it can be defended out loud:
    **a submission is flagged when most of its code came from outside the
    editor and it was not worked on afterwards.** Both halves are
    required. Paste-heavy but heavily rewritten is *not* flagged, and
    neither is a small paste inside otherwise hand-written code.

    Args:
        features: Output of :func:`from_trace`.

    Returns:
        The signal. ``flagged`` is conservative by design — see the
        module docstring on why the two error directions are not
        symmetric.
    """
    reasons: list[str] = []

    if features.paste_count == 0:
        # Not "clean" — unobserved and clean are different claims. The
        # features distinguish them (``final_length`` is known either
        # way) and the wording must not collapse them.
        if features.edit_count == 0:
            return ProcessSignal(
                flagged=False,
                reasons=["本次提交没有记录到编辑过程，无法判断代码的产生方式。"],
                summary="本次提交没有记录到编辑过程。",
            )
        reasons.append(
            f"未检测到粘贴，全程 {features.edit_count} 次编辑，代码为连续输入产生。"
        )
        return ProcessSignal(
            flagged=False,
            reasons=reasons,
            summary="编辑过程以连续输入为主，未发现需要关注的特征。",
        )

    reasons.append(
        f"共粘贴 {features.paste_count} 次、{features.pasted_chars} 个字符，"
        f"占最终代码的 {features.paste_ratio:.0%}。"
    )

    if features.pasted_chars < MIN_PASTED_CHARS:
        reasons.append(
            f"粘贴量小于 {MIN_PASTED_CHARS} 个字符，属于片段级粘贴，不计入过程特征。"
        )
        return ProcessSignal(
            flagged=False,
            reasons=reasons,
            summary="粘贴内容较短，属于正常的片段引用。",
        )

    if features.rewrite_ratio is None:
        # Unreachable in practice (paste_count > 0 implies pasted_chars >
        # 0 for any well-formed trace) but the ratio is None-typed, so
        # the branch has to exist rather than be assumed away.
        return ProcessSignal(
            flagged=False,
            reasons=reasons,
            summary="粘贴量无法换算为改写比例，不作判断。",
        )

    reasons.append(
        f"最后一次粘贴后又进行了 {features.post_paste_edits} 次编辑，"
        f"改动 {features.post_paste_chars} 个字符，"
        f"改写比例为 {features.rewrite_ratio:.0%}。"
    )

    if features.paste_ratio < HIGH_PASTE_RATIO:
        reasons.append(
            f"粘贴占比低于 {HIGH_PASTE_RATIO:.0%}，"
            f"代码仍有相当部分是自行编写的，不予标记。"
        )
        return ProcessSignal(
            flagged=False,
            reasons=reasons,
            summary="粘贴仅占代码的一部分，未见需要关注的特征。",
        )

    if features.rewrite_ratio > LOW_REWRITE_RATIO:
        reasons.append(
            f"粘贴后改写比例高于 {LOW_REWRITE_RATIO:.0%}，"
            f"说明粘贴内容经过了实质性修改。"
        )
        if features.runs_before_submit:
            reasons.append(
                f"本次会话中共运行/提交了 {features.runs_before_submit} 次，"
                f"属于反复调试的学习过程。"
            )
        return ProcessSignal(
            flagged=False,
            reasons=reasons,
            summary="粘贴内容经过了较多改写，未予标记。",
        )

    reasons.append(
        "代码几乎全部来自粘贴，且粘贴后几乎没有修改——"
        "这与自行完成一道题的典型编辑方式差别较大。"
    )
    return ProcessSignal(
        flagged=True,
        reasons=reasons,
        summary="本次提交的编辑方式与独立完成一道题的常见方式差异较大，建议回顾一下自己的解题思路。",
    )
