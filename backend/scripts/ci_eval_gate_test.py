"""Tests for ci_eval_gate.check_gate — fresh/stale/red/no-report exit codes."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.ci_eval_gate import check_gate  # noqa: E402
from scripts import eval_runner  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    proj = tmp_path / "Eval"
    (proj / "EvalHistory").mkdir(parents=True)
    (proj / "golden_set.yaml").write_text("version: 2\ncases: []\n")
    # pin the digest to a known value so we control fresh vs stale
    monkeypatch.setattr(eval_runner, "compute_code_digest", lambda root, code_root=None: "DIGEST_NOW")
    # ci_eval_gate imported the symbol directly — patch there too
    import scripts.ci_eval_gate as gate
    monkeypatch.setattr(gate, "compute_code_digest", lambda root: "DIGEST_NOW")
    # the golden set the gate recomputes the release over; empty unless a test sets one
    monkeypatch.setattr(eval_runner, "load_merged_golden_set", lambda root: _GOLDEN)
    _GOLDEN["cases"] = []
    return tmp_path, proj / "EvalHistory"


_GOLDEN: dict = {"cases": []}


_RELEASE = {"verdict": "release", "reason": "all 5 red-line and gate case(s) passed",
            "checks": [{"id": "redline", "passed": True}, {"id": "dimensions", "passed": True},
                       {"id": "denominator", "passed": True}, {"id": "record", "passed": True}]}


def _report(hist: Path, name: str, digest: str, bvt: dict, score: float | None = None,
            mtime: int | None = None, redline: dict | None = None, release=_RELEASE):
    payload = {
        "run_id": name, "triggered_at": "2026-06-26T00:00:00Z",
        "code_digest": digest, "bvt": bvt,
    }
    if release is not None:
        payload["release"] = release
    if score is not None:
        payload["overall_score"] = score
    if redline is not None:
        payload["redline"] = redline
    p = hist / name
    p.write_text(json.dumps(payload))
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))


def test_fresh_green_passes(ws):
    root, hist = ws
    _report(hist, "2026-06-26_manual.json", "DIGEST_NOW",
            {"total": 5, "passed": 5, "failed": 0, "error": 0, "green": True})
    code, msg = check_gate(root)
    assert code == 0, msg


def test_stale_blocks(ws):
    root, hist = ws
    _report(hist, "2026-06-26_manual.json", "OLD_DIGEST",
            {"total": 5, "passed": 5, "failed": 0, "error": 0, "green": True})
    code, msg = check_gate(root)
    assert code == 1 and "stale" in msg.lower()


def test_red_blocks(ws):
    root, hist = ws
    _report(hist, "2026-06-26_manual.json", "DIGEST_NOW",
            {"total": 5, "passed": 4, "failed": 1, "error": 0, "green": False})
    code, msg = check_gate(root)
    assert code == 1 and "red" in msg.lower()


def test_no_report_cannot_verify(ws):
    root, _ = ws
    code, msg = check_gate(root)
    assert code == 2


def test_pre_gate_report_cannot_verify(ws):
    """A report from before the gate (no bvt block) → cannot verify, not pass."""
    root, hist = ws
    (hist / "old.json").write_text(json.dumps({"run_id": "old", "overall_score": 100}))
    code, msg = check_gate(root)
    assert code == 2


# ── the release decision (core/eval_release) ─────────────────────────────────
_GREEN = {"total": 5, "passed": 5, "failed": 0, "error": 0, "green": True}


def _blocked(check, verdict="blocked"):
    checks = [{"id": c, "passed": c != check} for c in ("redline", "dimensions", "denominator", "record")]
    return {"verdict": verdict, "reason": f"{check} failed: G1", "checks": checks}


def test_a_blocked_release_blocks_naming_the_check(ws):
    root, hist = ws
    _report(hist, "now.json", "DIGEST_NOW", _GREEN, release=_blocked("dimensions"))
    code, msg = check_gate(root)
    assert code == 1 and "dimensions" in msg and "G1" in msg, msg


def test_an_incomplete_release_blocks(ws):
    root, hist = ws
    _report(hist, "now.json", "DIGEST_NOW", _GREEN, release=_blocked("denominator", "incomplete"))
    code, msg = check_gate(root)
    assert code == 1 and "incomplete" in msg and "denominator" in msg, msg


def test_a_report_without_a_release_block_cannot_verify(ws):
    root, hist = ws
    _report(hist, "now.json", "DIGEST_NOW", _GREEN, score=100.0, release=None)
    code, msg = check_gate(root)
    assert code == 2 and "release" in msg, msg


def _gate_case(cid):
    from scripts.golden_case_validator import compute_case_stamp

    c = {"id": cid, "gating": "gate", "tier": "stable", "eval_method": "programmatic",
         "evaluators": ["canary_pass"], "dimension": "cognition"}
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


def test_a_run_limited_to_some_cases_cannot_stand_in_for_the_rest(ws):
    """The stored verdict of a --cases run says release, but the gate recomputes
    over every gate case of the golden set: the case it did not run is missing."""
    root, hist = ws
    _GOLDEN["cases"] = [_gate_case("A"), _gate_case("B")]
    path = hist / "now.json"
    _report(hist, "now.json", "DIGEST_NOW", _GREEN)
    d = json.loads(path.read_text())
    d["filtered"] = True
    d["cases"] = [{"id": "A", "status": "passed"}]
    path.write_text(json.dumps(d))
    code, msg = check_gate(root)
    assert code == 1 and "denominator" in msg and "B" in msg, msg


def test_a_full_report_passes_the_recomputed_release(ws):
    root, hist = ws
    _GOLDEN["cases"] = [_gate_case("A"), _gate_case("B")]
    path = hist / "now.json"
    _report(hist, "now.json", "DIGEST_NOW", _GREEN)
    d = json.loads(path.read_text())
    d["cases"] = [{"id": "A", "status": "passed"}, {"id": "B", "status": "passed"}]
    path.write_text(json.dumps(d))
    code, msg = check_gate(root)
    assert code == 0, msg


def test_a_red_line_blocks_even_without_a_release_block(ws):
    """The missing-release check comes after the red-line veto, so a violated red
    line exits 1, never the soft-passed 2."""
    root, hist = ws
    _report(hist, "now.json", "DIGEST_NOW", _GREEN, release=None,
            redline={"violated": True, "total": 1, "violations": [{"id": "X", "status": "failed"}],
                     "skipped": []})
    code, msg = check_gate(root)
    assert code == 1 and "RED-LINE" in msg, msg


def test_the_release_logic_is_in_the_freshness_digest():
    from scripts.eval_runner import _GATE_CODE_PATHS

    assert "backend/core/eval_release.py" in _GATE_CODE_PATHS
    assert "backend/scripts/golden_case_validator.py" in _GATE_CODE_PATHS


def test_no_score_comparison_between_reports(ws):
    """A big score difference against an older report no longer blocks anything."""
    root, hist = ws
    _report(hist, "old.json", "OTHER", _GREEN, score=100.0, mtime=1_000_000)
    _report(hist, "now.json", "DIGEST_NOW", _GREEN, score=10.0, mtime=2_000_000)
    code, msg = check_gate(root)
    assert code == 0, msg


_GREEN_BVT = {"total": 5, "passed": 5, "failed": 0, "error": 0, "green": True}


def test_redline_violation_blocks_even_when_bvt_green(ws):
    """The crux: bvt is GREEN (its deterministic cases all pass) but a red-line
    case (invisible to bvt) FAILED → the push must still be blocked, with a
    message naming the RED-LINE, not a bvt-red."""
    root, hist = ws
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN_BVT, score=99.0,
            redline={"violated": True, "total": 1,
                     "violations": [{"id": "GS_RL_REFUSE", "status": "failed",
                                     "eval_method": "llm"}], "skipped": []})
    code, msg = check_gate(root)
    assert code == 1, msg
    assert "red-line" in msg.lower() or "redline" in msg.lower(), msg
    assert "GS_RL_REFUSE" in msg, msg


def test_redline_clean_still_passes(ws):
    root, hist = ws
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN_BVT, score=99.0,
            redline={"violated": False, "total": 2, "violations": [], "skipped": []})
    code, msg = check_gate(root)
    assert code == 0, msg


def test_redline_absent_falls_through_to_bvt(ws):
    """Backward compat: an old report with NO redline block must NOT crash and
    must fall through to the existing bvt check (fail-open on the new gate)."""
    root, hist = ws
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN_BVT, score=99.0)
    code, msg = check_gate(root)
    assert code == 0, msg  # no redline block → bvt green → pass


def test_stale_report_not_masked_by_redline_check(ws):
    """A stale report is blocked on FRESHNESS before the redline check — a
    red-line verdict from stale code can't be trusted either way."""
    root, hist = ws
    _report(hist, "2026-06-26_now.json", "OLD_DIGEST", _GREEN_BVT, score=99.0,
            redline={"violated": True, "total": 1,
                     "violations": [{"id": "X", "status": "failed"}], "skipped": []})
    code, msg = check_gate(root)
    assert code == 1 and "stale" in msg.lower(), msg


def test_load_history_real_import_resolves():
    """TECH.md:2671 lesson: a prior change shipped a dead import (load_history vs
    _load_history) that 7 injected-seam tests missed. Assert the REAL symbol the
    gate depends on resolves — not a mock."""
    from scripts.eval_runner import _load_history
    assert callable(_load_history)
