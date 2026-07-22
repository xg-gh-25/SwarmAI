"""Regression tests for the KNOWLEDGE.md 'Active Projects & DDD' index generator.

Tested behavior: the index (`_ch_inject_knowledge`) must be structure-aware
(six-section markers: skills/gates/Knowledge/bindings) AND classification-aware
(none/external/internal via classify_project), NOT the stale hardcoded 4-doc list.

Drives the REAL DddCultivationOrchestrator against a real temp workspace + real KNOWLEDGE.md;
asserts the actual written file content. Mutation-provable: reverting to the 4-doc
list drops the `[internal]` tag + `skills`/`bindings` markers and fails these.
"""
import textwrap

import pytest

from core.ddd_orchestrator import DddCultivationOrchestrator


def _write_ddd(pdir):
    pdir.mkdir(parents=True, exist_ok=True)
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        (pdir / doc).write_text(f"# {doc}\ncontent\n")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path
    (root / ".context").mkdir()
    # KNOWLEDGE.md must contain the insert-before anchor so the section lands.
    (root / ".context" / "KNOWLEDGE.md").write_text(
        "# KNOWLEDGE\n\n## The 11 Context Files\n\nstuff\n"
    )
    projects = root / "Projects"

    # An INTERNAL project: has bindings.yaml (kind:internal) + six-section extras.
    intp = projects / "IntProj"
    _write_ddd(intp)
    (intp / "skills" / "s_internal-brazil").mkdir(parents=True)
    (intp / "gates").mkdir()
    (intp / "Knowledge").mkdir()
    (intp / "bindings.yaml").write_text(textwrap.dedent("""\
        version: 1
        bindings:
          - repo: SomePkg
            kind: internal
            clone: "brazil ws create --name SomePkg"
            delivery_contract:
              remote_kind: code-amazon-cr
              branch: mainline
              review_path: cr
              auto_send: "false"
        """))

    # A NONE project: pure DDD, no bindings.
    nonep = projects / "NoneProj"
    _write_ddd(nonep)
    (nonep / "skills" / "s_ddd-manager").mkdir(parents=True)
    (nonep / "Knowledge").mkdir()

    return root


class TestKnowledgeIndex:
    def _run(self, workspace):
        DddCultivationOrchestrator()._ch_inject_knowledge(workspace, str(workspace))
        return (workspace / ".context" / "KNOWLEDGE.md").read_text()

    def test_classification_tag_present(self, workspace):
        content = self._run(workspace)
        assert "**IntProj** `[internal]`" in content, "internal classification tag missing"
        assert "**NoneProj** `[none]`" in content, "none classification tag missing"

    def test_structure_markers_present(self, workspace):
        content = self._run(workspace)
        # internal project shows its six-section structure incl. bindings
        line = next(ln for ln in content.splitlines() if "**IntProj**" in ln)
        assert "1 skills" in line
        assert "gates" in line
        assert "Knowledge/" in line
        assert "bindings" in line

    def test_none_project_has_no_bindings_marker(self, workspace):
        content = self._run(workspace)
        line = next(ln for ln in content.splitlines() if "**NoneProj**" in ln)
        assert "bindings" not in line, "none project must not show bindings marker"
        assert "1 skills" in line  # but still shows its skills

    def test_all_four_docs_still_listed(self, workspace):
        content = self._run(workspace)
        line = next(ln for ln in content.splitlines() if "**IntProj**" in ln)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            assert doc in line


class TestWriterConvergence:
    """run_99b70b3c: the two live writers of 'Active Projects & DDD' (the health-hook
    per-mtime writer + the orchestrator SESSION_CLOSE channel) MUST emit byte-identical
    lines via the shared describe_project_ddd_line, else they clobber/churn each other.
    """

    def test_both_call_styles_are_byte_identical(self, workspace):
        # orchestrator style (freshness=None → helper computes it) vs health-hook
        # style (explicit freshness) must produce the SAME line for the same project.
        from core.ddd_bindings import (
            describe_project_ddd_line, _compute_ddd_freshness, _DDD_DOC_NAMES,
        )
        d = workspace / "Projects" / "IntProj"
        orch = describe_project_ddd_line(d, freshness=None)
        docs = [f for f in _DDD_DOC_NAMES if (d / f).is_file()]
        hh = describe_project_ddd_line(d, freshness=_compute_ddd_freshness(d, docs))
        assert orch == hh
        assert orch.endswith("(updated today)")  # suffix present in BOTH now
        assert "`[internal]`" in orch


class TestMigratedLayoutIndex:
    """A DDD migrated to the six-section numbered tree (docs under 2-understanding/,
    skills under 4-capabilities/) MUST still produce an index line. Regression for
    run_af3dfd9f: describe_project_ddd_line read docs at ROOT via `d / f`, so a
    migrated DDD found 0 docs → returned None → VANISHED from the KNOWLEDGE.md
    index. Caught by live post-deploy smoke (not unit tests — this path had none).
    Fix: resolve docs via ddd_path (strangler new-then-old)."""

    def test_migrated_ddd_still_produces_index_line(self, tmp_path):
        from core.ddd_bindings import describe_project_ddd_line
        d = tmp_path / "Projects" / "MigratedBrain"
        # NEW layout: docs under 2-understanding/, skills under 4-capabilities/.
        und = d / "2-understanding"
        und.mkdir(parents=True)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (und / doc).write_text(f"# {doc}\ncontent\n")
        (d / "4-capabilities" / "s_cmhk-weekly-report").mkdir(parents=True)
        (und / "knowledge").mkdir()

        line = describe_project_ddd_line(d, freshness=None)

        assert line is not None, (
            "a migrated DDD (docs in 2-understanding/) must NOT vanish from the index"
        )
        assert "**MigratedBrain**" in line
        assert "1 skills" in line, "skills under 4-capabilities/ must be counted"
        assert "PRODUCT.md" in line and "TECH.md" in line

    def test_unmigrated_ddd_still_works(self, tmp_path):
        """Strangler back-compat: an un-migrated DDD (docs at root) still resolves."""
        from core.ddd_bindings import describe_project_ddd_line
        d = tmp_path / "Projects" / "LegacyBrain"
        d.mkdir(parents=True)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (d / doc).write_text(f"# {doc}\ncontent\n")
        line = describe_project_ddd_line(d, freshness=None)
        assert line is not None and "**LegacyBrain**" in line
