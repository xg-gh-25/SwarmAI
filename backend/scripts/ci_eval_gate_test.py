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
    return tmp_path, proj / "EvalHistory"


def _report(hist: Path, name: str, digest: str, bvt: dict, score: float | None = None,
            mtime: int | None = None):
    payload = {
        "run_id": name, "triggered_at": "2026-06-26T00:00:00Z",
        "code_digest": digest, "bvt": bvt,
    }
    if score is not None:
        payload["overall_score"] = score
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


# ── score-drift hard gate (run_95d9acbc) ────────────────────────────────────
# The gate blocks a push when the latest report's overall_score dropped more
# than SCORE_DRIFT_EPSILON below the most-recent DIFFERENT-code baseline.
# Comparing against a same-code re-run is NOT drift (LLM-judge noise) — proven
# on real data: 100.0→91.7 on identical code_digest 526db7a385.
_GREEN = {"total": 5, "passed": 5, "failed": 0, "error": 0, "green": True}


def test_score_drift_beyond_epsilon_blocks(ws):
    """latest 90 vs different-code baseline 95 → 5.0 drop > 2.0 → exit 1 blocked."""
    root, hist = ws
    _report(hist, "2026-06-25_base.json", "OLD_DIGEST", _GREEN, score=95.0, mtime=1000)
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN, score=90.0, mtime=2000)
    code, msg = check_gate(root)
    assert code == 1, msg
    assert "drift" in msg.lower() or "regress" in msg.lower(), msg


def test_score_within_epsilon_passes(ws):
    """latest 93.5 vs baseline 95 → 1.5 drop < 2.0 → pass (green+fresh)."""
    root, hist = ws
    _report(hist, "2026-06-25_base.json", "OLD_DIGEST", _GREEN, score=95.0, mtime=1000)
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN, score=93.5, mtime=2000)
    code, msg = check_gate(root)
    assert code == 0, msg


def test_score_improved_passes(ws):
    root, hist = ws
    _report(hist, "2026-06-25_base.json", "OLD_DIGEST", _GREEN, score=90.0, mtime=1000)
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN, score=96.0, mtime=2000)
    code, msg = check_gate(root)
    assert code == 0, msg


def test_drift_baseline_skips_same_code_rerun(ws):
    """The gate must compare against a DIFFERENT-code baseline, not a same-code
    re-run. Newest-by-mtime baseline shares latest's digest (a re-run, 8pt noise);
    the real different-code baseline is 91 → latest 90 = 1.0 drop < 2.0 → pass.
    Without digest-awareness this would false-block on the 98→90 same-code delta."""
    root, hist = ws
    _report(hist, "2026-06-24_realbase.json", "OLD_DIGEST", _GREEN, score=91.0, mtime=1000)
    _report(hist, "2026-06-25_rerun.json", "DIGEST_NOW", _GREEN, score=98.0, mtime=2000)
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN, score=90.0, mtime=3000)
    code, msg = check_gate(root)
    assert code == 0, msg


def test_single_report_fails_open_no_drift(ws):
    """Only one report (no baseline) → drift check skipped, green+fresh passes."""
    root, hist = ws
    _report(hist, "2026-06-26_now.json", "DIGEST_NOW", _GREEN, score=90.0, mtime=2000)
    code, msg = check_gate(root)
    assert code == 0, msg


def test_drift_latest_picked_by_mtime_not_filename(ws):
    """latest = newest by MTIME, not filename sort. The mtime-newest report
    (alphabetically EARLIER name) is the one gated. Here the real latest (by
    mtime) regressed 95→90 vs different-code baseline → block. A filename sort
    would pick the wrong 'latest'."""
    root, hist = ws
    _report(hist, "2026-06-20_base.json", "OLD_DIGEST", _GREEN, score=95.0, mtime=1000)
    # filename sorts LAST but is OLDER by mtime — must NOT be treated as latest
    _report(hist, "2026-06-99_decoy.json", "OLD_DIGEST", _GREEN, score=95.0, mtime=1500)
    # alphabetically earliest, but NEWEST mtime → the true latest
    _report(hist, "2026-06-01_now.json", "DIGEST_NOW", _GREEN, score=90.0, mtime=3000)
    code, msg = check_gate(root)
    assert code == 1, msg
    assert "drift" in msg.lower() or "regress" in msg.lower(), msg


def test_load_history_real_import_resolves():
    """TECH.md:2671 lesson: a prior change shipped a dead import (load_history vs
    _load_history) that 7 injected-seam tests missed. Assert the REAL symbol the
    gate depends on resolves — not a mock."""
    from scripts.eval_runner import _load_history
    assert callable(_load_history)
