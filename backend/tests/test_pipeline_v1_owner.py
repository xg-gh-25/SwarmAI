from __future__ import annotations

import json
from pathlib import Path

from core import run_owner_gate as gate
from core import security_hooks


def _run(tmp_path: Path, *, owner: str = "session-a", project: str = "proj") -> Path:
    run_file = tmp_path / "Projects" / project / ".artifacts" / "runs" / "run_one" / "run.json"
    run_file.parent.mkdir(parents=True)
    run_file.write_text(json.dumps({
        "id": "run_one", "project": project, "session_id": owner,
        "status": "active", "worktree_mode": "isolated",
    }))
    return run_file


def _cmd(command: str, project: str = "proj") -> str:
    return f"python backend/scripts/pipeline_v1.py {command} --project {project} --run-id run_one"


def _denied(value) -> bool:
    return (value or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_parser_recognizes_pipeline_v1_mutation():
    calls, single = gate.parse_cli_calls(_cmd("stage-start"))
    assert single
    assert calls == [{
        "tool": "pipeline_v1", "sub": "stage-start", "run_id": "run_one",
        "project": "proj", "stage": None, "status": None, "session_id": None,
        "ambiguous": False, "assigns": [],
        "argv": ["stage-start", "--project", "proj", "--run-id", "run_one"],
        "run_ids": ["run_one"], "projects": ["proj"], "session_ids": [],
    }]


def test_mutation_checks_canonical_project(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    result = gate._decide("session-a", _cmd("stage-start", "other"), {})
    assert _denied(result)
    assert "project mismatch" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_live_other_owner_denies_mutation(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: True)
    result = gate._decide("session-b", _cmd("stage-start"), {})
    assert _denied(result)
    assert "being worked on" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_dead_owner_is_taken_over_before_mutation(tmp_path, monkeypatch):
    run_file = _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *_a, **_k: None)
    assert gate._decide("session-b", _cmd("stage-start"), {}) is None
    assert json.loads(run_file.read_text())["session_id"] == "session-b"


def test_unknown_takeover_liveness_fails_closed(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: None)
    assert _denied(gate._decide("session-b", _cmd("stage-start"), {}))


def test_other_live_owner_blocks_reflect_mutation(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: True)
    result = gate._decide("session-b", _cmd("reflect-prepare") + " --artifact {}", {})
    assert _denied(result)
    assert "being worked on" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_read_only_status_does_not_take_over(tmp_path, monkeypatch):
    run_file = _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    assert gate._decide("session-b", _cmd("status"), {}) is None
    assert json.loads(run_file.read_text())["session_id"] == "session-a"


def test_create_rejects_foreign_explicit_session():
    command = ("python backend/scripts/pipeline_v1.py create --project proj "
               "--session-id session-b")
    result = gate._decide("session-a", command, {})
    assert _denied(result)
    assert "must equal this app session" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_unresolvable_mutation_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    assert _denied(gate._decide("session-a", _cmd("stage-complete"), {}))


def test_run_land_request_is_human_gated_and_token_bound(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    command = _cmd("run-land") + " --request land_abc --message land"
    assert security_hooks._is_v1_land_request(command)
    assert gate._decide("session-a", command, {}, phase="check") is None

    issued = {}
    def issue(run_dir, session_id, run_id, subcommand):
        issued.update(run_dir=run_dir, session_id=session_id,
                      run_id=run_id, subcommand=subcommand)
        return "token-1"
    monkeypatch.setattr("core.hook_token.issue", issue)
    result = gate._decide("session-a", command, {"command": command}, phase="approved")
    rewritten = result["hookSpecificOutput"]["updatedInput"]["command"]
    assert rewritten.startswith("SWARM_HOOK_TOKEN=token-1 ")
    assert issued["subcommand"] == "run-land:land_abc"


def test_every_v1_mutation_is_owner_checked(tmp_path, monkeypatch):
    _run(tmp_path, owner="session-a")
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: True)
    commands = {
        "gate0-start": "",
        "review-address": " --artifact {}",
        "review-proof": " --artifact {}",
        "resume": "",
        "restore": "",
        "abandon": " --reason stop",
        "decision-request": " --artifact {} --document REQUIREMENTS",
        "run-land": " --prepare",
        "accept-finding": " --prepare --canonical-id CF-1234abcd",
        "discard-snapshot": " --prepare --repo repo",
    }
    for subcommand, suffix in commands.items():
        result = gate._decide("session-b", _cmd(subcommand) + suffix, {})
        assert _denied(result), subcommand
