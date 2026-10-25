"""Conformance K5: v13 §D4 acceptance, end to end (conformance spec §3 K5).

Methodology: runs are driven only through ``pipeline-v1``
and the real hooks (``tests/pipeline_v1_rig.py``); assertions read the run's signed
event log, its sealed finals and git. Covered:

- the profile × {isolated, shared} matrix (F9): every code profile lands in both
  modes, the contract records the mode, and the TEST final of each repo is bound to
  the tree that landed;
- §D4 items 1, 4, 5, 6 on two real isolated runs of one repo, asserting finals, not
  only trees (F9);
- the §D4 end-to-end scenarios in BOTH modes: TEST red -> not_landed(tests_red) and
  push_ready false, the circuit breaker, a person's run-land (human_override), a
  decision with awaiting_human, abandon/restore;
- the shared landing primitive (``landing._land_in_place``): another session's
  deletion stays deleted, a staged change or a held index lock lands nothing, and a
  person's run-land after a partial_land keeps the landed repo and lands the rest.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig, _git

pytestmark = pytest.mark.timeout(300)

CODE_PROFILES = ("direct", "trivial", "full", "bugfix", "goal")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str = "direct", mode: str = "on", **kwargs) -> Rig:
        monkeypatch.setenv("SWARM_WORKTREE_MODE", mode)
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile, **kwargs)
        r.env["SWARM_WORKTREE_MODE"] = mode
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.run_id:
            assert r.v1("status").get("ok") is True, "the event log no longer folds"
        r.close()


def _final_tree(r: Rig) -> str:
    """The tree the run's last TEST report was submitted on (S6-1)."""
    tree = r.tested_tree()
    assert tree, "no TEST validation report"
    return tree


def _every_stage_checked_and_completed(r: Rig) -> None:
    """§D4: each stage's signed chain is stage_attempt -> a passing check for EVERY
    check the registry requires there -> stage_complete -> transition, and the land
    attempt passed every land check. The expectation comes from the check registry,
    not from the roster the run wrote."""
    from core.run_protocol import checks
    from core.run_protocol.lifecycle import PROFILE_STAGES
    events = r.events()
    by_id = {e["event_id"]: e for e in events}
    passed = {(e["payload"]["stage_attempt_event_id"], e["payload"]["check_id"])
              for e in events if e["kind"] == "check" and e["payload"]["passed"]}
    completed = {e["payload"]["stage_attempt_event_id"] for e in events
                 if e["kind"] == "stage_complete"}
    triggers = {e["payload"]["trigger_event_id"] for e in events if e["kind"] == "transition"}
    for stage in PROFILE_STAGES[r.profile]:
        if stage in ("deliver", "reflect"):
            continue
        variant = ("lite" if stage == "plan" and r.profile == "trivial"
                   else "full" if stage in {"evaluate", "plan"} else None)
        required = {spec.check_id for spec in checks.stage_checks(r.profile, stage, variant)}
        attempts = [e for e in events if e["kind"] == "stage_attempt" and e["stage"] == stage]
        assert attempts, f"{stage}: no stage_attempt"
        last = attempts[-1]
        assert required <= set(last["payload"]["check_ids"]), (stage, required)
        assert all((last["event_id"], cid) in passed for cid in required), stage
        assert last["event_id"] in completed, f"{stage}: no stage_complete"
        done = next(e for e in events if e["kind"] == "stage_complete"
                    and e["payload"]["stage_attempt_event_id"] == last["event_id"])
        assert done["event_id"] in triggers, f"{stage}: completion moved no transition"
    land = next(e for e in reversed(events) if e["kind"] == "land")
    attempt = by_id[land["payload"]["stage_attempt_event_id"]]
    for spec in checks.land_checks(r.profile):
        assert (attempt["event_id"], spec.check_id) in passed, spec.check_id


