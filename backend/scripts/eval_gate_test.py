"""Tests for the git-bound eval gate: code_digest + bvt block + ci_eval_gate.

Gate keystone (run_69b1c644 Cycle 4):
- compute_code_digest(root): git-ls-tree hash of eval-relevant paths + public
  golden_set content. Stable across report-commit (binds to INPUTS not HEAD).
- bvt block in run_result: gate_eligible (fast-deterministic, non-runtime_health)
  ∩ passed/failed; green = total>0 AND passed>0 AND failed==0 AND error==0.
  Empty set is RED (never vacuous-green); all-skipped is RED (passed>0 required).
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_runner import compute_code_digest, compute_bvt  # noqa: E402


def _git(cmd, cwd):
    subprocess.run(["git", *cmd], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    _git(["init"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    # eval_runner.py is in _GATE_CODE_PATHS — create it so the digest has content
    (tmp_path / "backend" / "scripts" / "eval_runner.py").write_text("v1\n")
    proj = tmp_path / "Eval"
    proj.mkdir(parents=True)
    (proj / "golden_set.yaml").write_text("version: 2\ncases: []\n")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    return tmp_path


def test_digest_stable_across_unrelated_commit(git_repo):
    """Committing a NON-code file (e.g. the eval report) must NOT change the digest."""
    d1 = compute_code_digest(git_repo, code_root=git_repo)
    # add an unrelated tracked file (mimics committing an EvalHistory report)
    (git_repo / "Eval" / "EvalHistory").mkdir()
    (git_repo / "Eval" / "EvalHistory" / "r.json").write_text("{}")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-m", "report"], git_repo)
    d2 = compute_code_digest(git_repo, code_root=git_repo)
    assert d1 == d2, "digest must bind to code inputs, not HEAD / unrelated files"


def test_digest_changes_when_code_changes(git_repo):
    d1 = compute_code_digest(git_repo, code_root=git_repo)
    (git_repo / "backend" / "scripts" / "eval_runner.py").write_text("v2-changed\n")
    d2 = compute_code_digest(git_repo, code_root=git_repo)
    assert d1 != d2, "digest must change when eval-relevant code changes"


def test_digest_changes_when_public_golden_set_changes(git_repo):
    d1 = compute_code_digest(git_repo, code_root=git_repo)
    (git_repo / "Eval" / "golden_set.yaml").write_text("version: 2\ncases: [{id: X}]\n")
    d2 = compute_code_digest(git_repo, code_root=git_repo)
    assert d1 != d2, "editing the public golden set must invalidate the digest"


def test_digest_changes_when_private_golden_set_changes(git_repo):
    """Gate-2 C1: bvt counts gate-eligible cases from the MERGED set, so a
    changed PRIVATE case must also invalidate the digest (else stale-green)."""
    priv = git_repo / "Eval" / "golden_set.private.yaml"
    priv.write_text("version: 2\ncases: []\n")
    d1 = compute_code_digest(git_repo, code_root=git_repo)
    priv.write_text("version: 2\ncases: [{id: PRIV_CHANGED}]\n")
    d2 = compute_code_digest(git_repo, code_root=git_repo)
    assert d1 != d2, "editing the private golden set must invalidate the digest"


def _cases_results(specs):
    """specs: list of (eval_method, evaluators, status)."""
    cases, results = [], []
    for i, (method, evs, status) in enumerate(specs):
        cid = f"C{i}"
        cases.append({"id": cid, "eval_method": method, "evaluators": evs})
        results.append({"id": cid, "status": status})
    return cases, results


def test_bvt_green_when_all_eligible_pass():
    cases, results = _cases_results([
        ("programmatic", ["file_contains"], "passed"),
        ("programmatic", ["trajectory_in_order"], "passed"),
    ])
    bvt = compute_bvt(cases, results)
    assert bvt["green"] is True
    assert bvt["total"] == 2 and bvt["passed"] == 2


def test_bvt_excludes_runtime_health_and_llm():
    cases, results = _cases_results([
        ("programmatic", ["file_contains"], "passed"),
        ("programmatic", ["runtime_health"], "failed"),  # excluded → not in bvt
        ("llm", ["goal_success"], "failed"),              # excluded → not in bvt
    ])
    bvt = compute_bvt(cases, results)
    assert bvt["total"] == 1, "only fast-deterministic file_contains counts"
    assert bvt["green"] is True


def test_bvt_red_on_failure():
    cases, results = _cases_results([
        ("programmatic", ["file_contains"], "passed"),
        ("programmatic", ["trajectory_in_order"], "failed"),
    ])
    bvt = compute_bvt(cases, results)
    assert bvt["green"] is False and bvt["failed"] == 1


def test_bvt_red_on_empty_set():
    """Vacuous-green guard: zero eligible cases = RED, never green."""
    cases, results = _cases_results([("llm", ["goal_success"], "passed")])
    bvt = compute_bvt(cases, results)
    assert bvt["total"] == 0 and bvt["green"] is False


def test_bvt_red_on_all_skipped():
    """all-skipped degenerate: passed==0 = RED even with failed==0/error==0."""
    cases, results = _cases_results([
        ("programmatic", ["file_contains"], "skipped"),
        ("programmatic", ["file_contains"], "skipped"),
    ])
    bvt = compute_bvt(cases, results)
    assert bvt["passed"] == 0 and bvt["green"] is False
