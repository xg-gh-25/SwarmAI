"""Reading why a Hive run's session stopped (core/hive_diagnostics; design
docs/design/2026-11-02-swarm-out-hive-deaths-diagnostics-states.md §2).

Questions each test answers:
- is the root script fixed text in which only the window and the session id change, and
  does the shell parse it? (it runs as root on the Hive)
- does a session id that could read as an option or carry a shell character stop the
  read before anything is sent?
- do the findings name what killed the runs on the demo Hive on 2026-11-02 (two OOM
  kills of ~5 GB node processes, two oom-kill stops of the service), from that Hive's
  own output kept as a fixture?
- run for real against 2,000 journal lines, does the script stay under the 20 KB budget,
  keep whole lines, say what each section left out, and keep only this session's
  non-access lines in the session section?
- does a Hive in another account, stopped, or gone answer ``not_reachable`` without an
  SSM call, and does a repeat within 60 s make no SSM call?
- is ``diagnose`` an owner-agent read the CLI calls on the route the registry lists?
"""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import hive_diagnostics as hd

FIXTURE = Path(__file__).parent / "fixtures" / "hive_diagnostics" / "demo-hive-2026-11-02.txt"
SID = "11111111-2222-4333-8444-555555555555"
T0 = datetime(2026, 11, 2, 6, 16, 17, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 2, 7, 28, 34, tzinfo=timezone.utc)


def test_the_script_is_the_pinned_template_with_only_the_window_and_id():
    a = hd.script(SID, T0, T1).splitlines()
    b = hd.script("abcdefgh_ijk-2", T0 + timedelta(days=1), T1 + timedelta(days=2)).splitlines()
    assert len(a) == len(b)
    differ = [(x, y) for x, y in zip(a, b) if x != y]
    assert [x.split("=", 1)[0] for x, _ in differ] == ["S", "U", "SID"]
    assert "S='2026-11-02 06:16:17 UTC'" in a and "U='2026-11-02 07:28:34 UTC'" in a and f"SID='{SID}'" in a
    # the shell parses it (no stray brace from the template's escaping)
    r = subprocess.run(["bash", "-n"], input="\n".join(a), text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("bad", ["-f-xxxxxxxx", "--since=1x", "abc def12", "abc'def12", "abc$(id)x", "short",
                                 "x" * 81, "", None, "a;b-cdefgh"])
def test_a_bad_session_id_raises_before_ssm(bad, monkeypatch):
    with pytest.raises(ValueError):
        hd.script(bad, T0, T1)
    # through read: the id comes from the Host's own log row; nothing is sent
    from core import swarm_out
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: {"hive_id": "h1", "session_id": bad})
    sent = []
    monkeypatch.setattr(hd, "_ssm_run", lambda *a: sent.append(a) or "")
    hd._cache.clear()
    if bad:
        with pytest.raises(ValueError):
            asyncio.run(hd.read("so_aaaaaaaaaaaa"))
    else:
        assert asyncio.run(hd.read("so_aaaaaaaaaaaa"))["reachable"] is False
    assert sent == []
    with pytest.raises(ValueError):
        hd.script(SID, T1, T0)                     # a window that ends before it starts


