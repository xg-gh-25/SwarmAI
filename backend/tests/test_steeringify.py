"""Tests for Steeringify v2 — structured Pattern-field extraction with cross-references.

Tests the 3-stage pipeline:
  1. extract_corrections() — parse C-entries with cross-reference detection
  2. group_and_propose() — group by explicit cross-refs, filter, format for STEERING.md
  3. write_approved_rules() — append to STEERING.md in correct format

Uses real C-entry text from EVOLUTION.md to ensure parsing works on actual data.
"""
from __future__ import annotations

import json
import textwrap



# ── Sample data: real C-entry formats ──

SAMPLE_EVOLUTION = textwrap.dedent("""\
    # SwarmAI Evolution Registry

    ## Corrections Captured

    ### C012 | 2026-04-25
    - **Correction**: WebFetch failed. First response was to ask user to paste content.
    - **Pattern**: **Tool-oriented thinking persists despite two prior corrections.** When a tool fails, first instinct is still "this tool doesn't work" -> report to user. The real rule is universal: **ANY tool failure triggers a 3-attempt alternative search before reporting to the user.** (1) Same goal via Bash/curl/Python. (2) Different tool. (3) Different approach. **This is the same rule as C007 but generalized.**
    - **Status**: active

    ### C007 | 2026-04-09
    - **Correction**: When aws-outlook-mcp failed, told user "open a new tab" instead of trying alternatives.
    - **Pattern**: Tool-oriented thinking vs goal-oriented thinking. **ANY tool/MCP unavailable -> exhaust alternatives before reporting.** (1) Binary available? JSON-RPC stdio. (2) API underneath? curl. (3) Another tool? Use it.
    - **Status**: active

    ### C009 | 2026-04-12
    - **Correction**: Pytest hook took 5 iterations because coded before thinking.
    - **Pattern**: Implementation-first thinking -> multi-round rework. **Every multi-file task must output: (1) problem, (2) scenarios, (3) simplest approach, (4) what could break — before writing code.** LL07's 5th recurrence.
    - **Status**: active

    ### C008 | 2026-04-12
    - **Correction**: Confidently answered arch question wrong.
    - **Pattern**: **Architecture topology questions MUST be verified against code or KNOWLEDGE.md before answering. Never infer process topology from general architecture knowledge.** Same root cause as C005.
    - **Status**: active

    ### C010 | 2026-04-15
    - **Correction**: User couldn't open 3rd chat tab.
    - **Pattern**: Same as C008 — asserting from stale mental model. **Every resource/concurrency question must start with verification — never from memory.** Related: C005.
    - **Status**: active

    ### C001 | 2026-03-13 (consolidated with C002)
    - **Correction**: Tab-switch streaming bug reported 4x.
    - **Pattern**: Diagnosis without commitment to durable fix.
    - **Status**: resolved — COE06/07

    ### C003 | 2026-03-15
    - **Correction**: Agent conflated in-app MCP bug with Claude Code session MCPs.
""")


# ── AC1: Pattern-field extraction + cross-reference detection ──

class TestExtractCorrections:
    """Test extract_corrections() parses C-entries with cross-refs."""

    def test_extracts_active_entries(self):
        from skills.s_steeringify.steeringify import extract_corrections

        entries = extract_corrections(SAMPLE_EVOLUTION)
        # Should find C012, C007, C009, C008, C010 (active with Pattern)
        # Should skip C001 (resolved) and C003 (no Pattern field with bold rules)
        active_ids = {e.id for e in entries}
        assert "C012" in active_ids
        assert "C007" in active_ids
        assert "C009" in active_ids
        assert "C008" in active_ids
        assert "C010" in active_ids
        assert "C001" not in active_ids  # resolved

    def test_extracts_bold_rules(self):
        from skills.s_steeringify.steeringify import extract_corrections

        entries = extract_corrections(SAMPLE_EVOLUTION)
        c012 = next(e for e in entries if e.id == "C012")
        # C012 has at least 2 bold prescriptive rules
        assert len(c012.bold_rules) >= 1
        # One should mention "tool failure" and "alternative"
        assert any("tool" in r.lower() and "alternative" in r.lower()
                    for r in c012.bold_rules)

    def test_detects_cross_references(self):
        from skills.s_steeringify.steeringify import extract_corrections

        entries = extract_corrections(SAMPLE_EVOLUTION)
        c012 = next(e for e in entries if e.id == "C012")
        # C012 Pattern says "same rule as C007"
        assert "C007" in c012.cross_refs

        c010 = next(e for e in entries if e.id == "C010")
        # C010 says "Same as C008" and "Related: C005"
        assert "C008" in c010.cross_refs
        assert "C005" in c010.cross_refs

    def test_preserves_dates(self):
        from skills.s_steeringify.steeringify import extract_corrections

        entries = extract_corrections(SAMPLE_EVOLUTION)
        c012 = next(e for e in entries if e.id == "C012")
        assert c012.date == "2026-04-25"

    def test_empty_input(self):
        from skills.s_steeringify.steeringify import extract_corrections

        assert extract_corrections("") == []
        assert extract_corrections("# No corrections\n") == []

    def test_entry_without_pattern_field(self):
        """C003 has no Pattern field at all — should be excluded."""
        from skills.s_steeringify.steeringify import extract_corrections

        entries = extract_corrections(SAMPLE_EVOLUTION)
        # C003 has no Pattern field → excluded
        ids = {e.id for e in entries}
        assert "C003" not in ids


# ── AC2: Cross-reference grouping ──

