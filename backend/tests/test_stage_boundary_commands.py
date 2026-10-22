"""Stage-boundary commands: ``artifact_cli.py stage-start`` / ``stage-complete``.

WHAT IS TESTED (pipeline-speed-systematic-fix §4-A, F2, G1, C4, C7)
- stage-complete validates EVERYTHING before writing; any failure prints one
  structured JSON line, exits 1 and leaves artifact files, manifest.json and
  run.json byte-identical. ``--check`` never writes.
- Every existing gate still blocks through stage-complete (missing required
  artifact field, invalid litmus, adversarial tier 'skipped' on a strict profile,
  stage-order violation) — the checks are validate_artifact_data + validate() run
  on the in-memory candidate, not a copy.
- Derived fields (F2): stage_doc_consumed, status, artifact_id,
  consumed_artifacts, token_cost (C5 measured / estimate / unavailable),
  files_changed from files_touched, findings counts, test layers/regressions from
  runner evidence (evidence wins; source recorded), push_ready upper bound.
- G1 exit contract: structure required at strict plan stage-complete (not on the
  legacy publish path); judging per kind test / probe / mutation / manual.
- direct profile (plan → build → deliver) end to end + its completion gate.
- DELIVER stage-complete generates a missing REPORT.md in-process (auto flag),
  then advances (docs → reflect) or leaves COMPLETE needing no run-report call
  (direct); a hand-written report is kept; a failed generation does not advance.
- goal_cycle is an advanceable state (PIPELINE_STATES derived from the profiles):
  a goal PLAN stage-complete moves the manifest to goal_cycle.
- Structured errors (A3): code / path / expected / got for several shapes, plus
  the additive ``structured_errors`` key on the legacy publish failure.
- stage-start: card, upstream, examples, stage_timing.started_at (C4 merge),
  review/deliver changed-file scope + expert dispatch + prompt FILES.
- Adversarial-review fixes: profile change may not drop a pending stage (HIGH-1);
  push_ready bound reads every report, freshness digests, passed>0, tests_pass
  (MED-2); replan after BUILD only adds criteria and never moves the manifest
  back (MED-3); legacy run-update caps push_ready (MED-4); direct without a
  contract is unmet (MED-5); write failure is one structured line with no stub
  re-point (MED-6); non-finite record numbers are rejected (LOW).

METHODOLOGY
- Command functions are driven in-process with a SimpleNamespace args object and
  a tmp_path workspace (SWARM_WORKSPACE via monkeypatch); stdout is parsed.
- "Writes nothing" is proven by snapshotting every file under the project's
  .artifacts/ tree (path → bytes) before and after the failing call.
- Evidence files (C2/C3) are hand-written fixtures in the documented shapes.
- The git scope test builds its fixture repo with plumbing (commit-tree), so no
  commit hooks run and no git config is touched.

KEY INVARIANTS
1. ok:false ⇒ zero bytes changed on disk.
2. ok:true ⇒ record completed + stage_doc_consumed + stage_timing.completed_at,
   artifact published through the publish code path, manifest advanced to the
   profile's NEXT stage (computed), next_action carries the continuation phrase.
3. push_ready stored ≤ the gate-computed bound; the model can only lower it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.artifact_cli as cli  # noqa: E402
from core.artifact_registry import ArtifactRegistry  # noqa: E402
from core.pipeline_profiles import PIPELINE_PROFILES, is_relaxed_profile  # noqa: E402

PROJECT = "TestProject"


# ── fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A tmp workspace with one project; returns (root, registry). The CLI session id
    the test process inherits is cleared, so stage commands never record — or measure
    tokens from — the transcript of the session running the tests."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    (tmp_path / "Projects" / PROJECT / ".artifacts").mkdir(parents=True)
    return tmp_path, ArtifactRegistry(tmp_path)


def _run_dir(root: Path, run_id: str) -> Path:
    return root / "Projects" / PROJECT / ".artifacts" / "runs" / run_id


def _make_run(root: Path, run_id: str, profile: str, stages=(), **extra) -> Path:
    d = _run_dir(root, run_id)
    d.mkdir(parents=True, exist_ok=True)
    recs = [s if isinstance(s, dict) else {"stage": s, "status": "completed"} for s in stages]
    state = {"id": run_id, "run_id": run_id, "project": PROJECT, "profile": profile,
             "status": "running", "requirement": "make X work", "stages": recs,
             "created_at": "2026-01-01T00:00:00+00:00", **extra}
    (d / "run.json").write_text(json.dumps(state), encoding="utf-8")
    return d


def _run_state(root: Path, run_id: str) -> dict:
    return json.loads((_run_dir(root, run_id) / "run.json").read_text(encoding="utf-8"))


def _snapshot(root: Path) -> dict:
    """Every file under .artifacts; run.json without ``stage_complete_failures`` (the
    refused-attempt counter is the one write a refused stage-complete makes)."""
    base = root / "Projects" / PROJECT / ".artifacts"
    out = {}
    for p in base.rglob("*"):
        if not p.is_file() or p.name == ".lock":  # the run lock file the counter took
            continue
        data = p.read_bytes()
        if p.name == "run.json":
            d = json.loads(data)
            d.pop("stage_complete_failures", None)
            data = json.dumps(d, sort_keys=True).encode()
        out[str(p.relative_to(base))] = data
    return out


def _call(fn, reg, capsys, **kw):
    """Run a cmd_* function; return (exit_code, last stdout JSON line)."""
    code = 0
    try:
        fn(SimpleNamespace(**kw), reg)
    except SystemExit as e:
        code = e.code
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, (json.loads(lines[-1]) if lines else None)


def _complete(reg, capsys, run_id, stage, artifact=None, record=None, check=False):
    payload = {"record": record or {}}
    if artifact is not None:
        payload["artifact"] = artifact
    return _call(cli.cmd_stage_complete, reg, capsys, project=PROJECT, run_id=run_id,
                 stage=stage, data=json.dumps(payload), check=check)


def _start(reg, capsys, run_id, stage):
    return _call(cli.cmd_stage_start, reg, capsys, project=PROJECT, run_id=run_id, stage=stage)


def _artifact_on_disk(root: Path, artifact_id: str) -> dict:
    manifest = json.loads((root / "Projects" / PROJECT / ".artifacts" / "manifest.json").read_text())
    entry = next(e for e in manifest["artifacts"] if e["id"] == artifact_id)
    return json.loads((root / "Projects" / PROJECT / ".artifacts" / entry["file"]).read_text())


def _write_evidence(run_dir: Path, rel: str, data: dict) -> None:
    """Through the runner's writer: runs made by run-create seal their evidence."""
    import pipeline_test
    pipeline_test._write_json(run_dir / "evidence" / rel, data)


