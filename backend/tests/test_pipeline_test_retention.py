"""Evidence retention and evidence size bounds in ``scripts/pipeline_test.py``.

Every runner call writes ``tests/<mode>-<ts>.json`` (+ ``logs-<mode>-<ts>/`` on
failures). Without pruning one real run reached 66 entries / 126 MB.

Methodology: unit tests build a ``tests/`` dir by hand (mtimes aged with
``os.utime``) and call ``_prune_evidence``; end-to-end tests run the real CLI in the
``rig`` tmp repo from test_pipeline_test (real nested pytest).

Properties:
- every ``latest-<mode>.json`` survives; the newest EVIDENCE_KEEP reports and log
  dirs per mode survive; a logs dir a latest-*.json points at survives; unrelated
  names, dotfiles (atomic-write temps) and the tdd index are never touched
- concurrency: an entry younger than EVIDENCE_MIN_AGE_S is never pruned, and a held
  prune lock skips pruning entirely
- a real call prunes only its own run's dir, never another run's
- an all-pass evidence report stays compact: each passed entry is nodeid + outcome
  (+ duration) and the file grows by a bounded number of bytes per passed test
- a megabyte parametrize id is capped in evidence and stdout (``nodeid_truncated``)
  and recheck re-runs that test's whole file instead of a nodeid that cannot match
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scripts import pipeline_test as pt
from tests.test_pipeline_test import (  # noqa: F401 — rig is a fixture
    PROJECT, RUN_ID, _evidence, _run, _write, rig)

OLD = time.time() - 3600


def _touch(p: Path, text: str = "{}", old: bool = True, is_dir: bool = False) -> Path:
    if is_dir:
        p.mkdir(parents=True)
        (p / "main1-0.out").write_text("log")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if old:
        os.utime(p, (OLD, OLD))
    return p


def _stamps(n: int) -> list[str]:
    return [f"20260101T0000{i:02d}" for i in range(n)]


def test_prune_keeps_latest_newest_three_per_mode_and_referenced_logs(tmp_path):
    t = tmp_path / "tests"
    for s in _stamps(5):
        _touch(t / f"verify-{s}.json")
        _touch(t / f"logs-verify-{s}", is_dir=True)
    _touch(t / "verify-20260101T000004-2.json")  # same-second second record: newest
    for s in _stamps(2):
        _touch(t / f"regression-{s}.json")
    ref = "logs-verify-20260101T000000"
    _touch(t / "latest-verify.json", json.dumps({"logs": str(t / ref)}))
    keep_other = [_touch(t / n) for n in ("latest-tdd.json", "tdd_index.json", "notes.json",
                                          ".verify-20260101T000000.json.123.tmp")]
    removed = pt._prune_evidence(t)
    names = {p.name for p in t.iterdir()}
    assert {f"verify-{s}.json" for s in ("20260101T000003", "20260101T000004",
                                          "20260101T000004-2")} <= names
    assert "verify-20260101T000000.json" not in names and "verify-20260101T000002.json" not in names
    assert {f"logs-verify-{s}" for s in _stamps(5)[2:]} <= names
    assert ref in names, "a logs dir referenced by latest-*.json is kept"
    assert "logs-verify-20260101T000001" not in names
    assert {f"regression-{s}.json" for s in _stamps(2)} <= names  # under the cap
    assert all(p.exists() for p in keep_other) and (t / "latest-verify.json").exists()
    assert sorted(removed) == sorted(["verify-20260101T000000.json", "verify-20260101T000001.json",
                                      "verify-20260101T000002.json", "logs-verify-20260101T000001"])


def test_prune_never_removes_fresh_entries_or_runs_under_a_held_lock(tmp_path):
    t = tmp_path / "tests"
    for s in _stamps(5):
        _touch(t / f"tdd-{s}.json", old=False)  # a concurrent writer's fresh files
    assert pt._prune_evidence(t) == [] and len(list(t.glob("tdd-*.json"))) == 5
    for p in t.glob("tdd-*.json"):
        os.utime(p, (OLD, OLD))
    fd = pt._try_lock(t / ".prune.lock", 0)
    try:
        assert pt._prune_evidence(t) == []  # another invocation is pruning: skip
    finally:
        pt._unlock(fd)
    assert len(pt._prune_evidence(t)) == 2 and len(list(t.glob("tdd-*.json"))) == 3


def test_real_call_prunes_its_own_run_only(rig, capsys, tmp_path):
    rig()
    runs = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs"
    mine, other = runs / RUN_ID / "evidence" / "tests", runs / "run_other" / "evidence" / "tests"
    for s in _stamps(5):
        _touch(mine / f"tdd-{s}.json")
        _touch(other / f"tdd-{s}.json")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files", "backend/tests/test_x.py")
    assert code == 0, out
    left = sorted(p.name for p in mine.glob("tdd-*.json"))
    assert len(left) == 3 and Path(out["evidence"]).name in left
    assert (mine / "latest-tdd.json").is_file() and (mine / "tdd_index.json").is_file()
    assert len(list(other.glob("tdd-*.json"))) == 5  # another run's dir is never touched


MANY = ("import pytest\n\n\n@pytest.mark.parametrize('i', range({k}))\n"
        "def test_p(i):\n    assert i >= 0\n")


def test_all_pass_evidence_is_compact_per_passed_test(rig, capsys, tmp_path):
    k = 150
    rig({"backend/tests/test_many.py": MANY.replace("{k}", str(k))})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files",
                     "backend/tests/test_many.py")
    assert code == 0 and out["counts"]["passed"] == k, out
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    for r in ev["results"]:
        assert set(r) <= {"nodeid", "outcome", "duration"} and r["outcome"] == "passed", r
        assert isinstance(r.get("duration"), float)
    per_test = [len(json.dumps(r, indent=1)) - len(r["nodeid"]) for r in ev["results"]]
    assert max(per_test) <= 70, max(per_test)  # bytes beyond the nodeid itself
    size = Path(out["evidence"]).stat().st_size
    assert size <= 3000 + sum(len(r["nodeid"]) + 75 for r in ev["results"]), size


GIANT = ("import pytest\n\n\n@pytest.mark.parametrize('s', ['ok', 'x' * 200000], ids=str)\n"
         "def test_g(s):\n    assert s == 'ok'\n")


def test_megabyte_nodeid_is_capped_and_recheck_reruns_its_file(rig, capsys, tmp_path):
    repo = rig()
    _write(repo, {"backend/tests/test_giant.py": GIANT})  # untracked → "new" on failure
    # verify (not tdd): a recheck only builds on verify / regression evidence.
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1 and out["counts"] == {**out["counts"], "passed": 1, "failed": 1}, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    (f,) = ev["failures"]
    assert f["nodeid_truncated"] is True and len(f["nodeid"]) <= pt.NODEID_CAP
    assert f["classification"] == "new" and "[sha:" in f["nodeid"]
    assert all(len(r["nodeid"]) <= pt.NODEID_CAP for r in ev["results"])
    assert Path(out["evidence"]).stat().st_size < 20000
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 1 and out["counts"]["failed"] == 1 and out["counts"]["passed"] == 1, out
    assert not out.get("runner_errors"), out  # the whole file ran, not an unmatchable nodeid
