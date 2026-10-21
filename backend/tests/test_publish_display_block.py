"""Tests for cmd_publish's EVALUATE `_display_block` (run_f6b09629).

WHAT IS TESTED
    The "需求理解" (requirement-clarification) real-time inline emit — the block the
    user should see in the streaming chat during EVALUATE — had NO backend producer:
    it was a pure `[MUST]` instruction telling the agent to hand-type a markdown block
    (F004 attention-decay → almost never emitted). The fix makes the structurally
    MANDATORY step `publish --stage evaluate` return the CLI-rendered block as a new
    `_display_block` FIELD inside the success-result JSON. The agent relays a
    ready-made block instead of recalling-and-retyping it.

METHODOLOGY
    cmd_publish is driven directly via an argparse-style Namespace + an ArtifactRegistry
    on a tmp workspace (project dir + run dir pre-created so publish does not fail).
    stdout is captured with capsys and parsed as JSON — because the WHOLE POINT of the
    A1 design (vs appending bare markdown after the JSON line) is that stdout stays a
    SINGLE valid JSON object on every path. A test that could not json.load the stdout
    would itself prove the contract broke.

KEY PROPERTIES
    - AC1: non-quiet evaluate + requirements[] + schema_version>=1 → result._display_block
      is a non-empty str carrying the audit block (FR rows).
    - AC2: --quiet same payload → stdout is a single JSON line == {"artifact_id": ...},
      NO _display_block (parse-proof contract preserved). Also proves AC1's block is
      ABSENT here, i.e. the field is gated on non-quiet.
    - AC3: stage=plan → no _display_block (gate keys on stage==evaluate).
    - AC4: evaluate but NO requirements[] (or schema_version<1) → no _display_block
      (mirrors the REPORT-side gate: relaxed/legacy runs get zero ceremony).
    - AC6: render fn raises → publish still exits 0 (best-effort) and result has no
      _display_block (never fail the publish for a cosmetic preview).

    Mutation proof: delete the _display_block population block in cmd_publish →
    test_ac1 goes RED (field absent on the one path that must carry it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# test_ac5 / the next test import pipeline_validator by its bare name: put scripts/ on
# sys.path here, not by luck of an earlier test importing scripts.artifact_cli first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def reg(tmp_path):
    """ArtifactRegistry on a tmp workspace with a pre-created project + run dir."""
    from core.artifact_registry import ArtifactRegistry

    proj = tmp_path / "Projects" / "TestProj"
    (proj / ".artifacts" / "runs" / "run_test1").mkdir(parents=True)
    for name in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        (proj / name).write_text("# T\n")
    # a minimal run.json so auto-record (publish --stage) has a target
    (proj / ".artifacts" / "runs" / "run_test1" / "run.json").write_text(json.dumps({
        "id": "run_test1", "project": "TestProj", "profile": "full",
        "status": "running", "stages": [],
    }))
    return ArtifactRegistry(tmp_path)


def _eval_data(with_reqs=True, schema_version=1):
    """A valid evaluation payload that PASSES the strict-profile schema (so publish
    reaches the _display_block logic, which runs AFTER validation). requirements[]/
    schema_version are the glass-box gate inputs; understanding/ambiguity_scan are
    required by the strict (full-profile) evaluate schema."""
    d = {
        "requirement": "让需求澄清在 streaming 里实时可见",
        "recommendation": "GO",
        "scope": "standard",
        "acceptance_criteria": ["AC1: display block present"],
        "scores": {"strategic": 7, "priority": 7, "feasibility": 8},
        "understanding": {
            "work_type": "existing-feature",
            "claim": "cmd_publish 当前不回传需求理解渲染块",
            "evidence": "code-trace artifact_cli.py:543,629 — result 无 _display_block 字段",
            "evidence_kind": "code-trace",
            "skeptic_verdict": "SUPPORTED",
            "alternative_considered": "后端 SSE emit — publish 无 SSE 通道,输给 agent-relay",
        },
        "ambiguity_scan": {
            "scanned_fields": ["who", "what", "why", "when", "acceptance_criteria"],
            "terms_checked": ["depends", "maybe"],
            "hits": [],
            "hit_count": 0,
            "all_resolved": True,
        },
        "pre_mortem": ["做成又一条 [MUST] 文案", "破坏 stdout JSON 契约"],
    }
    if schema_version is not None:
        d["schema_version"] = schema_version
    if with_reqs:
        d["requirements"] = [{
            "id": "FR-1",
            "statement": "需求澄清实时可见",
            "work_class": "user-story",
            "source": {"kind": "user-stated", "ref": "XG"},
            "status": "confirmed",
            "acceptance": [{"given": "EVALUATE 完成", "when": "publish", "then": "stdout 含块"}],
        }]
        d["clarifications"] = []
    return d


def _args(data: dict, *, stage="evaluate", quiet=False, run_id="run_test1",
          atype="evaluation"):
    return SimpleNamespace(
        data=json.dumps(data, ensure_ascii=False),
        stage=stage,
        quiet=quiet,
        run_id=run_id,
        project="TestProj",
        type=atype,
        producer="test",
        summary="t",
        topic="",
    )


def _run_publish(args, reg, capsys):
    """Call cmd_publish, return (parsed_stdout_dict | None, raw_stdout, raw_stderr).

    stdout MUST be a single valid JSON object (the A1 contract) — a json.loads
    failure here is itself a contract regression, surfaced as a test failure.
    """
    from scripts.artifact_cli import cmd_publish
    cmd_publish(args, reg)
    cap = capsys.readouterr()
    out = cap.out.strip()
    parsed = json.loads(out) if out else None
    return parsed, out, cap.err


# ── AC1: non-quiet evaluate + requirements[] → _display_block present ───────

def test_ac1_display_block_present_on_nonquiet_evaluate(reg, capsys):
    parsed, raw, _ = _run_publish(_args(_eval_data()), reg, capsys)
    # stdout is a single valid JSON object (A1 contract — not bare markdown appended)
    assert parsed is not None and "artifact_id" in parsed
    assert "\n" not in raw.strip(), "non-quiet success stdout must be a single JSON line"
    assert "_display_block" in parsed, "evaluate+requirements[] must carry the rendered block"
    block = parsed["_display_block"]
    assert isinstance(block, str) and block.strip()
    # it is the audit block, same source as REPORT §4.6
    assert "需求溯源审计" in block or "Requirement Provenance" in block
    assert "FR-1" in block


# ── AC2: --quiet → parse-proof single line, NO _display_block ───────────────

def test_ac2_quiet_stays_parse_proof_no_block(reg, capsys):
    parsed, raw, _ = _run_publish(_args(_eval_data(), quiet=True), reg, capsys)
    assert parsed == {"artifact_id": parsed.get("artifact_id")}
    assert set(parsed.keys()) == {"artifact_id"}, "quiet stdout must be ONLY artifact_id"
    assert "_display_block" not in parsed
    assert "\n" not in raw.strip()


# ── AC3: stage != evaluate → no block ───────────────────────────────────────

def test_ac3_non_evaluate_stage_no_block(reg, capsys):
    # a plan artifact with a requirements-looking field must still NOT emit the block
    plan_data = {"approach": "x", "acceptance_criteria": ["AC1"],
                 "requirements": [{"id": "FR-1"}], "schema_version": 1}
    parsed, _, _ = _run_publish(
        _args(plan_data, stage="plan", atype="design_doc"), reg, capsys)
    assert parsed is not None and "artifact_id" in parsed
    assert "_display_block" not in parsed


# ── AC4: evaluate but no requirements[] / stale schema_version → no block ────

def test_ac4_no_requirements_no_block(reg, capsys):
    parsed, _, _ = _run_publish(_args(_eval_data(with_reqs=False)), reg, capsys)
    assert parsed is not None and "artifact_id" in parsed
    assert "_display_block" not in parsed


def test_ac4_legacy_schema_version_no_block(reg, capsys):
    # requirements[] present but schema_version absent (legacy/half-activated) → gate off
    parsed, _, _ = _run_publish(
        _args(_eval_data(with_reqs=True, schema_version=None)), reg, capsys)
    assert parsed is not None and "artifact_id" in parsed
    assert "_display_block" not in parsed


# ── AC6: render fn raises → publish still succeeds, no block ─────────────────

def test_ac6_render_failure_degrades_gracefully(reg, capsys, monkeypatch):
    import pipeline_validator
    def _boom(requirement, data):
        raise RuntimeError("render exploded")
    monkeypatch.setattr(pipeline_validator, "render_audit_trail_md", _boom)
    # must not raise, must still emit a valid success JSON without the block
    parsed, _, err = _run_publish(_args(_eval_data()), reg, capsys)
    assert parsed is not None and "artifact_id" in parsed
    assert "_display_block" not in parsed


# ── AC5: the block is LITERALLY render_audit_trail_md's output (no reimpl) ───

def test_ac5_block_is_render_audit_trail_md_verbatim(reg, capsys):
    """Prove zero-drift reuse: the emitted _display_block must byte-equal a direct
    call to the SAME pure fn the REPORT side uses — if cmd_publish reimplemented
    rendering, the strings would differ."""
    from pipeline_validator import render_audit_trail_md
    data = _eval_data()
    parsed, _, _ = _run_publish(_args(data), reg, capsys)
    expected = render_audit_trail_md(data["requirement"], data)
    assert parsed["_display_block"] == expected, (
        "block must be the verbatim render_audit_trail_md output (same source as "
        "REPORT §4.6 — reimplementation would drift)")


# ── Zero-drift: publish block == REPORT-path render over the archive round-trip ─

def test_display_block_matches_report_render_after_archive_roundtrip(reg, capsys):
    """The design's load-bearing invariant is 'preview (publish _display_block) and
    archive (REPORT §4.6) CANNOT drift'. This pins the GATE+RENDER equivalence: given
    the same payload, publish's _display_block must byte-equal what the REPORT path
    produces from the archived artifact read back.

    Scope honesty (per adversarial review): the payload reaching cmd_publish is ALREADY
    post-json (the CLI parses --data from a JSON string), and the archive stores it with
    the SAME json serializer the publish path saw — so a lossy coercion (datetime→str)
    cannot differ between the two sides by construction; there is no datetime to inject
    at this layer. What this test DOES pin: (a) publish renders via the identical
    render_audit_trail_md the REPORT path uses, over the json-reparsed data, and (b) the
    two render argument sources agree — publish passes data['requirement'], REPORT passes
    run_state['requirement'] (equal in a well-formed run). A divergence in gate or render
    between the two call sites goes RED here."""
    from pipeline_validator import render_audit_trail_md
    import json as _json
    data = _eval_data()
    parsed, _, _ = _run_publish(_args(data), reg, capsys)
    publish_block = parsed["_display_block"]

    # The REPORT path reads the artifact back after json persistence; reproduce that
    # json reparse so this compares the SAME shape the REPORT render receives.
    roundtripped = _json.loads(_json.dumps(data, default=str))
    report_block = render_audit_trail_md(roundtripped.get("requirement", ""), roundtripped)

    assert publish_block == report_block, (
        "preview and archive MUST render identically — any divergence here is the "
        "exact drift the single-source design forbids")


# ── AC7: both pipeline docs instruct RELAY (not hand-type) ──────────────────

def test_ac7_docs_instruct_relay_not_handtype(reg):
    """The two orchestration docs must tell the agent to relay the publish
    _display_block, not to hand-type/emit a block from memory (the F004 fix)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]  # backend/
    eval_md = (root / "skills" / "s_autonomous-pipeline" / "stages" / "evaluate.md").read_text()
    instr_md = (root / "skills" / "s_autonomous-pipeline" / "INSTRUCTIONS.md").read_text()
    # both now reference the _display_block relay mechanism
    assert "_display_block" in eval_md, "evaluate.md must reference the _display_block relay"
    assert "_display_block" in instr_md, "INSTRUCTIONS.md must reference the _display_block relay"
    # evaluate.md explicitly forbids hand-typing from memory
    assert "Do NOT hand-type" in eval_md
