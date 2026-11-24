"""``swarm_out diagnose`` on an Elastic Workers (AgentCore) round (core/hive_diagnostics,
core/carriers/agentcore ``log_events``). No network: the CloudWatch Logs client is a fake
reached through the location's account session.

Questions each test answers:
- does diagnose read the round's CloudWatch lines (those naming its runtime session or its
  chat session, and a stream named by the session) in the round's window, through the
  account session for ``read``, and report them in the EC2 shape: a ``session`` section
  and ``session_error`` with the last lines, or ``none_seen``?
- does a runtime with no log group, or CloudWatch refusing the read, answer
  ``reachable: false`` with the reason, never an exception?
"""
from __future__ import annotations

import dataclasses
import time

import pytest

from core import hive_diagnostics as hd
from core import swarm_out
from core.carriers import agentcore as ac
from core.carriers import registry
from tests.test_carriers_agentcore import DID, FakeSession, _err, _open, world  # noqa: F401 — the fixture

GROUP = ac.LOG_GROUP_ROOT + "swarm_hive-AbC123" + "-DEFAULT"   # the fake runtime's one endpoint
CHAT = "chat-0000000001"


class FakeLogs:
    def __init__(self):
        self.groups = [GROUP, "/aws/bedrock-agentcore/runtimes/another_runtime-DEFAULT"]
        self.events: list[dict] = []
        self.streams: list[str] = []
        self.calls: list[tuple] = []
        self.error = None
        self.page = 1000

    def describe_log_groups(self, logGroupNamePrefix, **kw):
        self.calls.append(("groups", logGroupNamePrefix))
        if self.error:
            raise _err(self.error, "DescribeLogGroups")
        return {"logGroups": [{"logGroupName": g} for g in self.groups if g.startswith(logGroupNamePrefix)]}

    def describe_log_streams(self, logGroupName, **kw):
        self.calls.append(("streams", logGroupName))
        return {"logStreams": [{"logStreamName": s} for s in self.streams]}

    def filter_log_events(self, **kw):
        self.calls.append(("filter", kw))
        group = [e for e in self.events if e.get("group", GROUP) == kw["logGroupName"]]
        if "logStreamNames" in kw:
            got = [e for e in group if e["logStreamName"] in kw["logStreamNames"]]
        else:
            terms = [t.strip('?"') for t in kw["filterPattern"].split()]
            got = [e for e in group if any(t in e["message"] for t in terms)]
        got = sorted((e for e in got if kw["startTime"] <= e["timestamp"] <= kw["endTime"]),
                     key=lambda e: e["timestamp"])        # oldest first, as CloudWatch pages them
        start = int(kw.get("nextToken") or 0)
        page = got[start:start + self.page]
        more = start + self.page < len(got)
        return {"events": page, **({"nextToken": str(start + self.page)} if more else {})}


@pytest.fixture
def diag(world, monkeypatch):                       # noqa: F811 — the imported fixture
    logs = FakeLogs()

    class Session(FakeSession):
        def client(self, service, region_name=None, config=None):
            return logs if service == "logs" else super().client(service, region_name, config)

    def session_for(acct, region, purpose):
        world.purposes.append((acct["id"], region, purpose))
        return Session(world)
    monkeypatch.setattr(ac, "_session_for", session_for)
    monkeypatch.setitem(registry._made, "agentcore", world.carrier)
    hd._cache.clear()
    rows: dict = {}
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: rows[d])

    async def make(age_s: float = 3600):
        h = await _open(world)
        h = dataclasses.replace(h, opened_at=time.time() - age_s)
        rows[DID] = {"dispatch_id": DID, "hive_id": "ac1", "hive": "elastic", "session_id": CHAT,
                     "rounds": [h.with_session(CHAT).to_row()]}
        return h
    return logs, make


def _ev(at_s: float, msg: str, stream: str = "2026/11/24/[runtime-logs]abc", group: str = GROUP) -> dict:
    return {"timestamp": int(at_s * 1000), "message": msg, "logStreamName": stream, "eventId": f"{at_s}{msg}",
            "group": group}


