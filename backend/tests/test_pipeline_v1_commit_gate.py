"""R1: active_run_commit_gate for v1 runs (r6 §5: the fail-open fix).

The gate used to read ``files_touched`` from run.json; a v1 projection never writes
it, so a hand ``git commit`` of a v1 run's code was always approved. Ownership now
comes from the run itself: its protected contract (repo roots, mode), its worktrees
and the paths it changed against its fork base, and its reserved delivery tree.

Methodology: a real v1 run on the rig (real contract, worktree, git) and the real
gate. Invariants:
- a hand commit, in the main checkout, of a path the active run changed is denied;
- a hand commit of the run's own REPORT under .aidlc/deliverables/<run_id>/ is denied;
- a commit of a path the run did not change is approved;
- once the run is terminal, nothing is owned;
- a run.json without a protected contract owns nothing (old runs are not supported).
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from core import security_hooks
from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct") -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile)
        monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(r.ws))
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _commit(path, cwd):
    gate = security_hooks.create_active_run_commit_gate({})
    out = asyncio.run(gate({"tool_name": "Bash", "cwd": str(cwd),
                            "tool_input": {"command": f"git commit -m x -- {path}"}},
                           None, None))
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_a_hand_commit_of_a_file_the_v1_run_changed_is_denied(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)                                   # in the run's worktree
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    assert _commit("backend/core/mod.py", r.repo)
    (r.repo / "backend" / "core" / "other.py").write_text("def other():\n    return 3\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/other.py"], check=True)
    assert not _commit("backend/core/other.py", r.repo)      # not the run's change


def test_a_hand_commit_of_the_runs_report_is_denied(rig):
    r = rig()
    r.create()
    report = r.repo / ".aidlc" / "deliverables" / str(r.run_id) / "REPORT.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "-f", str(report)], check=True)
    assert _commit(str(report.relative_to(r.repo)), r.repo)


def test_a_terminal_run_owns_nothing(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "done here").get("ok") is True
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    assert not _commit("backend/core/mod.py", r.repo)


def test_a_run_without_a_protected_contract_owns_nothing(rig, tmp_path):
    r = rig()
    run = r.ws / "Projects" / "P" / ".artifacts" / "runs" / "run_old0001"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({
        "id": "run_old0001", "status": "running",
        "files_touched": [str(r.repo / "backend" / "core" / "mod.py")]}))
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    assert not _commit("backend/core/mod.py", r.repo)


def _gate_result(command, tool="Bash", cwd="/repo"):
    gate = security_hooks.create_active_run_commit_gate({})
    return asyncio.run(gate({"tool_name": tool, "tool_input": {"command": command},
                             "cwd": cwd}, None, None))


def test_not_a_commit_or_not_bash_is_approved():
    assert _gate_result("git status").get("decision") == "approve"
    assert _gate_result("git commit -m x", tool="Write").get("decision") == "approve"


def test_an_uncomputable_pending_set_fails_open(monkeypatch):
    monkeypatch.setattr(security_hooks, "_pending_commit_paths", lambda *a: None)
    assert _gate_result("git commit -m x").get("decision") == "approve"

    def boom(*a):
        raise RuntimeError("git gone")
    monkeypatch.setattr(security_hooks, "_pending_commit_paths", boom)
    assert _gate_result("git commit -m x").get("decision") == "approve"


def test_the_deliverable_matcher():
    ids = {"run_ab12"}
    pending = {"/r/.aidlc/deliverables/run_ab12/REPORT.md", "/r/.aidlc/deliverables/run_zz/x"}
    assert security_hooks._active_run_deliverables(pending, ids) == \
        ["/r/.aidlc/deliverables/run_ab12/REPORT.md"]


def test_a_git_C_commit_from_outside_the_repo_is_judged(rig, tmp_path):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    gate = security_hooks.create_active_run_commit_gate({})
    for cmd in (f"git -C {r.repo} commit -m x", f"git -C {r.repo.name} commit -m x"):
        cwd = elsewhere if r.repo.name not in cmd.split()[2] or "/" in cmd.split()[2] \
            else r.repo.parent
        out = asyncio.run(gate({"tool_name": "Bash", "cwd": str(cwd),
                                "tool_input": {"command": cmd}}, None, None))
        assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", cmd


def test_editing_run_json_does_not_unguard_the_run(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    rj = r.engine().run_dir / "run.json"
    data = json.loads(rj.read_text())
    rj.write_text(json.dumps({**data, "status": "completed", "project": "nope"}))
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    assert _commit("backend/core/mod.py", r.repo)


def test_an_unreadable_run_refuses_rather_than_approves(rig, monkeypatch):
    from core.run_protocol import engine as eng
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    monkeypatch.setattr(eng.RunEngine, "owned_paths",
                        lambda self: (_ for _ in ()).throw(RuntimeError("broken")))
    assert _commit("backend/core/mod.py", r.repo)


def test_a_retarget_to_another_repo_is_refused(rig, tmp_path):
    r = rig()
    r.create()
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    out = _gate_result(f"git --git-dir={other}/.git commit -m x", cwd=str(r.repo))
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_cd_then_commit_is_judged_in_the_repo_cd_names(rig, tmp_path):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    (r.repo / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 0\n")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/mod.py"], check=True)
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    out = _gate_result(f"cd {r.repo} && git commit -m x", cwd=str(elsewhere))
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_the_repos_own_git_dir_is_not_a_retarget(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert security_hooks._same_repo("--git-dir", str(repo / ".git"), str(repo.resolve()))
    other = tmp_path / "o"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    assert not security_hooks._same_repo("--git-dir", str(other / ".git"), str(repo.resolve()))


def test_a_committed_symlink_in_an_active_run_is_guarded(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    # a tracked symlink the run changed, pointing outside the repo
    link = r.repo / "backend" / "core" / "link.py"
    link.symlink_to("/etc/hostname")
    subprocess.run(["git", "-C", str(r.repo), "add", "backend/core/link.py"], check=True)
    # the run changes the same link in its worktree, so it owns it
    (r.worktree / "backend" / "core" / "link.py").unlink(missing_ok=True)
    (r.worktree / "backend" / "core" / "link.py").symlink_to("/etc/hostname")
    subprocess.run(["git", "-C", str(r.worktree), "add", "backend/core/link.py"], check=True)
    assert _commit("backend/core/link.py", r.repo)
