"""Conformance fixing spec 2026-10-23, K2: a multi-repo landing is all or nothing.

Design §A3.7: after the REPORT is rendered, a failure leaves no commit behind. Every
repo is checked before any repo's integration branch moves: its HEAD is still the
base, and the main checkout has no uncommitted change the fast-forward would refuse
or overwrite. Only then is each repo fast-forwarded; a failure in that last step
(another session moved a main in the millisecond window) is recorded as
``partial_land`` and the run ends not_landed(partial_land), never silently retried.

Methodology: two real tmp git repos; landing.commit_repos and the lifecycle fold.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.run_protocol import landing


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repos(tmp_path):
    roots, bases, bindings = {}, {}, {}
    for name in ("a", "b"):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("old\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        base = _git(repo, "rev-parse", "HEAD")
        (repo / "f.py").write_text(f"{name}-new\n")
        _git(repo, "add", "f.py")
        tree = _git(repo, "write-tree")
        _git(repo, "reset", "--hard", "-q", base)
        roots[name], bases[name] = str(repo), base
        bindings[name] = {"delivery_tree": tree}
    return roots, bases, bindings


def _land(tmp_path, roots, bases, bindings):
    return landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                                bindings=bindings, message="land",
                                progress_path=tmp_path / "progress.json")


def test_a_moved_second_repo_commits_nothing_anywhere(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    b = Path(roots["b"])
    (b / "other.py").write_text("moved\n")
    _git(b, "add", "-A")
    _git(b, "commit", "-q", "-m", "another session")
    with pytest.raises(landing.LandingError, match="b"):
        _land(tmp_path, roots, bases, bindings)
    assert _git(Path(roots["a"]), "rev-parse", "HEAD") == bases["a"], "repo a was committed"


def test_an_uncommitted_overlap_in_a_main_checkout_commits_nothing(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    (Path(roots["b"]) / "f.py").write_text("someone's unsaved work\n")
    with pytest.raises(landing.LandingError, match="b"):
        _land(tmp_path, roots, bases, bindings)
    assert _git(Path(roots["a"]), "rev-parse", "HEAD") == bases["a"]
    assert (Path(roots["b"]) / "f.py").read_text() == "someone's unsaved work\n"


def test_a_failure_after_every_check_passed_is_a_partial_land(tmp_path, monkeypatch):
    roots, bases, bindings = _repos(tmp_path)
    original = landing._fast_forward

    def race(root, commit, run_id=None):
        if root == roots["b"]:
            raise landing.LandingError("b: integration branch moved")
        return original(root, commit, run_id)
    monkeypatch.setattr(landing, "_fast_forward", race)
    with pytest.raises(landing.PartialLanding) as raised:
        _land(tmp_path, roots, bases, bindings)
    assert raised.value.landed == {"a": _git(Path(roots["a"]), "rev-parse", "HEAD")}
    assert raised.value.failed == "b"


def test_both_repos_land_when_nothing_moved(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    commits = _land(tmp_path, roots, bases, bindings)
    assert set(commits) == {"a", "b"}
    for name, sha in commits.items():
        assert _git(Path(roots[name]), "rev-parse", "HEAD") == sha
        assert _git(Path(roots[name]), "rev-parse", f"{sha}^") == bases[name]


def test_a_partial_land_ends_the_run_not_landed_partial_land(tmp_path, monkeypatch):
    """The engine records the race and takes the not-landed path; it does not
    report a landed run nor leave it to a silent retry."""
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path / "rig", monkeypatch)
    try:
        r.create()
        r.drive(until="deliver")

        def raced(**kwargs):
            raise landing.PartialLanding("repo2: integration branch moved",
                                         landed={"repo": "a" * 40}, failed="repo2")
        monkeypatch.setattr(landing, "commit_repos", raced)
        # the land command, in this process so the race above reaches it (S6-3: the
        # CLI runs it in its own process, as the caller session)
        from core.run_protocol import request_ctx
        from tests.pipeline_v1_rig import SESSION
        r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
        with request_ctx.active(request_ctx.Request(session_id=SESSION)):
            out = r.engine().normal_land(
                message="fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
        assert out.get("reason") == "partial_land", out
        state = r.state()
        assert state["landing"] == "not_landed" and state["reason"] == "partial_land", state
        events = [e for e in r.events() if e["kind"] == "partial_land"]
        assert events and events[0]["payload"]["failed"] == "repo2"
        audit = r.engine().build_audit(through="DELIVER", persist=False)
        assert audit["summary"]["quality"]["verdict"] == "BLOCK"
    finally:
        r.close()


# ── review round 1: git itself predicts the fast-forward; adoption is exact ──

def test_an_index_only_change_or_untracked_collision_commits_nothing(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    b = Path(roots["b"])
    (b / "f.py").write_text("index-only\n")
    _git(b, "add", "f.py")
    (b / "f.py").write_text("b-new\n")                 # the file equals the target...
    with pytest.raises(landing.LandingError, match="b"):
        _land(tmp_path, roots, bases, bindings)        # ...but the index would be lost
    assert _git(Path(roots["a"]), "rev-parse", "HEAD") == bases["a"]


def test_a_foreign_commit_with_the_same_tree_is_not_adopted(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    b = Path(roots["b"])
    foreign = _git(b, "commit-tree", bindings["b"]["delivery_tree"], "-p", bases["b"],
                   "-m", "someone else")
    _git(b, "merge", "--ff-only", "-q", foreign)
    with pytest.raises(landing.LandingError):
        _land(tmp_path, roots, bases, bindings)
    assert _git(Path(roots["a"]), "rev-parse", "HEAD") == bases["a"]


def test_the_partial_land_event_and_report_name_what_landed(tmp_path, monkeypatch):
    from core.run_protocol import audit
    value = audit.build(
        run_id="run_x", projection={"run_id": "run_x", "status": "completed", "landing": "not_landed",
                                    "reason": "partial_land", "human_decisions": 0},
        events=[{"kind": "run_create", "at": "2026-10-23T00:00:00+00:00", "payload": {}}],
        findings=[], repos_isolated=2,
        contract={"run_id": "run_x", "mode": "isolated", "expected_repos": ["a", "b"]},
        open_checks={}, audit_through="DELIVER",
        commits={"a": "c" * 40})
    report = audit.render(value, fmt="report")
    assert "landed: a @ cccccccccccc" in report and "not landed: b" in report
    assert value["summary"]["quality"]["verdict"] == "BLOCK"


# ── review round 2: the branch is bound, records are strict, history is not merged ──

def test_a_checkout_on_another_branch_lands_nothing(tmp_path):
    roots, bases, bindings = _repos(tmp_path)
    b = Path(roots["b"])
    _git(b, "checkout", "-q", "-b", "other")             # main and other both at base
    branches = {"a": "refs/heads/main", "b": "refs/heads/main"}
    with pytest.raises(landing.LandingError, match="b"):
        landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                             bindings=bindings, message="land", branches=branches,
                             progress_path=tmp_path / "progress.json")
    assert _git(Path(roots["a"]), "rev-parse", "HEAD") == bases["a"]
    assert _git(b, "rev-parse", "other") == bases["b"]


def test_a_progress_file_without_author_and_trailer_is_refused(tmp_path):
    """No old-run compatibility (usable-by-anyone r16): a progress file whose plan rows
    lack the frozen author / trailer is not resumed as a Swarm landing; nothing lands."""
    import json
    roots, bases, bindings = _repos(tmp_path)
    old = {"schema": 1, "run_id": "run_one", "commits": {},
           "plan": {n: {"base": bases[n], "delivery_tree": bindings[n]["delivery_tree"]}
                    for n in ("a", "b")}}
    (tmp_path / "progress.json").write_text(json.dumps(old))
    with pytest.raises(landing.LandingError, match="author / trailer"):
        _land(tmp_path, roots, bases, bindings)
    assert all(_git(Path(roots[n]), "rev-parse", "HEAD") == bases[n] for n in ("a", "b"))


def _stream():
    from tests.test_run_protocol_lifecycle import Stream
    return Stream("direct")


def test_a_stray_partial_land_is_refused():
    from core.run_protocol import lifecycle as lc
    stream = _stream()
    with pytest.raises(lc.LifecycleError):
        stream.add("partial_land", {"landed": {"fake": "not-a-sha"}, "failed": "b",
                                    "detail": "x", "stage_attempt_event_id": "e" * 64})
    with pytest.raises(lc.LifecycleError):            # bound to no landing at all
        _stream().add("partial_land", {"landed": {"a": "c" * 40}, "failed": "b",
                                       "detail": "x"})
    with pytest.raises(lc.LifecycleError):            # a land attempt that is not open
        _stream().add("partial_land", {"landed": {"a": "c" * 40}, "failed": "b",
                                       "detail": "x", "stage_attempt_event_id": "e" * 64})


# ── review round 3 ───────────────────────────────────────────────────────────

def test_a_fast_forward_that_moved_the_ref_before_failing_counts_as_landed(tmp_path, monkeypatch):
    roots, bases, bindings = _repos(tmp_path)
    original = landing._fast_forward

    def moved_then_failed(root, commit, run_id=None):
        original(root, commit, run_id)
        raise landing.LandingError("could not write ORIG_HEAD")
    monkeypatch.setattr(landing, "_fast_forward", moved_then_failed)
    commits = _land(tmp_path, roots, bases, bindings)
    assert set(commits) == {"a", "b"}


def test_adoption_needs_the_exact_landing_identity_and_message(tmp_path):
    import os
    roots, bases, bindings = _repos(tmp_path)
    a = Path(roots["a"])
    env = {**os.environ, **{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}, "GIT_AUTHOR_NAME": "Mallory",
           "GIT_COMMITTER_NAME": "Mallory"}
    impostor = subprocess.run(["git", "-C", str(a), "commit-tree", bindings["a"]["delivery_tree"],
                               "-p", bases["a"], "-m", "land"], check=True, capture_output=True,
                              text=True, env=env).stdout.strip()
    assert not landing._ours(str(a), impostor, bases["a"], bindings["a"]["delivery_tree"], "land",
                            "t <t@t>")
    env = {**os.environ, **{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}}
    padded = subprocess.run(["git", "-C", str(a), "commit-tree", bindings["a"]["delivery_tree"],
                             "-p", bases["a"], "-m", " land "], check=True, capture_output=True,
                            text=True, env=env).stdout.strip()
    assert not landing._ours(str(a), padded, bases["a"], bindings["a"]["delivery_tree"], "land",
                            "t <t@t>")


def test_a_later_manual_partial_land_is_the_audits_outcome():
    from core.run_protocol import audit
    events = [{"kind": "run_create", "at": "2026-10-23T00:00:00+00:00", "payload": {}},
              {"kind": "partial_land", "at": "2026-10-23T00:00:01+00:00",
               "payload": {"landed": {"a": "c" * 40}, "failed": "b"}}]
    value = audit.build(
        run_id="run_x", projection={"run_id": "run_x", "status": "completed",
                                    "landing": "not_landed", "reason": "tests_red",
                                    "human_decisions": 0},
        events=events, findings=[], repos_isolated=2,
        contract={"run_id": "run_x", "mode": "isolated", "expected_repos": ["a", "b"]},
        open_checks={}, audit_through="COMPLETED",
        commits={"a": "c" * 40})
    assert value["outcome"]["reason"] == "partial_land"
    assert "not landed: b" in audit.render(value, fmt="report")


def test_a_recovered_landed_repo_then_a_preflight_failure_is_a_partial_land(tmp_path):
    """Re-audit 99f96949 HIGH: repo a fast-forwarded, then the process died before the
    progress write; on the retry a is recognised as landed, and then repo b fails its
    phase-1 check. a IS landed: the result is a partial land, never "nothing moved"."""
    roots, bases, bindings = _repos(tmp_path)
    a = Path(roots["a"])
    made = landing._prepare_commit(roots["a"], bases["a"], bindings["a"]["delivery_tree"],
                                   "land", author="t <t@t>")
    landing._fast_forward(roots["a"], made)          # the crash came right after this
    b = Path(roots["b"])
    (b / "other.py").write_text("moved\n")
    _git(b, "add", "-A")
    _git(b, "commit", "-q", "-m", "another session")
    with pytest.raises(landing.PartialLanding) as raised:
        _land(tmp_path, roots, bases, bindings)
    assert raised.value.landed == {"a": _git(a, "rev-parse", "HEAD")}
    assert raised.value.failed == "b"
