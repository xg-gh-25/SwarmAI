"""Tests for Evolution Pipeline v3 Phase 1: judgment_classifier.

Classifies raw correction records (from corrections.jsonl) into:
  - axis: operational (skill bug) vs cognitive (judgment failure)
  - class_name: CLASS_A / CLASS_B / CLASS_C (cognitive only)
  - parent_principle: P1-P5 (which SOUL principle violated)

Tier-1 (mechanical): tool_failure records -> operational, no LLM.
Tier-2 (LLM, Sonnet): user_correction records -> cognitive class + principle.

Design: Knowledge/Designs/2026-06-22-evolution-pipeline-v3-governance-routing-design.md
DoD negative tests: AC2 (operational not escalated), AC5 (degrade to log).
Real-corpus rule (anti-repetition run_76273219): AC1 uses a REAL user_correction
record shape, not a synthetic magic-word fixture.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.evolution.judgment_classifier import (
    JudgmentClassification,
    classify_correction,
)


# --- Real-corpus-shaped fixtures (mirror actual corrections.jsonl records) ---

REAL_USER_CORRECTION = {
    "ts": 1781696331.242039,
    "session_id": "e95e5923-31d9-49c0-a777-cf8959b3fcc0",
    "type": "user_correction",
    # Verbatim shape from production corpus — CLASS_A ship-untested pushback.
    "prompt": (
        "你不是被一个 bug 咬了。你把三个未单独验证过的子系统同一次 "
        "./prod.sh build 全量推上生产 daemon,它们共用一条你自己都没测过的脆弱路径 "
        "kill → COLD → --resume。这是流程崩溃,不是手滑。"
    ),
}

REAL_TOOL_FAILURE = {
    "ts": 1781055459.4190772,
    "session_id": "cbcf9db7-45c6-431f-9fe7-95c7f74ead3f",
    "type": "tool_failure",
    "tool": "Bash",
    "input_summary": "{'command': 'python backend/scripts/artifact_cli.py ...'}",
    "error": "Exit code 2\npython: can't open file 'artifact_cli.py': No such file",
}


def _fake_bedrock_returning(label_json: dict):
    """Build a MagicMock bedrock client whose .converse returns labeled JSON."""
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": json.dumps(label_json)}]}},
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }
    return client


# --- AC1: classify a REAL user_correction into axis+class+principle ---

def test_ac1_classify_real_user_correction_cognitive():
    """AC1: a real user_correction is classified cognitive with a CLASS + principle.

    Uses the REAL record shape (Chinese ship-untested pushback) — not a synthetic
    magic-word fixture. This is the anti-repetition guard from run_76273219 where
    a keyword classifier scored 100% false-negative on real corrections.
    """
    client = _fake_bedrock_returning({
        "axis": "cognitive",
        "class_name": "CLASS_A",
        "parent_principle": "P1",
        "confidence": 0.8,
        "evidence": "shipped 3 unverified subsystems on untested resume path",
    })
    jc = classify_correction(
        REAL_USER_CORRECTION,
        evolution_classes=["CLASS_A", "CLASS_B", "CLASS_C"],
        bedrock_client=client,
    )
    assert jc is not None
    assert jc.axis == "cognitive"
    assert jc.class_name == "CLASS_A"
    assert jc.parent_principle == "P1"
    assert jc.tier == "llm"
    # cognitive must park, never auto-count
    assert jc.counter_state == "pending_confirm"
    # Tier-2 actually consulted the LLM
    client.converse.assert_called_once()


# --- AC2 (NEGATIVE): operational tool_failure NOT escalated to cognitive ---

def test_ac2_operational_tool_failure_not_escalated():
    """AC2 negative: a single-tool tool_failure stays operational, no LLM, no class."""
    # No bedrock client passed at all — operational path must not need one.
    jc = classify_correction(REAL_TOOL_FAILURE, evolution_classes=["CLASS_A"])
    assert jc is not None
    assert jc.axis == "operational"
    assert jc.class_name is None  # operational has no cognitive class
    assert jc.tier == "mechanical"
    assert jc.blast_radius == 1  # single tool
    # operational auto-counts (low stakes)
    assert jc.counter_state == "counted"


def test_ac2_operational_never_calls_llm():
    """AC2 corollary: operational path must not invoke the LLM even if client present."""
    client = _fake_bedrock_returning({"axis": "cognitive"})
    jc = classify_correction(REAL_TOOL_FAILURE, evolution_classes=[], bedrock_client=client)
    assert jc.axis == "operational"
    client.converse.assert_not_called()


# --- AC5 (NEGATIVE): classifier degrades to log-only on any exception ---

def test_ac5_llm_exception_degrades_to_none():
    """AC5 negative: a converse() that raises must yield None, never propagate."""
    client = MagicMock()
    client.converse.side_effect = RuntimeError("bedrock throttled")
    # Must not raise.
    jc = classify_correction(
        REAL_USER_CORRECTION, evolution_classes=["CLASS_A"], bedrock_client=client
    )
    assert jc is None


def test_ac5_malformed_llm_response_degrades_to_none():
    """AC5: unparseable LLM output (not JSON) degrades to None, no crash."""
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": "not json at all"}]}},
        "usage": {},
    }
    jc = classify_correction(
        REAL_USER_CORRECTION, evolution_classes=["CLASS_A"], bedrock_client=client
    )
    assert jc is None


def test_cognitive_with_invalid_class_degrades_to_none():
    """Adversarial #3: axis=cognitive but invalid class_name -> None, NOT a silent
    downgrade to operational+counted (which would mis-count in the wrong direction)."""
    client = _fake_bedrock_returning({
        "axis": "cognitive",
        "class_name": "CLASS_D",  # not in valid set
        "parent_principle": "P1",
        "confidence": 0.7,
    })
    jc = classify_correction(
        REAL_USER_CORRECTION, evolution_classes=["CLASS_A"], bedrock_client=client
    )
    assert jc is None


def test_operational_axis_with_null_class_still_counts():
    """LLM judging a user_correction as NOT a real correction (axis=operational,
    class=null) is honored as operational/counted — not degraded."""
    client = _fake_bedrock_returning({"axis": "operational", "class_name": None})
    jc = classify_correction(
        REAL_USER_CORRECTION, evolution_classes=["CLASS_A"], bedrock_client=client
    )
    assert jc is not None
    assert jc.axis == "operational"
    assert jc.counter_state == "counted"


def test_parse_label_ignores_trailing_object():
    """Adversarial #4: a multi-object LLM response parses the FIRST object cleanly."""
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [
            {"text": '{"axis":"cognitive","class_name":"CLASS_B","parent_principle":"P2"} extra {"junk":1}'}
        ]}},
        "usage": {},
    }
    jc = classify_correction(
        REAL_USER_CORRECTION, evolution_classes=["CLASS_B"], bedrock_client=client
    )
    assert jc is not None
    assert jc.class_name == "CLASS_B"


def test_ac5_unknown_record_type_returns_none():
    """A record type the classifier doesn't handle (subagent_finding) -> None, no crash."""
    rec = {"ts": 1.0, "session_id": "x", "type": "subagent_finding", "summary": "Error: rg"}
    jc = classify_correction(rec, evolution_classes=[])
    assert jc is None


def test_ac5_missing_fields_no_crash():
    """A record missing expected fields degrades gracefully."""
    assert classify_correction({}, evolution_classes=[]) is None
    assert classify_correction({"type": "user_correction"}, evolution_classes=[]) is None


# --- Dataclass contract ---

def test_classification_dataclass_fields():
    """JudgmentClassification carries all routing-relevant fields."""
    jc = JudgmentClassification(
        correction_ref="1781696331.24:e95e5923",
        axis="cognitive",
        class_name="CLASS_A",
        parent_principle="P1",
        skill_spread=[],
        blast_radius=0,
        evidence=["x"],
        tier="llm",
        confidence=0.8,
        counter_state="pending_confirm",
    )
    assert jc.correction_ref.startswith("1781696331")
    assert jc.counter_state == "pending_confirm"