async def test_agentcore_diagnose_reads_the_sessions_log_lines(diag, world):        # noqa: F811
    logs, make = diag
    h = await make()
    sid = h.run_ref
    now = time.time()
    logs.events = [_ev(now - 3000, f"supervisor: start {sid}"),
                   _ev(now - 2000, f"ERROR session {CHAT} stream_error: model refused"),
                   _ev(now - 1000, "unrelated line of another session"),
                   _ev(now - 500, "Traceback ... exit code 137", stream=f"2026/11/24/[runtime-logs]{sid}"),
                   _ev(now - 10 * 3600, f"ERROR {sid} before the round started")]
    logs.streams = [f"2026/11/24/[runtime-logs]{sid}", "2026/11/24/[runtime-logs]other"]
    out = await hd.read(DID)
    assert out["reachable"] is True and out["carrier"] == "agentcore" and out["session_id"] == sid
    lines = out["sections"]["session"]["lines"]
    assert len(lines) == 3 and lines[0].endswith(f"supervisor: start {sid}")
    assert not any("unrelated" in x or "before the round" in x for x in lines)
    f = out["findings"]
    assert [x["kind"] for x in f] == ["session_error"] and f[0]["count"] == 2
    assert f[0]["last"][-1].endswith("Traceback ... exit code 137")
    assert out["log_groups"] == [GROUP]
    # the filter names the round's two session ids, inside the round's window
    flts = [kw for kind, kw in logs.calls if kind == "filter" and "filterPattern" in kw]
    assert all(f["filterPattern"] == f'?"{sid}" ?"{CHAT}"' and f["logGroupName"] == GROUP for f in flts)
    assert min(f["startTime"] for f in flts) <= int(h.opened_at * 1000)
    assert max(f["endTime"] for f in flts) <= int(time.time() * 1000) + 1
    assert out["truncated"] is False
    assert ("acct1", "us-east-1", "read") in world.purposes
    assert world.requests == []                      # nothing reached the session
    # a repeat within the cache window reads nothing again
    n = len(logs.calls)
    assert (await hd.read(DID))["cached"] is True and len(logs.calls) == n


async def test_agentcore_diagnose_with_no_error_lines_sees_none(diag):
    logs, make = diag
    h = await make()
    logs.events = [_ev(time.time() - 100, f"supervisor: start {h.run_ref}")]
    out = await hd.read(DID)
    assert out["reachable"] is True and out["findings"] == [{"kind": "none_seen"}]


async def test_agentcore_diagnose_without_a_log_group(diag):
    logs, make = diag
    await make()
    logs.groups = []
    out = await hd.read(DID)
    assert out["reachable"] is False and out["reason"].startswith("no_log_group:")


async def test_agentcore_diagnose_when_cloudwatch_refuses(diag):
    logs, make = diag
    await make()
    logs.error = "AccessDeniedException"
    out = await hd.read(DID)
    assert out["reachable"] is False and out["reason"].startswith("logs_failed:")
    assert "AccessDeniedException" in out["reason"] and "logs:FilterLogEvents" in out["reason"]


async def test_an_expired_rounds_window_ends_with_its_lifetime(diag):
    logs, make = diag
    h = await make(age_s=ac.MAX_RUN_S + 3 * 3600)
    await hd.read(DID)
    flt = [kw for kind, kw in logs.calls if kind == "filter"][0]
    assert flt["endTime"] == int(ac.writes_end_at(h) * 1000)


async def test_the_carrier_has_the_diagnostics_duty_and_reads_no_bad_term():
    from core.carriers.base import DIAGNOSTICS
    assert DIAGNOSTICS in ac.AgentCoreCarrier.duties
    h = ac.RunHandle(carrier="agentcore", location_id="ac1", round=1, run_ref="x",
                     extra={"runtime_arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-1",
                            "region": "us-east-1"})
    with pytest.raises(ValueError):
        await ac.AgentCoreCarrier().log_events(h, 0, 1, ['a" OR "b'])


async def test_a_long_round_keeps_its_newest_lines_and_says_it_was_cut(diag, monkeypatch):
    logs, make = diag
    h = await make(age_s=7 * 3600)
    now = time.time()
    # one line naming the session every ten minutes for seven hours, three per page
    logs.events = [_ev(now - 7 * 3600 + i * 600, f"ERROR {h.run_ref} step {i}") for i in range(42)]
    logs.page = 3
    # two listings leave ten filter calls: far fewer than reading all 42 lines oldest first takes
    monkeypatch.setattr(ac, "LOG_CALLS_MAX", 12)
    out = await hd.read(DID)
    lines = out["sections"]["session"]["lines"]
    # read from the window's end backwards: the round's end is what is kept, never what is cut
    assert lines[-1].endswith("step 41") and lines[-2].endswith("step 40") and out["truncated"] is True
    # each step back starts from the last piece's width, not the whole window again: ten
    # calls read the last two hours whole
    assert [x.rsplit(" ", 1)[-1] for x in lines[-13:]] == [str(i) for i in range(29, 42)]
    assert out["findings"][0]["last"][-1].endswith("step 41")
    windows = [(kw["startTime"], kw["endTime"]) for kind, kw in logs.calls if kind == "filter"]
    assert all(w[1] >= int((now - 60) * 1000) for w in windows[:3])
    # within one read, the newest LOG_EVENTS_MAX are kept
    hd._cache.clear()
    logs.calls.clear()
    monkeypatch.setattr(ac, "LOG_CALLS_MAX", 60)
    monkeypatch.setattr(ac, "LOG_EVENTS_MAX", 5)
    lines = (await hd.read(DID))["sections"]["session"]["lines"]
    assert len(lines) == 5 and lines[0].endswith("step 37") and lines[-1].endswith("step 41")
    # and it stops once it holds them: a few calls, not a read of the whole window
    assert len([c for c in logs.calls if c[0] == "filter"]) <= 15


