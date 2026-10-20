"""Review/commit attribution and the small pipeline gaps found 2026-10-18 (run_1a309783).

Each test pins one acceptance criterion of the run:

    AC1  stage-start links the CURRENT session to the run whose stage it enters, so a
         session that created a newer run still credits reviewer markers to the run it
         is working on; a re-entry writes nothing else to run.json.
    AC2  run-commit's already-committed path credits only commits whose message names
         the run id (a sibling commit touching the same file is not credited), and
         ``run-commit --sha`` records a landed commit without any ``git add`` after
         checking it is reachable, newer than the run, names the run, and touches a
         files_touched path (paths normalised per repo).
    AC3  a sub-agent whose first EXPERT-ID line names a registered expert with
         ``spawn_as: adversarial`` is adversarial whatever the rest of the prompt says;
         D7's refusal lists experts that ran this run but left only a plain marker.
    AC4  REVIEW validation warns, naming the ids, when runtime_patterns omits an RP
         that --patterns-for selects for the run's changed files.
    AC5  the python-stdin test-gate deny is unchanged (fail-closed); its message says
         to write the code to a file and run that file.
    AC6  pipeline_test.select_backend picks the skill / expert linters and the rule
         preservation suite when a changed file is under backend/skills/ or experts/.
    AC7  stage-complete on a stage with no started_at measures from the latest earlier
         stage's completed_at.
    AC8  the legacy push_ready cap judges on the incoming record's own exit_evidence,
         and run-update --stage-json joins exit_evidence lists like stage-complete.
    AC9  the orchestrator counts a surface batch as emitted only when the marker was
         written (or the batch had no source rows); write_marker takes the run id from
         the run directory.
    AC10 cli-reference.md documents E_STAGE_NOT_OPEN and --outputs-surfaced.
"""
from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_stage_boundary_commands import (  # noqa: F401 — `ws` is a fixture
    DECISION, PROJECT, _make_run, _plan_artifact, _run_state, _snapshot, _start,
    _complete, cli, ws,
)

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CLI_REF = (BACKEND / "skills" / "s_autonomous-pipeline" / "stages" / "ref"
           / "instructions" / "cli-reference.md")


# ── AC1 ──────────────────────────────────────────────────────────────────────
def test_stage_start_links_current_session_to_run(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_link1"
    _make_run(root, rid, "bugfix", stages=["evaluate"], session_id="worker-session")
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "worker-session")
    seen = []
    import core.runtime_hooks as rh
    monkeypatch.setattr(rh, "write_session_run_map", lambda s, r, ts=None: seen.append((s, r)))

    assert _start(reg, capsys, rid, "think")[0] == 0
    assert seen == [("worker-session", rid)]
    # re-entry: the link is written again, run.json is not touched
    before = _snapshot(root)
    assert _start(reg, capsys, rid, "think")[0] == 0
    assert seen[-1] == ("worker-session", rid)
    assert _snapshot(root) == before


@pytest.mark.parametrize("extra", [{"session_id": "someone-else"}, {"status": "completed"}])
def test_stage_start_does_not_link_foreign_or_finished_run(ws, capsys, monkeypatch, extra):
    root, reg = ws
    rid = "run_link3"
    _make_run(root, rid, "bugfix", stages=["evaluate"], **extra)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "worker-session")
    seen = []
    import core.runtime_hooks as rh
    monkeypatch.setattr(rh, "write_session_run_map", lambda s, r, ts=None: seen.append((s, r)))
    _start(reg, capsys, rid, "think")
    assert seen == []


def test_stage_start_without_session_writes_no_link(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_link2"
    _make_run(root, rid, "bugfix", stages=["evaluate"])
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    seen = []
    import core.runtime_hooks as rh
    monkeypatch.setattr(rh, "write_session_run_map", lambda s, r, ts=None: seen.append((s, r)))
    assert _start(reg, capsys, rid, "think")[0] == 0
    assert seen == []


# ── AC2 ──────────────────────────────────────────────────────────────────────
def _git(repo, *a, env=None):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                          timeout=15, env=env)


