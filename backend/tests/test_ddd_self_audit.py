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
    _PER_PROJECT_BUDGET_USD,
    _build_audit_prompt,
    _classify_review_result,
    _count_parseable_findings,
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


class TestReviewResultClassification:
    """run_271c39df: the handler used to treat exit-1 as a binary fail and never
    parse stdout — so a budget-exhaust (which carries real PARTIAL analysis + the
    errors field) was discarded as opaque 'review failed (exit 1)'."""

    def test_budget_exhaust_by_subtype_is_partial_and_keeps_result(self):
        """AC1: budget-exhaust exit-1 (via subtype) → 'partial', partial analysis retained."""
        output = {
            "result": "Found drift: TECH.md says X but code says Y.",
            "subtype": "error_max_budget_usd",
            "total_cost_usd": 0.69,
            "errors": ["Reached maximum budget ($0.5)"],
        }
        v = _classify_review_result(1, output, "")
        assert v["status"] == "partial"
        assert "Found drift" in v["result_text"]  # analysis NOT discarded
        assert v["cost"] == 0.69

    def test_budget_exhaust_by_errors_substring_is_partial(self):
        """AC1: fallback path — detect budget-exhaust from the errors array even
        if subtype is absent (robustness against CLI shape drift)."""
        output = {"result": "partial analysis", "errors": ["Reached maximum budget ($2)"]}
        v = _classify_review_result(1, output, "")
        assert v["status"] == "partial"
        assert v["result_text"] == "partial analysis"

    def test_genuine_exit1_surfaces_error_detail_not_opaque(self):
        """AC2: a non-budget exit-1 (e.g. MCP/auth) → 'failed' WITH a real error
        detail (stderr tail), never an opaque 'exit 1'."""
        v = _classify_review_result(1, {"result": ""}, "MCP server failed to connect: timeout")
        assert v["status"] == "failed"
        assert "MCP server failed" in v["error_detail"]

    def test_genuine_exit1_prefers_errors_field_over_stderr(self):
        output = {"result": "", "errors": ["Auth error: token expired"]}
        v = _classify_review_result(1, output, "some stderr noise")
        assert v["status"] == "failed"
        assert "Auth error" in v["error_detail"]

    def test_clean_exit_is_clean(self):
        v = _classify_review_result(0, {"result": "no drift found"}, "")
        assert v["status"] == "clean"
        assert v["result_text"] == "no drift found"

    def test_budget_exhaust_wins_over_returncode(self):
        """A budget-exhaust with returncode 0 (CLI variance) is still 'partial', not 'clean' —
        the label must reflect incompleteness regardless of exit code."""
        output = {"result": "partial", "subtype": "error_max_budget_usd", "total_cost_usd": 2.1}
        v = _classify_review_result(0, output, "")
        assert v["status"] == "partial"

    def test_unrelated_maximum_budget_error_is_NOT_partial(self):
        """Gate-2 CRITICAL: an unrelated error merely containing 'maximum budget' must
        NOT be misclassified as a budget-exhaust partial. We match the CLI's actual
        phrase 'reached maximum budget', and subtype is authoritative."""
        output = {"result": "", "errors": ["DB pool maximum budget exceeded for connections"]}
        v = _classify_review_result(1, output, "some stderr")
        assert v["status"] == "failed", "loose substring caused a false-positive salvage"

    def test_non_string_errors_dont_crash_or_false_match(self):
        """Gate-2 MED: errors may carry non-string items (dicts/ints) — they must be
        filtered out of substring matching, not str()-matched."""
        output = {"result": "", "errors": [{"code": 500}, 42, None]}
        v = _classify_review_result(1, output, "real failure")
        assert v["status"] == "failed"
        assert "real failure" in v["error_detail"]


class TestParseableFindingCount:
    """Gate-2 HIGH: n_findings must reflect ACTUAL parseable todos, not raw '"title"'
    substrings — a budget cap can truncate the RADAR_TODOS JSON mid-block."""

    def test_valid_block_counts_findings(self):
        text = ('drift found\n<!-- RADAR_TODOS\n'
                '[{"title":"a","description":"x"},{"title":"b","description":"y"}]\n-->')
        assert len(_count_parseable_findings(text)) == 2

    def test_no_close_marker_counts_zero(self):
        """A block cut off before the closing --> (no regex match) yields 0."""
        text = 'partial analysis\n<!-- RADAR_TODOS\n[{"title":"a","description":"x"'
        assert _count_parseable_findings(text) == []

    def test_malformed_json_inside_block_counts_zero(self):
        """The load-bearing case: the marker CLOSES (regex matches) but the JSON
        inside is invalid (e.g. trailing comma / truncated obj) — json.loads fails,
        so we count 0, NOT the raw '"title"' substrings. This is what makes the
        parse (vs substring-count) actually matter."""
        # regex captures [...]; inside is invalid JSON but contains two '"title"'
        text = ('drift\n<!-- RADAR_TODOS\n'
                '[{"title":"a","description":"x"},{"title":"b",BROKEN]\n-->')
        assert _count_parseable_findings(text) == [], (
            "malformed JSON must count 0, not the substring count of '\"title\"'"
        )

    def test_no_block_counts_zero(self):
        assert _count_parseable_findings("clean review, no drift") == []


class TestBudgetCap:
    def test_budget_cap_exceeds_observed_review_cost(self):
        """AC3: the per-project cap must exceed the observed real review cost
        ($0.69 on a 46KB project, run_271c39df) — else normal reviews trip it."""
        assert _PER_PROJECT_BUDGET_USD >= 0.69, (
            "budget cap is below observed review cost — reviews will exit-1"
        )