def _landed_is_final_plus_report(r: Rig, head: str) -> None:
    """The WHOLE landed tree is the tested code tree plus the rendered REPORT."""
    code_tree = _final_tree(r)
    changed = _git(r.repo, "diff", "--name-only", code_tree, head).splitlines()
    assert changed == [f".aidlc/deliverables/{r.run_id}/REPORT.md"], changed


@pytest.mark.parametrize("mode", ["on", "off"], ids=["isolated", "shared"])
@pytest.mark.parametrize("profile", CODE_PROFILES)
def test_every_code_profile_lands_in_both_modes(rig, profile, mode):
    r = rig(profile, mode)
    r.create()
    base = _git(r.repo, "rev-parse", "main")
    state = r.drive()
    assert state["status"] == "completed" and state["landing"] == "landed", state
    engine = r.engine()
    assert engine.contract["mode"] == ("isolated" if mode == "on" else "shared")
    head = _git(r.repo, "rev-parse", "main")
    assert head != base and _git(r.repo, "rev-parse", "main^") == base
    landed_tree = _git(r.repo, "rev-parse", "main^{tree}")
    assert engine.landed_commits() == {"repo": head}
    assert landed_tree
    _landed_is_final_plus_report(r, head)
    _every_stage_checked_and_completed(r)
    push = engine.push_status()
    if mode == "off":                  # a shared run is never push-ready (MD-1)
        assert push["push_ready"] is False and "MD-1" in push["reasons"], push
    # the main checkout ends clean: the landed commit is what is checked out, and a
    # shared run's own edits became that commit (no leftover diff, no staged rest)
    assert _git(r.repo, "status", "--porcelain") == ""


# ── v13 §D4 concurrent isolation acceptance: items 1, 4, 5, 6 with finals ───

def _second_run(r: Rig, mode: str = "on") -> Rig:
    """A second run on the SAME repo, data dir and hook oracle as *r*."""
    import copy
    other = copy.copy(r)
    other.env = {**r.env, "SWARM_WORKTREE_MODE": mode}
    other.ctx = dict(r.ctx)
    other.run_id = None
    other.worktree = None
    return other


def _to_test_done(r: Rig, change: str) -> None:
    r.create()
    r.drive(until="build")
    r.edit({"backend/core/mod.py": change})
    assert r.report_and_complete().get("ok") is True
    r.drive(until="deliver")


def _mod_in(r: Rig, tree: str) -> str:
    return _git(r.repo, "cat-file", "-p", f"{tree}:backend/core/mod.py") + "\n"


A_CHANGE = "def add(a, b):\n    return b + a\n"
B_CHANGE = "def add(a, b):\n    return sum((a, b))\n"


