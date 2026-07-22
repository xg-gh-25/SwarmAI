"""Grep-CI gate: DDD section/doc PATHS have ONE source of truth (ddd_paths).

The six-section self-explaining tree redesign (2026-07-21) centralized the
physical DDD layout in ``core.ddd_paths`` (the P1 indirection resolver). Before
it, ~18 sites hardcoded ``project_dir / "TECH.md"`` and ``base / "Knowledge"`` —
the GUI10/OT07 "change-the-layout → hunt-every-file" bug class, and the reason
the tree could not be renamed to be self-explaining without breaking loaders.

This test is the anti-regression backstop (P7 — a gate, not a prose rule): it
FAILS if a stray hardcoded section-dir / canonical-doc PATH JOIN reappears under
``backend/`` outside the sanctioned spots. A new one = the single-source
guarantee broke = fix by routing through ``ddd_paths.ddd_path()`` instead.

What it flags (a path JOIN, not a mere mention):
  - ``<expr> / "PRODUCT.md" | "TECH.md" | "IMPROVEMENT.md" | "PROJECT.md"``
  - ``<expr> / "Knowledge"`` used as a PER-DDD section dir

Sanctioned exceptions (allowlisted):
  1. ``core/ddd_paths.py`` — the ONE resolver (defines the layout).
  2. ``# ddd-six-section-fallback`` / ``# ddd-canonical-fallback`` tagged lines —
     guarded-import fallbacks in zero-backend-dep job/script contexts.
  3. ``backend/templates/`` — self-contained DDD-skill templates (ship elsewhere,
     must not import SwarmAI core).
  4. test files.
  5. workspace-level ``Knowledge/`` — the CROSS-PROJECT store
     (``Knowledge/DailyActivity|Designs|Notes|Library``), NOT a DDD section. A join
     off a *workspace* path (``ws``/``workspace_path``/``SWARMWS``) is legitimately
     the store, not a per-DDD section, so those are not flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_RESOLVER_FILE = _BACKEND / "core" / "ddd_paths.py"

# A canonical-doc path JOIN: `<something> / "TECH.md"` (the `/` is the tell it's a
# path build, not a prose mention or a set-membership check).
_DOC_JOIN_RE = re.compile(
    r'/\s*["\'](?:PRODUCT|TECH|IMPROVEMENT|PROJECT)\.md["\']'
)
# A per-DDD section-dir JOIN off a PROJECT path (not a workspace path).
# The var-name list is broad on purpose (Gate-2 run_cfb0f28f caught ddd_bindings
# using bare `d` + eval_service using `... / "SwarmAI" / "gates"`): include short
# names (`d`) AND the chained `/ "<Project>" / "gates"` shape.
_SECTION_JOIN_RE = re.compile(
    r'(?:project_dir|proj_dir|ddd_dir|candidate|project_path|base|pdir|projects_dir'
    r'|\bd|"SwarmAI"|/ *"[A-Z][A-Za-z_]+")'
    r'\s*/\s*["\'](?:gates|skills)["\']'
)
# A VARIABLE-doc JOIN: `<project_dir_expr> / <doc-holder>` where the RHS holds a
# canonical doc NAME — either a loop/local var (`doc_name`, `doc`, `ddd_name`) OR
# an ATTRIBUTE access (`proposal.target_doc`, `p.target_doc`). The doc name isn't a
# literal on the line, so `_DOC_JOIN_RE` (matches "TECH.md") never sees it. Two
# waves recurred:
#   - run_3a636c88: 15 loop-var readers (`project_dir / doc_name`)
#   - run_6f636dd5: 3 ddd_cultivation.py attribute-shape readers
#     (`project_dir / proposal.target_doc`) → a migrated DDD's canonical docs live
#     under 2-understanding/, so the root join hit a non-existent path →
#     doc.exists()==False → cultivation returned "doc_missing" → SILENTLY stopped
#     sedimenting knowledge for every migrated DDD. The literal-only + loop-var
#     guards both missed the `\w+.target_doc` shape.
# Any such join MUST go through ddd_path (else the project vanishes from indexes /
# cultivation writes to root = split-brain). RHS alternatives:
#   - `doc_name|ddd_name|doc\b`  → local/loop var
#   - `\w+\.target_doc\b`        → attribute access (the run_6f636dd5 shape)
#   - `f["']\{doc`               → f-string-built doc path
_DOC_VAR_JOIN_RE = re.compile(
    r'(?:project_dir|proj_dir|ddd_dir|pdir)'
    r'\s*/\s*(?:doc_name|ddd_name|doc\b|\w+\.target_doc\b|f["\']\{doc)'
)

# The COMPREHENSION shape — `<anyvar> / doc ... for doc in DDD_CANONICAL_DOCS` on one
# line. This is the 3rd wave (run_775f3969): ddd_self_audit.py used a BARE loop var
# `d` from `PROJECTS_DIR.iterdir()` — `(d / doc).exists() for doc in DDD_CANONICAL_DOCS`
# — so the LHS-var list above (project_dir|proj_dir|…) never matched it, and the
# migration silently BLINDED the entire semantic-drift self-audit (discovery returned
# [] → the whole immune system no-op'd for weeks). We CANNOT add bare `d` as an LHS
# alt (`d /` is ubiquitous → false-positive storm); instead key on the unambiguous
# RHS marker: a `/ doc` join on the SAME line as the `DDD_CANONICAL_DOCS` iterable.
# That marker is specific enough to never false-positive on an unrelated `x / doc`.
# Order-independent: the `/ doc` join and the `DDD_CANONICAL_DOCS` iterable may appear
# in either order on the line — comprehension form puts the join first
# (`d / doc for doc in DDD_CANONICAL_DOCS`), statement form puts the iterable first
# (`for doc in DDD_CANONICAL_DOCS: p = d / doc`). Match both.
_DOC_COMPREHENSION_RE = re.compile(
    r'(/\s*doc\b.*\bDDD_CANONICAL_DOCS\b)|(\bDDD_CANONICAL_DOCS\b.*/\s*doc\b)'
)


def _is_allowlisted(path: Path, line: str) -> bool:
    if path == _RESOLVER_FILE:
        return True
    # A comment-only line is prose describing the pattern (e.g. the fix note that
    # cites `project_dir / doc`), never an executable path join — skip it. A real
    # bare-join in code is a statement, not a `#`-led line.
    if line.lstrip().startswith("#"):
        return True
    if "ddd-six-section-fallback" in line or "ddd-canonical-fallback" in line:
        return True
    if "templates" in path.parts:
        return True
    if path.name.startswith("test_") or "tests" in path.parts:
        return True
    return False


def _iter_backend_py():
    for py in _BACKEND.rglob("*.py"):
        parts = set(py.parts)
        if ".venv" in parts or "__pycache__" in parts or "node_modules" in parts:
            continue
        yield py


def test_no_stray_ddd_doc_path_joins():
    """No hardcoded canonical-doc path join outside ddd_paths + sanctioned spots."""
    offenders: list[str] = []
    for py in _iter_backend_py():
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _DOC_JOIN_RE.search(line) and not _is_allowlisted(py, line):
                offenders.append(f"{py.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, (
        "Hardcoded canonical-doc path joins found (route through "
        "core.ddd_paths.ddd_path instead — six-section SSOT):\n" + "\n".join(offenders)
    )


def test_no_stray_section_dir_joins():
    """No hardcoded per-DDD gates/skills section-dir join outside the resolver."""
    offenders: list[str] = []
    for py in _iter_backend_py():
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _SECTION_JOIN_RE.search(line) and not _is_allowlisted(py, line):
                offenders.append(f"{py.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, (
        "Hardcoded per-DDD section-dir joins found (route through "
        "core.ddd_paths.ddd_path(project_dir, 'gates'|'capabilities') instead):\n"
        + "\n".join(offenders)
    )


def test_no_stray_variable_doc_joins():
    """No `project_dir / doc_name` VARIABLE-doc join outside the resolver.

    This is the pattern that let 15 canonical-doc readers slip past the
    literal-only guards (run_3a636c88): the doc name is a loop variable, not a
    literal, so `_DOC_JOIN_RE` (which matches "TECH.md") never saw them. A migrated
    DDD keeps its docs under 2-understanding/, so a root `project_dir / doc_name`
    read returns a non-existent path → the project vanishes from indexes /
    cultivation writes to root = split-brain. Route through ddd_path(project_dir,
    doc_name)."""
    offenders: list[str] = []
    for py in _iter_backend_py():
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _is_allowlisted(py, line):
                continue
            # Two shapes of the same bug: LHS-var join (`project_dir / doc_name`) and
            # the comprehension join (`<anyvar> / doc ... for doc in DDD_CANONICAL_DOCS`,
            # the run_775f3969 self-audit-blinding shape with a bare loop var `d`).
            if _DOC_VAR_JOIN_RE.search(line) or _DOC_COMPREHENSION_RE.search(line):
                offenders.append(f"{py.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, (
        "Variable-doc path joins found (route through core.ddd_paths.ddd_path("
        "project_dir, doc_name) — a migrated DDD's docs are under 2-understanding/):\n"
        + "\n".join(offenders)
    )