class TestGroupAndPropose:
    """Test group_and_propose() uses cross-refs to group, not keywords."""

    def test_groups_by_cross_reference(self):
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        proposals = group_and_propose(entries)
        # C012 references C007 → should be in the same group
        tool_group = [p for p in proposals
                      if "C012" in p.source_ids and "C007" in p.source_ids]
        assert len(tool_group) >= 1

    def test_groups_transitive_refs(self):
        """C010→C008→C005: transitive graph should merge."""
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        proposals = group_and_propose(entries)
        # C010 refs C008, C008 refs C005 → all in one group
        arch_group = [p for p in proposals
                      if "C008" in p.source_ids and "C010" in p.source_ids]
        assert len(arch_group) >= 1

    def test_standalone_entries_need_recurrence(self):
        """C009 has no cross-refs and mentions no other C-entry → needs recurrence."""
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        # With min_recurrence=2 (default), standalone C009 should not appear
        proposals = group_and_propose(entries, min_group_size=2)
        solo_c009 = [p for p in proposals if p.source_ids == ["C009"]]
        assert len(solo_c009) == 0

    def test_confidence_scales_with_group_size(self):
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        proposals = group_and_propose(entries, min_group_size=1)
        if len(proposals) >= 2:
            # Larger groups should have higher confidence
            sorted_p = sorted(proposals, key=lambda p: len(p.source_ids), reverse=True)
            assert sorted_p[0].confidence >= sorted_p[-1].confidence

    def test_dedup_against_steering(self):
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        steering = "ANY tool failure triggers a 3-attempt alternative search before reporting"
        proposals = group_and_propose(entries, steering_text=steering)
        flagged = [p for p in proposals if p.already_in_steering]
        assert len(flagged) >= 1


# ── AC4: Effectiveness tracking (violation detection) ──

class TestEffectivenessTracking:
    """Detect when a new correction references an existing STEERING rule."""

    def test_detects_violation_of_existing_rule(self):
        from skills.s_steeringify.steeringify import extract_corrections, group_and_propose

        entries = extract_corrections(SAMPLE_EVOLUTION)
        # Pretend "tool failure" is already a STEERING rule
        steering = textwrap.dedent("""\
            ### Tool Failure Alternatives
            > Source: C007 | Added: 2026-04-10

            **ANY tool failure triggers a 3-attempt alternative search.**
        """)
        proposals = group_and_propose(entries, steering_text=steering)
        # C012 re-raised the same issue → should flag as violation
        violated = [p for p in proposals if p.violates_existing]
        assert len(violated) >= 1


# ── AC5: STEERING.md format matching ──

class TestWriteApprovedRules:
    """Test write_approved_rules() outputs correct STEERING.md format."""

    def test_writes_in_steering_format(self, tmp_path):
        from skills.s_steeringify.steeringify import ProposedRule, write_approved_rules

        steering = tmp_path / "STEERING.md"
        steering.write_text(
            "## Standing Rules\n\n### Existing Rule\n\nSome rule.\n\n"
            "---\n\n_Edit this file anytime._\n"
        )

        rules = [ProposedRule(
            title="Tool Failure Alternatives",
            body="**ANY tool failure triggers a 3-attempt alternative search before reporting to the user.** "
                 "When ANY tool or operation fails: (1) Try Bash/Python, (2) Try a different tool, "
                 "(3) Try a workaround. Only after ALL alternatives exhausted, tell the user.",
            source_ids=["C007", "C012"],
            confidence=0.85,
        )]
        count = write_approved_rules(rules, steering)
        assert count == 1

        content = steering.read_text()
        # Must have ### heading, > Source: provenance, bold principle
        assert "### Tool Failure Alternatives" in content
        assert "> Source: C007, C012" in content
        assert "Confidence: 0.85" in content
        assert "**ANY tool failure triggers" in content

    def test_inserts_before_separator(self, tmp_path):
        from skills.s_steeringify.steeringify import ProposedRule, write_approved_rules

        steering = tmp_path / "STEERING.md"
        steering.write_text(
            "## Standing Rules\n\n### Existing Rule\n\nSome rule.\n\n"
            "---\n\n_Edit this file anytime._\n"
        )

        rules = [ProposedRule(
            title="New Rule",
            body="**Never do X.**",
            source_ids=["C001"],
            confidence=0.7,
        )]
        write_approved_rules(rules, steering)

        content = steering.read_text()
        rule_pos = content.index("New Rule")
        sep_pos = content.index("---")
        assert rule_pos < sep_pos  # Rule before separator

    def test_respects_max_cap(self, tmp_path):
        from skills.s_steeringify.steeringify import ProposedRule, write_approved_rules

        existing = "## Standing Rules\n\n"
        for i in range(10):
            existing += f"### Rule {i}\n> Source: C{i:03d} | Added: 2026-01-01\n\nRule.\n\n"
        steering = tmp_path / "STEERING.md"
        steering.write_text(existing)

        rules = [ProposedRule(
            title="Overflow",
            body="**Should not be written.**",
            source_ids=["C999"],
            confidence=0.9,
        )]
        count = write_approved_rules(rules, steering)
        assert count == 0
        assert "Should not be written" not in steering.read_text()


# ── AC3: Hook integration (proposals.json) ──

class TestHookIntegration:
    """Test that the hook API produces valid proposals.json output."""

    def test_generate_proposals_json(self):
        from skills.s_steeringify.steeringify import (
            extract_corrections, group_and_propose,
        )

        entries = extract_corrections(SAMPLE_EVOLUTION)
        proposals = group_and_propose(entries, min_group_size=2)
        # Proposals should be JSON-serializable
        data = [
            {
                "title": p.title,
                "body": p.body,
                "source_ids": p.source_ids,
                "confidence": p.confidence,
                "already_in_steering": p.already_in_steering,
                "already_in_agent": p.already_in_agent,
                "violates_existing": p.violates_existing,
            }
            for p in proposals
        ]
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert len(parsed) >= 1
        assert all("source_ids" in item for item in parsed)