def _init_repo(tmp_path, name="r"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("v1\n")
    (repo / "g.py").write_text("g1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _short(repo, rev="HEAD"):
    return _git(repo, "rev-parse", "--short", rev).stdout.strip()


@pytest.fixture
def cws(tmp_path):
    (tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _write_run(ws_root, rid, files, created="2026-07-04T00:00:00+00:00"):
    d = ws_root / "Projects" / "TestProject" / ".artifacts" / "runs" / rid
    d.mkdir(parents=True, exist_ok=True)
    state = {"id": rid, "project": "TestProject", "requirement": "Fix the thing",
             "profile": "bugfix", "status": "running", "created_at": created,
             "stages": [{"stage": "deliver", "status": "completed", "push_ready": True},
                        {"stage": "reflect", "status": "recorded", "lessons": ["[pitfall] a durable lesson long enough to count"]}],
             "files_touched": files}
    (d / "run.json").write_text(json.dumps(state), encoding="utf-8")
    return d / "run.json"


def _commit_cli(ws_root, rid, sha=None, capsys=None):
    args = Namespace(project="TestProject", run_id=rid, force=False, sha=sha)
    out = {}
    with patch("scripts.artifact_cli._get_workspace", return_value=ws_root):
        try:
            cli.cmd_run_commit(args, reg=None)
        except SystemExit as e:
            out["exit"] = e.code
    if capsys is not None:
        cap = capsys.readouterr()
        out["text"] = cap.out + cap.err
    return out


def test_lookup_credits_only_commits_naming_the_run(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("sibling\n")
    _git(repo, "commit", "-qam", "another session's change to f.py")
    sibling = _short(repo)
    (repo / "f.py").write_text("mine\n")
    _git(repo, "commit", "-qam", "fix f\n\nRun: run_mine1")
    mine = _short(repo)
    got = cli._lookup_already_committed(str(repo), [str(repo / "f.py")],
                                        "2020-01-01T00:00:00+00:00", "run_mine1")
    shas = {c["sha"] for c in got}
    assert mine in shas
    assert sibling not in shas


def test_lookup_without_run_id_match_credits_nothing(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("x\n")
    _git(repo, "commit", "-qam", "hand commit without the id")
    assert cli._lookup_already_committed(str(repo), [str(repo / "f.py")],
                                         "2020-01-01T00:00:00+00:00", "run_none1") == []


def test_already_committed_message_names_the_run_id_fix(cws, tmp_path, capsys):
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("x\n")
    _git(repo, "commit", "-qam", "hand commit without the id")
    rf = _write_run(cws, "run_noid", [str(repo / "f.py")])
    out = _commit_cli(cws, "run_noid", capsys=capsys)
    assert not json.loads(rf.read_text()).get("commits")
    assert "run_noid" in out["text"] and "commit message" in out["text"]


def test_run_commit_sha_records_landed_commit_without_add(cws, tmp_path, capsys):
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("mine\n")
    _git(repo, "commit", "-qam", "fix f\n\nRun: run_sha1")
    mine = _short(repo)
    # a foreign uncommitted hunk in the same file must stay out of any commit
    (repo / "f.py").write_text("mine\nforeign\n")
    head_before = _short(repo)
    rf = _write_run(cws, "run_sha1", [str(repo / "f.py")])
    out = _commit_cli(cws, "run_sha1", sha=mine, capsys=capsys)
    assert out.get("exit") in (None, 0), out
    commits = json.loads(rf.read_text()).get("commits") or []
    assert any(c.get("sha", "").startswith(mine) or mine.startswith(c.get("sha", "-"))
               for c in commits), commits
    # no commit made by this call carries f.py (only deliverable B may land)
    new = _git(repo, "rev-list", f"{head_before}..HEAD").stdout.split()
    for c in new:
        assert "f.py" not in _git(repo, "show", "--name-only", "--format=", c).stdout.split()
    assert "foreign" not in _git(repo, "show", "HEAD:f.py").stdout
    assert "foreign" in (repo / "f.py").read_text()
    assert "f.py" not in _git(repo, "diff", "--cached", "--name-only").stdout


@pytest.mark.parametrize("case", ["no_run_id", "not_touching", "before_run", "not_ancestor",
                                  "unknown"])
def test_run_commit_sha_refuses_bad_sha(cws, tmp_path, capsys, case):
    repo = _init_repo(tmp_path)
    rid = "run_shabad"
    created = "2026-07-04T00:00:00+00:00"
    if case == "no_run_id":
        (repo / "f.py").write_text("x\n")
        _git(repo, "commit", "-qam", "no id here")
        sha = _short(repo)
    elif case == "not_touching":
        (repo / "g.py").write_text("x\n")
        _git(repo, "commit", "-qam", f"Run: {rid}")
        sha = _short(repo)
    elif case == "before_run":
        (repo / "f.py").write_text("x\n")
        env = {**os.environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
               "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        _git(repo, "commit", "-qam", f"Run: {rid}", env=env)
        sha = _short(repo)
    elif case == "not_ancestor":
        _git(repo, "checkout", "-qb", "side")
        (repo / "f.py").write_text("x\n")
        _git(repo, "commit", "-qam", f"Run: {rid}")
        sha = _short(repo)
        _git(repo, "checkout", "-q", "-")
    else:
        sha = "deadbeef"
    rf = _write_run(cws, rid, [str(repo / "f.py")], created=created)
    out = _commit_cli(cws, rid, sha=sha, capsys=capsys)
    assert out.get("exit") == 2, (case, out)
    assert not json.loads(rf.read_text()).get("commits")


# ── AC3 ──────────────────────────────────────────────────────────────────────
def test_registered_adversarial_expert_id_is_adversarial():
    from core.runtime_hooks import _is_adversarial_intent
    plain = "EXPERT-ID: security\nRead your instructions from /tmp/x/security.md."
    assert _is_adversarial_intent("general-purpose", "", plain) is True
    assert _is_adversarial_intent("general-purpose", "",
                                  "EXPERT-ID: not-an-expert\nRead /tmp/y.md") is False
    # the same extractor as attribution: a malformed first EXPERT-ID line is no id,
    # and a later well-formed one cannot take its place
    assert _is_adversarial_intent("general-purpose", "",
                                  "EXPERT-ID: Security!\nEXPERT-ID: security") is False
    assert _is_adversarial_intent("general-purpose", "", "summarise the doc") is False


def test_empty_expert_library_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    """A packaged build without experts/ must not fall back to keywords silently."""
    import logging
    import core.expert_manager as em
    import core.runtime_hooks as rh
    monkeypatch.setattr(em, "_EXPERTS_ROOT", tmp_path / "experts")
    monkeypatch.setattr(em, "_default_manager", em.ExpertManager(tmp_path / "experts"))
    monkeypatch.setattr(rh, "_ADV_EXPERTS_CACHE", [None])
    with caplog.at_level(logging.WARNING, logger="core.runtime_hooks"):
        assert rh._registered_adversarial_experts() == frozenset()
    assert "no spawn_as: adversarial expert" in caplog.text


def test_build_script_bundles_the_expert_library():
    text = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text(encoding="utf-8")
    assert "datas += [('experts', 'experts')]" in text


def test_d7_refusal_lists_experts_that_ran_without_adversarial_marker(tmp_path):
    from tests.test_run_commit_d7 import _audit_dir, _run_cli, _seed_run, _write_marker
    rid = "run_d7plain"
    ws_, _repo, _rd = _seed_run(tmp_path, rid, ["correctness", "security"])
    audit = _audit_dir(tmp_path)
    _write_marker(audit, "sessX", 1, "correctness", rid)
    (audit / "session_sessX_2.marker").write_text(json.dumps(
        {"ts": 2, "event": "SubagentStop", "session_id": "sessX", "run_id": rid,
         "expert_id": "security"}))
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws_))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    assert r.returncode == 2
    blob = r.stdout + r.stderr
    line = next(ln for ln in blob.splitlines() if '"blocked"' in ln)
    data = json.loads(line)
    assert data["missing_experts"] == ["security"]
    assert data["ran_without_adversarial_marker"] == ["security"]
    assert "EXPERT-ID was read" in data["error"] and "restart" in data["error"]


# ── AC4 ──────────────────────────────────────────────────────────────────────
def _expected_rp_ids(paths):
    import importlib.util
    sp_path = BACKEND / "skills" / "s_autonomous-pipeline" / "scripts" / "spawn_prompt.py"
    spec = importlib.util.spec_from_file_location("sp_for_test", sp_path)
    sp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sp)
    _h, _rows, hits = sp._select(sp.REVIEW_PATTERNS_PATH.read_text(encoding="utf-8"),
                                 paths, "")
    return [r["id"] for r in hits]


def test_review_warns_when_selected_rp_has_no_verdict():
    import pipeline_validator as pv
    files = [str(REPO / "backend" / "scripts" / "artifact_cli.py")]
    ids = _expected_rp_ids(["backend/scripts/artifact_cli.py"])
    assert len(ids) >= 2
    dropped = ids[-1]
    art = {"runtime_patterns": {"checked": len(ids) - 1, "patterns": [
        {"pattern": f"{i} checked", "status": "pass", "detail": "looked at the code path"}
        for i in ids[:-1]]}}
    warns = pv._check_semantic_depth("review", art, {"files_touched": files}, "P", "run_x")
    hit = [w for w in warns if "no verdict" in w]
    assert hit and dropped in hit[0]

    art["runtime_patterns"]["patterns"].append(
        {"pattern": f"{dropped} checked", "status": "N/A", "detail": "not applicable here"})
    warns = pv._check_semantic_depth("review", art, {"files_touched": files}, "P", "run_x")
    assert not [w for w in warns if "no verdict" in w]


# ── AC5 ──────────────────────────────────────────────────────────────────────
def test_stdin_gate_judges_what_the_code_can_do():
    """cee0e33b: stdin code is a test run only when it names a runner AND can run code
    (an import beyond pathlib / json / re / text modules, or exec / eval)."""
    from core.security_hooks import _classify_pipeline_test_command
    edit = "python3 - <<'EOF'\nopen('/tmp/x.md','w').write('run pytest here')\nEOF"
    assert _classify_pipeline_test_command(edit)[0] == []          # text only: allowed
    run = "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['pytest', '-q'])\nEOF"
    assert "python stdin pytest" in _classify_pipeline_test_command(run)[0]


def test_stdin_deny_message_names_the_pathlib_workaround():
    from core.security_hooks import _test_gate_hint
    hint = _test_gate_hint(["python stdin pytest"])
    assert "use pathlib only" in hint
    assert "runner above" in hint and "not a script file" not in hint
    assert _test_gate_hint(["pytest"]) == ""


# ── AC6 ──────────────────────────────────────────────────────────────────────
_LINT_SUITES = {"backend/tests/test_lint_skills_body.py", "backend/tests/test_lint_skills_size.py",
                "backend/tests/test_lint_experts.py", "backend/tests/test_check_rule_preservation.py"}


@pytest.mark.parametrize("changed", [
    "backend/skills/s_autonomous-pipeline/stages/plan.md",
    "backend/experts/code/security.md"])
def test_select_backend_adds_lint_suites_for_skill_or_expert_change(changed):
    import pipeline_test as pt
    sel, _skipped, _ = pt.select_backend(REPO, [changed], {}, 200)
    assert _LINT_SUITES <= set(sel), sel


def test_select_backend_no_lint_suites_for_unrelated_change():
    import pipeline_test as pt
    sel, _skipped, _ = pt.select_backend(REPO, ["docs/CODEBASE_METRICS.md"], {}, 200)
    assert not (_LINT_SUITES & set(sel))
    assert not (set(pt._CONTEXT_SUITES) & set(sel))


def test_select_backend_adds_context_suites_for_a_governed_context_change():
    import pipeline_test as pt
    sel, _skipped, _ = pt.select_backend(REPO, ["backend/context/STEERING.md"], {}, 200)
    assert set(pt._CONTEXT_SUITES) <= set(sel), sel


# ── AC7 ──────────────────────────────────────────────────────────────────────
def test_stage_without_started_at_measures_from_previous_completed_at(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_infer1"
    _make_run(root, rid, "bugfix", stages=["evaluate", "think"], stage_timing={
        "evaluate": {"started_at": "2026-01-01T00:00:00+00:00",
                     "completed_at": "2026-01-01T00:05:00+00:00"},
        "think": {"started_at": "2026-01-01T00:05:01+00:00",
                  "completed_at": "2026-01-01T00:09:00+00:00"}})
    seen = []
    monkeypatch.setattr(cli, "_measure_stage_tokens",
                        lambda sid, s, e, **kw: seen.append(s) or None)
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert seen == ["2026-01-01T00:09:00+00:00"]


# ── AC8 ──────────────────────────────────────────────────────────────────────
def _run_update(ws_root, rid, stage_json):
    args = Namespace(project="TestProject", run_id=rid, status=None, stage_json=stage_json,
                     taste_decision=None, profile=None, ddd_checksums=None,
                     files_touched=None, add_required_expert=None, force_checkpoint=False)
    with patch("scripts.artifact_cli._get_workspace", return_value=ws_root):
        cli.cmd_run_update(args, reg=None)


def test_legacy_cap_judges_incoming_exit_evidence_only(cws, monkeypatch):
    rid = "run_cap1"
    d = cws / "Projects" / "TestProject" / ".artifacts" / "runs" / rid
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "project": "TestProject", "profile": "bugfix", "status": "running",
        "created_at": "2026-07-04T00:00:00+00:00", "requirement": "x",
        "stages": [{"stage": "deliver", "status": "completed", "push_ready": False,
                    "exit_evidence": {"X1": "old attempt's evidence"}}]}))
    import pipeline_validator as pv
    seen = []

    def _judge(crit, run_dir, record, **kw):
        seen.append(dict(record))
        return {"met": False, "unmet": [], "results": []}
    monkeypatch.setattr(pv, "judge_exit_criteria", _judge)
    _run_update(cws, rid, json.dumps({"stage": "deliver", "status": "completed",
                                      "push_ready": True, "stage_doc_consumed": True}))
    assert seen, "cap must judge the record"
    assert "exit_evidence" not in seen[-1]


def test_run_update_stage_json_joins_exit_evidence_lists(cws):
    rid = "run_norm1"
    d = cws / "Projects" / "TestProject" / ".artifacts" / "runs" / rid
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "project": "TestProject", "profile": "bugfix", "status": "running",
        "created_at": "2026-07-04T00:00:00+00:00", "requirement": "x", "stages": []}))
    _run_update(cws, rid, json.dumps({"stage": "think", "status": "recorded",
                                      "exit_evidence": {"X1": ["a", " ", "b"]}}))
    rec = json.loads((d / "run.json").read_text())["stages"][-1]
    assert rec["exit_evidence"] == {"X1": "a; b"}


