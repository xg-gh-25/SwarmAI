"""Recovery, REVIEW, and landing integration tests for Pipeline v1."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from core.run_protocol import events as ev, git_tree, landing, lifecycle
from core.run_protocol.engine import EngineError, IntegrationRequired, RunEngine
from tests.review_bridge_helper import ReviewHookBridge
from core.run_protocol.review import ReviewOrchestrator
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(path, text="old\n"):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "a@b.c")
    _git(path, "config", "user.name", "T")
    (path / "a.py").write_text(text)
    (path / "validations.json").write_text(json.dumps({
        "schema": 1, "entries": [{"id": "docs", "paths": ["docs/**"],
        "cwd": ".", "argv": ["true"], "call_type": "lint"}]}))
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return path


def _requirements(readings=None, resolution="rewritten"):
    return {
        "requirements": [{"id": "R1", "story": "As a user", "acceptance": [{
            "id": "R1.1", "pattern": "event", "condition": "a request arrives",
            "response": "return a result", "negative": False, "universal": False}]}],
        "analysis": [{"kind": "missing_edge", "refs": ["R1.1"], "note": "edge",
                      "resolution": resolution}],
        "clarifications": [],
        "understanding": {"claim": "implement result", "readings": readings or ["one"],
                          "evidence": ["a.py:1"], "root_cause": None},
        "prior_knowledge": [],
    }


def _engine(tmp_path, monkeypatch, profile="full"):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects" / "P").mkdir(parents=True)
    repo = _repo(tmp_path / "repo")
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    return RunEngine.create(data_dir=data, workspace=workspace, project="P",
                            profile=profile, session_id="session_1",
                            repos={"repo": str(repo)}, run_id="run_one",
                            requirement="test change")


def test_gate0_is_the_executable_evaluate_gate_and_retries_once(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    requirements = _requirements(["one", "two"])
    first = engine.gate0_start(requirements)
    assert first["required"] and first["round"] == 1
    bridge = ReviewHookBridge(engine.store, "session_1")
    prompt = first["handout"]["prompt"]
    bound = bridge.track_tool_use("tool-1", prompt)
    assert bound
    bridge.record_answer(bound, "VERDICT: WRONG-FRAME\nroot is a symptom",
                         completed_at=AT)
    blocked = engine.stage_complete(requirements)
    assert blocked["gate0"] == "needs_round_2"
    second = engine.gate0_start()
    assert second["round"] == 2
    prompt = second["handout"]["prompt"]
    bound = bridge.track_tool_use("tool-2", prompt)
    assert bound
    bridge.record_answer(bound, "VERDICT: SUPPORTED\nevidence a.py:1",
                         completed_at="2026-10-22T00:01:00+00:00")
    assert engine.stage_complete(requirements)["ok"] is True


def test_review_handoff_derives_minimal_r2_from_address_events(tmp_path):
    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    review = ReviewOrchestrator(store, tmp_path / "run")
    bridge = ReviewHookBridge(store, "session_1")
    handout = review.handout(expert_id="correctness", stage="review", round=1,
                             attempt=1, at=AT, expert_instructions="Check",
                             review_input="diff")
    bound = bridge.track_tool_use("tool-r1", handout["prompt"])
    assert bound
    bridge.record_answer(
        bound,
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":1,'
        '"claim":"crash","fix":"guard"}', completed_at=AT)
    finding_id = review.findings()[0]["canonical_id"]
    handoff = review.record_fix_handoff(
        {"pass": 1, "items": [{"finding_id": finding_id, "hunk": "@@ fixed"}]},
        at=AT, attempt=1)
    assert handoff["addressed"] == [finding_id]
    assert review.r2_roster() == {"correctness": [finding_id]}
    text = review.r2_input("correctness", [finding_id], hunks=handoff["hunks"],
                           proofs=handoff["proofs"],
                           reclassifications=handoff["reclassifications"])
    assert "R2 ONLY" in text and finding_id in text


def test_decision_answer_resumes_same_stage_and_run_land_refuses_fake_oracle(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    requirements = _requirements(resolution="asked")
    engine.gate0_start(requirements)
    decision = {
        "request_id": "q1", "kind": "requirement_ambiguity",
        "question": "Which behavior?", "why_evidence_insufficient": "both fit",
        "refs": ["A1"], "options": [
            {"id": "a", "choice": "one", "consequence": "x",
             "reversible": True, "risk": "low"},
            {"id": "b", "choice": "two", "consequence": "y",
             "reversible": True, "risk": "low"}]}
    assert engine.request_decision(decision, document="REQUIREMENTS")["state"]["status"] == "awaiting_human"
    answered = engine.answer_decision(request_id="q1", option_id="b")
    assert answered["state"]["status"] == "active"
    assert answered["answer"]["choice"] == "two"
    with pytest.raises(EngineError, match="no land_request 'land_1'"):
        engine.execute_land_request("land_1", message="land")


def _event(authority, rows, kind, payload, *, stage=None, attempt=None, ordinal=None):
    event, _ = ev.make_event(
        writer=authority.writer(ev.EVENT_SPECS[kind].producer if isinstance(
            ev.EVENT_SPECS[kind].producer, str) else ev.EVENT_SPECS[kind].producer[0]),
        run_id="run_one", kind=kind, at=AT, payload=payload, stage=stage,
        attempt=attempt, ordinal=ordinal,
        prev_hash=ev.event_digest(rows[-1]) if rows else None)
    rows.append(event)
    state = lifecycle.fold(rows, authority, allow_pending_transition=True)
    if state.get("expected_transition"):
        _event(authority, rows, "transition", state["expected_transition"])
    return event


def test_restore_completed_not_landed_reopens_deliver():
    authority, rows = ev.EventAuthority(), []
    _event(authority, rows, "run_create",
           {"run_id": "run_one", "profile": "direct", "decision_cap": 2})
    run = lifecycle.fold(rows, authority)
    # Construct the terminal shape through the public event fold.
    for stage in ("build", "review"):
        from core.run_protocol import checks
        roster = [item.check_id for item in checks.stage_checks("direct", stage)]
        opened = _event(authority, rows, "stage_attempt", {"check_ids": roster},
                        stage=stage, attempt=1)
        for check_id in roster:
            _event(authority, rows, "check", {"stage_attempt_event_id": opened["event_id"],
                   "check_id": check_id, "passed": True, "detail": "pass"})
        _event(authority, rows, "stage_complete",
               {"stage_attempt_event_id": opened["event_id"]}, stage=stage, attempt=1)
    from core.run_protocol import checks
    roster = [item.check_id for item in checks.stage_checks("direct", "test")]
    opened = _event(authority, rows, "stage_attempt", {"check_ids": roster},
                    stage="test", attempt=1)
    for check_id in roster:
        _event(authority, rows, "check", {"stage_attempt_event_id": opened["event_id"],
               "check_id": check_id, "passed": True, "detail": "pass"})
    _event(authority, rows, "stage_complete",
           {"stage_attempt_event_id": opened["event_id"]},
           stage="test", attempt=1)
    _event(authority, rows, "land", {"landing": "not_landed", "commit": None,
                                     "reason": "checks_exhausted"}, ordinal=1)
    roster = ["DV-1"]          # the not-landed path's only DELIVER check (§A1.2)
    opened = _event(authority, rows, "stage_attempt", {"check_ids": roster},
                    stage="deliver", attempt=1)
    for check_id in roster:
        _event(authority, rows, "check", {"stage_attempt_event_id": opened["event_id"],
               "check_id": check_id, "passed": True, "detail": "pass"})
    _event(authority, rows, "stage_complete",
           {"stage_attempt_event_id": opened["event_id"]}, stage="deliver", attempt=1)
    _event(authority, rows, "reflect_complete", {}, ordinal=2)
    assert lifecycle.fold(rows, authority)["status"] == "completed"
    _event(authority, rows, "restore", {"snapshot_exists": True}, ordinal=3)
    state = lifecycle.fold(rows, authority)
    assert state["status"] == "paused" and state["stage"] == "deliver"
    assert state["landing"] is None and state["stages"]["reflect"] == "pending"


def test_multi_repo_landing_persists_partial_progress_and_retries(tmp_path, monkeypatch):
    roots, bases, bindings = {}, {}, {}
    for name in ("a", "b"):
        repo = _repo(tmp_path / name)
        base = _git(repo, "rev-parse", "HEAD")
        (repo / "a.py").write_text(f"{name}-new\n")
        _git(repo, "add", "a.py")
        tree = _git(repo, "write-tree")
        _git(repo, "reset", "--hard", "-q", base)
        roots[name], bases[name] = str(repo), base
        bindings[name] = {"delivery_tree": tree}
    progress = tmp_path / "landing-progress.json"
    original = landing._fast_forward
    def fail_second(root, commit):
        if root == roots["b"]:
            raise landing.LandingError("synthetic second-repo failure")
        return original(root, commit)
    monkeypatch.setattr(landing, "_fast_forward", fail_second)
    with pytest.raises(landing.PartialLanding, match="synthetic"):
        landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                             bindings=bindings, message="land", progress_path=progress)
    saved = json.loads(progress.read_text())
    assert set(saved["commits"]) == {"a"}
    monkeypatch.setattr(landing, "_fast_forward", original)
    commits = landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                                   bindings=bindings, message="land", progress_path=progress)
    assert set(commits) == {"a", "b"}
    assert all(_git(roots[name], "rev-parse", "HEAD") == sha for name, sha in commits.items())


def test_a_moved_main_rebases_the_worktree_and_needs_a_new_test_report(tmp_path, monkeypatch):
    """S6-1: no carried final. Main moved: the landing rebases the run's worktree onto it
    (``_sync_moved_bases``); the code tree changes, so the TEST report on the old tree no
    longer binds (LG-1) until the agent tests again and reports (``report_tests``)."""
    from core.run_protocol import checks
    engine = _engine(tmp_path, monkeypatch, profile="direct")
    root = __import__("pathlib").Path(engine.ensure_worktrees()["repo"])
    (root / "a.py").write_text("run change\n")
    report = {"reports": [{"repo": "repo", "rationale": "covers a.py", "tests_changed": [],
                           "commands": [{"command": "make test", "cwd": ".", "result": "pass"}]}]}
    engine._record_validation_reports("test", report)
    main = __import__("pathlib").Path(engine.contract["repo_roots"]["repo"])
    (main / "docs.md").write_text("unrelated\n")
    _git(main, "add", "docs.md")
    _git(main, "commit", "-qm", "docs")
    new_base = _git(main, "rev-parse", "HEAD")
    engine._sync_moved_bases()
    assert engine._fork_base("repo", str(root)) == new_base
    assert (root / "docs.md").read_text() == "unrelated\n"
    assert (root / "a.py").read_text() == "run change\n"
    ok, detail = checks.evaluate("LG-1", engine.check_context())
    assert not ok and "test again" in detail
    engine._record_validation_reports("test", report)
    ok, detail = checks.evaluate("LG-1", engine.check_context())
    # the report binds again; this hand-built run never completed REVIEW, so the only
    # remaining LG-1 problem is S6-2's review binding (real_e2e covers a reviewed run)
    assert not ok and "test again" not in detail and "never reviewed" in detail, detail


def test_accept_finding_requires_the_persons_approval_and_the_owner(tmp_path, monkeypatch):
    from core.run_protocol import findings

    engine = _engine(tmp_path, monkeypatch, profile="direct")
    orchestrator = ReviewOrchestrator(engine.store, engine.run_dir)
    bridge = ReviewHookBridge(engine.store, "session_1")
    handout = orchestrator.handout(
        expert_id="correctness", stage="review", round=1, attempt=1,
        at=AT, expert_instructions="Check", review_input="diff")
    bound = bridge.track_tool_use("tool-accept", handout["prompt"])
    assert bound
    bridge.record_answer(
        bound,
        '{"severity":"HIGH","kind":"behavior","file":"a.py","line":1,'
        '"claim":"crash","fix":"guard"}', completed_at=AT)
    canonical_id = orchestrator.findings()[0]["canonical_id"]
    prepared = engine.prepare_authorized_action(
        action="accept_finding", target=canonical_id, request_id="auth_accept")
    assert prepared["request"]["bindings"]["canonical_id"] == canonical_id
    with pytest.raises(IntegrationRequired, match="no calling session"):
        engine.execute_authorized_action("auth_accept")

    from core.run_protocol import landing   # the daemon, after the person approved
    landing.approve_from_event(engine.store, "auth_accept",
                               now=__import__("time").time(), at="2026-10-23T00:00:00Z")
    from core.run_protocol import request_ctx
    with request_ctx.active(request_ctx.Request(session_id="session_1")):
        engine.execute_authorized_action("auth_accept")
    row = findings.fold(engine.store.read_events(), engine.authority)[0]
    assert row["status"] == "accepted"


def test_discard_snapshot_requires_authorized_snapshot_identity(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, profile="direct")
    root = __import__("pathlib").Path(engine.ensure_worktrees()["repo"])
    (root / "a.py").write_text("unlanded\n")
    abandoned = engine.abandon(reason="test retention")
    ledger = abandoned["retention"][0]["ledger"]
    assert ledger and __import__("pathlib").Path(ledger).is_dir()
    engine.prepare_authorized_action(
        action="discard_snapshot", target="repo", request_id="auth_discard")

    from core.run_protocol import landing   # the daemon, after the person approved
    landing.approve_from_event(engine.store, "auth_discard",
                               now=__import__("time").time(), at="2026-10-23T00:00:00Z")
    from core.run_protocol import request_ctx
    with request_ctx.active(request_ctx.Request(session_id="session_1")):
        result = engine.execute_authorized_action("auth_discard")
    assert result["action"] == "discard_snapshot"
    assert not __import__("pathlib").Path(ledger).exists()


def test_a_snapshot_copied_to_another_repo_has_no_identity(tmp_path):
    """discard-snapshot signs (snapshot_id, repo_name); the ledger is found from the
    run and repo, and a meta.json copied from repo A under repo B is refused."""
    import json as _json
    from core.run_protocol import auth, retention
    ledger = tmp_path / "ledger" / "run_one"
    for name in ("a", "b"):
        (ledger / name).mkdir(parents=True)
    meta = _json.dumps({"run_id": "run_one", "repo_name": "a", "snapshot": "1" * 40})
    (ledger / "a" / "meta.json").write_text(meta)
    (ledger / "b" / "meta.json").write_text(meta)
    assert retention.snapshot_id(ledger / "a")
    with pytest.raises(retention.RetentionError, match="another run or repo"):
        retention.snapshot_id(ledger / "b")
    with pytest.raises(auth.AuthorizationError):
        auth.normalize_bindings("discard_snapshot", {"snapshot_id": "x"})