async def test_every_log_group_reads_its_newest_hour_before_one_takes_more(diag, monkeypatch):
    logs, make = diag
    h = await make()
    other = "/aws/bedrock-agentcore/runtimes/swarm_hive-AbC123-canary"
    logs.groups = [GROUP, other]
    now = time.time()
    # the first group is busy (twenty pages in the last hour); the session ended on the other
    logs.events = [_ev(now - 3000 + i, f"INFO {h.run_ref} tick {i}") for i in range(60)] + \
        [_ev(now - 40, f"ERROR {h.run_ref} out of memory"),
         _ev(now - 30, f"ERROR {h.run_ref} killed at its lifetime", group=other)]
    logs.page = 3
    # three listings (groups, two stream listings) leave nine filter calls: each read first
    # gets its share (four, then five); what the other group does not need goes back to the
    # busy group
    monkeypatch.setattr(ac, "LOG_CALLS_MAX", 12)
    out = await hd.read(DID)
    assert out["log_groups"] == sorted([GROUP, other]) and out["truncated"] is True
    lines = out["sections"]["session"]["lines"]
    # both groups' last lines are read, the busy group's too, though its pages did not fit
    assert lines[-1].endswith("killed at its lifetime") and lines[-2].endswith("out of memory")
    assert out["findings"][0]["kind"] == "session_error"
    # the calls the other group did not need went back to the busy group: none left unused
    assert len([c for c in logs.calls if c[0] == "filter"]) == 9
    # and the busy group goes on from where it was cut, not from the window's start again
    busy = [(kw["startTime"], kw["endTime"]) for kind, kw in logs.calls
            if kind == "filter" and kw["logGroupName"] == GROUP]
    assert len(busy) == len(set(busy))


async def test_a_read_cut_while_narrowing_goes_on_from_there(diag, monkeypatch):
    logs, make = diag
    h = await make()
    other = "/aws/bedrock-agentcore/runtimes/swarm_hive-AbC123-canary"
    logs.groups = [GROUP, other]
    now = time.time()
    logs.events = [_ev(now - 200 + i, f"INFO {h.run_ref} tick {i}") for i in range(200)]
    logs.page = 3
    # three listings leave ten filter calls: five for the busy group, which is cut while it
    # narrows, one the other group needs, four back to the busy group
    monkeypatch.setattr(ac, "LOG_CALLS_MAX", 13)
    out = await hd.read(DID)
    assert out["truncated"] is True
    busy = [(kw["startTime"], kw["endTime"]) for kind, kw in logs.calls
            if kind == "filter" and kw["logGroupName"] == GROUP]
    assert len(busy) == 9 and len(busy) == len(set(busy))       # no window asked for twice
    assert busy[-1][1] - busy[-1][0] < busy[4][1] - busy[4][0]   # it went on narrowing


async def test_a_read_stops_at_its_call_budget(diag, monkeypatch):
    logs, make = diag
    h = await make()
    logs.events = [_ev(time.time() - 60, f"ERROR {h.run_ref} late")]
    monkeypatch.setattr(ac, "LOG_CALLS_MAX", 2)            # the group listing and one stream listing
    out = await hd.read(DID)
    assert out["reachable"] is True and out["truncated"] is True
    assert not [c for c in logs.calls if c[0] == "filter"]


async def test_health_ping_access_lines_never_crowd_out_the_sessions_lines(diag):
    """The supervisor's "GET /ping" lines share the session's stream every 2 s: a real
    round read 678 lines, all pings. They are dropped (a 5xx answer is kept) and counted."""
    logs, make = diag
    h = await make()
    stream = f"2026/11/24/[runtime-logs]{h.run_ref}"
    logs.streams = [stream]
    now = time.time()
    logs.events = [_ev(now - 3000, "backend: ERROR stream_error in turn", stream)]
    logs.events += [_ev(now - 2900 + i * 2, 'supervisor: "GET /ping HTTP/1.1" 200 -', stream) for i in range(1200)]
    logs.events += [_ev(now - 10, 'supervisor: "POST /invocations HTTP/1.1" 503 -', stream)]
    out = await hd.read(DID)
    sec = out["sections"]["session"]
    assert not any("/ping" in x for x in sec["lines"])
    assert any("ERROR stream_error" in x for x in sec["lines"])
    assert any(" 503 " in x for x in sec["lines"])
    assert sec["access_lines_dropped"] == 1200
    assert out["findings"][0]["kind"] == "session_error"
