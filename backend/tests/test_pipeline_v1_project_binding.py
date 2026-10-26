"""A run's project comes from the repo's binding (design
docs/design/2026-10-26-run-knowledge.md §3.2, step 2).

What is tested, through the real ``pipeline-v1 create`` CLI and a real bindings.yaml
in the rig's workspace:
- a bound repo puts the run in its project; the contract records ``has_ddd`` and the
  bound repos;
- ``--project`` naming another project than the binding is refused, naming the bound one
  (red before step 2: create took any name, so the run lost its DDD silently);
- repos bound to two different projects are refused;
- an unbound repo keeps working with ``--project`` (has_ddd from that project's DDD docs)
  and create says the repo is not bound;
- a linked worktree of a bound repo binds like its main checkout;
- research without repos: has_ddd from the project's DDD docs.
"""
import json
import subprocess

from tests.pipeline_v1_rig import PROJECT, Rig, _git


def _bind(rig: Rig, project: str, repo) -> None:
    pdir = rig.ws / "Projects" / project
    (pdir / "2-understanding").mkdir(parents=True, exist_ok=True)
    (pdir / "2-understanding" / "TECH.md").write_text("# TECH\n\n## Traps\n\n- **x**: y.\n",
                                                      encoding="utf-8")
    (pdir / "bindings.yaml").write_text(
        "bindings:\n"
        f"  - repo: repo\n    kind: internal\n    clone: {repo}\n    worktree: {repo}\n"
        "    delivery_contract:\n      remote_kind: self-hosted-main\n      branch: main\n"
        "      review_path: none\n      auto_send: never\n", encoding="utf-8")


def _contract(rig: Rig) -> dict:
    from core.run_protocol.contract import read_contract
    return read_contract(rig.data, rig.run_id)


def _create(rig: Rig, *args: str) -> dict:
    return rig.v1("create", "--profile", "direct", "--requirement", "add a thing", *args)


def test_bound_repo_puts_the_run_in_its_project(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    _bind(r, "Bound", r.repo)
    out = _create(r, "--project", "Bound", "--repo", f"repo={r.repo}")
    assert out.get("ok") is True, out
    c = _contract(r)
    assert c["project"] == "Bound" and c["has_ddd"] is True and c["bound_repos"] == ["repo"]


def test_project_other_than_the_binding_is_refused(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    _bind(r, "Bound", r.repo)
    out = _create(r, "--project", PROJECT, "--repo", f"repo={r.repo}")
    assert out.get("ok") is not True and "Bound" in json.dumps(out), out
    assert not (r.ws / "Projects" / PROJECT / ".artifacts" / "runs").exists() or \
        not any((r.ws / "Projects" / PROJECT / ".artifacts" / "runs").iterdir())


def test_repos_bound_to_two_projects_are_refused(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_text("a\n")
    _git(other, "init", "-q", "-b", "main"); _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "init")
    _bind(r, "Bound", r.repo)
    _bind(r, "Elsewhere", other)
    out = _create(r, "--project", "Bound", "--repo", f"repo={r.repo}", "--repo", f"other={other}")
    assert out.get("ok") is not True and "Elsewhere" in json.dumps(out), out


def test_unbound_repo_needs_project_and_says_it_is_unbound(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    out = _create(r, "--project", PROJECT, "--repo", f"repo={r.repo}")
    assert out.get("ok") is True, out
    assert "not bound" in json.dumps(out.get("notes") or []), out
    c = _contract(r)
    assert c["project"] == PROJECT and c["has_ddd"] is False and c["bound_repos"] == []


def test_linked_worktree_binds_like_its_main_checkout(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    _bind(r, "Bound", r.repo)
    wt = tmp_path / "linked"
    subprocess.run(["git", "-C", str(r.repo), "worktree", "add", "-q", "-b", "side", str(wt)],
                   check=True, capture_output=True)
    out = _create(r, "--project", "Bound", "--repo", f"repo={wt}")
    assert out.get("ok") is True and not out.get("notes"), out
    c = _contract(r)
    assert c["project"] == "Bound" and c["bound_repos"] == ["repo"], c


def test_research_without_repos_has_ddd_from_the_project_docs(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="research", with_repo=False)
    _bind(r, "Bound", tmp_path / "nowhere")
    out = r.v1("create", "--project", "Bound", "--profile", "research")
    assert out.get("ok") is True, out
    assert _contract(r)["has_ddd"] is True
    r2 = Rig(tmp_path / "b", monkeypatch, profile="research", with_repo=False)
    out = r2.v1("create", "--project", PROJECT, "--profile", "research")
    assert out.get("ok") is True and _contract(r2)["has_ddd"] is False, out
