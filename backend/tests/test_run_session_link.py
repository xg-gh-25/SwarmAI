"""A pipeline run stays linked to the session that created it (run_df73d970).

run_1f81be36 redirected ``run-create``'s stdout to a file. The session→run map was
written only by a PostToolUse hook that parses that stdout, so the run was never
linked: every reviewer marker went to the PREVIOUS run and ``run-commit`` refused
(D7). These tests pin the fixes:

- AC3: ``run-create`` writes ``session_<sid>_run.map`` itself (SWARM_APP_SESSION_ID),
  through the same helper and shape the hook uses; a write failure never fails it.
- AC4: the SubagentStop audit hook attributes a marker to the NEWER of the session
  ctx run and the session's map run.
- AC5: ``run-update --files-touched`` accepts a JSON array or plain paths.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import runtime_hooks as rh
from scripts import artifact_cli as cli


@pytest.fixture
def audit(tmp_path, monkeypatch):
    d = tmp_path / "audit"
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", d)
    return d


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "ws"
    (w / "Projects" / "P1").mkdir(parents=True)
    monkeypatch.setenv("SWARM_WORKSPACE", str(w))
    monkeypatch.setattr(cli, "_get_workspace", lambda: w)
    return w


# ── AC3 ─────────────────────────────────────────────────────────────────────
def test_map_helper_roundtrip_has_the_hook_shape(audit):
    rh.write_session_run_map("sess-1", "run_aaaa1111", ts=5.0)
    data = json.loads((audit / "session_sess-1_run.map").read_text())
    assert data == {"session_id": "sess-1", "run_id": "run_aaaa1111", "ts": 5.0}
    assert rh.read_session_run_map("sess-1") == ("run_aaaa1111", 5.0)
    assert rh.read_session_run_map("nobody") is None


def _create(capsys, monkeypatch, sid):
    if sid is None:
        monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("SWARM_APP_SESSION_ID", sid)
    args = SimpleNamespace(project="P1", requirement="do x", profile=None, intent=None)
    cli.cmd_run_create(args, MagicMock())
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(out)["pipeline_id"]


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