# ── AC9 ──────────────────────────────────────────────────────────────────────
def test_surface_counts_as_emitted_only_when_marked_or_no_source_rows():
    from core.ui_actions import surface_counts_as_emitted
    src = [{"kind": "source-final", "path": "/x.py"}]
    assert surface_counts_as_emitted(True, src) is True
    assert surface_counts_as_emitted(False, src) is False
    assert surface_counts_as_emitted(False, [{"kind": "report", "path": "/r.md"}]) is True


def test_failed_marker_write_lets_next_surface_re_emit(tmp_path, monkeypatch):
    """A batch whose marker could not be written is not counted as emitted: the next
    surface_run_outputs call for the same commits builds and emits again."""
    import asyncio
    from tests.test_orchestrator_surface_wiring import (
        _drive, _make_orchestrator, _make_result_message, _marker_run, _surface_tool_use)
    _marker_run(tmp_path, monkeypatch, [{"sha": "c1", "files": ["b.py"]}])
    calls = []

    def _build(rid, workspace_root=None):
        calls.append(rid)
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)
    monkeypatch.setattr("core.ui_actions.record_surface", lambda snap, ev: False)
    orch = _make_orchestrator()
    _events, raised = asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-b"),
        _make_result_message()]))
    assert raised is None
    assert calls == ["run_mk", "run_mk"]


