"""E2E tests for KNOWLEDGE.md archive+strip lifecycle (run_a1ec08e7).

Drives the REAL _run_knowledge_lifecycle (no mock of the SUT) to verify the
decay→archive→strip loop closes for KNOWLEDGE.md the same way MEMORY's did —
but with KNOWLEDGE_EVERGREEN_SECTIONS protecting load-bearing reference sections
(Architecture Overview, The 11 Context Files, etc.) from being stripped.

Also tests the md_lock() contextmanager (all 4 KNOWLEDGE writers must
serialize on it, mirroring how MEMORY's writers share MEMORY.md.lock).

Mutation contract (verified in BUILD):
  - remove reclaim_noise_entries → old-unused NOT stripped → RED
  - remove KNOWLEDGE_EVERGREEN_SECTIONS from reclaim → evergreen entry stripped → RED
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.ddd_entry_lifecycle import parse_entries
from hooks.context_health_hook import ContextHealthHook


def _old(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _knowledge_doc() -> str:
    """KNOWLEDGE.md with an evergreen reference section + a disposable section.

    - "Architecture Overview" section (EVERGREEN): an old guideline that MUST be
      protected from strip even though it is dormant + old + guideline-typed.
    - "Scratch Notes" section (NON-evergreen): an old guideline that SHOULD be
      archived+stripped, and a fresh one that stays.
    """
    old = _old(120)
    today = date.today().isoformat()
    return f"""## Architecture Overview [model]

- [guideline] **load bearing arch fact** — port 18321, launchd label. ({old})
  <!-- ref:0 | last:{old} | decay:active -->

## Scratch Notes

- [guideline] **old disposable note** — an old operational scribble nobody cites. ({old})
  <!-- ref:0 | last:{old} | decay:active -->
- [guideline] **fresh disposable note** — written today. ({today})
  <!-- ref:0 | last:{today} | decay:active -->
"""


@pytest.fixture
def ws(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "KNOWLEDGE.md").write_text(_knowledge_doc(), encoding="utf-8")
    return tmp_path


@pytest.fixture
def hook():
    return ContextHealthHook()


class TestKnowledgeReclaim:
    def test_old_disposable_stripped(self, hook, ws):
        """AC3/AC5: an old dormant guideline in a NON-evergreen section is
        archived+stripped."""
        hook._run_knowledge_lifecycle(ws)
        content = (ws / ".context" / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "old disposable note" not in content, (
            "old non-evergreen note should be archived+stripped"
        )

    def test_stripped_entry_archived(self, hook, ws):
        """AC3: stripped entry preserved in KNOWLEDGE-archive.md (reversible)."""
        hook._run_knowledge_lifecycle(ws)
        archive = ws / ".context" / "KNOWLEDGE-archive.md"
        assert archive.exists(), "KNOWLEDGE-archive.md should be created"
        assert "old disposable note" in archive.read_text(encoding="utf-8")

    def test_evergreen_reference_protected(self, hook, ws):
        """AC5 (the CRITICAL Gate-1 finding): a load-bearing reference entry in
        an evergreen section is NEVER stripped, even when old+dormant+guideline."""
        hook._run_knowledge_lifecycle(ws)
        content = (ws / ".context" / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "load bearing arch fact" in content, (
            "evergreen Architecture Overview entry must NOT be stripped"
        )

    def test_fresh_entry_kept(self, hook, ws):
        """AC5: fresh entry within grace is kept."""
        hook._run_knowledge_lifecycle(ws)
        content = (ws / ".context" / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "fresh disposable note" in content

    def test_no_double_archive(self, hook, ws):
        """Regression guard (same class as MEMORY): reclaim is the single archive
        authority — a stripped entry appears exactly once in the archive."""
        hook._run_knowledge_lifecycle(ws)
        archive = (ws / ".context" / "KNOWLEDGE-archive.md").read_text(encoding="utf-8")
        assert archive.count("old disposable note") == 1


class TestKnowledgeMdLock:
    def test_nonblocking_lock_excludes_second_holder(self, tmp_path):
        """AC1/AC6: md_lock(blocking=False) yields False when the lock
        is already held by another fd."""
        from utils.file_lock import md_lock

        md = tmp_path / "KNOWLEDGE.md"
        md.write_text("x", encoding="utf-8")
        with md_lock(md, blocking=True) as got_first:
            assert got_first is True
            with md_lock(md, blocking=False) as got_second:
                assert got_second is False, (
                    "second non-blocking acquire must fail while first is held"
                )

    def test_blocking_lock_acquires_when_free(self, tmp_path):
        """AC1: blocking lock yields True when free and releases after."""
        from utils.file_lock import md_lock

        md = tmp_path / "KNOWLEDGE.md"
        md.write_text("x", encoding="utf-8")
        with md_lock(md, blocking=True) as got:
            assert got is True
        # released — can re-acquire
        with md_lock(md, blocking=True) as got2:
            assert got2 is True
