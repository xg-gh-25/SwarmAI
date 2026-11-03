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
    assert ref == f"refs/swarm/snapshots/{rig.run_id}/b1-repo"
    assert "contract.py" in _g(rig.repo, "ls-tree", "-r", "--name-only", ref).split()
    assert _g(rig.repo, "rev-parse", f"{ref}^") == head                        # on the run's base
    assert _g(wt, "rev-parse", "HEAD") == head and _g(wt, "status", "--porcelain") == "?? contract.py"
    # C: a later batch starts from a new ref; the first child's start keeps its own
    first = _g(rig.repo, "rev-parse", ref)
    (wt / "contract.py").write_text("def parse(text): return []\n")
    later = child_cli.worktree_base(rig.engine())[0]
    assert later == f"refs/swarm/snapshots/{rig.run_id}/b2-repo"
    assert _g(rig.repo, "rev-parse", ref) == first and _g(rig.repo, "rev-parse", later) != first


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


# ── the Swarm Run card (§5.9, I10, I12) ─────────────────────────────────────

def test_the_run_card_comes_once_from_the_call_that_sent_the_first_children(run):
    import time as _time
    from core import swarm_out, swarm_out_card, swarm_run_card
    rig, _ = run
    cmd = f'"$SWARM_BACKEND_BIN" pipeline-v1 child --project {PROJECT} --run-id {rig.run_id} dispatch W1'
    since = _time.time()
    assert swarm_out_card.cards(cmd, "{}", since) == []                       # nothing went out
    now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(since + 1))
    swarm_out._append(json.dumps({"dispatch_id": "so_000000000001", "parent_run": rig.run_id, "wp": "W1",
                                  "project": PROJECT, "sent_at": now}) + "\n")
    blocks = swarm_out_card.cards(cmd, "{}", since)
    assert blocks == [{"type": "swarm_run", "runId": rig.run_id, "project": PROJECT}]
    # a later dispatch shows in the same card: no second one
    assert swarm_run_card.cards(cmd.replace("W1", "W2"), "{}", since + 3600) == []
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "swarm_run" in _TOOL_ONLY_TYPES                                    # never in the model's context


def test_the_run_card_head_is_the_lead_run_and_its_packages_not_sent(run):
    from core import swarm_out, swarm_run
    rig, _ = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    swarm_out._append(json.dumps({"dispatch_id": "so_000000000001", "parent_run": rig.run_id, "wp": "W1",
                                  "project": PROJECT}) + "\n")
    v = swarm_run.view(rig.run_id)
    assert (v["project"], v["profile"], v["status"], v["children"], v["planned"]) == (PROJECT, "direct", "active", 1, 2)
    assert v["not_dispatched"] == [{"wp": "W2", "title": "docs", "after": ["W1"]}]
    assert v["children_cost_usd"] is None                                     # no ledger rows: unknown, not 0
    assert swarm_run.view("run_ffffffffffff") is None


