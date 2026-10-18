"""Runner evidence that exists but cannot be read is never "no evidence".

WHAT IS TESTED
- Real full-suite reports are several MB. They used to pass through a 2 MB cap
  sized for SOURCE files, which dropped them silently: ``mode:verify`` read as
  "no latest-verify.json evidence", and, worse, a regression-classified failure in
  a big report was invisible to the push_ready bound, which then judged an older,
  smaller (possibly green) report in its place. A >2 MB report is now read.
- A report that exists but cannot be used (over the evidence cap, not JSON, not an
  object) comes back from ``load_test_evidence`` as an ``unreadable`` placeholder
  ordered by its mtime, so: its ``mode:<m>`` criterion is unmet with the reason; a
  file criterion an older report would pass is unmet (the newer report may fail
  it); push_ready is capped with the reason; freshness reports ``unreadable``.
- An absent report adds nothing; probe / mutation criteria name unreadable files.

METHODOLOGY
- Hand-written evidence fixtures in the runner's documented C2 shape under a
  tmp_path run dir; pipeline_validator called directly (no CLI, no git).
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pipeline_validator as pv  # noqa: E402


def _c2(mode, results, failures=(), finished="2026-01-01T00:10:00Z", pad=0):
    counts = {"passed": 0, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for r in results:
        counts[{"passed": "passed", "failed": "failed"}[r["outcome"]]] += 1
    return {"schema": 1, "mode": mode, "run_id": "r", "started_at": finished,
            "finished_at": finished, "counts": counts, "results": list(results),
            "failures": list(failures), "logs": "x" * pad}


def _write(run_dir: Path, rel: str, data) -> Path:
    p = run_dir / "evidence" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _deliver() -> dict:
    return copy.deepcopy(pv.STAGE_TEMPLATES["deliver"])


_PASS = {"nodeid": "t.py::a", "outcome": "passed", "file": "t.py"}
_FAIL = {"nodeid": "t.py::a", "outcome": "failed", "file": "t.py"}
_BIG = 3 * 1024 * 1024  # bigger than the old 2 MB cap, like a real full-suite report


def test_multi_megabyte_report_is_read_and_judged(tmp_path):
    p = _write(tmp_path, "tests/latest-verify.json", _c2("verify", [_PASS], pad=_BIG))
    assert p.stat().st_size > 2 * 1024 * 1024
    res = pv.judge_exit_criteria([{"id": "X5", "kind": "test", "ref": "mode:verify",
                                   "expect": "pass"}], tmp_path)
    assert res["met"] is True, res["detail"]


def test_big_regression_report_caps_push_ready_instead_of_an_older_green_one(tmp_path):
    _write(tmp_path, "tests/latest-tdd.json", _c2("tdd", [_PASS], finished="2026-01-01T00:01:00Z"))
    _write(tmp_path, "tests/latest-regression.json", _c2(
        "regression", [_FAIL], pad=_BIG, finished="2026-01-01T00:05:00Z",
        failures=[{"nodeid": "t.py::a", "classification": "regression", "summary": "x"}]))
    evidence = pv.load_test_evidence(tmp_path)
    assert [e["mode"] for e in evidence] == ["regression", "tdd"]
    ok, reasons = pv.push_ready_upper_bound(_deliver(), "full", test_evidence=evidence[0],
                                            all_test_evidence=evidence)
    assert ok is False
    assert any(r.startswith("regression-classified failure in runner evidence") for r in reasons), reasons
    assert not any("unreadable" in r for r in reasons), reasons  # it was READ, not skipped


def test_newer_unreadable_report_blocks_a_mode_criterion_an_older_one_would_pass(tmp_path):
    _write(tmp_path, "tests/latest-verify.json", _c2("verify", [_PASS], finished="2026-01-01T00:01:00Z"))
    reg = _write(tmp_path, "tests/latest-regression.json", "{ truncated")
    os.utime(reg, (4_000_000_000, 4_000_000_000))
    res = pv.judge_exit_criteria([{"id": "X5", "kind": "test", "ref": "mode:verify",
                                   "expect": "pass"}], tmp_path)
    assert res["met"] is False
    assert "latest-regression.json" in res["detail"]["X5"] and "unreadable" in res["detail"]["X5"]


def test_dangling_symlink_and_pathological_nesting_are_unreadable_not_absent(tmp_path):
    tests = tmp_path / "evidence" / "tests"
    tests.mkdir(parents=True)
    (tests / "latest-verify.json").symlink_to(tmp_path / "gone.json")
    _write(tmp_path, "tests/latest-regression.json", "[" * 200_000)
    reasons = {e["mode"]: e.get("unreadable") for e in pv.load_test_evidence(tmp_path)}
    assert reasons == {"verify": "dangling symlink", "regression": "not valid JSON"}


def test_unparseable_newest_report_is_a_placeholder_not_a_gap(tmp_path):
    _write(tmp_path, "tests/latest-tdd.json", _c2("tdd", [_PASS], finished="2026-01-01T00:01:00Z"))
    v = _write(tmp_path, "tests/latest-verify.json", "{ truncated")
    os.utime(v, (4_000_000_000, 4_000_000_000))  # newer than the tdd report
    evidence = pv.load_test_evidence(tmp_path)
    assert evidence[0]["mode"] == "verify" and evidence[0]["unreadable"] == "not valid JSON"

    res = pv.judge_exit_criteria([
        {"id": "X1", "kind": "test", "ref": "t.py", "expect": "pass"},
        {"id": "X5", "kind": "test", "ref": "mode:verify", "expect": "pass"},
    ], tmp_path)
    assert sorted(res["unmet"]) == ["X1", "X5"]
    assert "unreadable" in res["detail"]["X5"] and "not valid JSON" in res["detail"]["X5"]
    assert "unreadable" in res["detail"]["X1"]

    ok, reasons = pv.push_ready_upper_bound(_deliver(), "full", test_evidence=evidence[0],
                                            all_test_evidence=evidence)
    assert ok is False
    assert any("unreadable" in r and "latest-verify.json" in r for r in reasons), reasons
    assert pv.evidence_freshness(evidence[0], ["t.py"])["status"] == "unreadable"


def test_an_older_unreadable_report_does_not_block_a_newer_green_one(tmp_path):
    old = _write(tmp_path, "tests/latest-recheck.json", "[1, 2]")
    os.utime(old, (1_000_000, 1_000_000))
    _write(tmp_path, "tests/latest-tdd.json", _c2("tdd", [_PASS], finished="2026-01-01T00:01:00Z"))
    evidence = pv.load_test_evidence(tmp_path)
    assert [e["mode"] for e in evidence] == ["tdd", "recheck"]
    assert evidence[1]["unreadable"] == "not a JSON object"
    res = pv.judge_exit_criteria([{"id": "X1", "kind": "test", "ref": "t.py",
                                   "expect": "pass"}], tmp_path)
    assert res["met"] is True, res["detail"]


def test_report_over_the_evidence_cap_names_the_cap(tmp_path, monkeypatch):
    _write(tmp_path, "tests/latest-verify.json", _c2("verify", [_PASS], pad=4096))
    monkeypatch.setattr(pv, "_EVIDENCE_MAX_BYTES", 1024)
    (ev,) = pv.load_test_evidence(tmp_path)
    assert "evidence cap" in ev["unreadable"]
    assert pv._read_evidence_json(tmp_path / "evidence/tests/latest-verify.json") is None


def test_absent_reports_add_nothing(tmp_path):
    assert pv.load_test_evidence(tmp_path) == []
    assert pv._load_evidence_json(tmp_path / "nope.json") == (None, None)


def test_probe_and_mutation_name_unreadable_files(tmp_path):
    _write(tmp_path, "probes/fuzz.json", "not json")
    _write(tmp_path, "mutations/latest.json", "not json")
    res = pv.judge_exit_criteria([
        {"id": "P1", "kind": "probe", "ref": "fuzz", "expect": "no_new_findings"},
        {"id": "M1", "kind": "mutation", "ref": "spec.json", "expect": "all_killed"},
        {"id": "P2", "kind": "probe", "ref": "absent", "expect": "no_new_findings"},
    ], tmp_path)
    assert res["detail"]["P1"] == "probe evidence unreadable (not valid JSON)"
    assert res["detail"]["M1"] == "mutation evidence unreadable (not valid JSON)"
    assert res["detail"]["P2"] == "no probe evidence"