def _c2(mode, run_id, results, failures=(), layers=None, finished="2026-01-01T00:10:00+00:00"):
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        key = {"passed": "passed", "failed": "failed", "error": "errors",
               "timeout": "timeouts", "skipped": "skipped"}[r["outcome"]]
        counts[key] += 1
    return {"schema": 1, "mode": mode, "run_id": run_id, "started_at": finished,
            "finished_at": finished, "duration_s": 1.0, "cached": False,
            "selected_files": [], "skipped_for_budget": [], "counts": counts,
            "results": list(results), "failures": list(failures),
            "layers": layers or {"ac_driven": {"run": True, "pass": 1, "fail": 0},
                                 "dependency_scoped": {"run": True, "tests": 1, "pass": 1},
                                 "import_smoke": {"run": True, "modules": 1, "pass": 1}}}


DECISION = [{"description": "kept the change local", "classification": "taste"}]


# ── direct profile: plan → build → deliver end to end (items 1, 2, 7, 8) ────


# ── every existing gate still blocks; a block writes NOTHING (item 1, X1) ──


def _specialist_high(sev="HIGH", who="security", resolved=True):
    return {"severity": sev, "confidence": 9, "resolved": resolved, "specialist": who,
            "finding": "backend/a.py run() line 7: shell=True on input. Fixed: list argv."}


def _assert_degraded(root, rid, stage, needle):
    """A gate error completed the stage DEGRADED on the first try (SOUL P5: one landing
    decision): the record names the error, a degraded DELIVER is not push-ready."""
    rec = next(r for r in _run_state(root, rid)["stages"] if r.get("stage") == stage)
    errs = (rec.get("degraded") or {}).get("errors") or []
    assert any(needle in e for e in errs), rec
    if stage == "deliver":
        assert rec.get("push_ready") is False and needle in rec["push_ready_reason"]


# ── derived fields from evidence (item 2) ──────────────────────────────────


# ── push_ready upper bound (item 2) ────────────────────────────────────────


def _green_ev():
    return _c2("regression", "r", [{"nodeid": "a::b", "outcome": "passed"}])


# ── Gate-2 Red Team trigger, HIGH half (validator-enforced) ────────────────
_RT = "red_team_dispatched"
_RT_POST = "2026-10-19T00:00:00+00:00"   # after RED_TEAM_CUTOVER → enforced
_RT_PRE = "2026-10-13T01:38:37+00:00"    # before it → grandfathered (warning only)


def _rt_markers(root, monkeypatch, run_id, expert_ids, *, readable=True):
    """Point the marker store at <root>/data and write one SubagentStop marker per
    expert id for run_id. readable=False → the store dir does not exist."""
    data = root / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    if not readable:
        return
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True, exist_ok=True)
    for i, eid in enumerate(expert_ids):
        (audit / f"session_s_adv_{i}.marker").write_text(json.dumps(
            {"ts": i, "event": "SubagentStop", "run_id": run_id, "expert_id": eid}))


