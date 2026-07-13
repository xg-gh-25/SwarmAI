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
