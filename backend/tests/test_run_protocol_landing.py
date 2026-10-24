"""Tests Pipeline v1 authorized manual landing.

Methodology: real temporary main + linked worktree, generated REPORT tree, in-memory
oracle, and actual commit-tree/ff-only integration. No production repositories.
"""
from __future__ import annotations

import subprocess

import pytest

from core.run_protocol import auth, checks, events as ev, git_tree, tree
from core.run_protocol.landing import (LandingError, approve_from_event, execute_request,
                                       prepare_request)
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _rig(tmp_path):
    main = tmp_path / "main"; main.mkdir()
    _git(main, "init", "-q"); _git(main, "config", "user.email", "a@b.c"); _git(main, "config", "user.name", "T")
    (main / "a.py").write_text("old\n"); _git(main, "add", "."); _git(main, "commit", "-qm", "base")
    base = _git(main, "rev-parse", "HEAD")
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-qb", "run/run_one", str(worktree), base)
    (worktree / "a.py").write_text("new\n")
    authority = ev.EventAuthority(b"l" * 32)
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    code_tree = tree.build_code_trees({"repo": str(worktree)}, "run_one",
                                      git_tree.write_tree, git_tree.list_reserved)["repo"]
    final = {"repos": {"repo": {"code_tree": code_tree, "base": base,
                                  "tests": {"verdict": "green"}, "validations": []}}}
    context = checks.make_context(
        profile="direct", requirement_variant="lite", design_variant="lite",
        requirements={}, design={}, events=[], authority=authority, final=final,
        run={"mode": "isolated", "code_trees": {"repo": code_tree}},
        expected_repos={"repo"}, validation_manifest={"repo": set()})
    return main, worktree, base, store, context


def test_prepare_approve_execute_binds_report_and_lands(tmp_path):
    main, worktree, base, store, context = _rig(tmp_path)
    prepared = prepare_request(
        store, run_id="run_one", session_id="session_1", action="land",
        repos={"repo": str(worktree)}, context=context, at=AT, request_id="q1",
        integration_roots={"repo": str(main)},
        render_report=lambda request, results: f"# Report\nrequest: {request}\n".encode())
    assert prepared["request"]["binding_digest"]
    kinds = [row["kind"] for row in store.read_events()]
    assert kinds[0] == "stage_attempt"
    assert kinds.count("check") == 5
    assert kinds.index("land_intent") < kinds.index("render") < len(kinds) - 1
    assert kinds[-1] == "land_request"
    approve_from_event(store, "q1", now=0, at=AT)     # the daemon, after the person
    commits = execute_request(
        store, "q1", session_id="session_1",
        repos={"repo": str(worktree)}, integration_roots={"repo": str(main)},
        message="feat: land\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>", at=AT, now=1)
    assert _git(main, "rev-parse", "HEAD") == commits["repo"]
    assert (main / "a.py").read_text() == "new\n"
    assert (main / ".aidlc/deliverables/run_one/REPORT.md").is_file()
    assert [row["kind"] for row in store.read_events()][-3:] == ["auth", "auth_consumed", "land"]
    assert prepared["request"] == store.read_events()[-4]["payload"]["request"]   # C1
    with pytest.raises(LandingError, match="already consumed"):
        execute_request(store, "q1", session_id="session_1",
                        repos={"repo": str(worktree)}, integration_roots={"repo": str(main)},
                        message="again", at=AT, now=2)
    assert [row["kind"] for row in store.read_events()][-1] == "integrity"


def test_tree_change_after_prepare_refuses_before_consuming(tmp_path):
    main, worktree, _base, store, context = _rig(tmp_path)
    prepared = prepare_request(
        store, run_id="run_one", session_id="session_1", action="land",
        repos={"repo": str(worktree)}, context=context, at=AT, request_id="q1",
        integration_roots={"repo": str(main)},
        render_report=lambda *_: b"# Report\n")
    approve_from_event(store, "q1", now=0, at=AT)
    (worktree / "a.py").write_text("changed again\n")
    with pytest.raises(LandingError, match="trees changed"):
        execute_request(store, "q1", session_id="session_1",
                        repos={"repo": str(worktree)}, integration_roots={"repo": str(main)},
                        message="x", at=AT, now=1)
    assert "auth_consumed" not in [row["kind"] for row in store.read_events()]


def test_plan_row_without_author_or_trailer_is_refused_not_filled_in():
    """No old-run compatibility (usable-by-anyone r16): a plan row missing the frozen
    author / trailer is not landed as Swarm with the Swarm trailer."""
    from core.run_protocol.landing import LandingError, _row_author, _row_message
    for row in ({"base": "b"}, {"base": "b", "author": "A <a@x>"}, {"base": "b", "trailer": ""}):
        with pytest.raises(LandingError):
            _row_message("m", row)
        with pytest.raises(LandingError):
            _row_author(row)
    assert _row_message("m", {"author": "A <a@x>", "trailer": ""}) == "m\n"


def test_landing_message_always_carries_the_project_trailer():
    from core.run_protocol.landing import LandingError, with_trailer
    TRAILER = "Co-Authored-By: Swarm <swarm@swarmai.dev>"
    assert with_trailer("feat: x", TRAILER) == f"feat: x\n\n{TRAILER}\n"
    once = with_trailer(f"feat: x\n\n{TRAILER}", TRAILER)
    assert once.count(TRAILER) == 1 and with_trailer(once, TRAILER) == once
    with pytest.raises(LandingError):
        with_trailer("  ", TRAILER)