def test_only_the_person_stops_a_whole_swarm_run(monkeypatch, tmp_path):
    """§5.9: the person stops the whole run; each child it stopped keeps the person's stop
    in its decision record (as the single-run stop does), a child it did not stop none."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core import swarm_out, swarm_out_record, swarm_out_settle, swarm_run, session_registry
    from routers import swarm_out as route
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)       # the real record, kept here
    run_id = "run_0123456789ab"
    monkeypatch.setattr(swarm_run, "lead", lambda r: swarm_run.Lead(run_id, PROJECT, SESSION, "active", "build")
                        if r == run_id else None)
    rows = [{"dispatch_id": "so_000000000001", "parent_run": run_id, "state": "running"},
            {"dispatch_id": "so_000000000002", "parent_run": run_id, "state": "stopped"},
            {"dispatch_id": "so_000000000003", "parent_run": "run_other", "state": "running"},
            {"dispatch_id": "so_000000000004", "parent_run": run_id, "state": "waiting"}]

    async def runs(_age):
        return {"runs": rows}
    stopped = []

    async def stop(did):
        if did == "so_000000000004":
            raise swarm_out.SwarmOutError("the Hive did not answer")
        stopped.append(did)
        return {"stopped": True}

    class _Router:
        async def interrupt_session(self, sid):
            stopped.append(sid)
            return {"success": True}
    monkeypatch.setattr(swarm_out, "list_runs", runs)
    monkeypatch.setattr(swarm_out, "stop_run", stop)
    monkeypatch.setattr(session_registry, "session_router", _Router())
    monkeypatch.setattr(route, "_require_desktop", lambda: None)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/swarm-out")
    c = TestClient(app)
    assert c.post(f"/api/swarm-out/swarm-runs/{run_id}/stop", json={"by": "agent", "session": SESSION, "why": "w"}).status_code == 403
    assert all(swarm_out_settle.load(f"so_00000000000{i}") == {} for i in range(1, 5))  # the agent's 403: nothing kept
    r = c.post(f"/api/swarm-out/swarm-runs/{run_id}/stop", json={"by": "person"})
    assert r.status_code == 200
    assert r.json() == {"stopped": ["so_000000000001"], "not_stopped": ["so_000000000004"], "lead_turn_stopped": True}
    assert stopped == ["so_000000000001", SESSION]
    kept = swarm_out_record.entries(swarm_out_settle.load("so_000000000001"))
    assert [(e["by"], e["decision"]) for e in kept] == [("person", "stop")]
    assert swarm_out_record.person_decisions(swarm_out_settle.load("so_000000000001"), by_agent=True) == 1
    for other in ("so_000000000002", "so_000000000003", "so_000000000004"):   # not running / not this run / not stopped
        assert swarm_out_record.entries(swarm_out_settle.load(other)) == []
    assert c.post("/api/swarm-out/swarm-runs/run_ffffffffffff/stop", json={"by": "person"}).status_code == 404


def test_a_research_run_fans_out_from_think_without_a_worktree(tmp_path, monkeypatch):
    """D7's aim, a research run's questions sent to Hives: child dispatch is not tied to a
    code stage; a research run (no worktree) sends children that return documents."""
    rig = Rig(tmp_path, monkeypatch, profile="research", with_repo=False)
    rig.create()
    fake = FakeDaemon()
    monkeypatch.setattr(child_cli, "_http", fake)
    plan = {"children": [{"wp": "Q1", "title": "prior art", "task": "survey the prior art", "acceptance": "a sourced report",
                          "execution": "native", "returns": "document"}]}
    assert _exec(rig, "plan", "--file", "-", stdin=json.dumps(plan))["ok"]
    out = _exec(rig, "dispatch", "Q1")
    assert out["ok"], out
    assert rig.engine().projection()["stage"] in ("evaluate", "think")
    assert [b["returns"] for m, p, b in fake.calls if p == "/dispatch"] == ["document"]
    # its next step says a child is still out, not only "every check passes" (E2E finding)
    from core import swarm_out
    rig.drive(until="think")
    swarm_out._append(json.dumps({"dispatch_id": fake.rows[0]["dispatch_id"], "parent_run": rig.run_id,
                                  "wp": "Q1"}) + "\n")
    nxt = _exec(rig, "status")["next"]
    assert nxt["children"] == {"open": 1, "work_packages": ["Q1"]} and "child status" in nxt["why"]


# ── the first review's fixes (impl §4: F, G, H, I, J, M) ────────────────────

def test_a_package_named_twice_goes_out_once_and_a_sent_one_stays_in_the_plan_as_sent(run):
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    out = _exec(rig, "dispatch", "W1", "W1")
    assert out["ok"] and len([1 for m, p, b in fake.calls if p == "/dispatch"]) == 1
    # W1 went out: it may be retitled, not removed or given other work
    w1, w2 = PLAN["children"]
    assert "stays in the plan" in _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [
        {**w2, "after": []}]}))["error"]
    assert "its task stay as sent" in _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [
        {**w1, "task": "something else"}, w2]}))["error"]
    assert _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [{**w1, "title": "the parser"}]}))["ok"]
    # a correction tells the daemon it is one (core/swarm_run.check_child_dispatch)
    fake.rows[0]["stage"] = "closed"
    _exec(rig, "dispatch", "--again", "W1", "--note-file", "-", stdin="fix it")
    assert [b.get("again") for m, p, b in fake.calls if p == "/dispatch"] == [None, True]
    assert all(b.get("parent_run") == rig.run_id for m, p, b in fake.calls if p == "/spec")


def test_every_child_command_fails_with_an_error(run, monkeypatch):
    """M: a daemon refusal (``detail``) and a CLI refusal (``error``) read the same."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    did = fake.rows[0]["dispatch_id"]
    real = fake.__call__

    def refuse(method, path, body=None, timeout=120):
        if path.endswith("/stop"):
            return {"ok": False, "status": 403, "detail": "an agent may not stop this run now: x"}
        return real(method, path, body, timeout)
    monkeypatch.setattr(child_cli, "_http", refuse)
    out = _exec(rig, "stop", did, "--why", "w")
    assert out["ok"] is False and out["error"] == "an agent may not stop this run now: x"


