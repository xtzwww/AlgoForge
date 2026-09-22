"""Loader for the knowledge-card corpus (阶段 8 · RAG 语料).

``corpus/knowledge_cards/`` holds one Markdown card per algorithm topic. Each
card opens with a YAML front-matter block and continues as prose::

    ---
    id: binary-search
    title: 二分查找与数值逼近
    knowledge_tags: [二分, 数组, 数值计算]
    related_problems: [p011-binary-search, p012-sqrt-integer]
    common_errors: [off_by_one, boundary_condition]
    ---

    # 二分查找与数值逼近
    ...

The split is deliberate and is explained in ``corpus/knowledge_cards/README.md``:
the front matter is what the retrieval layer *filters* on, the body is what
gets embedded and handed to the model. Cards are Markdown (not YAML like
``corpus/problems/``) because a card's payload is prose — see P2-15 in
``VIBECODING.md``.

Two fields exist to keep retrieval honest, and both are validated by
``tools/validate_knowledge_cards.py``:

* ``knowledge_tags`` must come from the tags the problem corpus already uses,
  otherwise a card can never be matched to a problem by tag.
* ``common_errors`` must come from :data:`app.eval_cases.ROOT_CAUSE_LABELS`,
  the closed taxonomy the Agent's output is scored against. A card using a
  label outside it would feed the model vocabulary it cannot be graded on.

This module only reads the corpus. Embedding it and writing it into Milvus is
the indexer's job (not yet written); talking to Milvus is
:mod:`app.rag.retriever`'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import DATA_ROOT

#: Default location of the card corpus.
#:
#: Anchored on ``app.config.DATA_ROOT`` rather than on ``__file__``: the
#: latter points *inside the bundle* once frozen, so ``parents[3]`` resolved
#: to a directory with no ``corpus/`` in it. That made a packaged build
#: unable to see any knowledge card — quietly, since retrieval degrades to
#: "no context" instead of raising (found 2026-09-22).
DEFAULT_CARDS_DIR = DATA_ROOT / "corpus" / "knowledge_cards"

#: Files that live in the corpus directory without being cards.
#: ``README.md`` documents the format; indexing it would put prose *about*
#: the corpus into the retrieval pool.
NON_CARD_FILES: frozenset[str] = frozenset({"README.md"})

_FENCE = "---"

#: Front-matter fields a card must declare. Kept in sync with
#: ``tools/validate_knowledge_cards.py``; a card missing one is a corpus bug,
#: not a card that should be skipped silently.
REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "knowledge_tags",
    "related_problems",
    "common_errors",
)


@dataclass(frozen=True)
class KnowledgeCard:
    """One algorithm knowledge card.

    Attributes:
        id: Stable identifier, also the filename stem. Used as the Milvus
            primary key, so it must be unique across the corpus.
        title: Human-facing Chinese title.
        knowledge_tags: Topics this card covers. Drawn from the problem
            corpus's own tags so the two can be joined.
        related_problems: Problem ids this card is written against, for
            showing the student "this is the concept behind that problem".
        common_errors: Root-cause labels this card warns about, from the
            closed set in :data:`app.eval_cases.ROOT_CAUSE_LABELS`.
        body: The Markdown prose, front matter removed.
        source: File the card was read from — carried for error messages
            and for "where did this come from" during debugging.
    """

    id: str
    title: str
    knowledge_tags: tuple[str, ...]
    related_problems: tuple[str, ...]
    common_errors: tuple[str, ...]
    body: str
    source: Path

    @property
    def embed_text(self) -> str:
        """The text handed to the embedder.

        Title first, then the body: the title names the knowledge point in
        the same words a student would search for, so leaving it out costs
        recall on exactly the queries this corpus exists to serve.

        Tags and problem ids stay **out** — they are short opaque tokens that
        add noise to a semantic embedding while carrying no information the
        body does not already have in context. They are for *filtering*
        (see :func:`cards_for_tags`), which is exact-match and does not need
        the embedder's help.
        """
        return f"{self.title}\n\n{self.body}".strip()

    def covers(self, tag: str) -> bool:
        """Whether this card declares *tag*."""
        return tag in self.knowledge_tags


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, object], str]:
    """Split *text* into (front matter, body), or raise ValueError.

    The filename is attached to every message: with a dozen cards in one
    directory, a bare "missing id" tells you nothing about which file to open.
    """
    import yaml

    if not text.lstrip().startswith(_FENCE):
        raise ValueError(
            f"{path.name}: missing YAML front matter (file must open with ---)"
        )

    _, _, rest = text.lstrip().partition(_FENCE)
    front, separator, body = rest.partition(f"\n{_FENCE}")
    if not separator:
        raise ValueError(f"{path.name}: front matter is not closed with a --- line")

    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: front matter is not valid YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: front matter must be a YAML mapping")

    return loaded, body


def _as_str_tuple(value: object, path: Path, field: str) -> tuple[str, ...]:
    """Coerce a front-matter list field into a tuple of strings.

    A scalar is accepted as a one-element list because ``knowledge_tags: 二分``
    is an easy slip that reads fine in YAML but would otherwise iterate over
    the characters of the string.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise ValueError(f"{path.name}: field {field!r} must be a list of strings")


