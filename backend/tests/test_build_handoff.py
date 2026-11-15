"""The clean-context BUILD switch (core/run_protocol/build_handoff.py).

What is tested, on a real direct run (temporary repo, workspace, data dir, worktree):
- the switch is off unless its app setting is exactly true;
- at BUILD, ``next`` names ``build-handoff`` only with the setting on, and is unchanged
  with it off;
- the prompt it writes (an audit blob) holds the requirement, the DESIGN_SPEC tasks and
  exit criteria, the worktree path, the BUILD card and the report shape.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.run_protocol import build_handoff
from core.run_protocol.engine import RunEngine
from core.run_protocol.next_step import next_step


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


@pytest.fixture
def build_run(tmp_path, monkeypatch):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "T")
    (repo / "a.js").write_text("module.exports = 1\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", requirement="make a.js export 2",
                              session_id="s", repos={"repo": str(repo)}, run_id="run_bh")
    wt = engine.ensure_stage_worktrees()["repo"]
    return engine, wt


def test_both_switches_default_off(monkeypatch):
    """The clean-BUILD switch is off unless the setting is the value true (the Bash
    observer measured with it was removed: see the module docstring)."""
    from core.app_config_manager import AppConfigManager

    class Config:
        def __init__(self, values):
            self.values = values

        def get(self, key, default=None):
            return self.values.get(key, default)

    for values, on in (({}, False), ({build_handoff.SETTING: "true"}, False),
                       ({build_handoff.SETTING: 1}, False), ({build_handoff.SETTING: True}, True)):
        monkeypatch.setattr(AppConfigManager, "instance", classmethod(lambda cls, v=values: Config(v)))
        assert build_handoff.enabled() is on


def test_next_names_build_handoff_only_when_on(build_run, monkeypatch):
    engine, _wt = build_run
    monkeypatch.setattr(build_handoff, "enabled", lambda value=...: False)
    off = next_step(engine)
    assert off["stage"] == "build" and "build_handoff" not in off
    monkeypatch.setattr(build_handoff, "enabled", lambda value=...: True)
    on = next_step(engine)
    assert "build-handoff --project P --run-id run_bh" in on["build_handoff"]
    assert on["why"].endswith(off["why"]) and "spawn_prompt" in on["why"]
    assert {k: v for k, v in on.items() if k not in ("build_handoff", "why")} == \
        {k: v for k, v in off.items() if k != "why"}


def test_handoff_prompt_holds_what_the_builder_needs(build_run):
    engine, wt = build_run
    design = {"tasks": [{"id": "T1", "file": "a.js", "change": "export 2"}],
              "exit_criteria": [{"id": "X1", "kind": "test", "ref": "t.test.js::two"}]}
    (engine.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (engine.run_dir / "artifacts" / "design_spec.json").write_text(json.dumps(design))
    out = build_handoff.write(engine)
    text = Path(out["prompt_file"]).read_text(encoding="utf-8")
    assert Path(out["prompt_file"]).parent == engine.store.paths.blobs
    for needle in ("make a.js export 2", str(wt), '"T1"', '"X1"', "# BUILD",
                   '"reports"', "no commit", "base-tree --project P --run-id run_bh"):
        assert needle in text, needle
    assert out["prompt_file"] in out["spawn_prompt"] and "run_bh" in out["spawn_prompt"]
    # each section carries its own part, not only the whole DESIGN_SPEC further down
    tasks = text.split("## Tasks (DESIGN_SPEC)")[1].split("## Exit criteria")[0]
    exits = text.split("## Exit criteria (DESIGN_SPEC)")[1].split("## The whole DESIGN_SPEC")[0]
    assert '"T1"' in tasks and '"X1"' in exits
    # the orchestrator's own steps are not in the builder's prompt
    assert "stage-complete" not in text and "child dispatch" not in text


def _transcript(tmp_path, agent_id, first_prompt):
    main = tmp_path / "sess.jsonl"; main.write_text("")
    sub = tmp_path / "sess" / "subagents"; sub.mkdir(parents=True, exist_ok=True)
    (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": first_prompt}}) + "\n")
    return str(main)


def _bash(command, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}


PV1 = '"$SWARM_BACKEND_BIN" pipeline-v1 '


def test_builder_may_not_change_the_run(tmp_path):
    """A sub-agent spawned with the BUILD-HANDOFF line is refused every pipeline-v1
    command outside the read-only ones; the orchestrator, another sub-agent and a
    read-only command are untouched."""
    main = _transcript(tmp_path, "b1", "BUILD-HANDOFF: run_bh\nRead /x whole.")
    _transcript(tmp_path, "e1", "EXPERT-ID: correctness\nPIPELINE-HANDOUT: run_bh x")
    builder = {"agent_id": "b1", "transcript_path": main}
    for cmd in ("stage-complete --artifact - --project P --run-id run_bh",
                "child dispatch --project P --run-id run_bh W1",
                "land --project P --run-id run_bh --message m"):
        assert build_handoff.builder_refusal(PV1 + cmd, builder), cmd
    for cmd in ("status --project P --run-id run_bh",
                "base-tree --project P --run-id run_bh --repo repo"):
        assert build_handoff.builder_refusal(PV1 + cmd, builder) is None, cmd
    assert build_handoff.builder_refusal("pytest tests -q", builder) is None
    other = {"agent_id": "e1", "transcript_path": main}
    assert build_handoff.builder_refusal(PV1 + "stage-complete --project P --run-id run_bh",
                                         other) is None
    assert build_handoff.builder_refusal(PV1 + "stage-complete --project P --run-id run_bh",
                                         {"transcript_path": main}) is None


def test_run_owner_gate_denies_the_builder(tmp_path):
    import asyncio
    from core.run_owner_gate import create_run_owner_gate
    main = _transcript(tmp_path, "b1", "BUILD-HANDOFF: run_bh\nRead /x whole.")
    gate = create_run_owner_gate({"sdk_session_id": "s"}, phase="check")
    out = asyncio.run(gate(_bash(PV1 + "stage-complete --project P --run-id run_bh",
                                 agent_id="b1", transcript_path=main), "t1", None))
    hso = out.get("hookSpecificOutput") or {}
    assert hso.get("permissionDecision") == "deny" and "BUILD-HANDOFF" in str(out)


def test_handoff_command_through_the_cli(build_run, monkeypatch, tmp_path):
    """`build-handoff` answers the run's owner at BUILD with the prompt and spawn prompt,
    and refuses another session and a run that is not at BUILD."""
    from core.run_protocol import cli_commands
    from core.run_protocol.request_ctx import Request, active
    engine, _wt = build_run
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "workspace"))

    def run(session):
        args = cli_commands.parser().parse_args(
            ["build-handoff", "--project", "P", "--run-id", "run_bh"])
        with active(Request(session_id=session)):
            return cli_commands.execute(args)

    ok = run("s")
    assert ok["ok"] is True and ok["spawn_prompt"].startswith("BUILD-HANDOFF: run_bh")
    assert Path(ok["prompt_file"]).is_file()
    assert run("someone-else")["code"] == "not_owner"
    (Path(_wt) / "a.js").write_text("module.exports = 2\n")
    engine.stage_complete({"reports": [{"repo": "repo", "commands": [
        {"command": "true", "cwd": ".", "result": "pass"}], "tests_changed": [],
        "rationale": "r"}]})
    assert engine.projection()["stage"] != "build"
    refused = run("s")
    assert refused["ok"] is False and "at BUILD" in refused["error"]


def test_prompt_names_what_the_gate_allows_and_keeps_a_long_requirement():
    """The builder is told exactly the commands builder_refusal lets through, and a
    requirement longer than any earlier cut reaches the prompt whole."""
    long_req = "start " + "x" * 30_000 + " THE-END"
    text = build_handoff.prompt_text(run_id="run_bh", project="P", requirement=long_req,
                                     design={}, worktrees={"repo": "/w"}, report_template={})
    assert long_req in text
    rules = text.split("## How to build")[1].split("## What you return")[0]
    for command in build_handoff.BUILDER_COMMANDS:
        assert f"`{command}`" in rules, command