@pytest.mark.parametrize("commits", [[{"sha": "c1", "files": ["b.py"]}], []])
def test_failed_marker_write_after_a_good_one_re_emits(tmp_path, monkeypatch, commits):
    """A marked batch, then a failed one: the third call still re-emits, also for a run
    with no commits (where the emitted-shas comparison cannot force it)."""
    import asyncio
    from tests.test_orchestrator_surface_wiring import (
        _drive, _make_orchestrator, _make_result_message, _marker_run, _surface_tool_use)
    _marker_run(tmp_path, monkeypatch, commits)
    calls, marks = [], iter([True, False])

    def _build(rid, workspace_root=None):
        calls.append(rid)
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)
    monkeypatch.setattr("core.ui_actions.record_surface", lambda snap, ev: next(marks, True))
    # a new commit lands after call 1, so call 2 builds; call 2's marker fails
    import json as _json
    d = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_mk"
    orig = _build

    def _build2(rid, workspace_root=None):
        out = orig(rid, workspace_root)
        if len(calls) == 1:
            (d / "run.json").write_text(_json.dumps({"id": "run_mk", "commits": commits + [
                {"sha": "c9", "files": ["b.py"]}]}), encoding="utf-8")
        return out
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build2)
    orch = _make_orchestrator()
    _events, raised = asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-b"),
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-c"),
        _make_result_message()]))
    assert raised is None
    assert calls == ["run_mk", "run_mk", "run_mk"]


