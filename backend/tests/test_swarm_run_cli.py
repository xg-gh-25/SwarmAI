"""``pipeline-v1 child …``: a lead run's commands on its children, and the merge tool
(design docs/design/2026-10-31-swarm-run.md §5.1-§5.4, §6; impl
docs/design/2026-11-03-swarm-run-impl.md steps 4-5; invariants I1, I2, I8, I16).

A real v1 run in a temporary workspace (tests/pipeline_v1_rig); the daemon is a fake
``child_cli._http`` that records what it was asked and answers like the routes do.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.run_protocol import child_cli, child_merge, cli_commands
from core.run_protocol.request_ctx import Request, active
from tests.pipeline_v1_rig import PROJECT, SESSION, Rig

OTHER = "bbbbbbbb-1111-2222-3333-444444444444"
URL = "https://github.com/acme/repo.git"

PLAN = {"children": [
    {"wp": "W1", "title": "parser", "task": "write the parser", "acceptance": "parser tests pass",
     "execution": "native", "returns": "commits", "why": "one module"},
    {"wp": "W2", "title": "docs", "task": "document the parser", "acceptance": "doc explains it",
     "execution": "native", "returns": "document", "after": ["W1"]},
]}


def _g(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True).stdout.strip()


class FakeDaemon:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.rows: list[dict] = []
        self.prepared: dict[str, dict] = {}
        self.n = 0

    def __call__(self, method, path, body=None, timeout=120):
        self.calls.append((method, path, body or {}))
        if path == "/runs":
            return {"runs": self.rows}
        if path == "/spec":
            return {"spec": {"revision": "rev1"}, "refusals": []}
        if path == "/dispatch":
            self.n += 1
            did = f"so_{self.n:012x}"
            self.rows.append({"dispatch_id": did, "parent_run": body["parent_run"], "wp": body["wp"],
                              "title": body["title"], "hive": "h1", "stage": "running", "state": "running",
                              "repos": [{"name": "repo", "url": URL, "access": "write", "sha": None}]})
            return {"dispatch_id": did, "hive": "h1"}
        if path.endswith("/return/prepare"):
            return self.prepared[path.split("/")[2]]
        return {"ok": True}


@pytest.fixture
def run(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, profile="direct")
    rig.create()
    fake = FakeDaemon()
    monkeypatch.setattr(child_cli, "_http", fake)
    return rig, fake


def _exec(rig, *argv, session=SESSION, stdin=None):
    args = cli_commands.parser().parse_args(["child", "--project", PROJECT, "--run-id", rig.run_id, *argv])
    with active(Request(session_id=session, stdin=stdin)):
        return cli_commands.execute(args)


# ── the plan and dispatching ────────────────────────────────────────────────

def test_only_the_run_owner_gives_child_commands(run):
    rig, fake = run
    out = _exec(rig, "status", session=OTHER)
    assert out["code"] == "not_owner" and fake.calls == []


def test_a_plan_is_checked_whole_and_kept_in_the_run(run):
    rig, _ = run
    out = _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    assert out["ok"] and [c["wp"] for c in out["plan"]] == ["W1", "W2"]
    assert json.loads((rig.engine().run_dir / "children.json").read_text())["children"][1]["after"] == ["W1"]
    for bad, word in ((dict(PLAN, children=[PLAN["children"][0], PLAN["children"][0]]), "unique"),
                      ({"children": [{**PLAN["children"][0], "acceptance": ""}]}, "acceptance is required"),
                      ({"children": [{**PLAN["children"][1]}]}, "not in the plan"),
                      ({"children": [{**PLAN["children"][0], "hive": "x"}]}, "unknown field")):
        assert word in _exec(rig, "plan", "--file", "-", stdin=json.dumps(bad))["error"]


def test_dispatching_sends_the_standard_route_with_the_run_and_records_each_child(run):
    """I1: the dispatch is the standard /spec then /dispatch, plus parent_run and wp; the
    run's log names which dispatch carries which package, never its state (I16)."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    out = _exec(rig, "dispatch", "W1", "W2")
    assert out["ok"] and [d["wp"] for d in out["dispatched"]] == ["W1", "W2"]
    sent = [b for m, p, b in fake.calls if p == "/dispatch"]
    assert {b["wp"] for b in sent} == {"W1", "W2"}
    assert all(b["parent_run"] == rig.run_id and b["session"] == SESSION and b["revision"] == "rev1"
               and b["brain"] == PROJECT for b in sent)
    kinds = [e for e in rig.engine().store.read_events() if e["kind"] == "child_dispatched"]
    assert sorted(e["payload"]["wp"] for e in kinds) == ["W1", "W2"]
    assert not any(e["kind"].startswith("child_") and e["kind"] != "child_dispatched"
                   for e in rig.engine().store.read_events())
    # a package that went out is corrected, not sent again as new
    assert "went out already" in _exec(rig, "dispatch", "W1")["error"]
    assert "--note-file" in _exec(rig, "dispatch", "--again", "W1")["error"]
    assert "still has an open child" in _exec(rig, "dispatch", "--again", "W1", "--note-file", "-",
                                              stdin="use the new API")["error"]
    fake.rows[0]["stage"] = "closed"
    again = _exec(rig, "dispatch", "--again", "W1", "--note-file", "-", stdin="use the new API")
    assert again["ok"] and again["dispatched"][0]["again_of"] == fake.rows[0]["dispatch_id"]
    last = [b for m, p, b in fake.calls if p == "/dispatch"][-1]
    assert "Correction after the previous attempt" in last["task"] and "use the new API" in last["task"]