def test_d4_1_two_isolated_runs_on_one_file_have_their_own_tree_and_final(rig):
    a = rig("direct")
    b = _second_run(a)
    _to_test_done(a, A_CHANGE)
    _to_test_done(b, B_CHANGE)
    ta, tb = a.tested_tree(), b.tested_tree()
    assert ta != tb
    assert _mod_in(a, ta) == A_CHANGE and _mod_in(b, tb) == B_CHANGE
    assert (a.repo / "backend/core/mod.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert {e["run_id"] for e in a.events()} == {a.run_id}
    assert {e["run_id"] for e in b.events()} == {b.run_id}


def test_d4_4_test_auth_abandon_restore_never_cross_runs(rig):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red, _prepare
    a = rig("trivial")
    b = _second_run(a)
    b.profile = "direct"
    _not_landed_tests_red(a)                          # A: completed(not_landed)
    a_tree = a.tested_tree()
    b.create()
    b.drive(until="build")
    b.edit({"backend/core/mod.py": B_CHANGE})
    # test: a report through A's run id records only on A's own tree, never B's
    assert a.tested_tree() == a_tree
    # auth: A's prepared landing request cannot be executed by B
    request_id = _prepare(a)["prepared"]["request"]["request_id"]
    base = _git(a.repo, "rev-parse", "main")
    stolen = b.v1("run-land", "--request", request_id, "--message", "fix: steal" +
                  "\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert stolen.get("ok") is not True, stolen
    assert _git(a.repo, "rev-parse", "main") == base
    assert not any(e["kind"] in ("auth", "auth_consumed", "land") for e in b.events())
    # abandon / restore: B's lifecycle never touches A
    before = [e["event_id"] for e in a.events()]
    assert b.v1("abandon", "--reason", "cross-run probe").get("ok") is True
    assert b.v1("restore").get("ok") is True
    assert [e["event_id"] for e in a.events()] == before


def test_d4_4_a_persons_answer_reaches_only_the_run_that_asked(rig):
    from tests.test_pipeline_v1_real_e2e import _decision
    a = rig("bugfix")
    b = _second_run(a)
    b.profile = "direct"
    a.create()
    b.create()
    a.gate0("WRONG-FRAME", n=1)
    a.gate0("WRONG-FRAME", n=2)
    asked = a.v1("decision-request", "--artifact", _decision(a), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert a.answer_card("a — swap")
    assert any(e["kind"] == "human_answer" for e in a.events())
    assert not any(e["kind"] in ("decision", "human_answer") for e in b.events())


def test_d4_5_a_shared_runs_checkout_edits_never_enter_an_isolated_final(rig):
    iso = rig("direct", "on")
    shared = _second_run(iso, mode="off")
    iso.create()
    shared.create()
    assert shared.engine().contract["mode"] == "shared"
    shared.drive(until="build")
    shared.edit({"backend/core/other.py": "def other():\n    return 1  # shared\n"})
    _ = iso.drive(until="build")
    iso.edit({"backend/core/mod.py": A_CHANGE})
    assert iso.report_and_complete().get("ok") is True
    iso.drive(until="deliver")
    tree = iso.tested_tree()
    assert "# shared" not in _git(iso.repo, "cat-file", "-p", f"{tree}:backend/core/other.py")
    assert "# shared" in (iso.repo / "backend/core/other.py").read_text()


# ── §D4 end-to-end scenarios in both modes ───────────────────────────────────

MODES = pytest.mark.parametrize("mode", ["on", "off"], ids=["isolated", "shared"])


@MODES
def test_tests_red_ends_not_landed_and_never_push_ready(rig, mode):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red
    r = rig("trivial", mode)
    base = _git(r.repo, "rev-parse", "main")
    _not_landed_tests_red(r)
    assert _git(r.repo, "rev-parse", "main") == base
    assert r.engine().push_status()["push_ready"] is False


@MODES
def test_a_persons_run_land_lands_as_human_override(rig, mode):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red, _prepare, _request
    r = rig("trivial", mode)
    _not_landed_tests_red(r)
    base = _git(r.repo, "rev-parse", "main")
    out = _request(r, _prepare(r))
    assert out.get("ok") is True, out
    head = _git(r.repo, "rev-parse", "main")
    assert _git(r.repo, "rev-parse", "main^") == base
    land = [e for e in r.events() if e["kind"] == "land"][-1]
    assert land["payload"]["landing"] == "human_override"
    assert land["payload"]["commits"] == {"repo": head}
    assert r.engine().landed_commits() == {"repo": head}
    assert _git(r.repo, "status", "--porcelain") == ""


@MODES
def test_a_decision_waits_for_the_person_then_continues(rig, mode):
    from tests.test_pipeline_v1_real_e2e import _decision
    from tests.pipeline_v1_rig import documents
    r = rig("bugfix", mode)
    r.create()
    r.gate0("WRONG-FRAME", n=1)
    r.gate0("WRONG-FRAME", n=2)
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert r.state()["status"] == "awaiting_human"
    assert r.answer_card("a — swap")
    out = r.v1("stage-complete", "--artifact",
               r.artifact("requirements", documents("bugfix")["evaluate"]))
    assert out.get("ok") is True and r.state()["stage"] == "think", out


@MODES
def test_abandon_then_restore_continues_to_a_bound_landing(rig, mode):
    r = rig("direct", mode)
    r.create()
    r.drive(until="review")
    assert r.v1("abandon", "--reason", "rehearse").get("ok") is True
    assert r.state()["status"] == "abandoned"
    assert r.v1("restore").get("ok") is True
    assert r.state()["status"] == "paused"
    assert r.v1("resume").get("ok") is True
    state = r.drive()
    assert state["landing"] == "landed", state
    _landed_is_final_plus_report(r, _git(r.repo, "rev-parse", "main"))


# ── the shared landing primitive ─────────────────────────────────────────────

def _shared_repo(tmp_path):
    """A checkout whose run edits sit uncommitted, and the delivery tree they make."""
    repo = tmp_path / "s"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("old\n")
    (repo / "g.py").write_text("g\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "f.py").write_text("new\n")
    (repo / "n.py").write_text("created by the run\n")
    report = ".aidlc/deliverables/run_s/REPORT.md"
    import os
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "idx")}
    subprocess.run(["git", "-C", str(repo), "read-tree", base], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "f.py", "n.py"], check=True, env=env)
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                          input=b"# REPORT\n", check=True, capture_output=True).stdout.decode().strip()
    subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo",
                    f"100644,{blob},{report}"], check=True, env=env)
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()
    return repo, base, tree, report