def test_workspace_repo_lookup_keeps_old_bounds(tmp_path):
    """The daemon workspace repo is committed by auto-commit sweeps that never name a
    run: there the lookup does not require the run id (else workspace runs could
    never complete)."""
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("swept\n")
    _git(repo, "commit", "-qam", "chore: chore (3), framework (1)")
    got = cli._lookup_already_committed(str(repo), [str(repo / "f.py")],
                                        "2020-01-01T00:00:00+00:00", "run_ws1",
                                        require_run_id=False)
    assert got and got[0]["sha"] == _short(repo)


def test_run_commit_in_workspace_repo_credits_auto_commit(cws, tmp_path, capsys):
    repo = _init_repo(tmp_path)
    (repo / "f.py").write_text("swept\n")
    _git(repo, "commit", "-qam", "chore: chore (3)")
    rf = _write_run(cws, "run_ws2", [str(repo / "f.py")])
    with patch("scripts.artifact_cli._rev_parse_toplevel", return_value=str(repo)):
        _commit_cli(cws, "run_ws2", capsys=capsys)
    assert json.loads(rf.read_text()).get("commits")


def test_write_marker_takes_run_id_from_run_directory(tmp_path):
    from core.surface_marker import MARKER_NAME, write_marker
    rd = tmp_path / "run_dirid"
    rd.mkdir()
    rj = rd / "run.json"
    rj.write_text(json.dumps({"id": "run_other", "commits": [{"sha": "abc1234"}]}))
    assert write_marker(rj, [{"kind": "source-final", "path": "/a.py"}],
                        commits=[{"sha": "abc1234"}]) is True
    data = json.loads((rd / MARKER_NAME).read_text())
    assert data["run_id"] == "run_dirid"
    assert data["commits"] == ["abc1234"]


# ── AC10 ─────────────────────────────────────────────────────────────────────
def test_cli_reference_documents_new_codes_and_flags():
    text = CLI_REF.read_text(encoding="utf-8")
    assert "E_STAGE_NOT_OPEN" in text
    assert "--outputs-surfaced" in text
    assert "--sha" in text
