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


def test_run_create_links_the_session_without_its_stdout(audit, ws, capsys, monkeypatch):
    run_id = _create(capsys, monkeypatch, "sess-link")
    assert rh.read_session_run_map("sess-link")[0] == run_id


def test_run_create_with_an_invalid_session_id_writes_no_map(audit, ws, capsys, monkeypatch):
    _create(capsys, monkeypatch, "bad sid!")
    assert not list(audit.glob("*.map")) if audit.exists() else True


def test_run_create_survives_a_map_write_failure(audit, ws, capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rh, "write_session_run_map", boom)
    assert _create(capsys, monkeypatch, "sess-fail").startswith("run_")


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


# ── AC5 ─────────────────────────────────────────────────────────────────────
def _run_update(ws, monkeypatch, *extra):
    rd = ws / "Projects" / "P1" / ".artifacts" / "runs" / "run_ft"
    rd.mkdir(parents=True, exist_ok=True)
    if not (rd / "run.json").exists():
        (rd / "run.json").write_text(json.dumps({"id": "run_ft", "project": "P1", "status": "running",
                                                 "stages": []}))
    monkeypatch.setattr(sys, "argv", ["artifact_cli.py", "run-update", "--project", "P1",
                                      "--run-id", "run_ft", *extra])
    cli.main()
    return json.loads((rd / "run.json").read_text()).get("files_touched")


def test_files_touched_accepts_plain_paths_and_json(ws, monkeypatch, capsys):
    assert _run_update(ws, monkeypatch, "--files-touched", "/r/a.py", "/r/b.py") == ["/r/a.py", "/r/b.py"]
    assert _run_update(ws, monkeypatch, "--files-touched", '["/r/b.py", "/r/c.py"]') == \
        ["/r/a.py", "/r/b.py", "/r/c.py"]


def test_files_touched_malformed_json_still_exits_1(ws, monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        _run_update(ws, monkeypatch, "--files-touched", '["/r/a.py"')
    assert e.value.code == 1


def test_unquoted_json_array_split_by_the_shell_still_exits_1(ws, monkeypatch, capsys):
    """Meta-review: `--files-touched ["a.py", "b.py"]` unquoted reaches argv as
    `[a.py,` `b.py]`; those fragments must fail validation, never be saved as paths."""
    with pytest.raises(SystemExit) as e:
        _run_update(ws, monkeypatch, "--files-touched", "[/r/a.py,", "/r/b.py]")
    assert e.value.code == 1


def test_normalized_files_touched_is_one_value_for_every_reader():
    """The normalizer only: plain paths serialize, JSON passes through unchanged.
    That the gate and the write read the same value rests on the single call at the
    top of cmd_run_update, which no test drives through the completion gate."""
    assert cli._normalize_files_touched_arg(None) is None
    assert json.loads(cli._normalize_files_touched_arg(["/r/a.py", "/r/b.py"])) == ["/r/a.py", "/r/b.py"]
    assert cli._normalize_files_touched_arg(['["/r/a.py"]']) == '["/r/a.py"]'
    assert cli._normalize_files_touched_arg('["/r/a.py"]') == '["/r/a.py"]'  # direct callers
