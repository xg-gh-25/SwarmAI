"""Swarm Out: what the person is shown is what gets delivered (fixing spec 2026-10-28 B).

Questions each test answers:
- do the Hive's bridge and the Host compute the same digest for the same request, for a
  Bash command, a non-Bash tool with non-ASCII input, and a command too long to show?
- is a refused answer shown to the person as not delivered (never as if it went through)?
- does a Host reading an older Hive's ask (no ``truncated`` field) still refuse to approve
  a command the Hive cut at the limit?
- are counts the Hive dropped carried to the card, and does one malformed ask hide no other?
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from core import swarm_out
from tests import _hive_work_fake as _work
from core.swarm_out_bridge import MAX_COMMAND_CHARS, _ask_record, approval_digest, approval_view


@pytest.fixture(autouse=True)
def _no_running_hives_from_the_store(monkeypatch):
    """list_runs also reads the Hives' status from the Host's store: here the tests'
    Hive h1 is running (a test that needs another store sets it)."""
    async def h1_running():
        return {"h1": "running"}
    monkeypatch.setattr(swarm_out, "_hive_statuses", h1_running)

NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
INST = {"id": "h1", "name": "worker-1", "status": "running", "cloudfront_domain": "abc123.cloudfront.net",
        "s3_bucket": "swarmai-hive-2361-us-east-1", "region": "us-east-1",
        "auth_user": "admin", "auth_password": "s3cret-pass", "account_ref": "a1"}


def _board_item(tool, tool_input):
    return {"toolName": tool, "toolInput": tool_input, "reason": "r", "requestId": "req-1"}


@pytest.mark.parametrize("tool,tool_input", [
    ("Bash", {"command": "rm -rf /tmp/x"}),
    ("Bash", {"command": "ls", "dangerouslyDisableSandbox": True}),
    ("mcp__deploy__run", {"host": "prod-東京", "target": "🚀", "command": "ship"}),
    ("Bash", {"command": "echo " + "x" * (MAX_COMMAND_CHARS + 10)}),
    ("Bash", {"command": "echo \ud800"}),                 # a lone surrogate from the model
])
def test_bridge_and_host_compute_the_same_digest(tool, tool_input):
    """The round trip the real system makes: bridge record → S3 JSON → Host clean."""
    rec = _ask_record("worker-1", "sess-1", "req-1", _board_item(tool, tool_input))
    wire = json.dumps(rec, ensure_ascii=True).encode()     # the bytes the bridge writes
    on_host = swarm_out._clean_ask(json.loads(wire), "worker-1")
    assert on_host["shown_sha256"] == approval_digest(rec["tool_name"], rec["command"], rec["reason"])
    assert on_host["truncated"] is rec["truncated"]


def test_a_non_bash_tool_shows_and_binds_its_whole_input():
    view = approval_view("mcp__deploy__run", {"command": "ship", "host": "prod"})
    assert '"host": "prod"' in view and '"command": "ship"' in view


def test_bash_shows_a_disabled_sandbox():
    assert "dangerouslyDisableSandbox" in approval_view("Bash", {"command": "ls", "dangerouslyDisableSandbox": True})
    assert approval_view("Bash", {"command": "ls"}) == "ls"


def test_a_lone_surrogate_hashes_instead_of_raising():
    assert len(approval_digest("Bash", "echo \ud800")) == 64


def test_an_older_hive_cut_at_the_limit_cannot_be_approved():
    old = {"kind": "approval", "session_id": "s", "request_id": "r", "asked_at": NOW,
           "tool_name": "Bash", "command": "x" * MAX_COMMAND_CHARS}   # no "truncated"
    assert swarm_out._clean_ask(old, "worker-1")["truncated"] is True
    short = {**old, "command": "ls"}
    assert swarm_out._clean_ask(short, "worker-1")["truncated"] is False


def test_counts_the_hive_dropped_reach_the_card():
    item = {"kind": "ask_user_question",
            "questions": [{"question": "q", "options": [{"label": f"o{i}"} for i in range(13)]}] * 22}
    rec = _ask_record("worker-1", "s", "r", item)
    assert rec["omitted"] == {"questions": 2, "options": 20 * 3}
    on_host = swarm_out._clean_ask(json.loads(json.dumps(rec)), "worker-1")
    assert on_host["omitted"]["questions"] >= 2 and on_host["omitted"]["options"] >= 60


# ── the Host's listing: refused answers stay visible as not delivered ──────────

class ListingS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects):
        self.objects = objects

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "LastModified": datetime.now(timezone.utc)}
                                    for k in s3.objects if k.startswith(Prefix)]}
        return P()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        body = self.objects[Key]

        class B:
            def read(self, n=-1):
                return body
        return {"Body": B()}


B = "bridge/worker-1/"


def _ask(sid, rid, command="ls"):
    return json.dumps({"kind": "approval", "session_id": sid, "request_id": rid, "asked_at": NOW,
                       "tool_name": "Bash", "command": command, "truncated": False}).encode()


def test_a_refused_answer_is_listed_as_not_delivered():
    s3 = ListingS3({
        B + "ask/s1/r1.json": _ask("s1", "r1"),
        B + "answer/s1/r1.json": b"{}",
        B + "closed/s1/r1.json": json.dumps({"request_id": "r1", "why": "answer_refused"}).encode(),
        B + "ask/s2/r2.json": _ask("s2", "r2"),
        B + "answer/s2/r2.json": b"{}",                     # answered, waiting for delivery
        B + "ask/s3/r3.json": _ask("s3", "r3"),
        B + "closed/s3/r3.json": json.dumps({"request_id": "r3"}).encode(),   # done
        B + "ask/s4/r4.json": _ask("s4", "r4"),             # still open
    })
    asks = swarm_out._open_asks(s3, "bucket", "worker-1")
    by_rid = {a["request_id"]: a for a in asks}
    assert set(by_rid) == {"r1", "r4"}
    assert by_rid["r1"]["refused"] is True and "refused" not in by_rid["r4"]


def test_one_malformed_ask_hides_no_other():
    s3 = ListingS3({B + "ask/s1/r1.json": b"{not json", B + "ask/s2/r2.json": _ask("s2", "r2")})
    assert [a["request_id"] for a in swarm_out._open_asks(s3, "bucket", "worker-1")] == ["r2"]


# ── answering ────────────────────────────────────────────────────────────────

@pytest.fixture
def answer_env(monkeypatch):
    objects = {B + "ask/s1/r1.json": _ask("s1", "r1")}

    class S3(ListingS3):
        def put_object(self, Bucket, Key, Body, **kw):
            self.objects[Key] = Body
    s3 = S3(objects)

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": s3)
    return s3


def test_an_approve_without_the_shown_content_is_refused(answer_env):
    with pytest.raises(swarm_out.SwarmOutError, match="changed"):
        asyncio.run(swarm_out.answer("h1", "s1", "r1", decision="approve", shown_sha256=None))


def test_a_deny_is_written_without_a_content_check(answer_env):
    asyncio.run(swarm_out.answer("h1", "s1", "r1", decision="deny"))
    assert json.loads(answer_env.objects[B + "answer/s1/r1.json"])["decision"] == "deny"


def test_a_read_timeout_is_recorded_like_a_timeout(monkeypatch, tmp_path):
    import httpx

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)

    def handler(request):
        if request.url.path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json=_work.ADMISSION)
        if _work.answer(request) is not None:
            return _work.answer(request)
        raise httpx.ReadTimeout("slow", request=request)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url=swarm_out._base(inst), transport=httpx.MockTransport(handler)))
    with pytest.raises(swarm_out.SwarmOutError, match="may be running"):
        asyncio.run(swarm_out.dispatch("worker-1", "Fix it", "do X"))
    row = json.loads((tmp_path / "dispatches.jsonl").read_text())
    assert row["session_id"] is None


def test_list_runs_names_owners_and_marks_an_unnamed_session(monkeypatch):
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "worker-1", "session_id": "s1", "title": "Known"},
            {"dispatch_id": "d2", "hive_id": "h1", "hive": "worker-1", "session_id": None, "title": "Timed out"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [
        {"kind": "approval", "session_id": "s1", "request_id": "r1"},
        {"kind": "approval", "session_id": "s9", "request_id": "r9"}])
    out = asyncio.run(swarm_out.list_runs())
    by_rid = {a["request_id"]: a for a in out["asks"]}
    assert by_rid["r1"]["dispatch_title"] == "Known" and by_rid["r1"]["possible_dispatch_title"] is None
    assert by_rid["r9"]["dispatch_title"] is None and by_rid["r9"]["possible_dispatch_title"] == "Timed out"
    assert {r["dispatch_id"]: r["session_unknown"] for r in out["runs"]} == {"d1": False, "d2": True}



def test_a_rewritten_reason_changes_the_digest():
    assert approval_digest("Bash", "ls", "lists files") != approval_digest("Bash", "ls", "routine, safe")


def test_an_old_close_is_not_read_and_not_listed():
    from datetime import timedelta
    reads = []

    class Old(ListingS3):
        def get_paginator(self, name):
            s3 = self

            class P:
                def paginate(self, Bucket, Prefix):
                    old = datetime.now(timezone.utc) - timedelta(seconds=swarm_out.ASK_MAX_AGE_S + 60)
                    yield {"Contents": [{"Key": k, "LastModified": old} for k in s3.objects if k.startswith(Prefix)]}
            return P()

        def get_object(self, Bucket, Key):
            reads.append(Key)
            return super().get_object(Bucket, Key)
    s3 = Old({B + "ask/s1/r1.json": _ask("s1", "r1"), B + "answer/s1/r1.json": b"{}",
              B + "closed/s1/r1.json": json.dumps({"why": "answer_refused"}).encode()})
    assert swarm_out._open_asks(s3, "bucket", "worker-1") == []
    assert reads == []


def test_a_refused_answer_marks_its_run_waiting_on_the_person(monkeypatch):
    """Design 2026-10-31-swarm-out-single-loop-fixes §2.1: an answer the Hive did not use
    leaves the run waiting on the person (answer on the Hive's page, or discard)."""
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "worker-1", "session_id": "s1", "title": "T"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [
        {"kind": "approval", "session_id": "s1", "request_id": "r1", "refused": True}])
    run = asyncio.run(swarm_out.list_runs())["runs"][0]
    assert (run["state"], run["state_reason"]) == ("waiting", "answer_refused")


