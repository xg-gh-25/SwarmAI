"""End-to-end tests of per-repo run worktrees without a bindings prerequisite
(docs/design/pipeline-run-worktree.md §16.9).

Methodology: the same harness as test_pipeline_worktree.py (temp SwarmWS, the real
artifact_cli run as a subprocess, the hook token issued in-process and burned by the
CLI through the token service). Invariants: with the switch on a run is isolated at
its first plan start even with no bindings; ``run-repo-add`` (owner token, run lock)
gives any isolatable repo a worktree pinned to its main and is idempotent; repos that
cannot be isolated are refused; same-named repos get distinct keys; once created, the
main checkout comes from git, never bindings, and a forged pinned main is refused;
the CLI prints follow-up commands as one absolute simple command.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import run_worktree as rw
from tests.test_pipeline_worktree import (  # noqa: F401 — flow / _hook_token_oracle are fixtures
    _create, _g, _hook_token_oracle, _land, _start, _state, cli, flow)


def _git_repo(path, hooks):
    path.mkdir(parents=True)
    _g(path, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("core.hooksPath", str(hooks))):
        _g(path, "config", k, v)
    (path / "o.py").write_text("o = 1\n")
    _g(path, "add", "-A")
    _g(path, "commit", "-q", "-m", "init")
    return path


def _unbind(flow):
    (flow["ws"] / "Projects" / "P" / "bindings.yaml").unlink()


def _repo_add(flow, rid, path, token=True):
    return cli("run-repo-add", "--project", "P", "--run-id", rid, "--path", str(path),
               token_for="run-repo-add" if token else None, run=rid, flow=flow)


def test_switch_on_without_bindings_isolates_with_no_worktree_yet(flow):
    _unbind(flow)
    rid, f = _create(flow)
    rc, out = _start(flow, rid, "plan")
    assert rc == 0, out
    st = _state(f)
    assert st["worktree_mode"] == "isolated" and st.get("worktrees") == []
    assert (rw.wt_root() / rid).is_dir()          # the write gate is active for this run
    assert isinstance(st.get("cli"), dict) and st["cli"]["script"].endswith("artifact_cli.py")


def test_no_hint_repo_and_no_token_falls_back_to_shared(flow, monkeypatch):
    _unbind(flow)
    rid, f = _create(flow)
    monkeypatch.delenv("SWARM_APP_SESSION_ID")          # a truly hookless executor
    rc, out = _start(flow, rid, "plan", token=False)
    assert rc == 0, out
    assert _state(f)["worktree_mode"] == "shared"


def test_hooked_session_without_a_token_is_refused_not_made_shared(flow):
    _unbind(flow)
    rid, f = _create(flow)
    rc, out = _start(flow, rid, "plan", token=False)    # the gate failed open once
    assert rc == 1 and out["code"] == "hookless_session"
    assert _state(f).get("worktree_mode") is None


def test_repo_add_refused_after_landing(flow):
    _unbind(flow)
    other = _git_repo(flow["tmp"] / "elsewhere" / "other", flow["tmp"] / "hooks")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    assert _repo_add(flow, rid, other)[0] == 0
    st = _state(f)
    st["worktrees"][0]["state"] = "integrated"
    f.write_text(json.dumps(st))
    rc, out = _repo_add(flow, rid, other)
    assert rc != 0 and out.get("code") == "run_settled"


def test_terminal_run_without_worktrees_disarms_the_gate(flow):
    _unbind(flow)
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    assert (rw.wt_root() / rid).is_dir()
    rc, _ = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned",
                "--reason", "test teardown of the run", token_for="run-update", run=rid, flow=flow)
    assert rc == 0 and not (rw.wt_root() / rid).exists()


def test_repo_add_creates_pinned_worktree_and_lands_it(flow):
    _unbind(flow)
    other = _git_repo(flow["tmp"] / "elsewhere" / "other", flow["tmp"] / "hooks")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    rc, out = _repo_add(flow, rid, other / "o.py")
    assert rc == 0, out
    wt = Path(out["worktree"])
    assert out["main"] == str(other) and wt.is_dir() and rw.run_for_path(wt)[0] == rid
    e = _state(f)["worktrees"][0]
    assert e["main"] == str(other) and e["state"] == "active"
    rc2, out2 = _repo_add(flow, rid, other)            # idempotent
    assert rc2 == 0 and out2["worktree"] == str(wt) and len(_state(f)["worktrees"]) == 1
    (wt / "o.py").write_text("o = 2\n")
    assert _start(flow, rid, "deliver")[0] == 0
    import pipeline_test  # the landing gate reads runner evidence, sealed by its writer
    tests_dir = f.parent / "evidence" / "tests"
    pipeline_test._write_json(tests_dir / "latest-verify.json", {
        "schema": 1, "mode": "verify", "run_id": rid, "finished_at": "2099-01-01T00:00:00+00:00",
        "counts": {"passed": 1}, "results": [], "failures": [],
        "worktree": {e["repo_name"]: {"tree": rw.change_tree(wt, rid), "untracked": []}}})
    res = _land(flow, rid, f)
    assert list(res["landed"]) == [e["repo_name"]]
    assert (other / "o.py").read_text() == "o = 2\n"
    assert _g(other, "log", "-1", "--format=%s") != "init"


def test_repo_add_needs_token_and_an_isolated_run(flow):
    _unbind(flow)
    other = _git_repo(flow["tmp"] / "elsewhere" / "other", flow["tmp"] / "hooks")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    rc, out = _repo_add(flow, rid, other, token=False)
    assert rc != 0 and out.get("code") == "hookless_session"
    assert _state(f)["worktrees"] == []
    rid2, f2 = _create(flow)
    rc, out = _repo_add(flow, rid2, other)              # not isolated (never started plan)
    assert rc != 0 and "isolated" in json.dumps(out)


def test_repo_add_refuses_a_repo_that_cannot_be_isolated(flow):
    _unbind(flow)
    other = _git_repo(flow["tmp"] / "elsewhere" / "other", flow["tmp"] / "hooks")
    (other / ".gitmodules").write_text("")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    rc, out = _repo_add(flow, rid, other)
    assert rc != 0 and out.get("code") == "not_isolatable"
    plain = flow["tmp"] / "plain"
    plain.mkdir()
    rc, out = _repo_add(flow, rid, plain)
    assert rc != 0 and out.get("code") == "not_isolatable"


def test_same_basename_repos_get_distinct_worktrees(flow):
    _unbind(flow)
    a = _git_repo(flow["tmp"] / "x" / "app", flow["tmp"] / "hooks")
    b = _git_repo(flow["tmp"] / "y" / "app", flow["tmp"] / "hooks")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    rc1, o1 = _repo_add(flow, rid, a)
    rc2, o2 = _repo_add(flow, rid, b)
    assert rc1 == 0 and rc2 == 0 and o1["worktree"] != o2["worktree"]
    keys = [e["repo_name"] for e in _state(f)["worktrees"]]
    assert len(set(keys)) == 2 and all(rw.REPO_NAME_RE.fullmatch(k) for k in keys)


def test_lifecycle_uses_git_not_bindings_once_created(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    _unbind(flow)                                       # bindings gone after creation
    rc, out = _start(flow, rid, "build")
    assert rc == 0 and out["worktrees"] == [str(rw.wt_path(rid, "swarmai"))], out


def test_forged_pinned_main_is_refused(flow):
    other = _git_repo(flow["tmp"] / "elsewhere" / "other", flow["tmp"] / "hooks")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    st = _state(f)
    st["worktrees"][0]["main"] = str(other)
    f.write_text(json.dumps(st))
    rc, out = _start(flow, rid, "build")
    assert rc == 1 and out["code"] == "worktree_invalid", out


def test_follow_up_commands_are_single_absolute_commands(flow):
    from core.run_owner_gate import parse_cli_calls
    rid, f = _create(flow)
    rc, out = _start(flow, rid, "plan")
    assert rc == 0, out
    calls, single = parse_cli_calls(out["complete_with"].replace("@<file", "@/tmp/x").split(" --data")[0])
    assert single and calls and calls[0]["tool"] == "artifact_cli"
    assert out["complete_with"].split()[1].startswith("/")


def test_refusals_and_follow_ups_print_absolute_single_commands(flow):
    from core.run_owner_gate import parse_cli_calls
    rid, f = _create(flow)
    assert isinstance(_state(f).get("cli"), dict)            # recorded at run-create
    rc, out = _start(flow, rid, "plan", token=False)          # bindings repo, no token
    assert rc == 1 and out["code"] == "hookless_session"
    cmd = out["next"].split(": ", 1)[1].split(" — ")[0]
    calls, single = parse_cli_calls(cmd)
    assert single and calls[0]["sub"] == "stage-start" and cmd.split()[0].startswith("/")


def test_stage_refusals_carry_absolute_rerun_commands(flow):
    from core.run_owner_gate import parse_cli_calls
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    (rw.wt_path(rid, "swarmai") / "probe.txt").write_text("x")     # un-added → deliver refuses
    rc, out = _start(flow, rid, "deliver")
    assert rc == 1 and out["code"] == "untracked_new_files"
    for k in ("rerun_stage_start", "rerun_stage_complete"):
        calls, single = parse_cli_calls(out[k].replace("@<file>", "@/tmp/f"))
        assert single and out[k].split()[1].startswith("/"), out[k]


def test_tool_copy_in_a_worktree_runs_against_a_foreign_workspace(flow, tmp_path):
    """The test suites run the worktree's own CLI against a temp workspace."""
    import os
    import subprocess
    import sys
    from tests.test_pipeline_worktree import BACKEND
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    copy = rw.wt_path(rid, "swarmai") / "artifact_cli.py"
    copy.write_text("import sys; sys.path.insert(0, %r)\nimport pipeline_worktree as p\n"
                    "p.refuse_if_worktree_copy(__file__)\nprint('ran')\n" % str(BACKEND / "scripts"))
    other_ws = tmp_path / "other-ws"
    other_ws.mkdir()
    p = subprocess.run([sys.executable, str(copy)], capture_output=True, text=True,
                       cwd=str(BACKEND), env=dict(os.environ, PYTHONPATH=str(BACKEND),
                                                  SWARM_WORKSPACE=str(other_ws)))
    assert p.returncode == 0 and "ran" in p.stdout
