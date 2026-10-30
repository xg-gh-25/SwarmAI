"""Tests for the md_lock() contextmanager KNOWLEDGE.md's writers serialize on
(mirroring how MEMORY's writers share MEMORY.md.lock).

The KNOWLEDGE.md decay->archive->strip loop these tests used to drive is gone:
every entry in a main file is in force (design 2026-10-30-brain-driven §2.3).
"""
from __future__ import annotations


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
