"""bvt_probe: each negative runs the real check on broken input and goes red.

Only the negatives and fixture-based positives run here: the live positives
need this machine's code-intel graph and workspace, which a bare checkout lacks
(they are exercised by the golden cases themselves).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bvt_probe  # noqa: E402


@pytest.mark.parametrize("cmd, token", [
    (["route"], "ROUTE"),
    (["load"], "GRAPH_LOAD"),
    (["summary"], "GRAPH_SUMMARY"),
    (["fresh"], "FRESHNESS"),
    (["detect"], "DETECT"),
    (["noise", "unused.md"], "DDD_NOISE"),
])
def test_negative_emits_teeth_and_never_the_ok_marker(cmd, token, capsys):
    rc = bvt_probe.main(cmd + ["negative"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"{token}_TEETH" in out
    assert f"{token}_OK" not in out


def test_noise_positive_passes_a_healthy_doc(tmp_path, capsys):
    doc = tmp_path / "IMPROVEMENT.md"
    doc.write_text(
        "## Lessons\n\n- [guideline] **fresh note** body. (2026-10-20)\n"
        "  <!-- ref:3 | last:none | decay:active | source:manual -->\n",
        encoding="utf-8")
    assert bvt_probe.main(["noise", str(doc)]) == 0
    assert "DDD_NOISE_OK" in capsys.readouterr().out


def test_summary_threshold_rejects_a_small_graph():
    class Small:
        def get_codebase_summary(self):
            return {"total_nodes": 100, "total_edges": 500}
    assert bvt_probe._summary_holds(Small())[0] is False


def test_negative_reports_fail_when_the_check_passes_broken_input(monkeypatch, capsys):
    monkeypatch.setattr(bvt_probe, "_load_holds", lambda project: (True, ""))
    rc = bvt_probe.main(["load", "negative"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "GRAPH_LOAD_FAIL" in out and "TEETH" not in out


def test_noise_negative_needs_an_explicit_fail_verdict(monkeypatch, capsys):
    """A noisy doc judged PASS (or an error with no verdict) is not teeth."""
    for report in ('{"verdict": "PASS"}', '{"doc": "x", "error": "not found"}'):
        monkeypatch.setattr(bvt_probe, "_noise_report", lambda doc, r=report: r)
        assert bvt_probe.main(["noise", "unused.md", "negative"]) == 1
        assert "DDD_NOISE_FAIL" in capsys.readouterr().out
