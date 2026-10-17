"""artifact_cli wiring for the §4-E delivery summary (pipeline-speed-systematic-fix).

WHAT IS TESTED
- run-create records ``session_id`` / ``intent`` / ``intent_source``: the session's
  own words win (SWARM_APP_SESSION_ID + capture_intent), then ``--intent``, then the
  requirement. A malformed session id is dropped, never stored.
- ``run-summary`` prints one JSON object whose ``_display_block`` is the rendered
  summary, and exits 1 on an unknown run.
- ``run-metrics`` MERGES into an existing METRICS.json: a ``speed_profile`` written
  earlier survives a later metrics extraction.
- A run created without SWARM_APP_SESSION_ID is still created, with a stderr
  warning; the delivery summary gets this repo as ``repo_root`` only for SwarmAI.
- REPORT.md's ``**Tokens:**`` header comes from ``run_token_usage`` (never the
  strict window) and carries its caveats (不含当前一轮 / 含建 run 前的同一轮);
  no measurable usage → the stage-sum ``估算 / estimate`` label.

METHODOLOGY
Command functions are driven in-process with SimpleNamespace args against a tmp
SWARM_WORKSPACE; stdout is parsed. capture_intent is stubbed (no data.db).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.artifact_cli as cli  # noqa: E402
import core.run_delivery_summary as rds  # noqa: E402
from core.artifact_registry import ArtifactRegistry  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    return tmp_path


def _create(capsys, ws, **kw) -> dict:
    args = SimpleNamespace(project="Proj", requirement="REQ text", profile=None,
                           intent=kw.pop("intent", None))
    cli.cmd_run_create(args, ArtifactRegistry(workspace_root=str(ws)))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return json.loads(Path(out["file"]).read_text(encoding="utf-8"))


def test_session_words_win(ws, capsys, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-123")
    seen = {}

    def fake(sid, now_iso):
        seen["sid"] = sid
        return ("我实在受不了了 太慢了", "session")

    monkeypatch.setattr(rds, "capture_intent", fake)
    st = _create(capsys, ws, intent="arg intent")
    assert seen["sid"] == "sess-123"
    assert st["session_id"] == "sess-123"
    assert st["intent"] == "我实在受不了了 太慢了"
    assert st["intent_source"] == "session"


def test_arg_then_requirement_fallback(ws, capsys, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-9")
    monkeypatch.setattr(rds, "capture_intent", lambda sid, now: None)
    st = _create(capsys, ws, intent="the --intent text")
    assert (st["intent"], st["intent_source"]) == ("the --intent text", "arg")
    st2 = _create(capsys, ws)
    assert (st2["intent"], st2["intent_source"]) == ("REQ text", "requirement")


def test_capture_failure_never_blocks_create(ws, capsys, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-9")

    def boom(sid, now):
        raise RuntimeError("db locked")

    monkeypatch.setattr(rds, "capture_intent", boom)
    st = _create(capsys, ws)
    assert st["intent_source"] == "requirement"
    assert st["session_id"] == "sess-9"


@pytest.mark.parametrize("bad", ["../../etc", "a b", "x" * 200, "id;rm -rf"])
def test_malformed_session_id_dropped(ws, capsys, monkeypatch, bad):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", bad)
    called = []
    monkeypatch.setattr(rds, "capture_intent", lambda *a: called.append(a))
    st = _create(capsys, ws)
    assert st["session_id"] is None
    assert called == []


def test_run_summary_prints_display_block(ws, capsys, monkeypatch):
    st = _create(capsys, ws)
    monkeypatch.setattr(rds, "build_delivery_summary",
                        lambda run_state, arts, run_dir, **kw: f"SUMMARY {run_state['id']}")
    cli.cmd_run_summary(SimpleNamespace(project="Proj", run_id=st["id"]), None)
    out = json.loads(capsys.readouterr().out)
    assert out == {"run_id": st["id"], "_display_block": f"SUMMARY {st['id']}"}


def test_run_summary_render_failure_degrades(ws, capsys, monkeypatch):
    st = _create(capsys, ws)

    def boom(*a, **k):
        raise KeyError("x")

    monkeypatch.setattr(rds, "build_delivery_summary", boom)
    cli.cmd_run_summary(SimpleNamespace(project="Proj", run_id=st["id"]), None)
    block = json.loads(capsys.readouterr().out)["_display_block"]
    assert "delivery summary unavailable" in block and "run-summary" in block


def test_run_summary_unknown_run_exits(ws, capsys):
    _create(capsys, ws)
    with pytest.raises(SystemExit) as ei:
        cli.cmd_run_summary(SimpleNamespace(project="Proj", run_id="run_deadbeef"), None)
    assert ei.value.code == 1


def test_run_metrics_preserves_speed_profile(ws, capsys):
    st = _create(capsys, ws)
    mfile = ws / "Projects" / "Proj" / ".artifacts" / "runs" / st["id"] / "METRICS.json"
    assert mfile.parent.is_dir()
    mfile.write_text(json.dumps({"speed_profile": {"tool_calls": 309}}), encoding="utf-8")
    cli.cmd_run_metrics(SimpleNamespace(project="Proj", run_id=st["id"]), None)
    capsys.readouterr()
    merged = json.loads(mfile.read_text(encoding="utf-8"))
    assert merged["speed_profile"] == {"tool_calls": 309}
    assert len(merged) > 1  # fresh extraction keys were written too


def test_no_session_id_still_creates_run_with_a_warning(ws, capsys):
    args = SimpleNamespace(project="Proj", requirement="REQ text", profile=None, intent=None)
    cli.cmd_run_create(args, ArtifactRegistry(workspace_root=str(ws)))
    cap = capsys.readouterr()
    out = json.loads(cap.out.strip().splitlines()[-1])
    assert json.loads(Path(out["file"]).read_text(encoding="utf-8"))["session_id"] is None
    warn = [json.loads(ln) for ln in cap.err.splitlines() if ln.strip().startswith("{")]
    assert any("no session_id" in w.get("warning", "") for w in warn), cap.err


@pytest.mark.parametrize("project,expect_root", [("Proj", None), ("SwarmAI", cli._BACKEND_DIR.parent)])
def test_summary_repo_root_only_for_swarmai(ws, capsys, monkeypatch, project, expect_root):
    args = SimpleNamespace(project=project, requirement="REQ", profile=None, intent=None)
    cli.cmd_run_create(args, ArtifactRegistry(workspace_root=str(ws)))
    rid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["pipeline_id"]
    seen = {}

    def fake(run_state, arts, run_dir, **kw):
        seen.update(kw)
        return "S"
    monkeypatch.setattr(rds, "build_delivery_summary", fake)
    assert cli._delivery_summary_for(project, rid) == "S"
    assert seen["repo_root"] == expect_root


# ── E4: REPORT header Tokens use run_token_usage with its coverage caveats ──

def _report_header(ws, capsys, monkeypatch, usage):
    st = _create(capsys, ws)
    run_file = ws / "Projects" / "Proj" / ".artifacts" / "runs" / st["id"] / "run.json"
    state = json.loads(run_file.read_text(encoding="utf-8"))
    state["session_id"] = "sess-r"
    run_file.write_text(json.dumps(state), encoding="utf-8")
    seen = {}

    def fake_run_usage(sid, start, end, db_path=None, turn_end_iso=None):
        seen.update(sid=sid, start=start)
        return usage

    def strict_window(*a, **k):
        raise AssertionError("REPORT must not use the strict token_usage_for_window")

    monkeypatch.setattr(rds, "run_token_usage", fake_run_usage)
    monkeypatch.setattr(rds, "token_usage_for_window", strict_window)
    cli.cmd_run_report(SimpleNamespace(project="Proj", run_id=st["id"]), None)
    capsys.readouterr()
    text = (run_file.parent / "REPORT.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "**Tokens:**" in ln)
    return line, seen


def test_report_tokens_use_run_token_usage_with_caveats(ws, capsys, monkeypatch):
    usage = {"cost_usd": 0.5, "input_tokens": 1000, "output_tokens": 200,
             "cache_read_tokens": 9999, "cache_creation_tokens": 34, "rows": 2,
             "tokens": 1234, "turns": 2, "complete": False,
             "excludes_current_turn": True, "includes_prerun": True}
    line, seen = _report_header(ws, capsys, monkeypatch, usage)
    assert seen["sid"] == "sess-r"
    assert "1,234 (measured, $0.50, cache read 9,999" in line
    assert "不含当前一轮" in line and "含建 run 前的同一轮" in line
    assert "估算" not in line


def test_report_tokens_complete_run_has_no_current_turn_caveat(ws, capsys, monkeypatch):
    usage = {"cost_usd": 1.0, "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0,
             "cache_creation_tokens": 0, "rows": 1, "tokens": 15, "turns": 1,
             "complete": True, "excludes_current_turn": False, "includes_prerun": True}
    line, _ = _report_header(ws, capsys, monkeypatch, usage)
    assert "15 (measured, $1.00" in line and "不含当前一轮" not in line
    assert "含建 run 前的同一轮" in line


def test_report_tokens_fall_back_to_estimate(ws, capsys, monkeypatch):
    line, _ = _report_header(ws, capsys, monkeypatch, None)
    assert "(估算 / estimate)" in line and "measured" not in line
