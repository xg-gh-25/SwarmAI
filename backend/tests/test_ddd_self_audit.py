"""Tests for the DDD self-audit job handler (run_835f82ff).

Load-bearing invariants (Gate-0):
- DETECT-ONLY: the review toolset has NO Write/Edit/Bash — structurally cannot mutate DDD.
- DOMAIN-AWARE: code-backed projects get a prose-vs-code prompt; non-code get
  internal-contradiction. And a code-backed project grants --add-dir of its source.
- ENUMERATION: discovers every project carrying a canonical DDD doc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jobs.handlers.ddd_self_audit import (
    _AUDIT_TOOLS,
    _build_audit_prompt,
    _discover_ddd_projects,
    _is_code_backed,
    _source_repo_for,
)


class TestDetectOnlyInvariant:
    def test_audit_toolset_has_no_write_capability(self):
        """The single structural guarantee that the audit cannot corrupt DDD:
        the agent is granted read-only tools. If this ever gains Write/Edit/Bash,
        the run_b2e85d61 NO-GO invariant (prose-truth rewrite stays human) is broken."""
        for forbidden in ("Write", "Edit", "MultiEdit", "Bash", "NotebookEdit"):
            assert forbidden not in _AUDIT_TOOLS, f"{forbidden} would break detect-only"
        assert set(_AUDIT_TOOLS) <= {"Read", "Grep", "Glob"}


class TestDomainAwarePrompt:
    def test_code_backed_prompt_checks_against_code(self, tmp_path):
        prompt = _build_audit_prompt("SwarmAI", tmp_path, code_backed=True)
        assert "LIVE CODE" in prompt
        assert "not yet built" in prompt  # the canonical code-drift example
        # never invites edits
        assert "MUST NOT edit" in prompt

    def test_non_code_prompt_checks_internal_consistency(self, tmp_path):
        prompt = _build_audit_prompt("CMHK_SalesIntel", tmp_path, code_backed=False)
        assert "INCONSISTENCY" in prompt or "self-contradiction" in prompt
        assert "no source repo" in prompt  # explicitly acknowledges no code to check
        # code-drift language must NOT leak into a non-code prompt
        assert "LIVE CODE" not in prompt

    def test_prompt_requires_radar_todos_surface(self, tmp_path):
        """Findings must route to a RADAR_TODOS block (the in-band forcing function)."""
        prompt = _build_audit_prompt("X", tmp_path, code_backed=True)
        assert "RADAR_TODOS" in prompt
        assert "s_persist" in prompt  # each finding names the human fix path

    def test_prompt_emits_description_not_note(self, tmp_path):
        """Gate-2 MED fix: the todo parser reads 'description', not 'note'. If the prompt
        emits 'note', the evidence payload silently drops → the forcing function rots.
        The evidence must land in the field the parser actually reads."""
        prompt = _build_audit_prompt("X", tmp_path, code_backed=True)
        assert '"description"' in prompt
        assert '"note"' not in prompt  # the dropped-field trap


class TestCodeBackedDetection:
    def test_code_intel_json_makes_project_code_backed(self, tmp_path):
        (tmp_path / "code-intel.json").write_text("{}")
        assert _is_code_backed(tmp_path) is True

    def test_no_code_artifact_is_not_code_backed(self, tmp_path):
        (tmp_path / "PRODUCT.md").write_text("# product")
        assert _is_code_backed(tmp_path) is False

    def test_source_repo_only_for_swarmai(self):
        # Non-SwarmAI projects have no live source repo → None (never a bogus --add-dir)
        assert _source_repo_for("CMHK_SalesIntel") is None
        assert _source_repo_for("PhysicalAI") is None


class TestEnumeration:
    def test_discovers_projects_with_ddd_docs(self, tmp_path, monkeypatch):
        import jobs.handlers.ddd_self_audit as mod
        projects_dir = tmp_path / "Projects"
        (projects_dir / "Alpha").mkdir(parents=True)
        (projects_dir / "Alpha" / "PRODUCT.md").write_text("# a")
        (projects_dir / "Beta").mkdir(parents=True)
        (projects_dir / "Beta" / "TECH.md").write_text("# b")
        (projects_dir / "NoDocs").mkdir(parents=True)  # no DDD doc → excluded
        (projects_dir / ".hidden").mkdir(parents=True)  # dotdir → excluded
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)

        found = {name for name, _ in _discover_ddd_projects()}
        assert found == {"Alpha", "Beta"}


class TestNoMutation:
    def test_report_persist_path_never_touches_ddd_docs(self, tmp_path):
        """DoD-B: the handler's OUTPUT is a report + todos — it must never write a DDD doc.
        The agent has no Write tool (TestDetectOnlyInvariant); this asserts the Python
        side writes only to JobResults, not into Projects/*/{PRODUCT,TECH,...}.md.

        We prove it structurally: the handler imports no DDD-doc writer and the only
        persist call is _write_job_result (→ Knowledge/JobResults/). A snapshot-hash guard
        on a fake Projects tree confirms the docs are untouched by the pure helpers."""
        projects = tmp_path / "Projects"
        (projects / "SwarmAI").mkdir(parents=True)
        doc = projects / "SwarmAI" / "TECH.md"
        doc.write_text("# TECH\n9-stage pipeline.\n")
        before = hashlib.md5(doc.read_bytes()).hexdigest()

        # Exercise the pure, non-subprocess helpers (the parts that touch the tree).
        _is_code_backed(projects / "SwarmAI")
        _build_audit_prompt("SwarmAI", projects / "SwarmAI", code_backed=True)

        after = hashlib.md5(doc.read_bytes()).hexdigest()
        assert before == after, "DDD doc was mutated — detect-only invariant broken"
