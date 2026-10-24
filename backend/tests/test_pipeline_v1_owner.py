from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import run_owner_gate as gate
from core import security_hooks


@pytest.fixture(autouse=True)
def _signed_owner_from_run_json(monkeypatch):
    """Unit stand-in for the signed owner (these runs have no audit area): run.json's
    session_id; a takeover's signed owner_transfer is a no-op here. The real path is
    covered end to end by test_pipeline_v1_human_loop."""
    def owner(project, run_id):
        root = Path(security_hooks._pipeline_runs_root())
        for f in root.glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
            return json.loads(f.read_text()).get("session_id")
        return None
    monkeypatch.setattr(gate, "_signed_owner", owner)

    def record(run_id, sid):
        root = Path(security_hooks._pipeline_runs_root())
        for f in root.glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
            f.write_text(json.dumps({**json.loads(f.read_text()), "session_id": sid}))
        return True
    from core import pipeline_human
    monkeypatch.setattr(pipeline_human, "record_owner", record)


def _run(tmp_path: Path, *, owner: str = "session-a", project: str = "proj") -> Path:
    run_file = tmp_path / "Projects" / project / ".artifacts" / "runs" / "run_one" / "run.json"
    run_file.parent.mkdir(parents=True)
    run_file.write_text(json.dumps({
        "id": "run_one", "project": project, "session_id": owner,
        "status": "active", "worktree_mode": "isolated",
    }))
    return run_file


_FORM = {"cli": "script"}


@pytest.fixture(autouse=True, params=["script", "program"])
def _cli_form(request):
    """Every test runs with both command forms the gate recognises until S6: the legacy
    script and the program form the agent is told to run (core.pipeline_command)."""
    _FORM["cli"] = request.param
    yield request.param
    _FORM["cli"] = "script"


def _cmd(command: str, project: str = "proj") -> str:
    head = ('"$SWARM_BACKEND_BIN" pipeline-v1' if _FORM["cli"] == "program"
            else "python backend/scripts/pipeline_v1.py")
    return f"{head} {command} --project {project} --run-id run_one"


def _passes(value) -> bool:
    """Allowed, with this session's id handed to the call."""
    if value is None:
        return True
    out = value.get("hookSpecificOutput", {})
    cmd = out.get("updatedInput", {}).get("command", "")
    return out.get("permissionDecision") == "allow" and "SWARM_APP_SESSION_ID=" in cmd


def _denied(value) -> bool:
    return (value or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_parser_recognizes_pipeline_v1_mutation(_cli_form):
    calls, single = gate.parse_cli_calls(_cmd("stage-complete"))
    assert single
    where = ({"script": "backend/scripts/pipeline_v1.py", "form": "script", "program": None}
             if _cli_form == "script" else
             {"script": None, "form": "program", "program": "$SWARM_BACKEND_BIN"})
    assert calls == [{
        "tool": "pipeline_v1", "sub": "stage-complete", "run_id": "run_one", **where,
        "project": "proj", "stage": None, "status": None, "session_id": None,
        "ambiguous": False, "assigns": [],
        "argv": ["stage-complete", "--project", "proj", "--run-id", "run_one"],
        "run_ids": ["run_one"], "projects": ["proj"], "session_ids": [],
    }]


def test_mutation_checks_canonical_project(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    result = gate._decide("session-a", _cmd("stage-complete", "other"), {})
    assert _denied(result)
    assert "project mismatch" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_live_other_owner_denies_mutation(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: True)
    result = gate._decide("session-b", _cmd("stage-complete"), {})
    assert _denied(result)
    assert "being worked on" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_dead_owner_is_taken_over_before_mutation(tmp_path, monkeypatch):
    run_file = _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *_a, **_k: None)
    assert _passes(gate._decide("session-b", _cmd("stage-complete"), {}))
    assert json.loads(run_file.read_text())["session_id"] == "session-b"


def test_unknown_takeover_liveness_fails_closed(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: None)
    assert _denied(gate._decide("session-b", _cmd("stage-complete"), {}))


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
    assert _passes(gate._decide("session-b", _cmd("status"), {}))
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


def test_run_land_request_is_recorded_only_once_approved(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    command = _cmd("run-land") + " --request land_abc --message land"
    assert security_hooks._is_v1_land_request(command)
    assert _passes(gate._decide("session-a", command, {}, phase="check"))

    approved = []   # the daemon records the person's approval (C1/C3)
    monkeypatch.setattr(gate, "_record_approval", lambda rid, req: approved.append((rid, req)))
    assert not approved, "the check phase records nothing"
    result = gate._decide("session-a", command, {"command": command}, phase="approved")
    assert _passes(result)
    assert approved == [("run_one", "land_abc")]


def test_an_unrecorded_approval_denies_the_command(tmp_path, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    command = _cmd("run-land") + " --request land_abc --message land"
    monkeypatch.setattr(gate, "_record_approval", lambda rid, req: "audit area not writable")
    result = gate._decide("session-a", command, {"command": command}, phase="approved")
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


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


def test_the_hook_hands_the_session_id_to_the_first_pipeline_call(tmp_path, monkeypatch):
    """A prewarmed subprocess adopted by a session has no SWARM_APP_SESSION_ID (R1
    review r2): the hook assigns this session's id to the one call it binds to."""
    _run(tmp_path, owner="session-a")
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    for command in (_cmd("status"), _cmd("status") + " | tail -3"):
        out = gate._decide("session-a", command, {"command": command})
        new = out["hookSpecificOutput"]["updatedInput"]["command"]
        assert new == "SWARM_APP_SESSION_ID=session-a " + command
    # not the first command: nothing to bind to; the CLI uses its own environment
    compound = "cd /x && " + _cmd("status")
    assert gate._decide("session-a", compound, {"command": compound}) is None
    assert gate._with_session(_cmd("status"), "prewarm-123") is None


def test_handing_the_session_id_over_is_idempotent():
    once = gate._with_session(_cmd("status"), "session-a")
    assert once and gate._with_session(once, "session-a") is None


def test_no_gate_module_defines_a_function_twice():
    """Re-audit 536bb62b LOW: a second _v1_mutation_check silently replaced the first
    (and dropped its owner check). A gate module defines each top-level name once."""
    import ast
    from pathlib import Path
    core = Path(__file__).resolve().parent.parent / "core"
    for name in ("run_owner_gate.py", "worktree_write_gate.py", "security_hooks.py"):
        tree = ast.parse((core / name).read_text(encoding="utf-8"))
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        dup = sorted({n for n in names if names.count(n) > 1})
        assert not dup, (name, dup)


def test_a_denied_takeover_command_transfers_nothing(tmp_path, monkeypatch):
    """B round 1 HIGH: a takeover command the gate then denies (it sets a variable)
    must not have written the owner transfer already."""
    run_file = _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *_a, **_k: None)
    command = "SWARM_WORKTREE_MODE=off " + _cmd("stage-complete")
    assert _denied(gate._decide("session-b", command, {"command": command}))
    assert json.loads(run_file.read_text())["session_id"] == "session-a"


def test_a_late_action_takes_over_only_once_approved(tmp_path, monkeypatch):
    """B round 2 HIGH: run-land --request on an idle foreign run took it over in the
    CHECK phase, before the person approved anything."""
    run_file = _run(tmp_path)
    monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_owner_live", lambda _owner: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *_a, **_k: None)
    command = _cmd("run-land") + " --request land_abc --message land"
    gate._decide("session-b", command, {"command": command}, phase="check")
    assert json.loads(run_file.read_text())["session_id"] == "session-a"


