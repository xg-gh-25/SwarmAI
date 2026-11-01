"""Pipeline v1 real end-to-end tests: TEST red, main moving, merge conflicts; the run's base and the review diff.

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig
from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── TEST red, a rerun green, main moving, merge conflict ────────────────────



def test_a_failed_test_report_ends_not_landed(rig):
    r = rig("trivial")
    base = _main(r)
    _not_landed_tests_red(r)
    assert r.state()["status"] == "completed"
    assert _main(r) == base


def test_main_moves_disjoint_lands_on_new_main(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    _landed_clean(r.drive())
    from tests.pipeline_v1_rig import _git
    assert _git(r.repo, "merge-base", "--is-ancestor", moved, "main") == ""



def test_main_moves_by_unrelated_commits_and_the_run_lands_on_that_attempt(rig):
    """S6-1 with design 2026-11-01-pipeline-delivery-friction E2: the landing rebases the
    worktree onto the moved main; main's new commits change none of the run's files, so
    the TEST report from before the sync stands and the run lands on top of the moved
    main. (A move that also changes a run file is tested again:
    test_pipeline_v1_base_moved.)"""
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    state = r.drive()
    _landed_clean(state)
    from tests.pipeline_v1_rig import _git
    assert _git(r.repo, "rev-parse", "main~1") == moved


def test_merge_conflict_three_times_ends_land_conflict(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    head = r.commit_on_main({"backend/core/mod.py": "def add(a, b):\n    return a + b + 0\n"})
    for _ in range(3):
        r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "land_conflict", state
    assert _main(r) == head


# ── B6: the run's base, not the worktree HEAD ───────────────────────────────


def test_a_commit_in_the_worktree_does_not_move_the_base_or_hide_the_change(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    _git(r.worktree, "add", "-A"); _git(r.worktree, "commit", "-qm", "wip")
    engine = r.engine()
    assert engine._fork_base("repo", str(r.worktree)) == _git(r.repo, "rev-parse", "main")
    # the committed change is still the run's change
    assert "backend/core/mod.py" in engine.check_context().run["changed_paths"]
    assert r.report_and_complete().get("ok") is True     # BX-3 sees the committed change


# ── B8: DS-2b asks pytest what it collects ──────────────────────────────────

# ── F-6: an expert with no usable output is dispatched once more, never twice ──

def test_an_unparsed_review_answer_is_redispatched_once(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    handouts = started.get("handouts") or []
    assert handouts, started
    r.spawn_reviewer(handouts[0]["prompt"], "I looked around.", "review-a")   # no envelope
    again = r.v1("review-start")
    assert [h["event"]["attempt"] for h in again["handouts"]] == [2], again
    r.spawn_reviewer(again["handouts"][0]["prompt"], "still nothing", "review-b")
    assert r.v1("review-start")["handouts"] == []        # no third dispatch


def test_a_committed_or_new_file_is_in_the_review_diff(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("trivial")
    r.create()
    r.drive(until="review")
    (r.worktree / "backend" / "core" / "new_mod.py").write_text("def n():\n    return 2\n")
    _git(r.worktree, "add", "backend/core/mod.py"); _git(r.worktree, "commit", "-qm", "wip")
    started = r.v1("review-start")
    prompt = "\n".join(__import__("pathlib").Path(h["path"]).read_text(encoding="utf-8") for h in started.get("handouts") or [])
    roster = [row["id"] for row in started.get("reviewers") or []]
    assert started.get("ok") is True, started
    assert roster, "a new code file always gets a reviewer"
    assert "new_mod.py" in prompt and "return b + a" in prompt, prompt[-800:]


def test_the_base_follows_the_recorded_branch_not_the_main_checkout(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    base = _git(r.repo, "rev-parse", "main")
    _git(r.repo, "checkout", "-q", "-b", "elsewhere")          # the person switches branch
    (r.repo / "elsewhere.txt").write_text("x\n")
    _git(r.repo, "add", "."); _git(r.repo, "commit", "-qm", "unrelated")
    engine = r.engine()
    assert engine._fork_base("repo", str(r.worktree)) == base
    assert "elsewhere.txt" not in engine.check_context().run["changed_paths"]
    _git(r.repo, "checkout", "-q", "main")
