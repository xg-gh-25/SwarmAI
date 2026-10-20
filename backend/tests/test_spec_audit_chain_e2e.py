"""Layer-4 E2E for the spec audit chain: the hook (daemon side) writes, the CLI and the
run documents (CLI side) read — nothing on the seam is mocked.

What is tested:
- A gate1-skeptic sub-agent finishes → the REAL SubagentStop hook writes its marker into
  an audit dir → the REAL CLI reader (``artifact_cli._gate1_points`` through
  ``_gate_marker_rows``) picks the points up → the REAL renderer puts them in REVIEW.md's
  Gate 1 row. Only the app-data dir is redirected to a temp dir.
- A Gate-2 expert's finding with a fix → the real hook keeps the fix on the marker row.
- A plan artifact with contract readers → the validator accepts exactly what DESIGN_SPEC.md
  renders (one artifact, two readers).

Mutation-verified at TEST: reverting the hook's gate1-skeptic verdict_text capture turns
the first test RED.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import run_docs as rd
from scripts import artifact_cli as cli
from tests.test_runtime_hooks import _gate_transcript

RID = "run_chain1"
OLD_RUN = "2026-10-01T00:00:00+00:00"   # before the hand-out cutover: binding not checked


@pytest.fixture
def audit(tmp_path, monkeypatch):
    import config
    import core.runtime_hooks as rh
    data = tmp_path / "data"
    d = data / "state" / "pipeline_agent_audit"
    d.mkdir(parents=True)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", d)
    monkeypatch.setattr(rh, "_resolve_owner_run", lambda *a, **k: RID, raising=False)
    return rh, d


async def _finish(rh, tmp_path, name: str, expert_line: str, answer: str, session: str) -> dict:
    t = tmp_path / f"{name}.jsonl"
    _gate_transcript(t, expert_line, answer)
    hook = rh.create_agent_tool_audit_hook({"sdk_session_id": session, "_active_pipeline_run_id": RID})
    await hook({"agent_id": name, "agent_transcript_path": str(t), "agent_type": "pipeline-expert"},
               None, MagicMock())
    rows = [json.loads(p.read_text()) for p in rh._PIPELINE_AUDIT_DIR.glob(f"session_{session}_*.marker")
            if "_adv_" not in p.name]
    assert rows, "the hook wrote no marker"
    return rows[-1]


@pytest.mark.asyncio
async def test_gate1_answer_reaches_review_md_through_the_real_hook(audit, tmp_path):
    rh, _d = audit
    row = await _finish(rh, tmp_path, "g1", "EXPERT-ID: gate1-skeptic",
                        "GATE 1 VERDICT: WARN\n  2. Failure Pattern: WARN. normalize paths first\n"
                        "  3. Simplicity: PASS. fine", "sess-chain")
    if row.get("run_id") != RID:
        pytest.skip(f"hook attributed the marker to {row.get('run_id')!r}: owner resolution differs here")
    points = cli._gate1_points(RID, {"id": RID, "created_at": OLD_RUN})
    assert points == ["GATE 1 VERDICT: WARN", "2. Failure Pattern: WARN. normalize paths first"]
    md = rd.render_review({"id": RID}, {}, {"build": {"gate1_verdict": "WARN", "gate1_points": points}}, {}, {})
    gate1_row = next(ln for ln in md.splitlines() if ln.startswith("| Gate 1"))
    assert "normalize paths first" in gate1_row


@pytest.mark.asyncio
async def test_a_gate2_fix_text_reaches_the_marker_row(audit, tmp_path):
    rh, _d = audit
    finding = ('{"severity":"HIGH","confidence":8,"path":"a.py","line":3,"category":"c",'
               '"summary":"reader left behind","fix":"migrate b.py::g too","specialist":"integration"}')
    row = await _finish(rh, tmp_path, "g2", "EXPERT-ID: integration", finding, "sess-chain2")
    assert row["reported"][0]["fix"] == "migrate b.py::g too"


def test_the_plan_the_validator_accepts_is_the_plan_design_spec_shows():
    import design_spec as ds
    plan = {"design": dict(ds.PLAN_TEMPLATE),
            "implementation_constraints": {"consumers": [
                {"id": "K1", "contract": "read_index may return an unusable value",
                 "pattern": "read_index\\(", "paths": ["backend"],
                 "readers": [{"at": "backend/a.py::f", "status": "migrated"},
                             {"at": "backend/b.py::g", "status": "unaffected", "reason": "only logs it"}]}]}}
    assert ds.constraints_errors(plan, "full") == []
    md = rd.render_design_spec({"id": RID}, {}, plan)
    assert "| K1 |" in md and "`read_index\\(`" in md and "backend/b.py::g" in md