def parse_card(text: str, path: Path) -> KnowledgeCard:
    """Build one :class:`KnowledgeCard` from raw file *text*.

    Raises:
        ValueError: if a required field is missing or malformed. Message
            always names the file.
    """
    front, body = _split_front_matter(text, path)

    missing = [field for field in REQUIRED_FIELDS if field not in front]
    if missing:
        raise ValueError(
            f"{path.name}: front matter missing field(s): {', '.join(missing)}"
        )

    card_id = str(front["id"]).strip()
    if not card_id:
        raise ValueError(f"{path.name}: id is empty")
    if card_id != path.stem:
        raise ValueError(
            f"{path.name}: id {card_id!r} does not match the filename "
            f"stem {path.stem!r}"
        )

    title = str(front["title"]).strip()
    if not title:
        raise ValueError(f"{path.name}: title is empty")

    if "##" not in body:
        raise ValueError(
            f"{path.name}: body has no '##' section — is this really a card?"
        )

    return KnowledgeCard(
        id=card_id,
        title=title,
        knowledge_tags=_as_str_tuple(front["knowledge_tags"], path, "knowledge_tags"),
        related_problems=_as_str_tuple(
            front["related_problems"], path, "related_problems"
        ),
        common_errors=_as_str_tuple(front["common_errors"], path, "common_errors"),
        body=body.strip(),
        source=path,
    )


def load_knowledge_cards(directory: str | Path | None = None) -> list[KnowledgeCard]:
    """Load every card in *directory*, sorted by id.

    Raises:
        FileNotFoundError: if the directory does not exist.
        ValueError: if a card is malformed. Failing loudly is the point: a
            card silently dropped here is a knowledge point the Agent will
            never retrieve, and nothing downstream would notice.
    """
    root = Path(directory) if directory is not None else DEFAULT_CARDS_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"knowledge card directory not found: {root}")

    cards: list[KnowledgeCard] = []
    seen: set[str] = set()

    for path in sorted(root.glob("*.md")):
        if path.name in NON_CARD_FILES:
            continue
        card = parse_card(path.read_text(encoding="utf-8"), path)
        if card.id in seen:
            raise ValueError(f"{path.name}: duplicate card id {card.id!r}")
        seen.add(card.id)
        cards.append(card)

    cards.sort(key=lambda card: card.id)
    return cards


def cards_for_tags(
    cards: list[KnowledgeCard], tags: list[str] | tuple[str, ...]
) -> list[KnowledgeCard]:
    """Return cards sharing at least one tag with *tags*, best match first.

    This is the cheap pre-filter: given the knowledge tags of the problem a
    student is stuck on, it narrows the corpus before any vector search runs.
    Ranking is by number of shared tags, then id, so the order is stable —
    the retrieval layer's output feeds a prompt, and an unstable order would
    make the same question produce different prompts run to run.
    """
    # ``strip()`` and not just a truthiness check: a whitespace-only tag is
    # truthy, so it would slip through and then match nothing — turning
    # "no tags given" into "no results" for the caller.
    wanted = {stripped for tag in tags if tag and (stripped := tag.strip())}
    if not wanted:
        return []

    scored: list[tuple[int, str, KnowledgeCard]] = []
    for card in cards:
        shared = len(wanted.intersection(card.knowledge_tags))
        if shared:
            scored.append((shared, card.id, card))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [card for _, _, card in scored]