def test_status_lists_the_children_from_swarm_out_and_what_is_not_sent(run):
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    fake.rows[0]["lead_actions"] = [{"key": "accept", "variant": "child"}, {"key": "rerun"}]
    fake.rows.append({"dispatch_id": "so_ffffffffffff", "parent_run": "run_other", "wp": "W9"})
    out = _exec(rig, "status")
    assert [c["wp"] for c in out["children"]] == ["W1"]                # only this run's
    assert out["children"][0]["commands"] == ["accept:child", "rerun"]
    assert out["not_dispatched"] == [{"wp": "W2", "title": "docs", "after": ["W1"]}]


def test_commands_go_only_to_this_runs_children_by_the_agent(run):
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    did = fake.rows[0]["dispatch_id"]
    fake.rows.append({"dispatch_id": "so_ffffffffffff", "parent_run": "run_other", "wp": "W9"})
    assert "not a child of" in _exec(rig, "stop", "so_ffffffffffff", "--why", "w")["error"]
    _exec(rig, "accept", did, "--why", "W1 is what the run needs", "--ended-at", "t1")
    m, p, b = fake.calls[-1]
    assert (p, b["by"], b["session"], b["value"], b["ended_at"]) == (f"/runs/{did}/verdict", "agent", SESSION, "pass", "t1")
    _exec(rig, "rerun", did, "--why", "w", "--text-file", "-", stdin="fix the edge case")
    assert fake.calls[-1][1] == f"/runs/{did}/rerun" and fake.calls[-1][2]["text"] == "fix the edge case"


# ── the merge tool (I8) ─────────────────────────────────────────────────────

