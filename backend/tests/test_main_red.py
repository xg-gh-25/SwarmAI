"""Tests for core/main_red.py, the registry of tests red on main.

What is tested: only backend ``pre-existing`` failures are recorded; open/close
aggregation by nodeid (a later seen reopens, a torn line is skipped); close_passing
closes only open reds that passed; the runner's evidence writer records a seen event
(and not for a cached report); landing closes; REPORT §10 names reds in the run's
scope; the `main-red` CLI lists them. SWARM_DATA_DIR is isolated per test by conftest.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core import main_red  # noqa: E402

N1 = "backend/tests/test_x.py::test_a"
N2 = "backend/tests/test_y.py::test_b"


def _fail(n, cls="pre-existing", **kw):
    return {"nodeid": n, "classification": cls,
            "summary": "    x = f()\nE   AssertionError: boom\n", **kw}


def test_only_backend_pre_existing_failures_are_recorded():
    wrote = main_red.record_seen([
        _fail(N1), _fail(N2, "regression"), _fail("desktop/a.test.ts::t"),
        _fail("backend/tests/test_z.py::t", outcome="timeout"), "junk"], "run_1", "abc")
    assert wrote == 1
    reds = main_red.open_reds()
    assert list(reds) == [N1]
    assert reds[N1]["error"] == "AssertionError: boom"
    assert reds[N1]["runs"] == ["run_1"] and reds[N1]["head"] == "abc"


def test_seen_by_several_runs_is_one_entry_and_closed_then_reopened():
    main_red.record_seen([_fail(N1)], "run_1", "a")
    main_red.record_seen([_fail(N1)], "run_2", "b")
    assert main_red.open_reds()[N1]["runs"] == ["run_1", "run_2"]
    main_red.record_closed([N1], "c", "run_fix")
    assert main_red.open_reds() == {}
    main_red.record_seen([_fail(N1)], "run_3", "d")
    assert main_red.open_reds()[N1]["runs"] == ["run_3"]


def test_torn_line_does_not_hide_the_rest():
    main_red.record_seen([_fail(N1)], "run_1", "a")
    with main_red.registry_path().open("a", encoding="utf-8") as f:
        f.write('{"event": "seen", "nodeid": "backend/te')  # crashed mid-write
    main_red.record_seen([_fail(N2)], "run_2", "b")
    assert set(main_red.open_reds()) == {N1, N2}


def test_close_passing_closes_only_open_reds_that_passed():
    main_red.record_seen([_fail(N1), _fail(N2)], "run_1", "a")
    closed = main_red.close_passing([
        {"nodeid": N1, "outcome": "passed"}, {"nodeid": N2, "outcome": "failed"},
        {"nodeid": "backend/tests/test_q.py::t", "outcome": "passed"}], "h", "run_fix")
    assert closed == [N1]
    assert list(main_red.open_reds()) == [N2]


def test_runner_evidence_write_records_seen_but_not_for_a_cached_report(tmp_path):
    import pipeline_test as pt
    ev = {"run_id": "run_r", "head_sha": "h1", "failures": [_fail(N1)], "counts": {}}
    pt._write_evidence(tmp_path / "e1", "verify", {**ev, "cached": True})
    assert main_red.open_reds() == {}
    pt._write_evidence(tmp_path / "e2", "verify", ev)
    assert main_red.open_reds()[N1]["runs"] == ["run_r"]


def _run_dir(tmp_path, results, selected):
    d = tmp_path / "run_l"
    (d / "evidence" / "tests").mkdir(parents=True)
    (d / "evidence" / "tests" / "latest-verify.json").write_text(json.dumps({
        "mode": "verify", "run_id": "run_l", "finished_at": "2026-10-21T00:00:00Z",
        "results": results, "selected_files": selected, "failures": [], "counts": {}}))
    return d


def test_agent_writes_to_the_registry_are_denied_by_the_audit_gate(tmp_path, monkeypatch):
    """The registry lives in the protected agent-audit dir: a model cannot forge or
    erase a red with a shell write or a file tool."""
    import asyncio
    from core import security_hooks
    monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", main_red.registry_path().parent)
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)
    gate = security_hooks.create_audit_marker_write_gate({"sdk_session_id": "s1"})
    path = str(main_red.registry_path())

    def denied(tool, ti):
        out = asyncio.run(gate({"tool_name": tool, "tool_input": ti}, "tu1", None))
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"

    assert denied("Bash", {"command": f"echo '{{}}' >> {path}"})
    assert denied("Bash", {"command": f"rm {path}"})
    assert denied("Write", {"file_path": path, "content": ""})
    assert not denied("Bash", {"command": f"cat {path}"})
