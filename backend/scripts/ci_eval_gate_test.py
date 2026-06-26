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
    proj = tmp_path / "Projects" / "SwarmAI"
    (proj / "EvalHistory").mkdir(parents=True)
    (proj / "golden_set.yaml").write_text("version: 2\ncases: []\n")
    # pin the digest to a known value so we control fresh vs stale
    monkeypatch.setattr(eval_runner, "compute_code_digest", lambda root, code_root=None: "DIGEST_NOW")
    # ci_eval_gate imported the symbol directly — patch there too
    import scripts.ci_eval_gate as gate
    monkeypatch.setattr(gate, "compute_code_digest", lambda root: "DIGEST_NOW")
    return tmp_path, proj / "EvalHistory"


def _report(hist: Path, name: str, digest: str, bvt: dict):
    (hist / name).write_text(json.dumps({
        "run_id": name, "triggered_at": "2026-06-26T00:00:00Z",
        "code_digest": digest, "bvt": bvt,
    }))


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