def test_findings_from_the_demo_hive_captures():
    sections = hd.parse(FIXTURE.read_text())
    assert set(sections) == {"state", "systemd", "kernel", "service", "session"}
    sections.pop("state")
    f = hd.findings(sections)
    kills = [(x["at"], x["victim"], x["rss_mb"], x["memcg"]) for x in f if x["kind"] == "oom_kill"]
    assert kills == [("2026-11-02T06:23:43", "node", 4948, "/system.slice/swarmai-hive.service"),
                     ("2026-11-02T06:28:20", "node", 5190, "/system.slice/swarmai-hive.service")]
    stops = [(x["at"], x["result"]) for x in f if x["kind"] == "service_stopped"]
    assert ("2026-11-02T06:23:44", "oom-kill") in stops and ("2026-11-02T06:28:20", "oom-kill") in stops
    # systemd's "Stopped" after each failure is part of that failure, not another stop
    assert len([s for s in stops if s[1] == "stopped"]) == 1
    err = next(x for x in f if x["kind"] == "session_error")
    assert any("exit code 143" in line and SID in line for line in err["last"])
    assert hd.findings({}) == [{"kind": "none_seen"}]


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_output_budget_and_session_filter(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lines = tmp_path / "journal.txt"
    other = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    pad = "x" * 180
    with lines.open("w") as f:
        for i in range(2000):
            ts = f"2026-11-02T06:{i % 60:02d}:00+0000 ip-10-0-0-1 swarmai-hive[1]: "
            k = i % 5
            if k == 0:
                f.write(ts + f'INFO:     1.2.3.4:0 - "GET /api/hive/records/{SID}?after=1 HTTP/1.1" 200 OK\n')
            elif k == 1:
                f.write(ts + f"core.session_unit - ERROR - session {SID} line {i} {pad}\n")
            elif k == 2:
                f.write(ts + f"core.session_unit - INFO - session {other} line {i} {pad}\n")
            elif k == 3:
                f.write(ts + f"core.lifecycle_manager - INFO - lifecycle_manager.memory_sample total=1MB line {i} {pad}\n")
            else:
                f.write(ts + f"core.other - INFO - unrelated line {i}\n")
    kernel = "".join(f"2026-11-02T06:28:{i % 60:02d}+0000 h kernel: Memory cgroup out of memory: Killed process {i} "
                     f"(node) total-vm:1kB, anon-rss:5000000kB {pad}\n" for i in range(300))
    (tmp_path / "kernel.txt").write_text(kernel)
    _stub(bin_dir, "journalctl", f'''for a in "$@"; do
  [ "$a" = "-k" ] && exec cat "{tmp_path}/kernel.txt"
  [ "$a" = "_PID=1" ] && {{ echo "2026-11-02T06:28:20+0000 h systemd[1]: swarmai-hive.service: Failed with result 'oom-kill'."; exit 0; }}
done
exec cat "{lines}"
''')
    _stub(bin_dir, "systemctl", 'echo "MemoryMax=infinity"; echo "OOMPolicy=continue"\n')
    _stub(bin_dir, "free", 'echo "Mem: 15672 803 8572"\n')
    _stub(bin_dir, "uptime", 'echo " 08:25:43 up 70 days"\n')
    if subprocess.run(["bash", "-c", "command -v tac"], capture_output=True).returncode != 0:
        _stub(bin_dir, "tac", 'exec tail -r "$@"\n')          # macOS has no tac
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    r = subprocess.run(["sh", "-c", hd.script(SID, T0, T1)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert len(out.encode()) < hd.MAX_OUTPUT
    sections = hd.parse(out)
    full = set(lines.read_text().splitlines()) | set(kernel.splitlines())
    for name in ("kernel", "service", "session"):
        s = sections[name]
        assert s["lines"] and s["omitted"] > 0 and s["omitted"] == s["total"] - len(s["lines"])
        assert all(line in full for line in s["lines"])                     # whole lines only
    assert sections["systemd"]["omitted"] == 0 and len(sections["systemd"]["lines"]) == 1
    sess = sections["session"]["lines"]
    assert all(SID in line and "HTTP/1.1" not in line and other not in line for line in sess)
    assert sections["session"]["total"] == 400                              # 2000 / 5 error lines
    # the newest lines are the ones kept
    assert sess[-1].endswith(f"line 1996 {pad}")
    assert "MemoryMax=infinity" in sections["state"]["lines"]


@pytest.fixture
def target_env(monkeypatch):
    from core import swarm_out
    from hive import accounts
    from hive.provisioner import HiveProvisioner
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: {"hive_id": "h1", "hive": "demo", "session_id": SID,
                                                              "sent_at": "2026-11-02T06:21:17Z"})
    inst = {"status": "running", "ec2_instance_id": "i-0abc", "region": "us-east-1", "account_ref": "a1"}

    async def get_instance(self, i):
        if inst.get("gone"):
            raise ValueError("not found")
        return dict(inst)

    async def get_account(self, ref):
        return {"auth_method": "x"}
    monkeypatch.setattr(HiveProvisioner, "_get_instance", get_instance)
    monkeypatch.setattr(HiveProvisioner, "_get_account", get_account)
    other = {"on": False}

    def session_for(row, region, purpose):
        assert purpose == "manage"
        if other["on"]:
            raise accounts.OtherAccountError("This Hive is in another AWS account")
        return object()
    monkeypatch.setattr(accounts, "session_for", session_for)
    from core import swarm_out_results
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {})
    sent = []
    monkeypatch.setattr(hd, "_ssm_run", lambda *a: sent.append(a) or FIXTURE.read_text())
    hd._cache.clear()
    return inst, other, sent


def test_other_account_or_stopped_hive_is_not_reachable_without_ssm(target_env):
    inst, other, sent = target_env
    other["on"] = True
    r = asyncio.run(hd.read("so_aaaaaaaaaaaa"))
    assert r["reachable"] is False and r["reason"].startswith("other_account")
    other["on"] = False
    inst["status"] = "stopped"
    hd._cache.clear()
    assert asyncio.run(hd.read("so_aaaaaaaaaaaa"))["reason"].startswith("hive_not_running")
    inst["gone"] = True
    assert asyncio.run(hd.read("so_aaaaaaaaaaaa"))["reason"].startswith("hive_gone")
    assert sent == []


def test_a_repeat_within_60s_makes_no_ssm_call(target_env, monkeypatch):
    _inst, _other, sent = target_env
    first = asyncio.run(hd.read("so_aaaaaaaaaaaa"))
    assert first["reachable"] is True and first["cached"] is False and len(sent) == 1
    assert {x["kind"] for x in first["findings"]} >= {"oom_kill", "service_stopped", "session_error"}
    assert first["hive_written"] is True and first["window"]["since"].endswith("Z")
    again = asyncio.run(hd.read("so_aaaaaaaaaaaa"))
    assert again["cached"] is True and len(sent) == 1
    real = hd.time.monotonic
    monkeypatch.setattr(hd.time, "monotonic", lambda: real() + hd.CACHE_S + 1)
    asyncio.run(hd.read("so_aaaaaaaaaaaa"))
    assert len(sent) == 2


def test_diagnose_is_an_owner_agent_read_the_cli_calls(monkeypatch):
    from core import swarm_out_ops
    from scripts import swarm_out as cli
    op = next(o for o in swarm_out_ops.ops() if o.name == "run.diagnostics")
    assert (op.method, op.path, op.weight) == ("GET", "/api/swarm-out/runs/{dispatch_id}/diagnostics", "read")
    assert op.callers == frozenset({swarm_out_ops.PERSON, swarm_out_ops.OWNER})
    calls = []
    monkeypatch.setattr(cli, "_call", lambda method, path, *a, **kw: calls.append((method, path, kw.get("framed"))) or 0)
    assert cli.main(["diagnose", "so_aaaaaaaaaaaa"]) == 0
    assert calls == [("GET", "/runs/so_aaaaaaaaaaaa/diagnostics", True)]


# ── review round 1 ───────────────────────────────────────────────────────────

def test_a_silent_runs_window_reads_up_to_now(target_env, monkeypatch):
    """A run whose result is not final may have died after its last record (its Hive
    went silent later): the journals are read up to now. A final result's run is read
    up to its last activity, so later runs do not crowd it out of the budgets."""
    from core import swarm_out_results
    old = "2026-11-02T06:30:00Z"
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "result": {"ended": None, "provisional": True}, "records": {"last_at": old}})
    t = asyncio.run(hd._target("so_aaaaaaaaaaaa"))
    assert (datetime.now(timezone.utc) - t["until"]).total_seconds() < 60
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "result": {"ended": "completed", "provisional": False, "ended_at": old}, "records": {"last_at": old}})
    t = asyncio.run(hd._target("so_aaaaaaaaaaaa"))
    assert t["until"] == datetime(2026, 11, 2, 6, 35, tzinfo=timezone.utc)


