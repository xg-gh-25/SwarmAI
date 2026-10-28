"""Offline product cases run in the shipped fixture workspace (design
2026-10-26-eval-as-product §3.8): never in the user's own projects or memory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.golden_set_store import (  # noqa: E402
    PRODUCT_ORIGIN, fixture_workspace, load_merged, product_golden_set_path,
)
from scripts import eval_runner as er  # noqa: E402
from scripts import scenario_runner as sr  # noqa: E402


def _case(origin=None, expected=("Read x.md",)):
    c = {"id": "OFF_X", "evaluators": ["trajectory_capture"], "tier": "behavior",
         "scenario": {"prompt": "read x.md", "system": "swarmai"},
         "expected_trajectory": list(expected), "trajectory_match": "any_order"}
    if origin:
        c["_origin"] = origin
    return c


def test_the_fixture_is_a_fresh_workspace_with_a_dot_context():
    with fixture_workspace() as ws:
        assert (ws / ".context" / "MEMORY.md").is_file() and not (ws / "context").exists()
        assert (ws / "Projects" / "Demo" / "2-understanding" / "PRODUCT.md").is_file()
        (ws / "scratch").write_text("x")             # a run may write in its copy
    assert not ws.exists()
    with fixture_workspace() as again:
        assert not (again / "scratch").exists()      # every run starts from the shipped copy


def _capture(case, monkeypatch):
    seen = {}

    def _raw(prompt, allowed_tools=None, timeout=240, system="bare", workspace=None):
        seen["ws"] = workspace
        seen["memory"] = (Path(workspace) / ".context" / "MEMORY.md").read_text() if workspace else None
        return ['Read {"file_path": "x.md"}'], "ok", ""
    monkeypatch.setattr("scripts.scenario_runner.run_scenario_raw", _raw)
    return er.eval_trajectory_capture(case), seen


def test_a_product_case_runs_in_the_fixture_and_a_private_case_in_the_users(monkeypatch):
    got, seen = _capture(_case(PRODUCT_ORIGIN), monkeypatch)
    assert got["status"] == "passed" and seen["ws"] is not None and "pnpm" in seen["memory"]
    got, seen = _capture(_case(), monkeypatch)
    assert got["status"] == "passed" and seen["ws"] is None      # the user's workspace


def test_the_swarmai_prompt_is_built_from_the_given_workspace(monkeypatch, tmp_path):
    built = []

    class _PB:
        def __init__(self, config):
            pass

        async def build_default_system_prompt(self, agent_config, working_directory):
            built.append(working_directory)
            return f"PROMPT FOR {working_directory}"
    monkeypatch.setattr("core.prompt_builder.PromptBuilder", _PB)
    monkeypatch.setattr("core.app_config_manager.AppConfigManager.instance",
                        classmethod(lambda cls: type("C", (), {"load": lambda self: None})()))
    monkeypatch.setattr(sr, "_SWARMAI_PROMPT_CACHE", {})
    a, b = tmp_path / "a", tmp_path / "b"
    assert sr._swarmai_system_prompt(a) == f"PROMPT FOR {a}"
    assert sr._swarmai_system_prompt(b) == f"PROMPT FOR {b}"     # not the first one's cache
    assert built == [str(a), str(b)]


def test_the_spawn_runs_in_and_may_read_the_given_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "_resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr(sr, "_cli_supports_bare", lambda p: True)
    monkeypatch.setattr(sr, "_build_cli_env", lambda x: {})
    seen = {}

    class _P:
        returncode = 0
        stdout = json.dumps({"type": "result", "result": "done"})
        stderr = ""

    def _run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"))
        return _P()
    monkeypatch.setattr(sr.subprocess, "run", _run)
    sr.run_scenario_raw("hi", ["Read"], 10, workspace=tmp_path)
    assert seen["cwd"] == str(tmp_path)
    assert seen["cmd"][seen["cmd"].index("--add-dir") + 1] == str(tmp_path)


def test_a_step_may_name_alternative_tools():
    case = _case(expected=("Grep|Read|Glob",))
    for actual in (['Grep {"pattern": "Postgres"}'], ['Read {"file_path": "a.md"}']):
        assert er._eval_trajectory_tool_strict(case, actual)["status"] == "passed", actual
    assert er._eval_trajectory_tool_strict(case, ['Bash {"command": "ls"}'])["status"] == "failed"
    named = _case(expected=("Grep|Read archive",))
    assert er._eval_trajectory_tool_strict(named, ['Read {"file_path": "Knowledge/Archives/x.md"}'])[
        "status"] == "passed"
    assert er._eval_trajectory_tool_strict(named, ['Read {"file_path": "TECH.md"}'])["status"] == "failed"


def test_every_offline_product_case_runs_in_the_product_agent_and_is_judged():
    cases = yaml.safe_load(product_golden_set_path().read_text(encoding="utf-8"))["cases"]
    offline = [c for c in cases if c.get("eval_method") == "behavior"]
    assert offline
    for c in offline:
        assert c["scenario"]["system"] == "swarmai", c["id"]
        assert c.get("decision_rubric") and c.get("expected_trajectory"), c["id"]
        assert set(c.get("allowed_tools", [])) <= {"Read", "Grep", "Glob"}, c["id"]
