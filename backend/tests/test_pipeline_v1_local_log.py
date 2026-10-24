"""S6-3 (design usable-by-anyone §3.3.5): pipeline commands run in their own process
and the event log is checksummed, not signed.

What is tested, through real CLI subprocesses (``tests/pipeline_v1_rig.py``) and the
real run_owner_gate:

- an adopted prewarm subprocess has no SWARM_APP_SESSION_ID; the gate hands this
  session's id to the CLI call and the created run's owner is that session;
- a torn final line (a crash mid-write) is dropped and the run goes on;
- a damaged complete record stops the run: every command refuses it and names the
  damage, and nothing more is written to the log;
- nothing signs any more: no key file is created and events carry a checksum.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

from tests.pipeline_v1_rig import PROJECT, PV1, SESSION, Rig

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile, **kwargs)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _log(r: Rig):
    return r.data / "state" / "pipeline_agent_audit" / "runs" / str(r.run_id) / "events.jsonl"


def test_an_adopted_prewarm_session_creates_a_run_it_owns(rig):
    from core import run_owner_gate
    r = rig()
    r.env.pop("SWARM_APP_SESSION_ID")              # a prewarmed subprocess's fixed env
    command = " ".join(map(shlex.quote, [sys.executable, str(PV1), "create", "--project",
                                         PROJECT, "--profile", "direct", "--requirement",
                                         "Swap the operands.", "--repo",
                                         f"repo={r.repo}"]))
    decided = run_owner_gate._decide(SESSION, command, {"command": command})
    handed = decided["hookSpecificOutput"]["updatedInput"]["command"]
    assert handed.startswith(f"SWARM_APP_SESSION_ID={SESSION} ")
    proc = subprocess.run(["bash", "-c", handed], env=r.env, capture_output=True, text=True,
                          timeout=120, cwd=str(r.root))
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out.get("ok") is True, (out, proc.stderr[-800:])
    r.run_id = out["pipeline_id"]
    assert r.engine().owner_session() == SESSION
    # without the hand-over the same CLI has no session and creates nothing
    proc = subprocess.run(["bash", "-c", command], env=r.env, capture_output=True, text=True,
                          timeout=120, cwd=str(r.root))
    assert "SWARM_APP_SESSION_ID" in proc.stdout, proc.stdout[-400:]


def test_a_torn_final_line_is_dropped_and_the_run_goes_on(rig):
    r = rig()
    r.create()
    with open(_log(r), "ab") as handle:
        handle.write(b'{"kind":"half a rec')            # the crash happened here
    out = r.v1("status")
    assert out.get("ok") is True and out["state"]["status"] == "active", out
    r.edit(Rig.CHANGE)
    assert r.report_and_complete().get("ok") is True
    assert r.state()["stage"] == "review"


def test_a_damaged_record_stops_the_run_for_a_person(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    r.report_and_complete()
    lines = _log(r).read_bytes().splitlines(keepends=True)
    assert len(lines) >= 3
    lines[1] = lines[1].replace(b'"at":"', b'"at":"X', 1)   # a middle record changed
    _log(r).write_bytes(b"".join(lines))
    size = _log(r).stat().st_size
    out = r.v1("status")                 # the CLI itself: a read
    assert out.get("ok") is False and out.get("code") == "log_damaged", out
    assert "checksum" in out["error"] and "a person" in out["error"]
    out = r.v1("stage-complete")         # a mutation: the session's hook stops it first
    assert out.get("ok") is False and "damaged record" in str(out.get("denied")), out
    assert _log(r).stat().st_size == size, "nothing is appended to a damaged log"


def test_nothing_is_signed(rig):
    r = rig()
    r.create()
    run_dir = _log(r).parent
    assert not (run_dir / "authority.key").exists()
    first = json.loads(_log(r).read_text().splitlines()[0])
    assert "checksum" in first and "signature" not in first
