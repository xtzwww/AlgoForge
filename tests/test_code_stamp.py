"""Tests for app.code_stamp — the fingerprint published on /health.

The value matters because two independent implementations must agree on
it: this one, and ``tools/sidecar-stale.mjs``, which recomputes the stamp
from disk in Node so the launcher can spot a backend running older code.
These tests pin the properties that make agreement possible — determinism,
path-order independence, and which files count — without depending on the
real ``app/`` tree, which changes on every edit.
"""

import re

import pytest

from app import code_stamp

HEX_16 = re.compile(r"^[0-9a-f]{16}$")


@pytest.fixture()
def fake_app(tmp_path, monkeypatch):
    """Point the stamp at a throwaway package tree."""
    monkeypatch.setattr(code_stamp, "APP_DIR", tmp_path)
    return tmp_path


def test_stamp_is_a_short_hex_digest(fake_app) -> None:
    (fake_app / "one.py").write_text("x = 1\n", encoding="utf-8")
    stamp = code_stamp.current_code_stamp()
    assert stamp is not None
    assert HEX_16.match(stamp)


def test_stamp_is_stable_across_calls(fake_app) -> None:
    """Same bytes in, same value out — otherwise every launch restarts."""
    (fake_app / "one.py").write_text("x = 1\n", encoding="utf-8")
    (fake_app / "two.py").write_text("y = 2\n", encoding="utf-8")
    assert code_stamp.current_code_stamp() == code_stamp.current_code_stamp()


def test_stamp_ignores_directory_enumeration_order(fake_app) -> None:
    """Content order is by path, so creation order must not matter.

    Windows and Linux hand back directory entries in different orders, and
    the Node side of this comparison walks the tree itself.
    """
    for name in ("a.py", "b.py", "c.py"):
        (fake_app / name).write_text(f"{name}\n", encoding="utf-8")
    first = code_stamp.current_code_stamp()

    for name in ("c.py", "a.py", "b.py"):
        (fake_app / name).unlink()
    for name in ("c.py", "a.py", "b.py"):
        (fake_app / name).write_text(f"{name}\n", encoding="utf-8")

    assert code_stamp.current_code_stamp() == first


def test_stamp_changes_when_content_changes(fake_app) -> None:
    (fake_app / "one.py").write_text("x = 1\n", encoding="utf-8")
    before = code_stamp.current_code_stamp()
    (fake_app / "one.py").write_text("x = 2\n", encoding="utf-8")
    assert code_stamp.current_code_stamp() != before


def test_stamp_changes_when_a_file_is_added(fake_app) -> None:
    (fake_app / "one.py").write_text("x = 1\n", encoding="utf-8")
    before = code_stamp.current_code_stamp()

    # A rename with identical bytes still has to register: the path is
    # part of what is hashed.
    (fake_app / "one.py").rename(fake_app / "renamed.py")
    assert code_stamp.current_code_stamp() != before

    # And so does a brand new file beside it.
    after_rename = code_stamp.current_code_stamp()
    (fake_app / "extra.py").write_text("z = 3\n", encoding="utf-8")
    assert code_stamp.current_code_stamp() != after_rename


def test_stamp_covers_non_python_files(fake_app) -> None:
    """Prompt templates live under app/ and are cached in-process.

    Editing one needs a restart just as much as editing a .py does, so the
    fingerprint has to move when it changes.
    """
    (fake_app / "prompts").mkdir()
    (fake_app / "prompts" / "analyze.md").write_text("v1\n", encoding="utf-8")
    before = code_stamp.current_code_stamp()
    (fake_app / "prompts" / "analyze.md").write_text("v2\n", encoding="utf-8")
    assert code_stamp.current_code_stamp() != before


def test_stamp_ignores_pycache_and_bytecode(fake_app) -> None:
    """Build output changes on every import and must not count.

    If it did, the launcher would restart a healthy backend on every run.
    """
    (fake_app / "one.py").write_text("x = 1\n", encoding="utf-8")
    before = code_stamp.current_code_stamp()

    cache = fake_app / "__pycache__"
    cache.mkdir()
    (cache / "one.cpython-313.pyc").write_bytes(b"\x00\x01\x02")
    (fake_app / "stray.pyc").write_bytes(b"\x03\x04")

    assert code_stamp.current_code_stamp() == before


def test_stamp_is_none_when_there_is_nothing_to_hash(fake_app) -> None:
    """An empty tree is "unknown", not "empty digest".

    A frozen build ships no readable sources; the caller has to be able to
    tell that apart from a real fingerprint, because unknown means restart.
    """
    assert code_stamp.current_code_stamp() is None


def test_real_tree_has_a_stamp() -> None:
    """Guard against the exclusions ever swallowing the whole package."""
    assert code_stamp.current_code_stamp() is not None