def _land_shared(tmp_path, repo, base, tree, **kw):
    from core.run_protocol import landing
    return landing.commit_repos(run_id="run_s", integration_roots={"s": str(repo)},
                                bases={"s": base}, bindings={"s": {"delivery_tree": tree}},
                                message="land", in_place=True,
                                progress_path=tmp_path / "progress.json", **kw)


def test_a_shared_landing_commits_the_runs_files_and_writes_only_its_report(tmp_path):
    repo, base, tree, report = _shared_repo(tmp_path)
    commits = _land_shared(tmp_path, repo, base, tree)
    assert _git(repo, "rev-parse", "main") == commits["s"]
    assert (repo / report).read_text() == "# REPORT\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_another_sessions_deletion_is_never_brought_back(tmp_path, monkeypatch):
    from core.run_protocol import landing
    repo, base, tree, report = _shared_repo(tmp_path)
    real = landing._land_in_place

    def deleted_meanwhile(*args, **kwargs):
        (repo / "n.py").unlink()                 # another session, between the phases
        return real(*args, **kwargs)
    monkeypatch.setattr(landing, "_land_in_place", deleted_meanwhile)
    _land_shared(tmp_path, repo, base, tree)
    assert not (repo / "n.py").exists()
    assert (repo / report).is_file()


def test_a_staged_change_or_a_held_index_lock_lands_nothing(tmp_path):
    from core.run_protocol import landing
    repo, base, tree, _report = _shared_repo(tmp_path)
    (repo / "g.py").write_text("someone staged this\n")
    _git(repo, "add", "g.py")
    with pytest.raises(landing.LandingError, match="staged"):
        _land_shared(tmp_path, repo, base, tree)
    assert _git(repo, "rev-parse", "main") == base
    _git(repo, "restore", "--staged", "g.py")
    (tmp_path / "progress.json").unlink()
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    with pytest.raises(landing.LandingError, match="index"):
        _land_shared(tmp_path, repo, base, tree)
    assert _git(repo, "rev-parse", "main") == base and lock.exists()
    assert (repo / "g.py").read_text() == "someone staged this\n"


def test_a_run_land_after_a_partial_land_keeps_the_landed_repo(tmp_path):
    from core.run_protocol import landing
    from tests.test_pipeline_v1_conformance_k2 import _repos
    roots, bases, bindings = _repos(tmp_path)
    first = landing.commit_repos(run_id="run_one", integration_roots={"a": roots["a"]},
                                 bases={"a": bases["a"]}, bindings={"a": bindings["a"]},
                                 message="normal land", progress_path=tmp_path / "p1.json")
    # the person's landing renders another REPORT: repo a's delivery tree differs now
    other = dict(bindings, a={"delivery_tree": _git(Path(roots["a"]), "rev-parse",
                                                     f"{bases['a']}^{{tree}}")})
    commits = landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                                   bindings=other, message="human land",
                                   progress_path=tmp_path / "p2.json", adopted=first)
    assert commits["a"] == first["a"] and _git(Path(roots["b"]), "rev-parse", "HEAD") == commits["b"]
    with pytest.raises(landing.LandingError, match="not on its branch"):
        landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                             bindings=other, message="human land",
                             progress_path=tmp_path / "p3.json", adopted={"a": "f" * 40})


