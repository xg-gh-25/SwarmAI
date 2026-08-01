"""Tests for ddd_drift_signal — the read-only DDD semantic-drift signal helper (run_562f45c7).

This helper closes the DDD-drift → eval feedback loop (option B-minimal): it parses
the latest ddd-self-audit report's SEMANTIC findings and maps each to the golden
cases at risk (via project+doc-aware affected_by matching), so drift surfaces on the
Eval Context Health tab + briefing WITHOUT a new scoring mechanism.

Load-bearing invariants (Gate-0/Gate-1):
- READ-ONLY: the helper never writes (no persisted drift count — R30#4 drift-bait).
- GRACEFUL: LLM-generated finding titles may lack a [project/DOC] tag, and report
  sections may be 'review failed' / 'timed out' / '0 finding(s)' with no RADAR_TODOS
  block — none of these may crash; they degrade to "no finding".
- PROJECT-AWARE mapping: a CMHK finding must NEVER flag a SwarmAI-referencing case
  (the reason we do NOT reuse eval_service.get_affected_cases, which filename-
  normalizes TECH.md across all 7 projects). Must handle BOTH affected_by forms:
  'Projects/SwarmAI/TECH.md' (prefixed) and bare 'SwarmAI/TECH.md'.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.ddd_drift_signal import (
    get_semantic_drift,
    map_at_risk_cases,
    _parse_doc_targets,
    _latest_report,
)


# ─── Fixture: a report mirroring the real 2026-07-20-ddd-self-audit.md shape ───

_REPORT_WITH_2_FINDINGS = textwrap.dedent("""\
    ---
    job_id: ddd-self-audit
    status: success
    ---

    # DDD Self-Audit — 2026-07-20

    Reviewed 5/7 projects · 2 drift finding(s).
    ## AIDLC — ⚠️ review failed (exit 1): no stderr

    ## CMHK_SalesIntel (non-code) — 1 finding(s)
    Found one internal contradiction.

    <!-- RADAR_TODOS
    [
      {"title": "DDD drift [CMHK_SalesIntel/PROJECT.md]: table count hardcoded",
        "description": "PROJECT.md:28 says 11 tables; TECH.md:157 says don't hardcode. Fix via s_persist.",
        "priority": "medium"}
    ]
    -->

    ## GitHub_Community (non-code) — 0 finding(s)
    The DDD is accurate and internally consistent.

    ## IVTHub (code-backed) — 0 finding(s)
    The IVTHub DDD documentation is accurate.

    ## PhysicalAI (non-code) — 1 finding(s)
    Cross-doc contradiction.

    <!-- RADAR_TODOS
    [
      {"title": "DDD drift [PhysicalAI/PRODUCT.md vs PROJECT.md]: whitepaper status",
        "description": "PRODUCT.md:23 says done; PROJECT.md:7 says in drafting. Fix via s_persist.",
        "priority": "medium"}
    ]
    -->

    ## SwarmAI — ⏱ timed out (240s), skipped
    """)

_REPORT_CLEAN = textwrap.dedent("""\
    # DDD Self-Audit — 2026-07-18

    Reviewed 6/7 projects · 0 drift finding(s).
    ## CMHK_SalesIntel (non-code) — 0 finding(s)
    The DDD is accurate and internally consistent.
    """)

# A finding whose LLM-written title has NO [project/DOC] bracket tag.
_REPORT_UNTAGGED_TITLE = textwrap.dedent("""\
    # DDD Self-Audit — 2026-07-19

    Reviewed 1/1 projects · 1 drift finding(s).
    ## CMHK_SalesIntel (non-code) — 1 finding(s)

    <!-- RADAR_TODOS
    [
      {"title": "table count is stale somewhere",
        "description": "no bracket tag in this title at all.",
        "priority": "medium"}
    ]
    -->
    """)


def _write_report(root: Path, name: str, body: str) -> None:
    jr = root / "Knowledge" / "JobResults"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / name).write_text(body, encoding="utf-8")


# ─── AC1: parse latest report → findings + drift_count ────────────────────────

class TestParseLatestReport:
    def test_parses_two_findings_across_projects(self, tmp_path):
        _write_report(tmp_path, "2026-07-20-ddd-self-audit.md", _REPORT_WITH_2_FINDINGS)
        drift = get_semantic_drift(tmp_path)
        assert drift["drift_count"] == 2, drift
        assert drift["report_date"] == "2026-07-20"
        projects = {f["project"] for f in drift["findings"]}
        assert projects == {"CMHK_SalesIntel", "PhysicalAI"}

    def test_multi_doc_tag_parsed_to_two_docs(self, tmp_path):
        _write_report(tmp_path, "2026-07-20-ddd-self-audit.md", _REPORT_WITH_2_FINDINGS)
        drift = get_semantic_drift(tmp_path)
        phys = next(f for f in drift["findings"] if f["project"] == "PhysicalAI")
        assert set(phys["docs"]) == {"PRODUCT.md", "PROJECT.md"}, phys

    def test_clean_report_zero_findings(self, tmp_path):
        _write_report(tmp_path, "2026-07-18-ddd-self-audit.md", _REPORT_CLEAN)
        drift = get_semantic_drift(tmp_path)
        assert drift["drift_count"] == 0
        assert drift["findings"] == []
        assert drift["report_date"] == "2026-07-18"

    def test_no_report_graceful_empty(self, tmp_path):
        # No JobResults dir at all → not an error, an empty signal.
        drift = get_semantic_drift(tmp_path)
        assert drift == {"report_date": None, "findings": [], "drift_count": 0}

    def test_latest_report_is_chosen(self, tmp_path):
        _write_report(tmp_path, "2026-07-18-ddd-self-audit.md", _REPORT_CLEAN)
        _write_report(tmp_path, "2026-07-20-ddd-self-audit.md", _REPORT_WITH_2_FINDINGS)
        drift = get_semantic_drift(tmp_path)
        # 07-20 is newer → 2 findings, not the clean 07-18.
        assert drift["report_date"] == "2026-07-20"
        assert drift["drift_count"] == 2


class TestGracefulDegradation:
    def test_untagged_title_does_not_crash(self, tmp_path):
        _write_report(tmp_path, "2026-07-19-ddd-self-audit.md", _REPORT_UNTAGGED_TITLE)
        drift = get_semantic_drift(tmp_path)
        # The finding still counts (it IS a drift), but project is None (untagged).
        assert drift["drift_count"] == 1
        assert drift["findings"][0]["project"] is None
        assert drift["findings"][0]["docs"] == []

    def test_failed_and_timed_out_sections_yield_nothing(self, tmp_path):
        # _REPORT_WITH_2_FINDINGS has AIDLC (failed) + SwarmAI (timed out) sections
        # with no RADAR_TODOS — they must not add phantom findings.
        _write_report(tmp_path, "2026-07-20-ddd-self-audit.md", _REPORT_WITH_2_FINDINGS)
        drift = get_semantic_drift(tmp_path)
        projects = {f["project"] for f in drift["findings"]}
        assert "AIDLC" not in projects
        assert "SwarmAI" not in projects

    def test_parse_doc_targets_forms(self):
        assert _parse_doc_targets("DDD drift [CMHK_SalesIntel/PROJECT.md]: x") == (
            "CMHK_SalesIntel", ["PROJECT.md"])
        assert _parse_doc_targets("DDD drift [PhysicalAI/PRODUCT.md vs PROJECT.md]: y") == (
            "PhysicalAI", ["PRODUCT.md", "PROJECT.md"])
        assert _parse_doc_targets("no bracket here") == (None, [])


# ─── AC2: project-aware at-risk case mapping (the cross-project guard) ─────────

class TestAtRiskMapping:
    def _findings(self):
        return [{"project": "CMHK_SalesIntel", "docs": ["PROJECT.md"],
                 "title": "t", "detail": "d"}]

    def test_maps_prefixed_affected_by(self):
        cases = [{"id": "GS_CMHK", "affected_by": ["Projects/CMHK_SalesIntel/PROJECT.md"]}]
        at_risk = map_at_risk_cases(self._findings(), cases)
        assert [r["case_id"] for r in at_risk] == ["GS_CMHK"]

    def test_maps_bare_affected_by(self):
        # The ONLY real DDD-doc form in the golden set is bare (GS_TRAJ_USES_DDD).
        findings = [{"project": "SwarmAI", "docs": ["TECH.md"], "title": "t", "detail": "d"}]
        cases = [{"id": "GS_TRAJ_USES_DDD", "affected_by": ["AGENT.R10", "SwarmAI/TECH.md"]}]
        at_risk = map_at_risk_cases(findings, cases)
        assert [r["case_id"] for r in at_risk] == ["GS_TRAJ_USES_DDD"]

    def test_no_cross_project_false_positive(self):
        # THE load-bearing guard: a CMHK finding must NOT flag a SwarmAI TECH.md case,
        # even though both docs are 'PROJECT.md'/'TECH.md' basename-wise.
        findings = [{"project": "CMHK_SalesIntel", "docs": ["PROJECT.md"],
                     "title": "t", "detail": "d"}]
        cases = [
            {"id": "GS_SWARM", "affected_by": ["Projects/SwarmAI/PROJECT.md"]},
            {"id": "GS_SWARM_BARE", "affected_by": ["SwarmAI/PROJECT.md"]},
        ]
        at_risk = map_at_risk_cases(findings, cases)
        assert at_risk == [], f"cross-project false positive: {at_risk}"

    def test_untagged_finding_maps_nothing(self):
        findings = [{"project": None, "docs": [], "title": "t", "detail": "d"}]
        cases = [{"id": "GS_X", "affected_by": ["Projects/SwarmAI/TECH.md"]}]
        assert map_at_risk_cases(findings, cases) == []

    def test_case_without_affected_by_never_crashes(self):
        findings = [{"project": "SwarmAI", "docs": ["TECH.md"], "title": "t", "detail": "d"}]
        cases = [{"id": "GS_NOAB"}]  # no affected_by key
        assert map_at_risk_cases(findings, cases) == []


# ─── QUALITY: read-only invariant (R30#4 — nothing persisted) ─────────────────

class TestReadOnlyInvariant:
    def test_source_has_no_file_writes(self):
        import core.ddd_drift_signal as mod
        src = Path(mod.__file__).read_text()
        # No write path: no open(...,'w'), no write_text, no json.dump-to-file.
        assert ".write_text(" not in src, "helper must not write files (R30#4)"
        assert "open(" not in src or "'w'" not in src, "helper must not open files for write"
        assert "json.dump(" not in src, "helper must not persist a drift artifact"

    def test_get_semantic_drift_is_idempotent(self, tmp_path):
        _write_report(tmp_path, "2026-07-20-ddd-self-audit.md", _REPORT_WITH_2_FINDINGS)
        a = get_semantic_drift(tmp_path)
        b = get_semantic_drift(tmp_path)
        assert a == b
        # And it created no new files.
        jr = tmp_path / "Knowledge" / "JobResults"
        assert sorted(p.name for p in jr.iterdir()) == ["2026-07-20-ddd-self-audit.md"]

