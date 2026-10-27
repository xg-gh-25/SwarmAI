"""The DDD skeleton: one template source, provisioning from it, aligning existing brains.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.1 and §5.10.
"""
from __future__ import annotations

from pathlib import Path

from core import ddd_skeleton as sk
from core.project_registry import DDD_CANONICAL_DOCS


def test_sections_are_the_designed_set():
    assert {doc: sk.skeleton_sections(doc) for doc in DDD_CANONICAL_DOCS} == {
        "PRODUCT.md": ["Vision", "What Makes It Different", "Strategic Priorities",
                       "Success Criteria", "Non-Goals"],
        "TECH.md": ["Architecture", "Key Subsystems", "Conventions", "Runtime Traps"],
        "IMPROVEMENT.md": ["What Worked", "What Failed", "What to Watch For"],
        "PROJECT.md": ["Current Focus", "Recent Decisions", "Open Items", "Blocked By"],
    }


def test_skeleton_holds_no_placeholder_text_or_project_name():
    """Only headings, a title and <!-- --> guidance: nothing a reader could take for
    content, nothing that names one project (usable by anyone)."""
    import re
    for doc, text in sk.load_skeleton().items():
        body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert all(ln.startswith("#") for ln in lines), (doc, lines)
        assert "SwarmAI" not in text and "_" not in body.replace("{project_name}", ""), doc


def test_provisioning_writes_the_skeleton(tmp_path):
    from core.ddd_paths import ddd_path
    from core.swarm_workspace_manager import SwarmWorkspaceManager
    import asyncio
    proj = tmp_path / "Projects" / "Acme"
    proj.mkdir(parents=True)
    asyncio.run(SwarmWorkspaceManager().provision_project_ddd("Acme", workspace_path=str(tmp_path)))
    for doc in DDD_CANONICAL_DOCS:
        p = ddd_path(proj, doc)
        assert p.exists(), doc
        assert sk.is_skeleton(doc, p.read_text(encoding="utf-8"), "Acme"), doc


def test_missing_and_near_matches():
    text = "# T\n\n## Architecture\nx\n\n## Key Subsystems (architecture shape)\ny\n\n## conventions\n"
    assert sk.missing_sections("TECH.md", text) == ["Key Subsystems", "Runtime Traps"]
    assert sk.near_matches("TECH.md", text) == {"Key Subsystems": "Key Subsystems (architecture shape)"}


def test_align_appends_only_true_gaps_and_keeps_existing_bytes():
    text = "# T\n\n## Architecture\nkeep me\n\n## Key Subsystems (architecture shape)\ny"
    added, near, new = sk.align("TECH.md", text)
    assert added == ["Conventions", "Runtime Traps"] and "Key Subsystems" in near
    assert new.startswith(text)
    assert new[len(text):] == "\n\n## Conventions\n\n## Runtime Traps\n"
    assert sk.align("TECH.md", new)[0] == []  # idempotent


def test_align_project_is_dry_run_by_default(tmp_path):
    proj = tmp_path / "P"
    (proj / "2-understanding").mkdir(parents=True)
    tech = proj / "2-understanding" / "TECH.md"
    tech.write_text("# T\n\n## Architecture\nx\n", encoding="utf-8")
    report = sk.align_project(proj)
    assert report["docs"]["TECH.md"]["to_add"] == ["Key Subsystems", "Conventions", "Runtime Traps"]
    assert report["docs"]["TECH.md"]["added"] == [] and report["unresolved"] is True
    assert tech.read_text(encoding="utf-8") == "# T\n\n## Architecture\nx\n"
    assert report["docs"]["PRODUCT.md"] == {"exists": False}
    sk.align_project(proj, apply=True)
    assert sk.missing_sections("TECH.md", tech.read_text(encoding="utf-8")) == []
    assert not (proj / "2-understanding" / "PRODUCT.md").exists(), "never creates a doc"


def test_swarmai_seed_is_separate_from_the_skeleton():
    seed = Path(sk.SKELETON_DIR).parent / "swarmai-seed"
    assert seed.is_dir() and (seed / "PRODUCT.md").exists()
    assert (seed / "PRODUCT.md").read_text(encoding="utf-8") != sk.load_skeleton()["PRODUCT.md"]


def test_near_match_needs_a_word_boundary():
    text = "## Key Subsystemsless\n"
    assert sk.near_matches("TECH.md", text) == {}
    assert sk.starts_with_section("Vision: no evidence yet", "Vision")
    assert not sk.starts_with_section("Visionary roadmap", "Vision")


def test_headings_inside_code_fences_do_not_count():
    text = "# T\n\n```md\n## Architecture\n```\n"
    assert "Architecture" in sk.missing_sections("TECH.md", text)


def test_align_keeps_crlf_line_endings(tmp_path):
    proj = tmp_path / "P"
    proj.mkdir()
    tech = proj / "TECH.md"
    tech.write_bytes(b"# T\r\n\r\n## Architecture\r\nbody")
    sk.align_project(proj, apply=True)
    data = tech.read_bytes()
    assert data.startswith(b"# T\r\n\r\n## Architecture\r\nbody\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_align_on_an_empty_doc_has_no_leading_blank_lines():
    added, _near, new = sk.align("PROJECT.md", "")
    assert new.startswith("## Current Focus")


def test_swarmai_seed_has_every_skeleton_section():
    seed = Path(sk.SKELETON_DIR).parent / "swarmai-seed"
    for doc in DDD_CANONICAL_DOCS:
        assert sk.missing_sections(doc, (seed / doc).read_text(encoding="utf-8")) == [], doc
