"""Validate the knowledge-card corpus (阶段 8 · RAG 语料).

Every card under ``corpus/knowledge_cards/`` is checked for:

1. **Front matter present and complete** — ``id`` / ``title`` /
   ``knowledge_tags`` / ``related_problems`` / ``common_errors``, plus a
   non-empty Markdown body with at least one ``##`` section.
2. **``id`` matches the filename.** The indexer will key documents by id,
   and a mismatch means the same card can be referenced two ways.
3. **``related_problems`` name real problems.** A card pointing at a
   problem that does not exist would surface a dangling reference in the
   Agent's retrieved context.
4. **``knowledge_tags`` come from the problem corpus.** Free-form tags
   cannot be matched against a problem's tags at retrieval time, which is
   the only thing that makes filtering possible.
5. **``common_errors`` come from the closed label set.** This one matters
   most: ``ROOT_CAUSE_LABELS`` is a closed taxonomy that both the Agent's
   output and the evaluation scoring are anchored to. A card mentioning a
   label outside it would feed the model vocabulary it cannot be scored
   against — see ``corpus/knowledge_cards/README.md``.

Finally it reports which of the problem corpus's knowledge tags are
covered by at least one card, so gaps are visible rather than assumed.

Run it after editing any card:

    python tools/validate_knowledge_cards.py

Exit code is non-zero if any card fails validation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))

import yaml  # noqa: E402

from app.eval_cases import ROOT_CAUSE_LABELS  # noqa: E402

CARDS_DIR = ROOT / "corpus" / "knowledge_cards"
PROBLEMS_DIR = ROOT / "corpus" / "problems"

REQUIRED_FIELDS = ("id", "title", "knowledge_tags", "related_problems", "common_errors")

#: Not a card — it documents the format.
SKIP_FILES = {"README.md"}


def _split_front_matter(text: str) -> tuple[dict[str, object] | None, str]:
    """Split a card into its YAML front matter and Markdown body.

    Returns ``(None, body)`` when the file does not open with a front-matter
    block, which the caller reports as an error rather than a crash.
    """
    if not text.lstrip().startswith("---"):
        return None, text
    _, _, rest = text.lstrip().partition("---")
    front, sep, body = rest.partition("\n---")
    if not sep:
        return None, text
    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError:
        return None, body
    if not isinstance(loaded, dict):
        return None, body
    return loaded, body


def _load_problem_facts() -> tuple[set[str], set[str]]:
    """Return (all problem ids, all knowledge tags used by the corpus)."""
    ids: set[str] = set()
    tags: set[str] = set()
    for path in sorted(PROBLEMS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        ids.add(data["id"])
        tags.update(data.get("knowledge_tags") or [])
    return ids, tags


def validate_card(
    path: Path,
    front: dict[str, object] | None,
    body: str,
    problem_ids: set[str],
    problem_tags: set[str],
) -> list[str]:
    """Return a list of human-readable problems; empty means the card is fine."""
    problems: list[str] = []

    if front is None:
        return ["缺少 YAML front-matter（文件应以 --- 开头，且以 --- 结束）"]

    for field in REQUIRED_FIELDS:
        if field not in front:
            problems.append(f"front-matter 缺少字段：{field}")
    if problems:
        return problems

    card_id = str(front["id"])
    if card_id != path.stem:
        problems.append(f"id（{card_id}）与文件名（{path.stem}）不一致")

    if not str(front["title"]).strip():
        problems.append("title 为空")

    for tag in front["knowledge_tags"] or []:
        if tag not in problem_tags:
            problems.append(f"knowledge_tags 含题库未使用的标签：{tag}")

    for pid in front["related_problems"] or []:
        if pid not in problem_ids:
            problems.append(f"related_problems 指向不存在的题号：{pid}")

    for label in front["common_errors"] or []:
        if label not in ROOT_CAUSE_LABELS:
            problems.append(f"common_errors 含闭集外的错因标签：{label}")

    if "##" not in body:
        problems.append("正文没有 ## 小节")

    return problems


def main() -> int:
    problem_ids, problem_tags = _load_problem_facts()

    cards = [p for p in sorted(CARDS_DIR.glob("*.md")) if p.name not in SKIP_FILES]
    if not cards:
        print(f"{CARDS_DIR} 下没有卡片。")
        return 1

    failed = 0
    covered_tags: set[str] = set()

    for path in cards:
        text = path.read_text(encoding="utf-8")
        front, body = _split_front_matter(text)
        problems = validate_card(path, front, body, problem_ids, problem_tags)
        if problems:
            failed += 1
            print(f"[FAIL] {path.name}")
            for item in problems:
                print(f"       - {item}")
        else:
            assert front is not None  # validate_card guarantees this
            covered_tags.update(front["knowledge_tags"] or [])
            print(f"[ OK ] {path.name}")

    missing = sorted(problem_tags - covered_tags)
    print()
    print(f"卡片数：{len(cards)}，通过：{len(cards) - failed}，失败：{failed}")
    print(f"知识点覆盖：{len(covered_tags)}/{len(problem_tags)}")
    if missing:
        print(f"未被任何卡片覆盖的标签：{'、'.join(missing)}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
