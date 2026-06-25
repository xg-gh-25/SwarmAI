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

# Real operator/transient NOISE samples drawn verbatim from the live 802-record
# OPERATIONAL pollution (corrections.jsonl). These are NOT recurring tool-misuse
# patterns — they are one-off operator slips / transient infra. They must be
# IGNORED (not counted), or they inflate the tracker and drive fake governance
# proposals ("Recurring OPERATIONAL 749x — propose an L1 rule").
NOISE_SAMPLES = {
    "file-not-found": "File does not exist. Note: your current working directory is /Users/gawan/.swarm-ai",
    "no-such-file": "ls: backend/scripts/scenario_runner.py: No such file or directory",
    "python-traceback-probe": (
        "Exit code 1\nTraceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
        "KeyError: 'artifact_id'"
    ),
    "shell-arg-too-long": "xargs: command line cannot be assembled, too long",
    "user-interrupt": "[Request interrupted by user for tool use]",
    "git-rpc-408": "Exit code 1\nerror: RPC failed; HTTP 408 curl 22 The requested URL returned error: 408",
    "ripgrep-timeout": "Ripgrep search timed out after 20 seconds. The search may have matched files",
    "glob-no-match": "(eval):1: no matches found: http://127.0.0.1:18321/api/workspace/tree?depth=10",
}

# A GENUINE in-our-code defect (not operator noise): a real AttributeError raised
# by SwarmAI code. This is the one operational shape that COULD be worth counting
# if it recurred — so the gate must NOT over-filter it to ignored.
GENUINE_CODE_FAILURE = {
    "ts": 1781055460.0,
    "session_id": "abcd1234-0000-0000-0000-000000000000",
    "type": "tool_failure",
    "tool": "Bash",
    "input_summary": "{'command': 'python -c ...'}",
    "error": (
        "Exit code 1\nTraceback (most recent call last):\n"
        "  File \"backend/core/session_unit.py\", line 1894, in _acquire\n"
        "AttributeError: 'SessionUnit' object has no attribute '_pid'"
    ),
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
    """AC2 negative: a single-tool tool_failure stays operational, no LLM, no class.

    REAL_TOOL_FAILURE's error is "No such file" — operator NOISE — so post-fix it
    classifies operational but counter_state='ignored' (not counted). It still must
    NOT be escalated to cognitive / call the LLM.
    """
    # No bedrock client passed at all — operational path must not need one.
    jc = classify_correction(REAL_TOOL_FAILURE, evolution_classes=["CLASS_A"])
    assert jc is not None
    assert jc.axis == "operational"
    assert jc.class_name is None  # operational has no cognitive class
    assert jc.tier == "mechanical"
    assert jc.blast_radius == 1  # single tool
    # "No such file" is operator noise → ignored, NOT counted (the fix).
    assert jc.counter_state == "ignored"


# --- Noise quality-gate: operator/transient noise is IGNORED, not counted ---

@pytest.mark.parametrize("label,error_text", list(NOISE_SAMPLES.items()))
def test_operational_noise_is_ignored(label, error_text):
    """Each real noise sample → operational axis but counter_state='ignored'.

    Ignored = will not feed the recurrence counter that drives fake governance.
    """
    record = {"ts": 1.0, "session_id": "x", "type": "tool_failure",
              "tool": "Bash", "error": error_text}
    jc = classify_correction(record, evolution_classes=[])
    assert jc is not None, f"{label}: must still classify (not None)"
    assert jc.axis == "operational", f"{label}: stays operational"
    assert jc.counter_state == "ignored", f"{label}: noise must be ignored, not counted"


def test_genuine_code_failure_still_counts():
    """A real in-our-code AttributeError (traceback from backend/core/...) is NOT
    operator noise → counter_state='counted'. The gate must not over-filter."""
    jc = classify_correction(GENUINE_CODE_FAILURE, evolution_classes=[])
    assert jc is not None
    assert jc.axis == "operational"
    assert jc.counter_state == "counted", "a genuine code defect must still count"


def test_string_probe_traceback_is_noise_but_real_file_traceback_is_not():
    """Discriminator: a traceback from <string> (inline -c probe) is noise; a
    traceback from a real source file is a genuine failure. Same 'Traceback' word,
    opposite verdict — proves the gate is not a blunt keyword match (run_76273219)."""
    probe = {"type": "tool_failure", "tool": "Bash",
             "error": "Traceback (most recent call last):\n  File \"<string>\", line 1\nKeyError: 'x'"}
    real = {"type": "tool_failure", "tool": "Bash",
            "error": "Traceback (most recent call last):\n  File \"backend/core/x.py\", line 9\nValueError: bad"}
    assert classify_correction(probe, evolution_classes=[]).counter_state == "ignored"
    assert classify_correction(real, evolution_classes=[]).counter_state == "counted"


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
