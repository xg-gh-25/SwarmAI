"""A pipeline run stays linked to the session that created it (run_df73d970).

run_1f81be36 redirected the create command's stdout to a file. The session→run map
was written only by a PostToolUse hook that parses that stdout, so the run was never
linked and every reviewer marker went to the PREVIOUS run. These tests pin the fixes
that remain with Pipeline v1:

- the map helper writes the same shape the hook and run_owner_gate (at a takeover) use;
- AC4: the SubagentStop audit hook attributes a marker to the NEWER of the session
  ctx run and the session's map run.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core import runtime_hooks as rh


@pytest.fixture
def audit(tmp_path, monkeypatch):
    d = tmp_path / "audit"
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", d)
    return d


# ── the map helper ──────────────────────────────────────────────────────────
def test_map_helper_roundtrip_has_the_hook_shape(audit):
    rh.write_session_run_map("sess-1", "run_aaaa1111", ts=5.0)
    data = json.loads((audit / "session_sess-1_run.map").read_text())
    assert data == {"session_id": "sess-1", "run_id": "run_aaaa1111", "ts": 5.0}
    assert rh.read_session_run_map("sess-1") == ("run_aaaa1111", 5.0)
    assert rh.read_session_run_map("nobody") is None


# ── AC4 ─────────────────────────────────────────────────────────────────────
async def _finish_subagent(ctx, tmp_path):
    t = tmp_path / "sub.jsonl"
    t.write_text('{"isSidechain": true, "type": "user", "message": {"content": '
                 '"EXPERT-ID: concurrency\\n\\nAdversarially review this changeset."}}\n')
    hook = rh.create_agent_tool_audit_hook(ctx)
    await hook({"agent_id": "a-1", "agent_transcript_path": str(t), "agent_type": "general-purpose"},
               None, MagicMock())


def _marker_run_ids(audit, sid):
    return {json.loads(p.read_text()).get("run_id") for p in audit.glob(f"session_{sid}_*.marker")}


@pytest.mark.asyncio
async def test_marker_takes_a_newer_map_over_a_stale_ctx(audit, tmp_path):
    ctx = {"sdk_session_id": "s1", "_active_pipeline_run_id": "run_old", "_active_pipeline_run_id_ts": 1.0}
    rh.write_session_run_map("s1", "run_new", ts=2.0)
    await _finish_subagent(ctx, tmp_path)
    assert _marker_run_ids(audit, "s1") == {"run_new"}
    assert (audit / "run_new.marker").exists() and not (audit / "run_old.marker").exists()


@pytest.mark.asyncio
async def test_marker_keeps_a_newer_ctx_over_an_old_map(audit, tmp_path):
    ctx = {"sdk_session_id": "s2", "_active_pipeline_run_id": "run_ctx", "_active_pipeline_run_id_ts": 9.0}
    rh.write_session_run_map("s2", "run_map", ts=2.0)
    await _finish_subagent(ctx, tmp_path)
    assert _marker_run_ids(audit, "s2") == {"run_ctx"}


@pytest.mark.asyncio
async def test_marker_uses_the_map_when_ctx_has_no_run(audit, tmp_path):
    rh.write_session_run_map("s3", "run_only_map", ts=2.0)
    await _finish_subagent({"sdk_session_id": "s3"}, tmp_path)
    assert _marker_run_ids(audit, "s3") == {"run_only_map"}