# ── review round 2: after the CAS nothing escapes unrecorded or unsafely ────

def test_a_failure_after_the_cas_is_landed_and_named_unsettled(tmp_path, monkeypatch):
    from core.run_protocol import landing
    repo, base, tree, report = _shared_repo(tmp_path)

    def broken(self, built):
        raise OSError("disk full while installing the index")
    monkeypatch.setattr(landing._IndexLock, "install", broken)
    commits = _land_shared(tmp_path, repo, base, tree)
    assert _git(repo, "rev-parse", "main") == commits["s"]       # the commit landed
    assert commits.unsettled == ["s"]                            # and it says so
    assert not (repo / ".git" / "index.lock").exists()


def test_the_report_is_never_written_through_a_symlink(tmp_path):
    repo, base, tree, report = _shared_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".aidlc").symlink_to(outside, target_is_directory=True)
    commits = _land_shared(tmp_path, repo, base, tree)
    assert commits.unsettled == ["s"]
    assert not any(outside.rglob("REPORT.md"))


def test_an_adopted_shared_commit_is_settled_or_named(tmp_path):
    from core.run_protocol import landing
    repo, base, tree, report = _shared_repo(tmp_path)
    import os
    env = {**os.environ, **{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}}    # the repo's own identity (no pipeline.toml)
    commit = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", base, "-m",
                             "normal land"], check=True, capture_output=True, text=True,
                            env=env).stdout.strip()
    _git(repo, "update-ref", "refs/heads/main", commit, base)    # a CAS, then a crash
    commits = _land_shared(tmp_path, repo, base, tree, adopted={"s": commit})
    assert commits == {"s": commit} and commits.unsettled == []
    assert (repo / report).read_text() == "# REPORT\n"
    assert _git(repo, "status", "--porcelain") == ""


# ── review round 3 ───────────────────────────────────────────────────────────

def test_a_partial_land_names_a_landed_checkout_it_left_unfinished(tmp_path, monkeypatch):
    from core.run_protocol import landing
    roots, bases, bindings = {}, {}, {}
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        repo, base, tree, _report = _shared_repo(tmp_path / name)
        roots[name], bases[name], bindings[name] = str(repo), base, {"delivery_tree": tree}
    real, real_install = landing._land_in_place, landing._IndexLock.install

    def install(self, built):
        if str(self.git_dir).startswith(roots["a"]):
            raise OSError("disk full")              # repo a: after its CAS
        return real_install(self, built)

    def second_refused(root, branch, base, commit, **kw):
        if root == roots["b"]:
            raise landing.LandingError("raced")     # repo b: before its CAS
        return real(root, branch, base, commit, **kw)
    monkeypatch.setattr(landing._IndexLock, "install", install)
    monkeypatch.setattr(landing, "_land_in_place", second_refused)
    with pytest.raises(landing.PartialLanding) as caught:
        landing.commit_repos(run_id="run_s", integration_roots=roots, bases=bases,
                             bindings=bindings, message="land", in_place=True,
                             progress_path=tmp_path / "progress.json")
    assert set(caught.value.landed) == {"a"} and caught.value.unsettled == ["a"]


def test_a_stale_file_where_the_report_goes_is_not_called_finished(tmp_path):
    repo, base, tree, report = _shared_repo(tmp_path)
    (repo / report).parent.mkdir(parents=True)
    (repo / report).write_text("someone else's text\n")
    commits = _land_shared(tmp_path, repo, base, tree)
    assert commits.unsettled == ["s"]
    assert (repo / report).read_text() == "someone else's text\n"


