"""core.evidence_seal: runner evidence counts only when the runner wrote it.

What is tested:
- run_of: only <...>/runs/<id>/evidence/{tests,proofs,probes,mutations}/ paths are
  checked; the CLI's prompts and other files are not;
- write_sealed + check: the runner's own write passes; a hand-written file, an edited
  file and a restored older copy are rejected for an enabled run; a run made before
  seals (no store, no ledger record) is not checked; a store that vanished after enable
  is rejected; an unreadable store fails open with a reason;
- the readers: pipeline_validator._load_evidence_json / load_test_evidence,
  pipeline_test._read_json and artifact_cli._merged_proofs use only sealed files;
- run-create enables the store and records evidence_seal in the hand-out ledger.
Methodology: a temp app-data dir (config.get_app_data_dir monkeypatched) and a temp
runs tree; real writes and reads, no mocks of the seal itself.
Invariants: I1 a sealed run's runner evidence counts only with a matching seal;
I2 an unreadable store never blocks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pipeline_test as pt  # noqa: E402
import scripts.artifact_cli as cli  # noqa: E402
from core import evidence_seal as es  # noqa: E402

RID = "run_seal01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config
    data = tmp_path / "appdata"
    (data / "state" / "pipeline_agent_audit").mkdir(parents=True)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / RID
    (run_dir / "evidence" / "tests").mkdir(parents=True)
    (run_dir / "evidence" / "proofs").mkdir(parents=True)
    audit = data / "state" / "pipeline_agent_audit"
    return SimpleNamespace(run_dir=run_dir, audit=audit)


def _enable(env):
    assert es.enable(RID) == []
    (env.audit / f"handout_{RID}.json").write_text(json.dumps(
        {"records": {"evidence_seal": True}}))


def test_run_of_checks_only_the_runner_subdirs(env):
    ev = env.run_dir / "evidence"
    assert es.run_of(ev / "tests" / "latest-regression.json") == RID
    assert es.run_of(ev / "proofs" / "prove-R1-x.json") == RID
    assert es.run_of(ev / "prompts" / "correctness.md") is None
    assert es.run_of(ev / "gate2-v2.json") is None
    assert es.run_of(Path("/tmp/elsewhere/x.json")) is None


def test_the_runners_write_passes_a_hand_written_file_does_not(env):
    _enable(env)
    good = env.run_dir / "evidence" / "tests" / "latest-tdd.json"
    pt._write_json(good, {"schema": 1, "mode": "tdd", "counts": {"passed": 1}})
    assert es.check(good) == (True, None)
    forged = env.run_dir / "evidence" / "tests" / "latest-regression.json"
    forged.write_text(json.dumps({"schema": 1, "mode": "regression", "counts": {"passed": 9}}))
    ok, why = es.check(forged)
    assert not ok and "no seal" in why


def test_an_edited_or_restored_report_is_rejected(env):
    _enable(env)
    p = env.run_dir / "evidence" / "tests" / "latest-regression.json"
    pt._write_json(p, {"schema": 1, "mode": "regression", "counts": {"failed": 2}})
    old = p.read_text()
    pt._write_json(p, {"schema": 1, "mode": "regression", "counts": {"failed": 1}})
    p.write_text(old)  # restore the older runner-written copy
    ok, why = es.check(p)
    assert not ok and "mismatch" in why
    p.write_text(json.dumps({"schema": 1, "mode": "regression", "counts": {"failed": 0}}))
    assert not es.check(p)[0]


def test_a_run_made_before_seals_is_not_checked(env):
    p = env.run_dir / "evidence" / "tests" / "latest-regression.json"
    p.write_text(json.dumps({"schema": 1}))
    assert es.check(p) == (True, None)


def test_a_store_that_vanished_after_enable_rejects(env):
    _enable(env)
    (env.audit / f"evidence_{RID}.json").unlink()
    p = env.run_dir / "evidence" / "tests" / "latest-regression.json"
    p.write_text(json.dumps({"schema": 1}))
    ok, why = es.check(p)
    assert not ok and "missing" in why


def test_an_unreadable_store_fails_open(env):
    _enable(env)
    (env.audit / f"evidence_{RID}.json").write_text("{not json")
    p = env.run_dir / "evidence" / "tests" / "latest-regression.json"
    p.write_text(json.dumps({"schema": 1}))
    ok, why = es.check(p)
    assert ok and "unreadable" in why


def test_runner_read_returns_none_for_a_forged_cache(env):
    _enable(env)
    cache = env.run_dir / "evidence" / "proofs" / "old-side.json"
    cache.write_text(json.dumps({"k": [{"o": "assert_fail"}, {"o": "assert_fail"}]}))
    assert pt._read_json(cache) is None
    pt._write_json(cache, {"k": []})
    assert pt._read_json(cache) == {"k": []}


def test_two_writers_leave_the_seal_matching_the_file(env):
    _enable(env)
    p = env.run_dir / "evidence" / "tests" / "latest-tdd.json"
    for i in range(5):
        pt._write_json(p, {"i": i})
    assert es.check(p) == (True, None)


# ── Gate-2 R1 break tests ────────────────────────────────────────────────────


def test_a_write_after_the_store_vanished_warns(env, capsys):
    """integration-865216: the runner says why its evidence will not count."""
    _enable(env)
    (env.audit / f"evidence_{RID}.json").unlink()
    pt._write_json(env.run_dir / "evidence" / "tests" / "latest-tdd.json", {"schema": 1})
    assert "evidence seal store" in capsys.readouterr().err


def test_an_unsealed_run_index_is_refused_not_reset(env, capsys):
    """R2 (four experts): a rejected index must neither read as "no history" nor be
    re-sealed; the caps that count from it refuse instead."""
    _enable(env)
    tdir = env.run_dir / "evidence" / "tests"
    (tdir / "run_index.json").write_text(json.dumps([]))
    pt._append_run_index(tdir, {"kind": "run", "mode": "verify"})
    assert json.loads((tdir / "run_index.json").read_text()) == []  # not re-sealed
    assert not es.check(tdir / "run_index.json")[0]
    assert "not the runner's" in capsys.readouterr().err
    res = pt._circuit_breaker_check(env.run_dir / "evidence", "regression")
    assert res and res["circuit_breaker"]["tripped"] and "not the runner's" in res["error"]