def test_a_missing_hive_or_account_is_not_reachable_not_an_error(target_env, monkeypatch):
    from core import swarm_out
    from hive.provisioner import HiveProvisioner
    _inst, _other, sent = target_env
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: {"session_id": SID})
    assert asyncio.run(hd.read("so_aaaaaaaaaaaa"))["reason"].startswith("hive_gone")
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: {"hive_id": "h1", "session_id": SID})

    async def no_account(self, ref):
        raise ValueError("Account a1 not found")
    monkeypatch.setattr(HiveProvisioner, "_get_account", no_account)
    assert asyncio.run(hd.read("so_bbbbbbbbbbbb"))["reason"].startswith("no_account")
    assert sent == []


def test_the_route_answers_400_for_a_bad_session_id(monkeypatch):
    from fastapi import HTTPException
    from routers import swarm_out as route
    monkeypatch.setattr(route, "_require_desktop", lambda: None)

    async def bad(dispatch_id):
        raise ValueError("not a session id this read can use")
    monkeypatch.setattr(hd, "read", bad)
    with pytest.raises(HTTPException) as e:
        asyncio.run(route.run_diagnostics("so_aaaaaaaaaaaa"))
    assert e.value.status_code == 400 and "session id" in str(e.value.detail)


def test_concurrent_reads_share_one_command_and_at_most_two_run(target_env, monkeypatch):
    import time as _t
    _inst, _other, sent = target_env

    def slow(*a):
        sent.append(a)
        _t.sleep(0.3)
        return FIXTURE.read_text()
    monkeypatch.setattr(hd, "_ssm_run", slow)

    async def same():
        return await asyncio.gather(hd.read("so_aaaaaaaaaaaa"), hd.read("so_aaaaaaaaaaaa"))
    a, b = asyncio.run(same())
    assert len(sent) == 1 and a["reachable"] and b["reachable"] and b["cached"] is True
    hd._cache.clear()
    sent.clear()

    async def three():
        return await asyncio.gather(*(hd.read(f"so_{c * 12}") for c in "abc"))
    out = asyncio.run(three())
    assert len(sent) == 2 and [o.get("reason", "").split(":")[0] for o in out].count("busy") == 1
    assert hd._inflight == {}


def test_the_command_stops_on_the_hive_after_its_timeout(monkeypatch):
    seen = {}

    class Client:
        def send_command(self, **kw):
            seen.update(kw)
            return {"Command": {"CommandId": "c1"}}

        def get_command_invocation(self, **kw):
            return {"Status": "Success", "StandardOutputContent": "@@ state 0 0\n"}

    class Session:
        def client(self, name, region_name=None):
            assert name == "ssm"
            return Client()
    monkeypatch.setattr(hd, "POLL_S", 0)
    assert hd._ssm_run(Session(), "us-east-1", "i-0abc", "echo") == "@@ state 0 0\n"
    assert seen["Parameters"]["executionTimeout"] == [str(hd.SSM_TIMEOUT_S)]
    assert seen["Parameters"]["commands"] == ["echo"] and seen["InstanceIds"] == ["i-0abc"]