def _deliverable_rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, profile="direct",
              extra={".aidlc/deliverables/run_old/REPORT.md": "the old report\n",
                     ".aidlc/deliverables/run_old/NOTES.md": "notes\n"})
    rig.create()
    fake = FakeDaemon()
    monkeypatch.setattr(child_cli, "_http", fake)
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1", "W2")
    return rig, fake


def test_merge_keeps_the_deliverables_as_the_chain_has_them(tmp_path, monkeypatch):
    """G: a child that changes or deletes a deliverable the run's base has brings neither in."""
    rig, fake = _deliverable_rig(tmp_path, monkeypatch)
    d1 = fake.rows[0]["dispatch_id"]
    base = _g(rig.repo, "rev-parse", "main")
    wt = rig.repo.parent / "child-x"
    _g(rig.repo, "worktree", "add", "-q", "--detach", str(wt), base)
    (wt / ".aidlc/deliverables/run_old/REPORT.md").write_text("rewritten by the child\n")
    (wt / ".aidlc/deliverables/run_old/NOTES.md").unlink()
    (wt / "a.py").write_text("x = 1\n")
    _g(wt, "add", "-A")
    _g(wt, "commit", "-qm", "child")
    tip = _g(wt, "rev-parse", "HEAD")
    _g(rig.repo, "update-ref", f"refs/hive/{d1}/k1", tip)
    fake.prepared[d1] = {"base": base, "tip": tip, "repo": str(rig.repo), "key": "k1",
                         "files": [{"path": "a.py"}], "protected_files": []}
    out = _exec(rig, "merge", d1)
    assert out["ok"], out
    chain = out["merged"][0]["chain"]
    assert _g(rig.repo, "show", f"{chain}:.aidlc/deliverables/run_old/REPORT.md") == "the old report"
    assert _g(rig.repo, "show", f"{chain}:.aidlc/deliverables/run_old/NOTES.md") == "notes"
    assert _g(rig.repo, "show", f"{chain}:a.py") == "x = 1"


def test_one_childs_git_failure_is_its_own_and_the_rest_merge(run, monkeypatch):
    """H: a git error on one child is reported for it; the next is still put on the chain."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1", "W2")
    d1, d2 = fake.rows[0]["dispatch_id"], fake.rows[1]["dispatch_id"]
    _prepared(rig, fake, d1, {"a.py": "a\n"})
    _prepared(rig, fake, d2, {"b.py": "b\n"})
    real = child_merge._merge_tree
    calls = []

    def flaky(wt, base, ours, theirs):
        calls.append(theirs)
        if len(calls) == 1:
            raise child_merge.MergeError("git merge-tree failed: disk")
        return real(wt, base, ours, theirs)
    monkeypatch.setattr(child_merge, "_merge_tree", flaky)
    out = _exec(rig, "merge", d1, d2)
    assert [m["ok"] for m in out["merged"]] == [False, True] and "disk" in out["merged"][0]["detail"]
    assert "b.py" in _g(rig.repo, "ls-tree", "-r", "--name-only", out["merged"][1]["chain"]).split()


def test_apply_refuses_when_the_worktree_changes_while_the_chain_is_merged(run, monkeypatch):
    """I: an edit made while the chain was being merged is never overwritten."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    d1 = fake.rows[0]["dispatch_id"]
    _prepared(rig, fake, d1, {"a.py": "a\n"})
    assert _exec(rig, "merge", d1)["ok"]
    wt = Path(next(iter(rig.engine().effective_repos().values())))
    real = child_merge._worktree_tree
    n = []

    def editing(w, run_id):
        n.append(1)
        if len(n) == 2:
            (wt / "late.txt").write_text("written while merging\n")
        return real(w, run_id)
    monkeypatch.setattr(child_merge, "_worktree_tree", editing)
    out = _exec(rig, "apply")
    assert not out["ok"] and "changed while" in out["applied"][0]["detail"]
    assert not (wt / "a.py").exists() and (wt / "late.txt").read_text() == "written while merging\n"


