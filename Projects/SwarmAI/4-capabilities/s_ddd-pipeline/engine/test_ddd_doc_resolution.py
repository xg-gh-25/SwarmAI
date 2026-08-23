"""Regression tests for DDD doc path resolution (resolve_ddd_doc).

Root cause guarded here (2026-08-10): apply_to_ddd() and _check_maturity()
used `project_dir / target_doc`, but project DDD docs live under
`<project_dir>/2-understanding/`. Every proposal hit doc_missing →
maturity=False → auto-approval was structurally impossible from ~2026-05-16
onward, so ALL reflect proposals piled into the Need You queue.

These tests FORCE the broken layout (docs in 2-understanding/, NOT root) so a
future refactor that reintroduces the flat-path assumption fails loudly.
"""
import tempfile
import shutil
from pathlib import Path

import pytest

from persist_routing import resolve_ddd_doc
from ddd_cultivation import CultivationProposal, apply_to_ddd
from ddd_auto_approval import evaluate_auto_approval, _check_maturity


def _make_project(tmp: Path, *, layout: str = "2-understanding", maturity: str = "growing") -> Path:
    """Create a project dir with a TECH.md carrying the given maturity."""
    proj = tmp / "Projects" / "Demo"
    doc_dir = proj / layout if layout else proj
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "TECH.md").write_text(
        "# TECH\n\n## Runtime Traps\n"
        f"<!-- maturity: {maturity} | sources: 5 | verified: true | used: true | days: 30 | trust: high | promoted: none -->\n\n"
        "- existing trap.\n",
        encoding="utf-8",
    )
    return proj


@pytest.fixture()
def tmp(tmp_path):
    return tmp_path


def _proposal():
    return CultivationProposal(
        id="t1", target_doc="TECH.md", target_section="Runtime Traps",
        content="A verified runtime trap lesson long enough to pass the value floor and be meaningful.",
        source_run_id="run_demo", source_stage="reflect", confidence=0.8,
    )


def test_resolves_project_doc_to_understanding_subdir(tmp):
    proj = _make_project(tmp, layout="2-understanding")
    resolved = resolve_ddd_doc(proj, "TECH.md")
    assert resolved == proj / "2-understanding" / "TECH.md"
    assert resolved.exists()


def test_cross_project_docs_resolve_to_context(tmp):
    proj = tmp / "Projects" / "Demo"
    proj.mkdir(parents=True)
    for doc in ("MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md"):
        assert resolve_ddd_doc(proj, doc) == tmp / ".context" / doc


def test_legacy_flat_layout_still_found(tmp):
    """A repo still using flat root layout must keep working (fallback)."""
    proj = _make_project(tmp, layout="")  # TECH.md at project root
    assert resolve_ddd_doc(proj, "TECH.md") == proj / "TECH.md"


def test_fresh_write_targets_canonical_subdir(tmp):
    """When neither location exists yet, resolve to canonical 2-understanding/."""
    proj = tmp / "Projects" / "Demo"
    proj.mkdir(parents=True)
    assert resolve_ddd_doc(proj, "TECH.md") == proj / "2-understanding" / "TECH.md"


def test_gate_reads_maturity_from_real_location(tmp):
    """THE regression: gate must read maturity from 2-understanding/, not root."""
    proj = _make_project(tmp, maturity="growing")
    assert _check_maturity(_proposal(), proj) is True
    decision = evaluate_auto_approval(_proposal(), proj)
    assert decision.approved is True, decision.reason


def test_apply_writes_into_understanding_subdir(tmp):
    proj = _make_project(tmp, maturity="growing")
    status = apply_to_ddd(_proposal(), proj)
    assert status == "applied", status
    written = (proj / "2-understanding" / "TECH.md").read_text(encoding="utf-8")
    assert "A verified runtime trap lesson" in written


def test_sparse_section_still_escalates_after_fix(tmp):
    """Fix must NOT auto-approve sparse sections — only unblock the path."""
    proj = _make_project(tmp, maturity="sparse")
    assert _check_maturity(_proposal(), proj) is False
    assert evaluate_auto_approval(_proposal(), proj).approved is False


def test_all_projects_docs_resolve_in_real_workspace():
    """Integration guard: every real project's IMPROVEMENT.md must resolve.

    The 2026-05→08 stall was invisible because the flat glob found 0 projects
    while all 7 live under 2-understanding/. This asserts the resolver sees the
    real layout so a future migration that moves docs again fails loudly here.
    """
    ws = Path("/Users/gawan/.swarm-ai/SwarmWS")
    proj_root = ws / "Projects"
    if not proj_root.is_dir():
        pytest.skip("real workspace not present")
    projects = [p for p in proj_root.iterdir() if p.is_dir()]
    for p in projects:
        # Skip dirs that aren't DDD projects (no 2-understanding and no flat doc)
        if not (p / "2-understanding").is_dir() and not (p / "IMPROVEMENT.md").exists():
            continue
        resolved = resolve_ddd_doc(p, "IMPROVEMENT.md")
        assert resolved.exists(), f"IMPROVEMENT.md unresolved for project {p.name} -> {resolved}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
