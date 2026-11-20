"""Fetched feeds and the briefs built from them are never auto-injected, wherever they sit
(design docs/design/2026-11-20-inbox.md §10, §11 test 13).

- ``core.recall_filter.filter_recall`` drops a hit of ANY domain whose path has a
  ``DailyBriefs`` or ``Signals`` folder anywhere in it (nested folders and
  ``Knowledge/Archives/`` included), not only at the start of the path.
- The files stay in the Library index (an explicit search still finds them): a real
  ``sync_knowledge_index`` over a tmp ``Knowledge/`` indexes them, the real library leg
  returns them, and ``filter_recall`` over that result keeps only the ordinary note.
- ``core.recall_multi.is_external_source`` fences a ``Signals`` or ``DailyBriefs`` file
  at any depth as untrusted (a brief is a model's summary of fetched messages), so an
  on-demand read of the archived history still reads as data. Folder names compare
  case-insensitively.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from core.recall_filter import filter_recall, is_no_inject
from core.recall_multi import BucketedRecall, is_external_source

_Q = "zebrafish migration ledger"
_TEXT = "zebrafish migration ledger notes " + "x" * 80

_BLOCKED = [
    "Signals/2026-10-01-digest.md",
    "Archives/Signals/2026-09-01-weekly.md",
    "Archives/DailyBriefs/2026-11-18.md",
    "Notes/research/Signals/feed.md",
    "Notes/team/DailyBriefs/2026-11-19.md",
    "Notes/signals/lower-case.md",
]


def _lib(src: str) -> dict:
    return {"source": src, "heading": "h", "content": _TEXT, "score": 1.0}


@pytest.mark.parametrize("src", _BLOCKED)
def test_segment_blocked_path_is_no_inject(src):
    assert is_no_inject({"source": src})
    assert is_no_inject({"doc": src})


@pytest.mark.parametrize("src", ["Notes/plain.md", "Notes/Signals.md", "Designs/signals-design.md",
                                 "Archives/MEMORY-archive-2026-04.md"])
def test_ordinary_path_is_injectable(src):
    assert not is_no_inject({"source": src})


def test_segment_blocked_in_every_domain():
    buckets = {
        "library": [_lib(s) for s in _BLOCKED] + [_lib("Notes/plain.md")],
        "ddd": [{"doc": "knowledge/Signals/x.md", "section": "s", "content": _TEXT, "score": 1.0},
                {"doc": "TECH.md", "section": "s", "content": _TEXT, "score": 1.0}],
    }
    out = filter_recall(BucketedRecall(query=_Q, buckets=buckets))
    assert [h["source"] for h in out.buckets["library"]] == ["Notes/plain.md"]
    assert [h["doc"] for h in out.buckets["ddd"]] == ["TECH.md"]


def test_all_blocked_leg_is_marked_filtered():
    out = filter_recall(BucketedRecall(query=_Q, buckets={"library": [_lib(s) for s in _BLOCKED]},
                                       hit_layers={"library": "fts"}))
    assert out.buckets["library"] == []
    assert out.hit_layers["library"] == "filtered"


def test_indexed_but_never_auto_injected(tmp_path, monkeypatch):
    from core import vec_db
    from core.knowledge_store import KnowledgeStore, sync_knowledge_index
    from core.recall_multi import _recall_library

    k = tmp_path / "Knowledge"
    # distinct bodies: the library leg collapses chunks with the same text
    files = {
        "Notes/plain.md": "# Plain\n\n## Ledger\n\nour own " + _TEXT,
        "Notes/research/Signals/feed.md": "# Feed\n\n## Ledger\n\nfetched feed " + _TEXT,
        "Archives/DailyBriefs/2026-11-18.md": "# Brief\n\n## Ledger\n\nmorning brief " + _TEXT,
        "Archives/Signals/2026-09-01-weekly.md": "# Weekly\n\n## Ledger\n\nweekly rollup " + _TEXT,
    }
    for rel, body in files.items():
        p = k / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    store = KnowledgeStore(conn)
    store.ensure_tables()
    sync_knowledge_index(store, k)
    assert set(files) <= store.get_indexed_files(), "every file is in the Library index"

    @contextmanager
    def _open(*_a, **_k):
        yield conn

    monkeypatch.setattr(vec_db, "open_vec_db", _open)
    hits, layer = _recall_library(_Q)
    assert layer == "fts"
    assert set(files) <= {h["source"] for h in hits}, "an explicit Library search finds them all"

    out = filter_recall(BucketedRecall(query=_Q, buckets={"library": hits}))
    assert [h["source"] for h in out.buckets["library"]] == ["Notes/plain.md"]


@pytest.mark.parametrize("src", ["Signals/a.md", "Archives/Signals/a.md", "Notes/x/Signals/a.md",
                                 "Archives/DailyBriefs/2026-11-01.md", "Notes/dailybriefs/b.md"])
def test_fetched_content_at_any_depth_is_external(src):
    assert is_external_source(src)


@pytest.mark.parametrize("src", ["Notes/a.md", "Notes/Signals.md", "Designs/daily-briefs.md"])
def test_other_paths_are_not_external(src):
    assert not is_external_source(src)


def test_the_fence_and_the_no_inject_rule_share_one_set():
    from core.recall_filter import NO_INJECT_SEGMENTS
    from core.recall_multi import EXTERNAL_SOURCE_SEGMENTS
    assert NO_INJECT_SEGMENTS is EXTERNAL_SOURCE_SEGMENTS