def test_report_lists_a_child_whose_event_was_never_written(run):
    """J: REPORT reads Swarm Out's dispatch log; the run's log only adds again_of."""
    from core import swarm_out
    rig, _ = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    swarm_out._append(json.dumps({"dispatch_id": "so_000000000077", "parent_run": rig.run_id, "wp": "W2",
                                  "hive": "h2"}) + "\n")
    value = rig.engine().build_audit(through="DELIVER", persist=False)
    kids = value["details"]["children"]
    assert [(c["dispatch_id"], c["wp"], c["hive"], c["outcome"]) for c in kids] == \
        [("so_000000000077", "W2", "h2", "open")]



# ── the second review's fixes (impl §5: Q, U) ───────────────────────────────

def test_a_package_is_marked_sent_before_it_goes_and_the_plan_keeps_it(run, monkeypatch):
    """Q: while a dispatch is on its way (no row, no event yet), and when the daemon does
    not answer, the plan cannot change what was sent; a plain refusal frees it again."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    w1, w2 = PLAN["children"]
    seen = {}
    real = fake.__call__

    def mid_dispatch(method, path, body=None, timeout=120):
        if path == "/dispatch" and "plan" not in seen:
            # the dispatch is on its way: a plan written now must keep W1 as sent
            seen["plan"] = _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [
                {**w1, "task": "something else"}, w2]}))
        return real(method, path, body, timeout)
    monkeypatch.setattr(child_cli, "_http", mid_dispatch)
    assert _exec(rig, "dispatch", "W1")["ok"]
    assert "stay as sent" in seen["plan"]["error"]
    # the daemon is down: what went out is still known
    monkeypatch.setattr(child_cli, "_http", lambda *a, **k: {"ok": False, "status": None, "detail": "down"})
    assert "stays in the plan" in _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [
        {**w2, "after": []}]}))["error"]
    # a plain refusal sent nothing: W2 may change again
    monkeypatch.setattr(child_cli, "_http", lambda m, p, b=None, timeout=120: (
        {"ok": False, "status": 400, "detail": "refused"} if p == "/dispatch" else real(m, p, b, timeout)))
    assert not _exec(rig, "dispatch", "W2")["ok"]
    monkeypatch.setattr(child_cli, "_http", fake)
    assert _exec(rig, "plan", "--file", "-", stdin=json.dumps({"children": [w1, {**w2, "task": "a new task"}]}))["ok"]


def test_apply_names_the_paths_that_changed_during_the_lay_in(run, monkeypatch):
    """U: an edit in the last moment before the lay-in is reported, not silently lost."""
    rig, fake = run
    _exec(rig, "plan", "--file", "-", stdin=json.dumps(PLAN))
    _exec(rig, "dispatch", "W1")
    d1 = fake.rows[0]["dispatch_id"]
    _prepared(rig, fake, d1, {"a.py": "a\n"})
    assert _exec(rig, "merge", d1)["ok"]
    wt = Path(next(iter(rig.engine().effective_repos().values())))
    real = child_merge.rw._out

    def out(cwd, *args, **kw):
        res = real(cwd, *args, **kw)
        if args[:2] == ("read-tree", "--reset"):
            (wt / "a.py").write_text("an edit in the last moment\n")
        return res
    monkeypatch.setattr(child_merge.rw, "_out", out)
    got = _exec(rig, "apply")
    assert not got["ok"] and got["applied"][0]["differs"] == ["a.py"]


def test_the_runs_snapshot_commits_need_no_git_identity(tmp_path):
    """A fresh Hive or a CI runner has no git identity: the chain's and a child base's
    commits are the run's own snapshots and carry a fixed one (E2E finding)."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "init")
    _g(repo, "config", "user.useConfigOnly", "true")     # no identity may be guessed here
    head = _g(repo, "rev-parse", "HEAD")
    tree = _g(repo, "rev-parse", "HEAD^{tree}")
    sha = child_merge._commit(repo, tree, head, "snapshot")
    assert _g(repo, "log", "-1", "--format=%an <%ae>", sha) == "swarm-run <run@swarm.local>"


def test_a_runs_review_snapshot_needs_no_git_identity_either(tmp_path):
    """The run's review snapshot (run_worktree.snapshot_commit) is one of its own commits
    too: on a Hive without a git identity a child pipeline run reaches REVIEW."""
    from core import run_worktree as rw
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "init")
    _g(repo, "config", "user.useConfigOnly", "true")
    (repo / "a.txt").write_text("b\n")
    snap = rw.snapshot_commit(repo, "run_0123456789ab", "r1")
    assert _g(repo, "log", "-1", "--format=%an <%ae>", snap["sha"]) == "swarm-run <run@swarm.local>"
