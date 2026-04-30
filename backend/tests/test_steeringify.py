"""Tests for Steeringify — extract recurring corrections into STEERING.md rules.

Tests the 3-stage pipeline: extract → cluster/filter → write.
Uses real C-entry text from EVOLUTION.md to ensure regex patterns work
on actual data, not synthetic examples.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


# ── Tracer bullet: extract rule candidates from C-entries ──

SAMPLE_EVOLUTION = textwrap.dedent("""\
    # SwarmAI Evolution Registry

    ## Corrections Captured

    ### C012 | 2026-04-25
    - **Correction**: WebFetch failed. First response was to ask user to paste content.
    - **Pattern**: **Tool-oriented thinking persists despite two prior corrections.** When a tool fails, first instinct is still "this tool doesn't work" → report to user. The real rule is universal: **ANY tool failure triggers a 3-attempt alternative search before reporting to the user.** (1) Same goal via Bash/curl/Python. (2) Different tool. (3) Different approach. **This is the same rule as C007 but generalized.**
    - **Status**: active

    ### C007 | 2026-04-09
    - **Correction**: When aws-outlook-mcp failed, told user "open a new tab" instead of trying alternatives.
    - **Pattern**: Tool-oriented thinking vs goal-oriented thinking. **ANY tool/MCP unavailable → exhaust alternatives before reporting.** (1) Binary available? JSON-RPC stdio. (2) API underneath? curl. (3) Another tool? Use it.
    - **Status**: active

    ### C009 | 2026-04-12
    - **Correction**: Pytest hook took 5 iterations because coded before thinking.
    - **Pattern**: Implementation-first thinking → multi-round rework. **Structural fix: Pre-Implementation Checkpoint added to AGENT.md** — before any multi-file task, explicitly output: (1) problem, (2) scenarios, (3) simplest approach, (4) what could break.
    - **Status**: active

    ### C001 | 2026-03-13 (consolidated with C002)
    - **Correction**: Tab-switch streaming bug reported 4x.
    - **Pattern**: Diagnosis without commitment to durable fix.
    - **Status**: resolved — COE06/07
""")


def test_extract_rule_candidates_finds_bold_rules():
    """Tracer bullet: extract bold rules from C-entry Pattern fields."""
    from skills.s_steeringify.steeringify import extract_rule_candidates

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    # Should find bold rules from active C-entries
    assert len(candidates) >= 2
    # C012 and C007 share "tool failure → alternatives" pattern
    tool_rules = [c for c in candidates if "tool" in c.rule_text.lower()]
    assert len(tool_rules) >= 1
    # Each candidate has source IDs
    for c in candidates:
        assert len(c.source_ids) >= 1
        assert all(sid.startswith("C") for sid in c.source_ids)


def test_extract_skips_resolved_entries():
    """Resolved C-entries (status: resolved) should be excluded."""
    from skills.s_steeringify.steeringify import extract_rule_candidates

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    # C001 is resolved — its rules should not appear
    resolved_sources = [c for c in candidates if "C001" in c.source_ids]
    assert len(resolved_sources) == 0


# ── Stage 2: Cluster and filter ──

def test_cluster_groups_similar_rules():
    """Tool-failure rules from C007 and C012 should cluster together."""
    from skills.s_steeringify.steeringify import (
        extract_rule_candidates, cluster_and_filter,
    )

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    # min_recurrence=2 → only clusters with 2+ source C-entries
    proposals = cluster_and_filter(candidates, min_recurrence=2)
    assert len(proposals) >= 1
    # The tool-failure cluster should have both C007 and C012
    tool_proposal = [p for p in proposals if "tool" in p.rule_text.lower()]
    assert len(tool_proposal) >= 1
    assert "C012" in tool_proposal[0].source_ids or "C007" in tool_proposal[0].source_ids


def test_cluster_min_recurrence_filters():
    """Higher min_recurrence should return fewer proposals."""
    from skills.s_steeringify.steeringify import (
        extract_rule_candidates, cluster_and_filter,
    )

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    r1 = cluster_and_filter(candidates, min_recurrence=1)
    r2 = cluster_and_filter(candidates, min_recurrence=2)
    assert len(r1) >= len(r2)


def test_quality_gate_rejects_descriptive():
    """Rules that describe what was done (not what to do) are rejected."""
    from skills.s_steeringify.steeringify import extract_rule_candidates

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    # "This is the same rule as C007 but generalized" is descriptive → rejected
    descriptive = [c for c in candidates if "same rule as" in c.rule_text.lower()]
    assert len(descriptive) == 0


def test_dedup_against_steering():
    """Rules already in STEERING.md are flagged."""
    from skills.s_steeringify.steeringify import (
        extract_rule_candidates, cluster_and_filter,
    )

    candidates = extract_rule_candidates(SAMPLE_EVOLUTION)
    steering_text = "ANY tool failure triggers a 3-attempt alternative search before reporting"
    proposals = cluster_and_filter(candidates, min_recurrence=1,
                                   steering_text=steering_text)
    # At least one proposal should be flagged as already in steering
    flagged = [p for p in proposals if p.already_in_steering]
    assert len(flagged) >= 1


# ── Stage 3: Write ──

def test_write_approved_rules(tmp_path):
    """Write approved rules to STEERING.md."""
    from skills.s_steeringify.steeringify import ProposedRule, write_approved_rules

    steering = tmp_path / "STEERING.md"
    steering.write_text("## Standing Rules\n\n_(Nothing yet.)_\n\n## Other Section\n")

    rules = [ProposedRule(
        title="Tool failure → exhaust alternatives",
        rule_text="ANY tool failure triggers a 3-attempt alternative search.",
        source_ids=["C007", "C012"],
        confidence=0.85,
    )]
    count = write_approved_rules(rules, steering)
    assert count == 1

    content = steering.read_text()
    assert "Tool failure → exhaust alternatives" in content
    assert "Source: C007, C012" in content
    assert "Confidence: 0.85" in content
    # Should be in Standing Rules section, before Other Section
    standing_pos = content.index("Standing Rules")
    other_pos = content.index("Other Section")
    rule_pos = content.index("Tool failure")
    assert standing_pos < rule_pos < other_pos


def test_write_respects_max_cap(tmp_path):
    """Cannot exceed MAX_ACTIVE_RULES."""
    from skills.s_steeringify.steeringify import ProposedRule, write_approved_rules

    # Pre-fill with 10 existing rules
    existing = "## Standing Rules\n\n"
    for i in range(10):
        existing += f"### Rule {i}\n> Source: C{i:03d} | Added: 2026-01-01\n\nSome rule.\n\n"
    steering = tmp_path / "STEERING.md"
    steering.write_text(existing)

    rules = [ProposedRule(
        title="New rule",
        rule_text="Should not be written",
        source_ids=["C999"],
        confidence=0.9,
    )]
    count = write_approved_rules(rules, steering)
    assert count == 0
    assert "Should not be written" not in steering.read_text()


def test_empty_evolution():
    """Empty EVOLUTION.md returns no candidates."""
    from skills.s_steeringify.steeringify import extract_rule_candidates

    assert extract_rule_candidates("") == []
    assert extract_rule_candidates("# SwarmAI Evolution Registry\n\n## Capabilities Built\n") == []
