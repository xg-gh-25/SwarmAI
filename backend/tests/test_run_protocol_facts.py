"""Unit tests of core/run_protocol/facts.py (conformance fixing spec K1).

Methodology: pure functions over constructed rows/events plus tiny real git repos.
Invariants: a closure binds its own repo's current tree; refs resolve or are refused;
a base is named from the recorded branch or not at all.
"""
from __future__ import annotations

import subprocess

import pytest

from core.run_protocol import facts


def _proof(event_id, tree, repo=None):
    payload = {"code_tree": tree}
    if repo is not None:
        payload["repo"] = repo
    return {"event_id": event_id, "kind": "proof", "payload": payload}


def _row(cid, status="refuted", proof="p1"):
    return {"canonical_id": cid, "status": status, "evidence_event_id": proof,
            "severity": "HIGH"}


def test_a_closure_binds_its_own_repos_tree_not_any_repos():
    """Review round 2: tree ids can coincide across repos; repo A's old tree being
    repo B's current tree does not keep A's closure."""
    events = [_proof("p1", "t" * 40, repo="a")]
    trees = {"a": "u" * 40, "b": "t" * 40}
    assert facts.stale_closures([_row("CF-1")], events, trees) == {"CF-1"}
    assert facts.stale_closures([_row("CF-1")], events, {"a": "t" * 40, "b": "u" * 40}) == set()


def test_a_proof_without_repo_binds_only_in_a_one_repo_run():
    events = [_proof("p1", "t" * 40)]
    assert facts.stale_closures([_row("CF-1")], events, {"a": "t" * 40}) == set()
    assert facts.stale_closures([_row("CF-1")], events,
                                {"a": "t" * 40, "b": "t" * 40}) == {"CF-1"}


def test_an_open_finding_is_not_a_stale_closure():
    assert facts.stale_closures([_row("CF-1", status="open")], [], {"a": None}) == set()


def _repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / "a.py").write_text("x = 1\ny = 2\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "i"], check=True)
    tree = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                          capture_output=True, text=True).stdout.strip()
    return root, tree


@pytest.mark.parametrize("ref", ["x", "a.py:9", "b.py:1", "/etc/passwd:1", "../a.py:1",
                                 "event:nope", "test:t.py::t"])
def test_unresolvable_refs_are_refused(tmp_path, ref):
    root, tree = _repo(tmp_path)
    with pytest.raises(facts.FactError):
        facts.resolve_refs([ref], root=root, code_tree=tree, event_ids={"e1"},
                           final_nodes={})


def test_resolvable_refs_record_what_they_point_at(tmp_path):
    root, tree = _repo(tmp_path)
    out = facts.resolve_refs(["a.py:1-2", "event:e1", "test:t.py::t"], root=root,
                             code_tree=tree, event_ids={"e1"}, final_nodes={"t.py::t": "pass"})
    assert [row["kind"] for row in out] == ["path", "event", "test"]
    assert out[0]["lines"] == [1, 2] and len(out[0]["blob"]) == 40


def test_a_worktree_base_needs_its_recorded_branch(tmp_path):
    root, _tree = _repo(tmp_path)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", "run/x", str(wt)],
                   check=True)
    with pytest.raises(facts.FactError):
        facts.resolve_base(wt, root, None)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    assert facts.resolve_base(wt, root, "refs/heads/main") == head
    assert facts.resolve_base(root, root, None) == head          # shared: the checkout
