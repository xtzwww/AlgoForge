"""Tests for the knowledge-card loader (阶段 8 · RAG 语料).

Two kinds of assertion live here, and the split matters:

* **Contract tests** run against hand-written card text, so they pin the
  parsing rules (front matter, id/filename agreement, scalar-vs-list
  coercion) without depending on what the corpus currently happens to say.
* **Corpus tests** run against ``corpus/knowledge_cards/`` itself. They are
  what turns "we wrote twelve cards" into something the suite can check —
  a card silently dropped by the loader is a knowledge point the Agent can
  never retrieve, and nothing downstream would notice.

The corpus tests deliberately assert *invariants* (ids unique, every card
carries tags from the problem corpus, coverage is complete) rather than
exact counts where a count would just be restated every time a card is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.eval_cases import ROOT_CAUSE_LABELS
from app.problems_corpus import load_catalogue
from app.rag.documents import (
    DEFAULT_CARDS_DIR,
    NON_CARD_FILES,
    cards_for_tags,
    load_knowledge_cards,
    parse_card,
)

VALID_CARD = """---
id: sample-card
title: 示例卡片
knowledge_tags: [二分, 数组]
related_problems: [p011-binary-search]
common_errors: [off_by_one]
---

# 示例卡片

## 要点

正文。
"""


# ------------------------------------------------------------------ contract


def test_parses_a_well_formed_card() -> None:
    card = parse_card(VALID_CARD, Path("sample-card.md"))

    assert card.id == "sample-card"
    assert card.title == "示例卡片"
    assert card.knowledge_tags == ("二分", "数组")
    assert card.related_problems == ("p011-binary-search",)
    assert card.common_errors == ("off_by_one",)
    assert "正文" in card.body


def test_front_matter_is_stripped_from_the_body() -> None:
    """The body is what gets embedded, so front matter must not leak into it."""
    card = parse_card(VALID_CARD, Path("sample-card.md"))

    assert "knowledge_tags" not in card.body
    assert card.body.lstrip().startswith("#")


def test_missing_front_matter_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing YAML front matter"):
        parse_card("# 只有正文\n\n## 小节\n", Path("sample-card.md"))


def test_unclosed_front_matter_is_rejected() -> None:
    with pytest.raises(ValueError, match="not closed"):
        parse_card("---\nid: sample-card\ntitle: 标题\n", Path("sample-card.md"))


def test_id_must_match_the_filename() -> None:
    """A mismatch means one card can be referenced two ways."""
    with pytest.raises(ValueError, match="does not match the filename stem"):
        parse_card(VALID_CARD, Path("other-name.md"))


def test_missing_required_field_is_rejected() -> None:
    text = VALID_CARD.replace("common_errors: [off_by_one]\n", "")

    with pytest.raises(ValueError, match="missing field"):
        parse_card(text, Path("sample-card.md"))


def test_a_scalar_tag_is_coerced_to_one_element() -> None:
    """``knowledge_tags: 二分`` reads fine in YAML but is not a list.

    Without coercion the value would be iterated character by character,
    producing tags like "二" and "分" that match nothing.
    """
    text = VALID_CARD.replace("[二分, 数组]", "二分")

    card = parse_card(text, Path("sample-card.md"))

    assert card.knowledge_tags == ("二分",)


def test_body_without_a_section_heading_is_rejected() -> None:
    """Guards against a front-matter-only file passing as a card."""
    text = VALID_CARD.replace("## 要点", "正文")

    with pytest.raises(ValueError, match="no '##' section"):
        parse_card(text, Path("sample-card.md"))


def test_embed_text_leads_with_the_title() -> None:
    """The title is what a student would search for; dropping it costs recall."""
    card = parse_card(VALID_CARD, Path("sample-card.md"))

    assert card.embed_text.startswith("示例卡片")
    assert "正文" in card.embed_text
    # Tags are for filtering, not for the embedder — see KnowledgeCard.embed_text.
    assert "knowledge_tags" not in card.embed_text


# ------------------------------------------------------------------- corpus


def test_default_directory_points_at_the_shipped_corpus() -> None:
    assert DEFAULT_CARDS_DIR.is_dir(), f"corpus missing at {DEFAULT_CARDS_DIR}"
    assert DEFAULT_CARDS_DIR.name == "knowledge_cards"


def test_shipped_corpus_loads() -> None:
    cards = load_knowledge_cards()

    assert cards, "the knowledge corpus is empty —阶段 8 的检索将没有语料"
    assert len({card.id for card in cards}) == len(cards), "card ids must be unique"


def test_every_card_uses_tags_the_problem_corpus_actually_has() -> None:
    """Otherwise the card can never be matched to a problem by tag."""
    problem_tags: set[str] = set()
    for problem in load_catalogue():
        problem_tags.update(problem.knowledge_tags)

    unknown = {
        tag: card.id
        for card in load_knowledge_cards()
        for tag in card.knowledge_tags
        if tag not in problem_tags
    }

    assert not unknown, f"卡片用了题库没有的知识点标签：{unknown}"


def test_every_card_uses_labels_from_the_closed_set() -> None:
    """Cards are prompt context; a label outside the taxonomy cannot be scored."""
    allowed = set(ROOT_CAUSE_LABELS)

    unknown = {
        label: card.id
        for card in load_knowledge_cards()
        for label in card.common_errors
        if label not in allowed
    }

    assert not unknown, f"卡片用了闭集外的错因标签：{unknown}"


def test_every_card_points_at_real_problems() -> None:
    known_ids = {problem.problem_id for problem in load_catalogue()}

    dangling = {
        pid: card.id
        for card in load_knowledge_cards()
        for pid in card.related_problems
        if pid not in known_ids
    }

    assert not dangling, f"卡片引用了不存在的题号：{dangling}"


def test_every_knowledge_tag_is_covered_by_some_card() -> None:
    """Coverage is the point of the corpus: a tag with no card is a dead end.

    The retrieval layer filters by tag, so an uncovered tag means the Agent
    gets no context for any problem carrying it — silently.
    """
    problem_tags: set[str] = set()
    for problem in load_catalogue():
        problem_tags.update(problem.knowledge_tags)

    covered = {tag for card in load_knowledge_cards() for tag in card.knowledge_tags}

    missing = problem_tags - covered
    assert missing == set(), f"未被覆盖的知识点：{sorted(missing)}"


def test_loading_skips_non_card_files() -> None:
    """README.md documents the format; indexing it would put prose about the
    corpus into the retrieval pool."""
    ids = {card.id for card in load_knowledge_cards()}

    assert "README" not in ids
    existing = {path.name for path in DEFAULT_CARDS_DIR.glob("*.md")}
    stale = NON_CARD_FILES - existing
    assert not stale, f"被列为非卡片文件但不存在：{stale}——守卫已过期"


def test_missing_directory_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_knowledge_cards(DEFAULT_CARDS_DIR / "does-not-exist")


# --------------------------------------------------------------- tag filtering


def test_cards_for_tags_ranks_by_number_of_shared_tags() -> None:
    cards = load_knowledge_cards()

    ranked = cards_for_tags(cards, ["动态规划", "背包问题"])

    assert ranked, "expected at least one DP card"
    assert ranked[0].id == "knapsack", f"背包卡片应排第一，实际是 {ranked[0].id}"


def test_cards_for_tags_is_stable_for_equal_scores() -> None:
    """Order feeds a prompt; unstable order would make the same question
    produce a different prompt on each run."""
    cards = load_knowledge_cards()

    first = [card.id for card in cards_for_tags(cards, ["基础语法"])]
    second = [card.id for card in cards_for_tags(list(reversed(cards)), ["基础语法"])]

    assert first == second


def test_cards_for_tags_with_no_tags_returns_nothing() -> None:
    """An empty tag list means "no filter", not "match everything" —
    callers pass the problem's tags, and a problem without tags should not
    drag the whole corpus into the prompt."""
    assert cards_for_tags(load_knowledge_cards(), []) == []


def test_cards_for_tags_ignores_blank_entries() -> None:
    assert cards_for_tags(load_knowledge_cards(), ["", "  "]) == []
