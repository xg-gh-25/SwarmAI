"""Completion summary accuracy: measured test counts and in-run "done" marks.

Two inaccuracies in what the completion summary and REPORT show:

- The test numbers came from the author-written TEST artifact (``tests_total`` /
  ``failed``), so a run with 3 pre-existing failures rendered "1218/1218 通过".
  TEST stage-complete now overwrites them from the runner's own evidence
  (``evidence/tests/latest-regression.json``): ``tests_total`` = results that ran
  (skipped excluded), ``pre_existing_failures`` / ``flaky_failures`` by
  classification, and ``failed`` = every other failing result, so ``failed`` keeps
  meaning "new failures" for METRICS.
- ``_item_done`` ignored ``mitigation`` / ``disposition``, where writers actually mark
  a risk they fixed in-run ("fixed: added a test"), so fixed risks stayed under
  遗留问题. A keyword counts only with a separator after it, so a proposal such as
  "fixed in follow-up" or "accepted-risk owner TBD" stays open.

Methodology: the TEST derive is driven through the real ``cmd_stage_complete`` on a
tmp workspace; the renderers and the predicate are called directly.
"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.artifact_cli as cli  # noqa: E402
import pipeline_validator as pv  # noqa: E402
from core import run_delivery_summary as rds  # noqa: E402
from core.artifact_registry import ArtifactRegistry  # noqa: E402

PROJECT = "TestProject"
DECISION = [{"description": "d", "classification": "mechanical", "reasoning": "r"}]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    (tmp_path / "Projects" / PROJECT / ".artifacts").mkdir(parents=True)
    return tmp_path, ArtifactRegistry(tmp_path)


def _make_run(root: Path, run_id: str) -> Path:
    d = root / "Projects" / PROJECT / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    stages = [{"stage": s, "status": "completed"} for s in ("evaluate", "think", "build", "review")]
    (d / "run.json").write_text(json.dumps({
        "id": run_id, "run_id": run_id, "project": PROJECT, "profile": "trivial",
        "status": "running", "requirement": "r", "stages": stages,
        "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    return d


def _evidence(run_dir: Path, run_id: str, counts: dict, failures: list) -> None:
    p = run_dir / "evidence" / "tests" / "latest-regression.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "schema": 1, "mode": "regression", "run_id": run_id,
        "started_at": "2026-01-01T00:10:00+00:00", "finished_at": "2026-01-01T00:10:00+00:00",
        "duration_s": 1.0, "cached": False, "selected_files": [], "skipped_for_budget": [],
        "counts": counts, "results": [], "failures": failures,
        "layers": {"ac_driven": {"run": True, "pass": 1, "fail": 0},
                   "dependency_scoped": {"run": True, "tests": 1, "pass": 1},
                   "import_smoke": {"run": True, "modules": 1, "pass": 1}}}), encoding="utf-8")


def _complete_test(reg, capsys, run_id, artifact):
    code = 0
    try:
        cli.cmd_stage_complete(SimpleNamespace(
            project=PROJECT, run_id=run_id, stage="test", check=False,
            data=json.dumps({"artifact": artifact, "record": {"decisions": DECISION}})), reg)
    except SystemExit as e:
        code = e.code
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, json.loads(lines[-1])


def _stored(root: Path, artifact_id: str) -> dict:
    base = root / "Projects" / PROJECT / ".artifacts"
    entry = next(e for e in json.loads((base / "manifest.json").read_text())["artifacts"]
                 if e["id"] == artifact_id)
    return json.loads((base / entry["file"]).read_text())


def _fail(nodeid, cls, outcome="failed"):
    return {"nodeid": nodeid, "classification": cls, "outcome": outcome, "summary": "x"}


# ── AC1: counts derived from the runner's evidence ──────────────────────────

def test_counts_overwritten_from_evidence(ws, capsys):
    root, reg = ws
    rd = _make_run(root, "run_cnt")
    _evidence(rd, "run_cnt",
              {"passed": 10, "failed": 3, "errors": 1, "timeouts": 0, "skipped": 2},
              [_fail("a::1", "pre-existing"), _fail("a::2", "pre-existing"),
               _fail("a::3", "flaky"), _fail("a::4", "regression", "error")])
    art = copy.deepcopy(pv.STAGE_TEMPLATES["test"])
    art.update(tests_total=999, failed=0)            # the author's claim
    code, out = _complete_test(reg, capsys, "run_cnt", art)
    assert code == 0, out
    stored = _stored(root, out["artifact_id"])
    assert stored["tests_total"] == 14               # 10 + 3 + 1 + 0, skipped excluded
    assert stored["pre_existing_failures"] == 2
    assert stored["flaky_failures"] == 1
    assert stored["failed"] == 1                     # only the regression
    assert out["derived"]["test_counts"]["source"] == "evidence/tests/latest-regression.json"


def test_an_author_written_tests_reused_never_survives_the_evidence(ws, capsys):
    root, reg = ws
    rd = _make_run(root, "run_reuse_claim")
    _evidence(rd, "run_reuse_claim",
              {"passed": 4, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}, [])
    art = copy.deepcopy(pv.STAGE_TEMPLATES["test"])
    art.update(tests_reused=900)                     # the author's claim; nothing was reused
    code, out = _complete_test(reg, capsys, "run_reuse_claim", art)
    assert code == 0, out
    assert "tests_reused" not in _stored(root, out["artifact_id"])


def test_quality_line_says_how_many_passes_were_reused():
    line = rds._quality_line({}, {}, {"tests_total": 1220, "failed": 0, "tests_reused": 900})
    assert "测试 1220/1220 通过" in line and "其中 900 个复用（输入未变）" in line
    assert "复用" not in rds._quality_line({}, {}, {"tests_total": 5, "failed": 0, "tests_reused": None})


def test_counts_kept_without_evidence(ws, capsys):
    root, reg = ws
    _make_run(root, "run_noev")
    art = copy.deepcopy(pv.STAGE_TEMPLATES["test"])
    art.update(tests_total=42, failed=0)
    code, out = _complete_test(reg, capsys, "run_noev", art)
    assert code == 0, out
    stored = _stored(root, out["artifact_id"])
    assert stored["tests_total"] == 42 and stored["failed"] == 0
    assert "pre_existing_failures" not in stored


def test_counts_helper_shape():
    ev = {"counts": {"passed": 1217, "failed": 3, "errors": 0, "timeouts": 0, "skipped": 0},
          "failures": [_fail(f"t::{i}", "pre-existing") for i in range(3)]}
    assert cli._test_counts_from_evidence(ev) == {
        "tests_total": 1220, "failed": 0, "pre_existing_failures": 3, "flaky_failures": 0}
    assert cli._test_counts_from_evidence(None) is None
    assert cli._test_counts_from_evidence({"counts": "bad"}) is None


def test_a_timeout_stays_failed_whatever_its_classification():
    """pipeline_test._exit_code blocks on a hang regardless of classification, so a
    pre-existing or flaky TIMEOUT is still a failure the summary must show."""
    ev = {"counts": {"passed": 5, "failed": 0, "errors": 0, "timeouts": 2, "skipped": 0},
          "failures": [_fail("t::hang1", "pre-existing", "timeout"),
                       _fail("t::hang2", "flaky", "timeout")]}
    assert cli._test_counts_from_evidence(ev) == {
        "tests_total": 7, "failed": 2, "pre_existing_failures": 0, "flaky_failures": 0}


# ── AC2 / AC3: both doors render the measured numbers ───────────────────────

def test_quality_line_names_pre_existing_and_flaky():
    line = rds._quality_line({}, {}, {"tests_total": 1220, "failed": 0,
                                      "pre_existing_failures": 3, "flaky_failures": 1})
    assert "测试 1216/1220 通过" in line
    assert "3 个既有失败" in line and "1 个 flaky" in line


def test_quality_line_never_goes_negative():
    """Hand-written counts (no evidence) may already include pre-existing in failed."""
    line = rds._quality_line({}, {}, {"tests_total": 3, "failed": 3, "pre_existing_failures": 3})
    assert "测试 0/3 通过" in line


def test_quality_line_unchanged_without_new_fields():
    line = rds._quality_line({}, {}, {"tests_total": 50, "failed": 2})
    assert "测试 48/50 通过" in line and "既有失败" not in line and "flaky" not in line


def test_report_metrics_carry_pre_existing():
    m = cli._report_test_metrics({"passed": True, "tests_total": 1220, "failed": 0,
                                  "pre_existing_failures": 3, "flaky_failures": 1})
    assert m["pre_existing_failures"] == 3 and m["flaky_failures"] == 1
    assert cli._report_tests_phrase(m) == "1220 run, 0 failed, 3 pre-existing, 1 flaky"


def test_summary_door_passes_pre_existing(ws, monkeypatch):
    seen = {}

    def fake_build(run_state, arts, run_dir, repo_root=None):
        seen.update(arts["test"])
        return "ok"

    import core.run_delivery_summary as mod
    monkeypatch.setattr(mod, "build_delivery_summary", fake_build)
    monkeypatch.setattr(cli, "_load_stage_artifacts", lambda p, r, s: {
        "test": {"passed": True, "tests_total": 1220, "failed": 0,
                 "pre_existing_failures": 3, "flaky_failures": 0}})
    root, _ = ws
    _make_run(root, "run_door")
    assert cli._delivery_summary_for(PROJECT, "run_door") == "ok"
    assert seen["pre_existing_failures"] == 3 and seen["tests_total"] == 1220


# ── AC4 / AC5: done marks in mitigation / disposition ───────────────────────

@pytest.mark.parametrize("field,text", [
    ("mitigation", "fixed: added a parity test"),
    ("mitigation", "FIXED: same"),
    ("mitigation", "accepted; documented in the report"),
    ("mitigation", "Accepted : preflight does not read outcome"),
    ("mitigation", "fixed — capped at the limit"),
    ("mitigation", "resolved - covered by test_x"),
    ("disposition", "fixed: file-state cache"),
    ("disposition", "closed: duplicate of R1"),
    ("mitigation", "done: removed the branch"),
])
def test_done_marks_in_mitigation_or_disposition(field, text):
    assert rds._item_done({"description": "risk", field: text}) is True


@pytest.mark.parametrize("text", [
    "add a parity test", "fixed in follow-up", "accepted risk owner TBD",
    "accepted-risk owner TBD", "closed-loop test to add", "done-criteria unclear",
    "ToDo: make roots delegate", "none",
])
def test_proposals_stay_open(text):
    assert rds._item_done({"description": "risk", "mitigation": text}) is False


def test_fixed_risk_leaves_leftovers():
    deliver = {"meta_review": {"risks": [
        {"severity": "LOW", "description": "no parity test", "mitigation": "fixed: added one"},
        {"severity": "LOW", "description": "preflight blind", "mitigation": "add a check"}]}}
    left = rds.review_summary({}, deliver)["leftovers"]
    assert any("preflight blind" in x for x in left)
    assert not any("no parity test" in x for x in left)


def test_push_ready_doc_states_marking():
    doc = (_BACKEND / "skills" / "s_autonomous-pipeline" / "stages" / "ref" / "deliver"
           / "push-ready.md").read_text(encoding="utf-8")
    assert "fixed: <how>" in doc and "accepted: <why>" in doc
    # resolved is the mark the push-ready HIGH check reads; the doc must require it.
    assert '"resolved": true' in doc
