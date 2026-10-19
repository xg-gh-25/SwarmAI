"""Gate-2 findings: one classifier for every reader, and the worktree-copy guard (run_925edb0e).

WHAT IS TESTED
- ``core.gate2_findings``: the cases where pipeline_validator._blocked_findings,
  artifact_cli._binding_findings and artifact_cli._recheck_items used to disagree (a MED
  with no confidence, a HIGH at conf 3, severity BLOCKER, a confidence written "8/10"),
  and that all three readers now give the classifier's answer; a recorded MED with no
  confidence covers a reported conf >= 7 MED (fail closed).
- ``pipeline_worktree.refuse_if_worktree_copy``: a worktree copy runs against a sandbox
  workspace (a test shelling out to the CLI in an isolated run) and still refuses the live
  workspace (also spelled with ~) and a workspace that holds the owning run.

METHODOLOGY: pure functions called directly; the guard with a tmp data dir and HOME.
"""
from __future__ import annotations

import pytest

from core import gate2_findings as g2


def test_classifier_disagreement_cases():
    med_none = {"severity": "MED", "finding": "x"}
    high3 = {"severity": "HIGH", "confidence": 3}
    blocker = {"severity": "BLOCKER", "confidence": 9}
    med_str = {"severity": "MEDIUM", "confidence": "8/10"}
    med_str5 = {"severity": "MED", "confidence": "5/10"}
    low = {"severity": "LOW", "confidence": 10}
    assert g2.is_blocking(med_none) and g2.must_record(med_none)
    assert g2.is_blocking(high3) and not g2.must_record(high3)
    assert g2.is_blocking(blocker) and g2.must_record(blocker)
    assert g2.confidence_of("8/10") == 8.0 and g2.is_blocking(med_str) and g2.must_record(med_str)
    assert not g2.is_blocking(med_str5) and not g2.must_record(med_str5)
    assert not g2.is_blocking(low) and not g2.must_record(low)
    assert g2.confidence_of(True) is None and g2.confidence_of("n/a") is None
    assert g2.is_blocking({"severity": "MED", "confidence": "high"})      # unparseable: fail closed


def test_validator_uses_classifier():
    from scripts.pipeline_validator import _blocked_findings
    cases = [{"severity": "BLOCKER", "resolved": False},
             {"severity": "MED", "confidence": "8/10", "resolved": False},
             {"severity": "MED", "confidence": "5/10", "resolved": False},
             {"severity": "MED", "resolved": False},
             {"severity": "HIGH", "confidence": 2, "resolved": False},
             {"severity": "HIGH", "resolved": True},
             {"severity": "LOW", "resolved": False}]
    got = _blocked_findings(cases)
    assert got == [c for c in cases if not c.get("resolved") and g2.is_blocking(c)]
    assert len(got) == 4


def test_binding_and_recheck_use_classifier():
    from scripts import artifact_cli as cli
    rows = [{"severity": "BLOCKER", "confidence": 9, "path": "a.py", "line": 1, "category": "c", "summary": "s"},
            {"severity": "MED", "confidence": None, "path": "b.py", "line": 2, "category": "c", "summary": "s"},
            {"severity": "HIGH", "confidence": 3, "path": "c.py", "line": 3, "category": "c", "summary": "s"},
            {"severity": "MED", "confidence": "8/10", "path": "d.py", "line": 4, "category": "c", "summary": "s"}]
    assert {r["path"] for r in cli._binding_findings(rows)} == {"a.py", "b.py", "d.py"}
    rec_none = {"specialist": "x", "severity": "MED", "finding": "b.py: merged, no confidence"}
    assert cli._record_covers(rec_none, dict(rows[3], path="b.py", confidence=9), "x")
    fixed = [dict(r, specialist="correctness", resolved=True, finding=r["path"] + " x") for r in rows]
    items, _ = cli._recheck_items(fixed)
    assert len(items["correctness"]) == 4       # a no-confidence MED is a re-check item now
    assert any("[MED]" in i and "b.py" in i for i in items["correctness"])


def test_worktree_copy_runs_against_a_sandbox_workspace(tmp_path, monkeypatch):
    """A worktree copy pointed at a sandbox workspace runs (a test shelling out to the CLI
    in an isolated run); the owning workspace refuses, also when spelled with ~."""
    import os
    import pipeline_worktree as pw
    data = tmp_path / "data"
    live = data / "SwarmWS"
    live.mkdir(parents=True)
    script = data / "worktrees" / "run_abc12345" / "swarmai" / "backend" / "scripts" / "artifact_cli.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    (data / "worktrees" / "run_abc12345" / ".workspace").write_text(os.path.realpath(live))
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.delenv("SWARM_WORKTREE_ROOT", raising=False)
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "sandbox"))
    pw.refuse_if_worktree_copy(str(script))                       # sandbox: runs
    monkeypatch.setenv("SWARM_WORKSPACE", str(live))
    with pytest.raises(SystemExit):
        pw.refuse_if_worktree_copy(str(script))                   # owning workspace
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SWARM_WORKSPACE", "~/data/SwarmWS")       # owning, spelled with ~
    with pytest.raises(SystemExit):
        pw.refuse_if_worktree_copy(str(script))
    monkeypatch.delenv("SWARM_WORKSPACE")
    with pytest.raises(SystemExit):
        pw.refuse_if_worktree_copy(str(script))                   # default = live: refused


def test_severity_words_and_reverse_cover():
    """red-team (run_82253fe2): one HIGH vocabulary with the Red Team trigger; a reported
    MED with no confidence is not covered by a recorded conf-3 MED."""
    from scripts import artifact_cli as cli
    for label in ("P0", "P1", "CRIT", "BLOCKING", "HIGH (conf 9)", "MEDIUM-HIGH"):
        assert g2.severity_of({"severity": label}) == "high", label
    assert g2.severity_of({"severity": "MED (shared state)"}) == "med"
    assert g2.is_blocking({"severity": "P0", "confidence": 2})
    reported = {"severity": "MED", "path": "x.py", "summary": "s"}
    low = {"specialist": "c", "severity": "MED", "confidence": 3, "finding": "x.py: s"}
    assert not cli._record_covers(low, reported, "c")
    assert cli._record_covers(dict(low, confidence=8), reported, "c")
