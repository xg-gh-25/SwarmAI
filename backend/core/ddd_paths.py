"""ddd_paths — the six-section DDD layout resolver (single source of truth).

The P1 indirection layer of the DDD six-section self-explaining tree redesign
(design 2026-07-21). Before this module, ~18 sites hardcoded
`project_dir / "TECH.md"` and `base / "Knowledge"`; the physical layout was
smeared across the codebase, so a paradigm change meant a hunt-every-file edit
(the GUI10/OT07 bug class) and the tree could not be renamed to be
self-explaining without breaking loaders.

This resolver centralizes the mapping from a logical KEY (a canonical doc name
or a section name) to its PHYSICAL path under the numbered six-section tree:

    <project>/
    ├── AGENTS.md            ← ① Identity (stays at ROOT — external door-plate)
    ├── 2-understanding/     ← ② Understanding: 4 canonical docs + knowledge/ corpus
    │   ├── PRODUCT.md TECH.md IMPROVEMENT.md PROJECT.md
    │   └── knowledge/       ← recall corpus (was Projects/<x>/Knowledge/)
    ├── 3-gates/             ← ③ Gates (was gates/)
    ├── 4-capabilities/      ← ④ Capabilities (was skills/)
    ├── aim.json             ← ① manifest (root) · bindings.yaml ⑤ + REFRESHER.md ⑥
    │                          are single well-known files kept at ROOT for now
    │                          (bindings.yaml has 67 consumers — relocating it is a
    │                          separate contract migration, not bundled here).
    └── .artifacts/          ← pipeline working memory (not a section)

**Strangler migration semantics (design H, run Gate-1=B):**
  - READ  (``ddd_path``): new-then-old fallback. If the NEW path is absent but
    the file/dir exists at its OLD (pre-migration) location, return the OLD path
    so un-migrated projects keep resolving. This is what makes per-DDD rollout
    safe (no big-bang).
  - WRITE (``ddd_write_path``): ALWAYS the new path. Never dual-writes — a write
    that could land at old-or-new is the split-brain bug class this design kills.

The 4 canonical doc NAMES remain ``project_registry.DDD_CANONICAL_DOCS`` (their
SSOT is unchanged); this module maps their LOCATION only.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.project_registry import DDD_CANONICAL_DOCS

# ─── Layout constants (the ONE place the physical tree is described) ─────────

# Section directory names, numbered so the file tree reads ①→⑥ top-to-bottom.
UNDERSTANDING_DIR = "2-understanding"
GATES_DIR = "3-gates"
CAPABILITIES_DIR = "4-capabilities"
# ⑤ Delivery (bindings.yaml) + ⑥ Refresher (REFRESHER.md) are single well-known
# files kept at ROOT for now — bindings.yaml has 67 consumers, so relocating it
# into a 5-delivery/ dir is a separate contract migration deliberately NOT bundled
# into this run. These constants name the FUTURE dirs (reserved) but the active
# key map resolves ⑤⑥ to root until that migration happens.
DELIVERY_DIR = "5-delivery"     # reserved (not yet materialized)
REFRESHER_DIR = "6-refresher"   # reserved (not yet materialized)

# The recall corpus lives UNDER ② (was the per-DDD Projects/<x>/Knowledge/).
KNOWLEDGE_CORPUS_DIR = f"{UNDERSTANDING_DIR}/knowledge"

# ① Identity file — stays at project ROOT (the external "this is a DDD" marker;
# Claude Code / Kiro / Quick read it at a fixed root path, design H4).
IDENTITY_FILE = "AGENTS.md"

# Logical section KEY → physical relative dir under the project root.
# Callers use the KEY; only this map knows the numbered layout.
_SECTION_KEY_TO_DIR: dict[str, str] = {
    "understanding": UNDERSTANDING_DIR,
    "knowledge": KNOWLEDGE_CORPUS_DIR,   # ② recall corpus
    "gates": GATES_DIR,
    "capabilities": CAPABILITIES_DIR,
    "skills": CAPABILITIES_DIR,          # legacy alias → ④
    "delivery": ".",                     # ⑤ bindings.yaml kept at root (see note above)
    "refresher": ".",                    # ⑥ REFRESHER.md kept at root (see note above)
}

# For each logical KEY, where the same content lived BEFORE the migration.
# Used by the strangler READ fallback so un-migrated DDDs keep resolving.
_SECTION_KEY_TO_OLD_DIR: dict[str, str] = {
    "understanding": ".",         # 4 docs were at project root
    "knowledge": "Knowledge",     # per-DDD Knowledge/
    "gates": "gates",
    "capabilities": "skills",
    "skills": "skills",
    "delivery": ".",              # aim.json / bindings.yaml were at root
    "refresher": ".",             # REFRESHER.md was at root
}

_CANONICAL_DOC_SET = frozenset(DDD_CANONICAL_DOCS)


def _relpath(key: str, *, new: bool) -> str:
    """Relative path (from project root) for a logical key, new or old layout.

    A canonical doc key (e.g. "TECH.md") → under ② (new) or root (old).
    A section key (e.g. "gates") → the section dir (new) or its old dir.
    A path-like key ("knowledge/foo.md") → prefixed with the corpus dir.
    """
    # Canonical doc: lives directly under ② (new) or at root (old).
    if key in _CANONICAL_DOC_SET:
        return f"{UNDERSTANDING_DIR}/{key}" if new else key

    # AGENTS.md never moves — root in both layouts.
    if key == IDENTITY_FILE:
        return key

    # Section key (possibly with a trailing sub-path: "knowledge/foo.md").
    head, _, tail = key.partition("/")
    table = _SECTION_KEY_TO_DIR if new else _SECTION_KEY_TO_OLD_DIR
    if head in table:
        base = table[head]
        if base == ".":
            return tail or ""
        return f"{base}/{tail}" if tail else base

    # Unknown key: pass through unchanged (root-relative). Lets callers resolve
    # ad-hoc files (e.g. ".artifacts") without the resolver having to enumerate
    # every possible name.
    return key


def ddd_path(project_dir, key: str) -> Path:
    """Resolve a logical KEY to its physical path under ``project_dir`` (READ).

    Strangler semantics: returns the NEW (numbered-layout) path, UNLESS the new
    path does not exist AND the OLD path does — then returns the OLD path so an
    un-migrated DDD keeps resolving. This makes per-DDD rollout safe.

    Args:
        project_dir: the DDD project root (str or Path).
        key: a canonical doc name ("TECH.md"), a section key ("gates",
             "knowledge", "capabilities", ...), or a section sub-path
             ("knowledge/rocky.md"). "AGENTS.md" always resolves to root.

    Returns:
        Absolute-or-project-relative Path. If neither new nor old exists, the
        NEW path is returned (the canonical write-forward location).
    """
    root = Path(project_dir)
    new_rel = _relpath(key, new=True)
    new_path = root / new_rel if new_rel else root

    old_rel = _relpath(key, new=False)
    old_path = root / old_rel if old_rel else root

    if new_path == old_path:
        return new_path
    if not new_path.exists() and old_path.exists():
        return old_path
    return new_path


def ddd_write_path(project_dir, key: str) -> Path:
    """Resolve a logical KEY to its NEW physical path (WRITE — always new).

    Never falls back to the old layout: a write must land deterministically at
    the new location, or reads and writes diverge (split-brain). Creates parent
    dirs so the caller can write immediately.
    """
    root = Path(project_dir)
    new_rel = _relpath(key, new=True)
    path = root / new_rel if new_rel else root
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def section_dir(project_dir, section_key: str) -> Path:
    """Resolve a section directory KEY to its path (READ, strangler-aware)."""
    return ddd_path(project_dir, section_key)
