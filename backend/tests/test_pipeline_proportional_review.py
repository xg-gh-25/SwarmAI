"""Proportional review and continuations that make progress (XG 2026-10-20).

What is tested:
- ``_small_change``: a few non-sensitive source lines are too small to review; more
  lines, a new code file or a sensitive path (auth / data / infra / config) are not;
- ``_dispatch_experts`` dispatches nobody for a small change, declared experts included;
- ``_small_review_delta``: a small delta since the Gate-2 review keeps it (real git
  trees); a larger one or an added file does not;
- T5 ``_stalled_calls`` counts calls that cut the unfinished count by < 10%, and
  ``_continuation_series`` reads the current round from the run index;
- T4 a file whose prefix was already replayed runs its rest alone (context-unverified);
- T6 the per-file reuse key ignores HEAD (a main commit outside the file's closure).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))
import artifact_cli as cli  # noqa: E402
import pipeline_test as pt  # noqa: E402
import pipeline_worktree as pw  # noqa: E402


def _scope(lines: int, path: str = "backend/core/x.py", status: str = "M") -> dict:
    return {"changed_files": [{"path": path, "status": status, "added": lines, "removed": 0,
                               "is_test": False}],
            "source_insertions": lines, "source_deletions": 0}


def test_small_change_rule():
    assert cli._small_change(_scope(5))
    assert cli._small_change(_scope(cli.SMALL_CHANGE_LINES + 1)) is None
    assert cli._small_change(_scope(3, "backend/routers/chat.py")) is None
    assert cli._small_change(_scope(3, "backend/core/auth_token.py")) is None
    assert cli._small_change(_scope(3, "backend/core/new.py", "A")) is None
    assert cli._small_change(_scope(3, "docs/readme.md")) is None  # docs: own rule


def test_governed_context_change_gets_security_and_meta_review_at_any_size():
    """A 3-line edit to a system-prompt source is not small: security reviews it, and
    the non-empty plan brings the meta-review (stage-start's rule)."""
    for path in ("backend/context/STEERING.md", "backend/context/SOUL.md",
                 "/Users/u/.swarm-ai/worktrees/run_x/swarmai/backend/context/AGENT.md"):
        s = _scope(3, path)
        assert cli._small_change(s) is None, path
        assert not cli._is_docs_only(s), path
        ids = {e["id"] for e in cli._dispatch_experts(profile="direct", work_type=None,
                                                      scope=s, required=[])}
        assert ids == {"security"}, (path, ids)
    # plain docs and an unrelated small code edit keep their rules
    assert cli._dispatch_experts(profile="direct", work_type=None,
                                 scope=_scope(80, "docs/readme.md"), required=[]) == []
    assert cli._dispatch_experts(profile="direct", work_type=None, scope=_scope(4),
                                 required=[]) == []


def test_small_change_dispatches_nobody_even_declared_experts():
    out = cli._dispatch_experts(profile="full", work_type="feature", scope=_scope(4),
                                required=["correctness", "security"])
    assert out == []
    big = cli._dispatch_experts(profile="full", work_type="feature", scope=_scope(80),
                                required=[])
    assert {e["id"] for e in big} >= {"correctness", "security"}


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                           "user.email=t@e.invalid", "-c", "core.hooksPath=/dev/null", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


def _tree(repo: Path, files: dict) -> str:
    for k, v in files.items():
        (repo / k).write_text(v)
    _git(repo, "add", "-A")
    return _git(repo, "write-tree")


def test_small_review_delta_keeps_the_review(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    body = "".join(f"line {i}\n" for i in range(50))
    t0 = _tree(repo, {"a.py": body})
    t1 = _tree(repo, {"a.py": body.replace("line 3\n", "line three\n")})
    t2 = _tree(repo, {"a.py": "".join(f"x {i}\n" for i in range(50))})
    t3 = _tree(repo, {"a.py": body, "b.py": "new\n"})
    wts = [({"repo_name": "r"}, repo, None)]
    rec = {"at": 1.0, "trees": {"r": t0}}
    assert pw._small_review_delta(rec, wts, {"r": t1}) == 2
    assert pw._small_review_delta(rec, wts, {"r": t2}) is None   # 100 lines
    assert pw._small_review_delta(rec, wts, {"r": t3}) is None   # added file
    assert pw._small_review_delta({"trees": {"r": t0}}, wts, {"r": t1}) is None  # never reviewed


def test_stalled_calls_count_progress_by_unfinished_tests():
    assert pt._stalled_calls([100, 50, 20]) == 0
    assert pt._stalled_calls([100, 97, 95]) == 2
    assert pt._stalled_calls([100, 50, 48, 47]) == 2
    assert pt._stalled_calls([100, 105]) == 1     # it grew: a stall
    assert pt._stalled_calls([10, 0]) == 0


def test_continuation_series_reads_the_current_round(tmp_path):
    rows = [{"kind": "run", "mode": "regression", "unfinished": 9},      # an old round
            {"kind": "run", "mode": "regression", "unfinished": 132},    # this round
            {"kind": "run", "mode": "recheck", "auto_recheck_of": "regression", "unfinished": 105},
            {"kind": "overlay", "mode": "regression", "unfinished": 105},
            {"kind": "overlay", "mode": "regression", "unfinished": 102}]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / pt.RUN_INDEX).write_text(json.dumps(rows))
    assert pt._continuation_series(tmp_path, "regression") == [132, 105, 102]


def test_a_prefix_is_replayed_once(tmp_path):
    f = "backend/tests/test_a.py"
    prev = {"results": [{"nodeid": f"{f}::t1", "outcome": "passed"},
                        {"nodeid": f"{f}::t2", "outcome": "timeout"}],
            "context_replayed": [f]}
    fails = [{"nodeid": f"{f}::t2", "deadline": True, "notrun": True}]
    ctx = SimpleNamespace(durations={}, warnings=[])
    plan = {"targets": {f: {f"{f}::t2"}}}
    pt._replay_file_context(ctx, plan, prev, fails)
    assert not plan.get("ordered") and plan["context_unverified"] == [f"{f}::t2"]
    plan = {"targets": {f: {f"{f}::t2"}}}
    pt._replay_file_context(ctx, plan, {**prev, "context_replayed": []}, fails)
    assert plan["ordered"] and plan["context_replayed"] == [f]


def test_per_file_key_ignores_head(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend" / "tests").mkdir(parents=True)
    (repo / "backend" / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    ctx = SimpleNamespace(repo=repo, python=sys.executable)
    files = ["backend/tests/test_a.py"]
    k1 = pt._l2_file_keys(ctx, files, [], None, "aaaa")
    k2 = pt._l2_file_keys(ctx, files, [], None, "bbbb")
    assert k1 == k2
