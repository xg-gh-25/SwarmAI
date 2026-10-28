"""Pipeline v1 real end-to-end tests: REFLECT runs inside the landing (CDF S3 report enqueue).

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── D1: REFLECT is the daemon's background work ─────────────────────────────

def test_the_agent_cannot_complete_reflect_and_the_landing_does(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    out = r.v1("reflect-complete")
    assert out.get("ok") is not True                       # the command is gone
    # the landing's own command runs REFLECT before it returns (standalone §4.6)
    r.v1("land", "--message", "fix: swap the operands\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    state = r.state()
    assert state["status"] == "completed" and state.get("reflect") in ("ok", None), state
    complete = [e for e in r.events() if e["kind"] == "reflect_complete"]
    assert len(complete) == 1 and complete[0]["producer"] == "hook"


def test_measuring_after_the_daemon_completed_reflect_refreshes_the_final_audit(rig, monkeypatch):
    import json
    from core.run_protocol import reflect_job, usage
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    r.v1("land", "--message", "fix: swap the operands\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    reflect_job.process_run(r.engine())
    engine = r.engine()
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    token = audit["summary"]["token_cost"]
    # the daemon found no transcript when REFLECT ended (r6 §2.3), and the review
    # expert it spawned was not metered either: sub-agent tokens are uncounted (§E5)
    assert token["verdict"] == "BLOCK" and token["not_available"], token
    monkeypatch.setattr(usage.transcript_usage, "resolve_transcripts", lambda ids: ["t"])
    monkeypatch.setattr(usage.transcript_usage, "usage_in_window", lambda *a, **k: {
        "input_tokens": 10, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "output_tokens": 5, "responses": 2})
    engine.measure_usage()
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    assert audit["summary"]["token_cost"]["verdict"] != "BLOCK", audit["summary"]["token_cost"]
    assert audit["summary"]["token_cost"]["not_available"] is None