def test_a_plain_close_with_no_answer_is_not_read():
    reads = []

    class Counting(ListingS3):
        def get_object(self, Bucket, Key):
            reads.append(Key)
            return super().get_object(Bucket, Key)
    s3 = Counting({B + "ask/s1/r1.json": _ask("s1", "r1"),
                   B + "closed/s1/r1.json": json.dumps({"request_id": "r1"}).encode()})
    assert swarm_out._open_asks(s3, "bucket", "worker-1") == []
    assert reads == []


def test_a_dispatch_with_no_session_id_is_not_reported_as_stopped(monkeypatch):
    """A timed-out dispatch has no session id to look up: running is unknown, not False."""
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "worker-1", "session_id": "s1", "title": "A"},
            {"dispatch_id": "d2", "hive_id": "h1", "hive": "worker-1", "session_id": None, "title": "B"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})

    async def live(inst):
        return {"s1": {"streaming": True, "waiting_input": False, "activity": "streaming"}}
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [])
    monkeypatch.setattr(swarm_out, "_session_states", live)
    out = asyncio.run(swarm_out.list_runs())
    assert {r["dispatch_id"]: r["state"] for r in out["runs"]} == {"d1": "running", "d2": "unknown"}


def test_a_running_hive_never_dispatched_to_still_shows_its_questions(monkeypatch):
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])

    async def running():
        return {"h1": "running"}

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})

    async def live(inst):
        return {}
    monkeypatch.setattr(swarm_out, "_hive_statuses", running)
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [
        {"kind": "approval", "session_id": "s7", "request_id": "r7"}])
    monkeypatch.setattr(swarm_out, "_session_states", live)
    out = asyncio.run(swarm_out.list_runs())
    assert [a["request_id"] for a in out["asks"]] == ["r7"]
    assert out["asks"][0]["dispatch_title"] is None and out["errors"] == []


def test_an_unreadable_store_keeps_the_logs_hives(monkeypatch):
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "worker-1", "session_id": "s1", "title": "A"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def broken():
        raise OSError("db locked")

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_hive_statuses", broken)
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [
        {"kind": "approval", "session_id": "s1", "request_id": "r1"}])
    out = asyncio.run(swarm_out.list_runs())
    assert [a["request_id"] for a in out["asks"]] == ["r1"]
    assert out["errors"] == ["could not list the running Hives"]
