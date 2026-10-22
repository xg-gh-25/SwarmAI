"""Layer-4 E2E for the spec audit chain: the hook (daemon side) writes, the CLI and the
run documents (CLI side) read — nothing on the seam is mocked.

What is tested:
- A Gate-2 expert's finding with a fix → the real hook keeps the fix on the marker row.
- A plan artifact with contract readers → the validator accepts exactly what DESIGN_SPEC.md
  renders (one artifact, two readers).

"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import run_docs as rd
from tests.test_runtime_hooks import _gate_transcript

RID = "run_chain1"


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
async def test_a_review_fix_text_reaches_the_marker_row(audit, tmp_path):
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
