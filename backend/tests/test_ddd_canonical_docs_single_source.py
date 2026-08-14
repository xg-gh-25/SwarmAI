"""Grep-CI gate: DDD canonical-4 doc tuple has ONE source of truth.

Run 0 (run_393e3dc1, code-intel v3): the tuple
("PRODUCT.md","TECH.md","IMPROVEMENT.md","PROJECT.md") was hardcoded in 22
places across ~15 files — the GUI10/OT07 "add-a-doc → hunt-every-file" bug
class. Run 0 collapsed them to project_registry.DDD_CANONICAL_DOCS.

This test is the anti-regression backstop: it FAILS if a stray literal copy of
the 4-tuple reappears anywhere under backend/ (outside the sanctioned
exceptions). A new copy = the single-source guarantee broke = fix by importing
DDD_CANONICAL_DOCS instead.

Sanctioned exceptions (allowlisted):
  1. project_registry.py — the ONE definition (+ its explanatory comment).
  2. `# ddd-canonical-fallback` tagged lines — guarded-import fallbacks in
     job/script subprocess contexts where core may not be on the path. The
     import is tried first; the literal only fires on ImportError, and stays
     byte-identical to the constant.
  3. backend/templates/ — ddd-skills templates are SELF-CONTAINED by design
     (option A, user-decided 2026-07-16): they ship to other projects and must
     not import SwarmAI's core, so they keep their own literal.
  4. test files — may assert against the literal on purpose (incl. this file).
"""

from __future__ import annotations

import re
from pathlib import Path

# ORDER-INDEPENDENT detector (adversarial fix, run_393e3dc1 Gate-2): the first
# version hard-coded the sequence PRODUCT→TECH→IMPROVEMENT→PROJECT and was
# VACUOUS against reordered copies (e.g. TECH.md-first), which is the single
# most likely way a stray literal re-enters. It let 3 real copies pass green.
# Now: flag any line that quotes ALL FOUR canonical doc names (any order) AND
# is literal-tuple/list/set-shaped (has a collection delimiter), so prose that
# merely mentions the four names ("A | B | C | D" in a docstring) is not a hit.
_DOC_LITERAL_RE = re.compile(r'["\'](PRODUCT|TECH|IMPROVEMENT|PROJECT)\.md["\']')
# A collection literal has commas separating the quoted names (tuple/list/set).
_COLLECTION_HINT_RE = re.compile(r'["\']\w+\.md["\']\s*,')


def _line_has_canonical_tuple(line: str) -> bool:
    """True if `line` embeds all 4 canonical doc names as a comma-separated
    collection literal (any order). Prose mentions (no commas between quotes,
    or pipe-separated) are NOT flagged."""
    names = {m.group(1) for m in _DOC_LITERAL_RE.finditer(line)}
    if names != {"PRODUCT", "TECH", "IMPROVEMENT", "PROJECT"}:
        return False
    return bool(_COLLECTION_HINT_RE.search(line))

_BACKEND = Path(__file__).resolve().parent.parent
_CONSTANT_DEF_FILE = _BACKEND / "core" / "project_registry.py"


def _is_allowlisted(path: Path, line: str) -> bool:
    # 2. tagged guarded-import fallback
    if "ddd-canonical-fallback" in line:
        return True
    # 1. the one definition file (constant def + its explanatory comment)
    if path == _CONSTANT_DEF_FILE:
        return True
    # 3. self-contained templates (option A)
    if "templates" in path.parts:
        return True
    # 4. test files
    if path.name.startswith("test_") or "tests" in path.parts:
        return True
    return False


def test_no_stray_ddd_canonical_tuple_literals():
    """No hardcoded copy of the canonical-4 tuple outside sanctioned spots."""
    offenders: list[str] = []
    for py in _BACKEND.rglob("*.py"):
        # skip virtualenv / caches
        if any(part in {".venv", "__pycache__", "node_modules"} for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _line_has_canonical_tuple(line) and not _is_allowlisted(py, line):
                offenders.append(f"{py.relative_to(_BACKEND)}:{i}: {line.strip()[:100]}")

    assert not offenders, (
        "Stray hardcoded DDD canonical-4 tuple(s) found — import "
        "project_registry.DDD_CANONICAL_DOCS instead (Run 0 single-source rule):\n"
        + "\n".join(offenders)
    )


def test_detector_has_teeth_order_independent():
    """The detector MUST catch reordered/any-order literal copies (Gate-2 fix).

    Mutation test: the original regex was order-vacuous and let TECH.md-first
    copies pass. These assertions fail if that regression returns.
    """
    # canonical order → caught
    assert _line_has_canonical_tuple(
        '    for d in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):')
    # REORDERED (TECH first) → MUST still be caught
    assert _line_has_canonical_tuple(
        '    for d in ("TECH.md", "PRODUCT.md", "IMPROVEMENT.md", "PROJECT.md"):')
    assert _line_has_canonical_tuple(
        '    x = {"TECH.md", "IMPROVEMENT.md", "PRODUCT.md", "PROJECT.md"}')
    # prose mention (pipe-separated, no comma-collection) → NOT flagged
    assert not _line_has_canonical_tuple(
        '    # "PRODUCT.md" | "TECH.md" | "IMPROVEMENT.md" | "PROJECT.md"')
    # partial (only 3 names) → NOT flagged
    assert not _line_has_canonical_tuple('("PRODUCT.md", "TECH.md", "PROJECT.md")')


def test_constant_is_the_canonical_four():
    """The single source holds exactly the 4 canonical docs, in order."""
    import sys
    sys.path.insert(0, str(_BACKEND))
    from core.project_registry import DDD_CANONICAL_DOCS, SPEC_DETAILS_DIR

    assert DDD_CANONICAL_DOCS == ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")
    # spec-details is a derived projection dir, NOT a 5th canonical doc.
    assert SPEC_DETAILS_DIR == "spec-details"
    assert f"{SPEC_DETAILS_DIR}.md" not in DDD_CANONICAL_DOCS


def test_backward_compat_aliases_track_the_constant():
    """Legacy names (_DDD_FILES, _DDD_DOC_NAMES) still equal the constant."""
    import sys
    sys.path.insert(0, str(_BACKEND))
    from core.project_registry import DDD_CANONICAL_DOCS, _DDD_FILES
    from core.ddd_bindings import _DDD_DOC_NAMES

    assert _DDD_FILES == DDD_CANONICAL_DOCS
    assert _DDD_DOC_NAMES == DDD_CANONICAL_DOCS