def _child_commit(repo: Path, base: str, files: dict[str, str], ref: str) -> str:
    """A child's commit on *base* in the host repo, as return-prepare leaves it."""
    wt = repo.parent / ("child-" + ref.split("/")[2])
    _g(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    for name, text in files.items():
        (wt / name).parent.mkdir(parents=True, exist_ok=True)
        (wt / name).write_text(text)
    _g(wt, "add", "-A")
    _g(wt, "commit", "-qm", "child")
    tip = _g(wt, "rev-parse", "HEAD")
    _g(repo, "update-ref", ref, tip)
    return tip


def _prepared(rig, fake, did, files, protected=()):
    base = _g(rig.repo, "rev-parse", "main")
    tip = _child_commit(rig.repo, base, files, f"refs/hive/{did}/k1")
    fake.prepared[did] = {"base": base, "tip": tip, "repo": str(rig.repo), "key": "k1",
                          "files": [{"path": p} for p in files], "protected_files": list(protected)}
    return tip


def test_merge_chains_children_on_the_run_snapshot_and_apply_keeps_the_worktrees_edits(run):
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1", "W2")
    d1, d2 = fake.rows[0]["dispatch_id"], fake.rows[1]["dispatch_id"]
    wt = Path(next(iter(rig.engine().effective_repos().values())))
    (wt / "mine.txt").write_text("the lead run's own contract\n")          # untracked, the run's own
    main_before = _g(rig.repo, "rev-parse", "main")
    _prepared(rig, fake, d1, {"a.py": "print('a')\n", ".aidlc/deliverables/run_child/REPORT.md": "r"})
    _prepared(rig, fake, d2, {"b.md": "doc\n"})
    out = _exec(rig, "merge", d1, d2)
    assert out["ok"], out
    chain = out["merged"][-1]["chain"]
    tree = _g(rig.repo, "ls-tree", "-r", "--name-only", chain)
    assert {"a.py", "b.md", "mine.txt"} <= set(tree.split()) and ".aidlc/deliverables/run_child/REPORT.md" not in tree
    assert _g(rig.repo, "rev-parse", "main") == main_before                  # main never moves
    assert not (wt / "a.py").exists()                                         # merge does not touch the worktree
    (wt / "mine.txt").write_text("edited after the merge\n")                  # an edit the chain has not taken
    head = _g(wt, "rev-parse", "HEAD")
    out = _exec(rig, "apply")
    assert out["ok"], out
    assert (wt / "a.py").read_text() == "print('a')\n" and (wt / "b.md").exists()
    assert (wt / "mine.txt").read_text() == "edited after the merge\n"        # not overwritten
    assert _g(wt, "rev-parse", "HEAD") == head and _g(wt, "diff", "--cached", "--name-only") == ""
    assert _g(rig.repo, "rev-parse", "main") == main_before


def test_merge_refuses_git_hooks_and_reports_a_conflict_without_changing_the_chain(run):
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1", "W2")
    d1, d2 = fake.rows[0]["dispatch_id"], fake.rows[1]["dispatch_id"]
    _prepared(rig, fake, d1, {".pre-commit-config.yaml": "repos: []\n"}, protected=[".pre-commit-config.yaml"])
    out = _exec(rig, "merge", d1)
    assert not out["ok"] and out["merged"][0]["refused"] and "run code on its own" in out["merged"][0]["detail"]
    _prepared(rig, fake, d2, {"x.txt": "one\n"})
    assert _exec(rig, "merge", d2)["ok"]
    chain = child_merge.rw.snapshot_ref(rig.run_id, child_merge._label("chain", "repo"))
    tip = _g(rig.repo, "rev-parse", chain)
    fake.rows.append({"dispatch_id": "so_eeeeeeeeeeee", "parent_run": rig.run_id, "wp": "W3",
                      "repos": [{"name": "repo", "url": URL, "access": "write"}]})
    _prepared(rig, fake, "so_eeeeeeeeeeee", {"x.txt": "two\n"})
    out = _exec(rig, "merge", "so_eeeeeeeeeeee")
    assert not out["ok"] and out["merged"][0]["conflicts"] == ["x.txt"]
    assert _g(rig.repo, "rev-parse", chain) == tip                             # left as it was


def test_a_child_can_start_from_the_runs_worktree_as_it_is_now(run):
    """§7 point 1: the lead run writes a contract first and its children start from it."""
    rig, fake = run
    plan = {"children": [{**PLAN["children"][0], "base": ["@worktree"]}]}
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(plan))
    wt = Path(next(iter(rig.engine().effective_repos().values())))
    (wt / "contract.py").write_text("def parse(text): ...\n")                   # untracked, the contract
    head = _g(wt, "rev-parse", "HEAD")
    out = _exec(rig, "dispatch", "W1")
    assert out["ok"], out
    sent = [b for m, p, b in fake.calls if p == "/dispatch"][-1]
    ref = sent["base"][0]
    assert ref.startswith(f"refs/swarm/snapshots/{rig.run_id}/base-")
    assert "contract.py" in _g(rig.repo, "ls-tree", "-r", "--name-only", ref).split()
    assert _g(rig.repo, "rev-parse", f"{ref}^") == head                        # on the run's base
    assert _g(wt, "rev-parse", "HEAD") == head and _g(wt, "status", "--porcelain") == "?? contract.py"


def test_next_names_the_open_children_and_report_lists_what_the_lead_run_did(run):
    """§5.2 / §5.8: BUILD's next step says which children are still open; REPORT lists
    each child, how it ended and the lead run's decisions on it (its decision record)."""
    from core import swarm_out, swarm_out_record
    from core.run_protocol import audit
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    did = fake.rows[0]["dispatch_id"]
    swarm_out._append(json.dumps({"dispatch_id": did, "parent_run": rig.run_id, "wp": "W1"}) + "\n")
    out = _exec(rig, "status")
    assert out["next"]["children"] == {"open": 1, "work_packages": ["W1"]}
    assert "child status" in out["next"]["why"]
    swarm_out_record.append_quietly(did, "agent", "accept", "W1 meets its acceptance", SESSION, "accept", None, {})
    from core import swarm_out_settle
    swarm_out_settle._update(did, lambda rec: rec.update(handled_at="2026-11-03T00:00:00Z"))
    assert "children" not in _exec(rig, "status")["next"]                    # nothing open now
    value = rig.engine().build_audit(through="DELIVER", persist=False)
    assert value["details"]["children"][0]["outcome"] == "handled"
    text = audit.render(value, fmt="report")
    assert "## Hive children (Swarm Run)" in text and "W1 meets its acceptance" in text
    assert did.replace("_", "\\_") in text                                  # markdown-escaped