def _rt_publish(reg, capsys, art, run_id):
    args = SimpleNamespace(project=PROJECT, type="delivery", data=json.dumps(art),
                           producer="s_autonomous-pipeline", summary="s", topic="",
                           stage="deliver", run_id=run_id, quiet=True)
    code = 0
    try:
        cli.cmd_publish(args, reg)
    except SystemExit as e:
        code = e.code
    cap = capsys.readouterr()
    return code, [json.loads(ln) for ln in cap.err.splitlines() if ln.strip().startswith("{")]


# ── G1 exit contract: structure + judging per kind (item 7) ────────────────


# ── stage-record schema (item 5) ────────────────────────────────────────────


# ── stage-start: scope, experts, prompt files, size (item 3) ───────────────

def _git(repo: Path, *args: str, env=None) -> str:
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                       timeout=30, env={**os.environ, **(env or {})})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _fixture_repo(tmp_path: Path) -> Path:
    """A repo whose HEAD holds core/a.py — built with plumbing (no commit hooks)."""
    repo = tmp_path / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "update-index", "--add", "core/a.py")
    tree = _git(repo, "write-tree")
    ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"}
    sha = _git(repo, "commit-tree", tree, "-m", "init", env=ident)
    _git(repo, "update-ref", "HEAD", sha)
    return repo


# ── adversarial-review fixes: HIGH-1 … MED-6, LOW ──────────────────────────

def _run_update_args(run_id, **kw):
    ns = SimpleNamespace(project=PROJECT, run_id=run_id, status=None, stage_json=None,
                         taste_decision=None, profile=None, ddd_checksums=None,
                         files_touched=None, add_required_expert=None, force=False,
                         reason=None, force_checkpoint=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# ── DELIVER generates REPORT.md itself; goal_cycle is an advanceable state ──

def test_pipeline_states_cover_every_profile_stage():
    from core.artifact_registry import PIPELINE_STATES
    for name, stages in PIPELINE_PROFILES.items():
        assert set(stages) <= set(PIPELINE_STATES), name
    assert PIPELINE_STATES[:8] == tuple(PIPELINE_PROFILES["full"])  # full's order kept


# ── dispatch scope norms: source-only size, one security owner, review list ──

def _scope(files):
    """files = [(path, status, added, removed)] → a scope dict shaped like
    _changed_files_scope's output (is_test + source counts derived the same way)."""
    changed = [{"path": p, "status": s, "added": a, "removed": r,
                "is_test": cli._is_test_path(p)} for p, s, a, r in files]
    src = [c for c in changed if not c["is_test"]]
    ins = sum(c["added"] for c in changed)
    s_ins = sum(c["added"] for c in src)
    return {"changed_files": changed, "diff_insertions": ins,
            "diff_deletions": sum(c["removed"] for c in changed),
            "source_insertions": s_ins, "source_deletions": sum(c["removed"] for c in src)}


# ── H1 / H5: review rounds counted from completion markers, enforced at stage-complete ──


def _budget_run(root, rid):
    _make_run(root, rid, "direct",
              stage_timing={"plan": {"started_at": "2026-01-01T00:00:00+00:00"}})


# ── gate speed: evidence handed to the gate, not re-read by it ──


# ── transcript ids + per-response token cost (run_045b96b2) ─────────────────
# token_usage gets one row per TURN end, and a pipeline run sits inside one turn,
# so a stage window used to hold no row and every stage read "unavailable". The
# stage commands now record the CLI session id (CLAUDE_CODE_SESSION_ID) in
# run.json transcripts[] and measure the window from that transcript first.

TX_SID = "0d206ebe-9c4f-40ee-875a-491716e326b8"
TX_SID2 = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def cli_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TX_SID)
    return tmp_path / "claude" / "projects" / "-ws"


def _no_db_usage(monkeypatch):
    mod = types.ModuleType("core.run_delivery_summary")
    mod.token_usage_for_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "core.run_delivery_summary", mod)




    # (stage-complete recording the new id: test_stage_commands_record_the_transcript_id)


# ── exit_evidence lists (run_a87bfe3f) ─────────────────────────────────────────
# stages/test.md describes fixes/unfixed per item, so agents write lists; the record
# schema wants strings. 4+ runs were rejected on exactly this shape. The parse step
# joins a list of strings into one string; an empty or all-blank list becomes '' so
# a manual exit criterion it names stays unmet (never a placeholder that looks like
# evidence).


# ── one landing decision: a gate error degrades the stage on the FIRST try ──


# ── one landing decision (SOUL P5): tests decide landing; review gaps lower push_ready ──


# ── a direct run always goes through PLAN (XG 2026-10-20); BUILD may be skipped ──
