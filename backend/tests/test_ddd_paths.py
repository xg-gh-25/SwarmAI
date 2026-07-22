"""Tests for ddd_paths — the six-section layout resolver (SSOT).

This resolver is the P1 indirection layer of the DDD six-section
self-explaining tree redesign (design 2026-07-21). Every consumer that used to
hardcode `project_dir / "TECH.md"` or `base / "Knowledge"` routes through
ddd_path() so the physical layout lives in ONE place.

Contract under test:
  - ddd_path(project_dir, key) maps section keys + the 4 canonical docs to the
    numbered six-section layout (2-understanding/, 3-gates/, ...).
  - AGENTS.md stays at project root (the external "this is a DDD" door-plate).
  - Strangler READ: during migration, a doc/dir present at the OLD path resolves
    to the OLD path (new-then-old fallback); WRITE always targets NEW.
"""

from __future__ import annotations

import pytest

from core import ddd_paths


# ─── Tracer bullet: the core doc-resolution contract ─────────────────────────

def test_canonical_doc_resolves_to_understanding_dir(tmp_path):
    """A canonical doc key resolves to 2-understanding/<doc> for a fresh tree."""
    proj = tmp_path / "MyBrain"
    (proj / "2-understanding").mkdir(parents=True)
    (proj / "2-understanding" / "TECH.md").write_text("# tech")

    resolved = ddd_paths.ddd_path(proj, "TECH.md")

    assert resolved == proj / "2-understanding" / "TECH.md"


# ─── Strangler READ: new-then-old fallback (the split-brain guard) ───────────

def test_read_falls_back_to_old_path_when_unmigrated(tmp_path):
    """An un-migrated DDD (doc still at root) resolves to the OLD path on READ."""
    proj = tmp_path / "LegacyBrain"
    proj.mkdir()
    (proj / "TECH.md").write_text("# tech at old root")  # NOT migrated yet

    resolved = ddd_paths.ddd_path(proj, "TECH.md")

    assert resolved == proj / "TECH.md"  # old path, because new is absent


def test_read_prefers_new_path_when_both_exist(tmp_path):
    """Once migrated, READ prefers the NEW path even if an old stub lingers."""
    proj = tmp_path / "MigratedBrain"
    (proj / "2-understanding").mkdir(parents=True)
    (proj / "2-understanding" / "TECH.md").write_text("# new")
    (proj / "TECH.md").write_text("# stale old stub")

    resolved = ddd_paths.ddd_path(proj, "TECH.md")

    assert resolved == proj / "2-understanding" / "TECH.md"


def test_read_defaults_to_new_when_neither_exists(tmp_path):
    """When neither exists, READ returns the NEW (write-forward) path."""
    proj = tmp_path / "EmptyBrain"
    proj.mkdir()

    resolved = ddd_paths.ddd_path(proj, "PRODUCT.md")

    assert resolved == proj / "2-understanding" / "PRODUCT.md"


# ─── Strangler WRITE: always NEW, never old (no dual-write) ──────────────────

def test_write_always_targets_new_even_when_old_exists(tmp_path):
    """WRITE must be deterministic-new; an old file present must NOT redirect it."""
    proj = tmp_path / "MigratingBrain"
    proj.mkdir()
    (proj / "IMPROVEMENT.md").write_text("# old root copy")  # old still present

    write_path = ddd_paths.ddd_write_path(proj, "IMPROVEMENT.md")

    assert write_path == proj / "2-understanding" / "IMPROVEMENT.md"
    assert write_path.parent.is_dir()  # parent auto-created for immediate write


# ─── AGENTS.md stays at ROOT in both layouts (H4 door-plate) ─────────────────

def test_agents_md_stays_at_root(tmp_path):
    proj = tmp_path / "AnyBrain"
    (proj / "2-understanding").mkdir(parents=True)
    assert ddd_paths.ddd_path(proj, "AGENTS.md") == proj / "AGENTS.md"
    assert ddd_paths.ddd_write_path(proj, "AGENTS.md") == proj / "AGENTS.md"


# ─── Section dirs resolve to numbered layout ─────────────────────────────────

def test_section_dirs_map_to_numbered_layout(tmp_path):
    proj = tmp_path / "SectBrain"
    for d in ("3-gates", "4-capabilities", "2-understanding/knowledge"):
        (proj / d).mkdir(parents=True)

    assert ddd_paths.ddd_path(proj, "gates") == proj / "3-gates"
    assert ddd_paths.ddd_path(proj, "capabilities") == proj / "4-capabilities"
    assert ddd_paths.ddd_path(proj, "skills") == proj / "4-capabilities"  # legacy alias
    assert ddd_paths.ddd_path(proj, "knowledge") == proj / "2-understanding" / "knowledge"


def test_section_subpath_resolves_under_section(tmp_path):
    """A sub-path key ('knowledge/rocky.md') resolves under the section dir."""
    proj = tmp_path / "SubBrain"
    (proj / "2-understanding" / "knowledge").mkdir(parents=True)
    (proj / "2-understanding" / "knowledge" / "rocky.md").write_text("x")

    resolved = ddd_paths.ddd_path(proj, "knowledge/rocky.md")

    assert resolved == proj / "2-understanding" / "knowledge" / "rocky.md"


def test_unmigrated_knowledge_dir_falls_back(tmp_path):
    """An un-migrated per-DDD Knowledge/ resolves to the OLD dir on READ."""
    proj = tmp_path / "LegacyKnow"
    (proj / "Knowledge").mkdir(parents=True)
    (proj / "Knowledge" / "note.md").write_text("x")

    assert ddd_paths.ddd_path(proj, "knowledge") == proj / "Knowledge"
