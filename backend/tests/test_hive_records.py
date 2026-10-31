"""A Hive run's record (core/hive_records; design
docs/design/2026-10-29-hive-run-record-and-worker-role.md §4, invariants I2–I5, I8, I9):
sealed once in order on the Hive, taken back once by the host, and read only from the
host's copy after that.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

SID = "sess_record_test_1"
DID = "so_" + "d" * 12


@pytest.fixture
def hive_ws(tmp_path, monkeypatch):
    ws = tmp_path / "hive"
    ws.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "hive-data"))
    return ws


def _turn_rows(text: str, *, cmd: str = "pytest -q", err: bool = False, i: str = "a") -> list[dict]:
    return [
        {"id": f"u{i}", "role": "user", "created_at": "2026-10-29T01:00:00Z", "content": [{"type": "text", "text": "go"}]},
        {"id": f"a{i}", "role": "assistant", "created_at": "2026-10-29T01:00:01Z", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd},
             "display": {"tool": "Bash", "object": cmd}},
            {"type": "thinking", "thinking": "secret reasoning"}]},
        {"id": f"r{i}", "role": "assistant", "created_at": "2026-10-29T01:00:02Z", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err, "content": "x" * 50_000}]},
        {"id": f"o{i}", "role": "assistant", "created_at": "2026-10-29T01:00:03Z", "content": [
            {"type": "text", "text": text},
            {"type": "turn_outcome", "ended": "completed", "at": "2026-10-29T01:00:03+00:00",
             "duration_ms": 1000, "usage": {"input_tokens": 10, "output_tokens": 5}}]},
    ]


# ── the Hive side ────────────────────────────────────────────────────────────

def test_entries_are_sealed_in_order_and_never_twice(hive_ws):
    from core import hive_records as hr
    a = hr.append(hive_ws, SID, "turn", data=b'{"rows": []}')
    tmp = hr.tmp_path(hive_ws, SID, "0" * 16)
    tmp.write_bytes(b"bundle bytes")
    b = hr.append(hive_ws, SID, "bundle", src=tmp, key="0" * 16)
    assert (a["seq"], b["seq"]) == (1, 2) and not tmp.exists()
    d = hr.session_dir(hive_ws, SID)
    assert (d / b["file"]).read_bytes() == b"bundle bytes"
    assert b["sha256"] == hashlib.sha256(b"bundle bytes").hexdigest() and b["size"] == 12
    seqs: list[int] = []

    def many():
        for _ in range(10):
            seqs.append(hr.append(hive_ws, SID, "closed", data=b"{}")["seq"])
    ts = [threading.Thread(target=many) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(seqs) == list(range(3, 43))                      # each seq once
    listed = hr.entries(hive_ws, SID, after=40)
    assert [e["seq"] for e in listed["entries"]] == [41, 42] and not listed["more"]
    page = hr.entries(hive_ws, SID, after=0, limit=5)
    assert [e["seq"] for e in page["entries"]] == [1, 2, 3, 4, 5] and page["more"]


def test_what_an_entry_may_not_be(hive_ws, tmp_path):
    from core import hive_records as hr
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "notes", data=b"x")
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "bundle", data=b"x")               # a bundle needs its key
    with pytest.raises(ValueError):
        hr.append(hive_ws, "../etc", "turn", data=b"x")
    outside = tmp_path / "outside"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "bundle", src=outside, key="1" * 16)
    assert outside.exists()
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "closed", data=b"x" * 5000)        # over its cap
    d = hr.session_dir(hive_ws, SID)
    assert [p.name for p in d.iterdir() if not p.name.startswith(".")] in ([], [hr.MANIFEST])
    assert hr.entries(hive_ws, SID)["entries"] == []


def test_a_torn_last_manifest_line_ends_the_list(hive_ws):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    d = hr.session_dir(hive_ws, SID)
    with open(d / hr.MANIFEST, "a") as f:
        f.write('{"seq": 2, "kind": "tu')
    with open(d / hr.MANIFEST, "a") as f:                        # a later line after the torn one
        f.write(json.dumps({"seq": 7, "kind": "closed", "file": "x", "sha256": "0" * 64, "size": 1}) + "\n")
    assert [e["seq"] for e in hr.entries(hive_ws, SID)["entries"]] == [1]   # nothing after a tear
    d.joinpath(hr.MANIFEST).write_text(d.joinpath(hr.MANIFEST).read_text().split("\n")[0] + "\n"
                                       + '{"seq": 2, "kind": "tu')
    assert hr.append(hive_ws, SID, "closed", data=b"{}")["seq"] == 2     # the tear is cut first
    assert [e["seq"] for e in hr.entries(hive_ws, SID)["entries"]] == [1, 2]


def test_an_entry_is_served_only_as_listed(hive_ws, tmp_path):
    from core import hive_records as hr
    e = hr.append(hive_ws, SID, "closed", data=b'{"reason": "reviewed"}')
    fd, size = hr.open_entry(hive_ws, SID, 1)
    assert os.read(fd, 100) == b'{"reason": "reviewed"}' and size == e["size"]
    os.close(fd)
    with pytest.raises(FileNotFoundError):
        hr.open_entry(hive_ws, SID, 2)
    p = hr.session_dir(hive_ws, SID) / e["file"]
    p.write_bytes(b"changed afterwards, longer")
    with pytest.raises(FileNotFoundError):
        hr.open_entry(hive_ws, SID, 1)                            # not the size listed
    p.unlink()
    (tmp_path / "t").write_bytes(b'{"reason": "reviewed"}')
    p.symlink_to(tmp_path / "t")
    with pytest.raises(OSError):
        hr.open_entry(hive_ws, SID, 1)


def test_a_turn_is_the_rows_after_the_last_sealed_row_projected_for_the_summary():
    from core import hive_records as hr
    from core import swarm_out_results as sor
    one, two = _turn_rows("first", i="a"), _turn_rows("second", cmd="npm run build", err=True, i="b")
    assert hr.turn_slice(one) == (one, True)
    assert hr.turn_slice(one + two, after_id="oa") == (two, False)
    proj = [json.loads(hr.turn_projection(hr.turn_slice(r, after_id=a)[0]))["rows"]
            for r, a in ((one, None), (one + two, "oa"))]
    body = json.dumps(proj)
    assert "secret reasoning" not in body and "x" * 3000 not in body            # dropped / capped
    agent = {"brain": "B", "validations": [{"id": "tests", "command": "pytest -q"},
                                           {"id": "build", "command": "npm run build"}]}
    from_record = sor.summarize(proj[0] + proj[1], False, agent)
    direct = sor.summarize(one + two, False, agent)
    for k in ("ended", "turns", "checks", "tokens", "report", "kinds", "cost_partial"):
        assert from_record[k] == direct[k], k
    assert from_record["checks"] == [{"id": "tests", "status": "ran"}, {"id": "build", "status": "errored"}]


def test_a_too_large_turn_keeps_its_end():
    from core import hive_records as hr
    rows = [{"role": "assistant", "content": [{"type": "text", "text": "y" * 150_000}]} for _ in range(10)]
    rows += _turn_rows("the end")
    body = hr.turn_projection(rows)
    got = json.loads(body)
    assert len(body) <= hr.MAX_BYTES["turn"] and got["truncated"]
    assert got["rows"][-1]["content"][0]["text"] == "the end"


def test_an_unreadable_turn_is_a_visible_gap(hive_ws):
    from core import hive_records as hr
    e = hr.seal_turn(hive_ws, SID, None)
    body = json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text())
    assert body == {"rows": [], "error": "rows unreadable"}


def _inbox(rows):
    from tests.test_hive_lessons import _inbox as put
    put(rows)


def _pitfall(title, scope="project:P"):
    from tests.test_hive_lessons import _pitfall as p
    return p(title, scope)


def test_lessons_are_sealed_once_each_and_closed_follows(hive_ws):
    from core import hive_records as hr
    _inbox([("S7", SID, "main", _pitfall("one")), ("S7", SID, "main", _pitfall("two"))])
    hr.seal_end(hive_ws, SID, "reviewed")
    es = hr.entries(hive_ws, SID)["entries"]
    assert [e["kind"] for e in es] == ["lessons", "closed"]
    first = json.loads((hr.session_dir(hive_ws, SID) / es[0]["file"]).read_text())
    assert sorted(x["unit"]["title"] for x in first["lessons"]) == ["one", "two"]
    assert "id" not in first["lessons"][0]                          # the Hive's inbox ids stay there
    assert hr.seal_lessons(hive_ws, SID) is None                    # nothing new
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute(
        "INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
        "content_hash,envelope,project_snapshot,created_at,unit_kind,source_v2,extraction_pass,"
        "evidence_root_ids,dedup_key) VALUES('c9','k9','S7',?,'session',?,'x:v2','h9','{}','{}',"
        "'2026-10-29T00:01:00+00:00','cognition','S7','main','[]','d9')",
        (SID, json.dumps(_pitfall("three"))))
    conn.commit()
    conn.close()
    hr.seal_end(hive_ws, SID, "reviewed")                           # a later review of a resumed session
    es = hr.entries(hive_ws, SID)["entries"]
    assert [e["kind"] for e in es] == ["lessons", "closed", "lessons", "closed"]
    third = json.loads((hr.session_dir(hive_ws, SID) / es[2]["file"]).read_text())
    assert [x["unit"]["title"] for x in third["lessons"]] == ["three"]
    hr.seal_end(hive_ws, SID, "deleted")
    assert hr.entries(hive_ws, SID)["entries"][-1]["kind"] == "closed"


def test_every_end_of_a_session_job_and_a_deletion_close_the_record(hive_ws, monkeypatch):
    from core import hive_records as hr
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    hr.on_session_job_end(SID, "queued")                           # retried: not an end
    assert hr.entries(hive_ws, SID)["entries"] == [] if hr.session_dir(hive_ws, SID).exists() else True
    hr.on_session_job_end(SID, "failed")
    hr.on_session_job_end(SID, "done")
    hr.on_session_deleted(SID)
    reasons = [json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text())["reason"]
               for e in hr.entries(hive_ws, SID)["entries"] if e["kind"] == "closed"]
    assert reasons == ["review_failed", "reviewed", "deleted"]
    monkeypatch.delenv("SWARMAI_MODE")
    hr.on_session_job_end(SID, "done")                              # not a Hive: nothing
    assert len(hr.entries(hive_ws, SID)["entries"]) == 3


def test_the_session_job_executor_closes_the_record(hive_ws, monkeypatch):
    from core import hive_records as hr
    from core.ingestion import s2_jobs
    from core.ingestion.store import connect_rw
    from core.ingestion.worker import S2JobExecutor
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr("core.ingestion.worker.ingestion_admission",
                        lambda lane, **kw: SimpleNamespace(run=True, llm=True))
    s2_jobs.request(SID, "session_end")
    conn = connect_rw()
    try:
        assert S2JobExecutor(extract=lambda sid: None).drain(conn) == 1
    finally:
        conn.close()
    assert [e["kind"] for e in hr.entries(hive_ws, SID)["entries"]] == ["closed"]


def test_a_deleted_session_closes_its_record(hive_ws, monkeypatch):
    from core import hive_records as hr
    from core.ingestion.sources.s2_session import SessionExtractionHook
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    asyncio.run(SessionExtractionHook().execute(SimpleNamespace(deleting=True, session_id=SID)))
    assert [e["kind"] for e in hr.entries(hive_ws, SID)["entries"]] == ["closed"]


def test_a_landing_seals_its_runs_lessons_for_the_creator_session(hive_ws, monkeypatch, tmp_path):
    from core import hive_records as hr
    from core.run_protocol import reflect_job
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    from tests.test_hive_lessons import _contract
    _contract(tmp_path / "hive-data", "run_landed", SID)
    _inbox([("S3", "run_landed", "main", _pitfall("from the run"))])
    reflect_job._seal_run_lessons(SimpleNamespace(run_id="run_landed", contract={"session_id": SID}))
    es = hr.entries(hive_ws, SID)["entries"]
    assert [e["kind"] for e in es] == ["lessons"]
    reflect_job._seal_run_lessons(SimpleNamespace(run_id="x", contract={}))      # never raises


def test_the_sweep_removes_old_records_and_stale_temporary_files(hive_ws):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    old = "sess_old_record_1"
    hr.append(hive_ws, old, "closed", data=b"{}")
    stale = hr.tmp_path(hive_ws, SID, "2" * 16)
    stale.write_bytes(b"x")
    fresh = hr.tmp_path(hive_ws, SID, "3" * 16)
    fresh.write_bytes(b"x")
    now = time.time()
    for p in hr.session_dir(hive_ws, old).iterdir():
        os.utime(p, (now - 31 * 86400, now - 31 * 86400))
    os.utime(stale, (now - 2 * 86400, now - 2 * 86400))
    assert hr.sweep(hive_ws, now) == {"removed": 1, "tmp": 1}
    assert not hr.session_dir(hive_ws, old).exists() and not stale.exists() and fresh.exists()
    from jobs.handlers.records_sweep import run_records_sweep
    import core.project_registry as pr
    pr_get = pr.get_swarmws
    try:
        pr.get_swarmws = lambda: hive_ws
        assert run_records_sweep()["status"] == "success"
    finally:
        pr.get_swarmws = pr_get


def test_the_records_sweep_is_a_worker_job():
    from jobs import scheduler
    from jobs.system_jobs import SYSTEM_JOB_IDS
    assert "records-sweep" in scheduler.WORKER_SYSTEM_JOBS and "records-sweep" in SYSTEM_JOB_IDS


def test_bundles_are_sealed_after_the_thread_and_a_failure_is_said(hive_ws):
    from core import hive_bundle, hive_records as hr
    tmp = hr.tmp_path(hive_ws, SID, "4" * 16)
    tmp.write_bytes(b"commits")
    facts = hive_bundle.seal_bundles(hive_ws, SID, [{"bundle": {"key": "4" * 16, "tmp": str(tmp)}},
                                                    {"bundle": {"key": "5" * 16, "tmp": str(tmp)}},
                                                    {"dir": "x"}])
    assert facts[0]["bundle"] == {"key": "4" * 16, "sha256": hashlib.sha256(b"commits").hexdigest(),
                                  "size": 7, "seq": 1}
    assert facts[1]["bundle"] == {"key": "5" * 16, "error": hive_bundle.ERR_FAILED}   # its file was gone
    assert hr.latest_bundle(hive_ws, SID, "4" * 16).read_bytes() == b"commits"


# ── the turn's seal in the router ────────────────────────────────────────────

def test_the_router_seals_each_turn_in_a_task_of_its_own_and_only_on_a_hive(hive_ws, monkeypatch):
    from core import hive_records as hr
    from core.session_router import SessionRouter
    import core.project_registry as pr
    import database
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    rows = _turn_rows("done")

    class _Msgs:
        fail = False

        async def list_by_session_paginated(self, sid, limit=None):
            if self.fail:
                raise RuntimeError("db")
            return rows
    msgs = _Msgs()
    monkeypatch.setattr(database, "db", SimpleNamespace(messages=msgs))

    router = SimpleNamespace(_seal_turn_record=lambda *a: SessionRouter._seal_turn_record(router, *a))

    async def turn_end():
        SessionRouter._spawn_turn_seal(router, SID)             # does not wait
        from core import session_router as srm
        await asyncio.gather(*list(srm._SEAL_TASKS))
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    asyncio.run(turn_end())
    assert not hr.session_dir(hive_ws, SID).exists()
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    asyncio.run(turn_end())
    es = hr.entries(hive_ws, SID)["entries"]
    assert [e["kind"] for e in es] == ["turn"]
    body = json.loads((hr.session_dir(hive_ws, SID) / es[0]["file"]).read_text())
    assert body["first_turn"] and body["rows"][-1]["content"][0]["text"] == "done"
    msgs.fail = True
    asyncio.run(turn_end())
    last = hr.entries(hive_ws, SID)["entries"][-1]
    assert json.loads((hr.session_dir(hive_ws, SID) / last["file"]).read_text())["error"]


def _no_outcome(rows):
    return rows[:-1] + [{**rows[-1], "content": [b for b in rows[-1]["content"] if b["type"] != "turn_outcome"]}]


@pytest.mark.parametrize("missing", ["this", "previous"])
def test_a_turn_starts_after_the_last_sealed_row_whichever_outcome_is_missing(hive_ws, missing):
    from core import hive_records as hr
    one, two = _turn_rows("first", i="a"), _turn_rows("second", i="b")
    if missing == "this":
        two = _no_outcome(two)
    else:
        one = _no_outcome(one)
    hr.seal_turn(hive_ws, SID, one)
    hr.seal_turn(hive_ws, SID, one + two)
    es = hr.entries(hive_ws, SID)["entries"]
    bodies = [json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text()) for e in es]
    assert [[r["id"] for r in b["rows"]] for b in bodies] == [[r["id"] for r in one], [r["id"] for r in two]]
    assert bodies[0]["first_turn"] and not bodies[1]["first_turn"]


def test_rows_that_could_not_be_read_are_held_by_the_next_turn(hive_ws):
    from core import hive_records as hr
    one, two, three = (_turn_rows(t, i=i) for t, i in (("1", "a"), ("2", "b"), ("3", "c")))
    hr.seal_turn(hive_ws, SID, one)
    hr.seal_turn(hive_ws, SID, None)                               # turn 2: rows unreadable
    hr.seal_turn(hive_ws, SID, one + two + three)
    es = hr.entries(hive_ws, SID)["entries"]
    last = json.loads((hr.session_dir(hive_ws, SID) / es[-1]["file"]).read_text())
    assert [r["id"] for r in last["rows"]] == [r["id"] for r in two + three]


def test_a_turn_longer_than_the_rows_read_back_is_not_the_first(hive_ws, monkeypatch):
    from core import hive_records as hr
    monkeypatch.setattr(hr, "TURN_ROWS", 4)
    hr.seal_turn(hive_ws, SID, _turn_rows("a", i="a"))
    hr.seal_turn(hive_ws, SID, _turn_rows("b", i="b"))              # the marker's row not among them
    es = hr.entries(hive_ws, SID)["entries"]
    bodies = [json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text()) for e in es]
    assert [b["first_turn"] for b in bodies] == [False, False] and len(bodies[1]["rows"]) == 4


def test_a_later_turns_rows_are_not_this_turns():
    from core import hive_records as hr
    one = _turn_rows("first", i="a")
    for r in one:
        r["created_at"] = r["created_at"].rstrip("Z")              # naive local, as the table writes
    later = [{"id": "u9", "role": "user", "created_at": "2026-10-29T09:00:00", "content": "next"}]
    sliced, _ = hr.turn_slice(one + later, until="2026-10-29T05:00:00")
    assert sliced == one


def test_a_long_command_keeps_its_check():
    from core import hive_records as hr
    from core import swarm_out_results as sor
    cmd = "echo " + "a" * 3000 + " && pytest -q"
    rows = _turn_rows("done", cmd=cmd)
    proj = json.loads(hr.turn_projection(rows))["rows"]
    agent = {"brain": "B", "validations": [{"id": "tests", "command": "pytest -q"}]}
    assert sor.summarize(proj, False, agent)["checks"] == [{"id": "tests", "status": "ran"}]


# ── the Hive's endpoints ─────────────────────────────────────────────────────

def test_the_record_endpoints(hive_ws, monkeypatch):
    from fastapi import HTTPException
    from core import hive_records as hr
    from routers import hive_brains
    monkeypatch.setattr(hive_brains, "_workspace", lambda: hive_ws)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_brains.get_record(SID))
    assert e.value.status_code == 404                               # not a Hive
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_brains.get_record(SID))
    assert e.value.status_code == 404                               # no record yet
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_brains.get_record("bad"))
    assert e.value.status_code == 400
    hr.append(hive_ws, SID, "closed", data=b'{"reason": "reviewed"}')
    got = asyncio.run(hive_brains.get_record(SID, after=0))
    assert [x["seq"] for x in got["entries"]] == [1]
    resp = asyncio.run(hive_brains.get_record_entry(SID, 1))

    async def body():
        return b"".join([c async for c in resp.body_iterator])
    assert asyncio.run(body()) == b'{"reason": "reviewed"}'
    with pytest.raises(HTTPException):
        asyncio.run(hive_brains.get_record_entry(SID, 9))


# ── the host side ────────────────────────────────────────────────────────────

class _Hive:
    """A Hive serving its record from ``ws``, with ways to misbehave."""

    def __init__(self, ws: Path):
        self.ws = ws
        self.tamper: dict[int, bytes] = {}
        self.listing: list | None = None
        self.calls: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        from core import hive_records as hr
        self.calls.append(req.url.path)
        parts = req.url.path.split("/")
        if req.url.path.startswith("/api/hive/records/") and len(parts) == 5:
            d = hr.session_dir(self.ws, parts[4])
            if not (d / hr.MANIFEST).exists():
                return httpx.Response(404)
            body = hr.entries(self.ws, parts[4], int(req.url.params.get("after", 0)))
            if self.listing is not None:
                body = {"entries": self.listing, "more": False}
            return httpx.Response(200, json=body)
        if req.url.path.startswith("/api/hive/records/") and len(parts) == 6:
            seq = int(parts[5])
            if seq in self.tamper:
                return httpx.Response(200, content=self.tamper[seq])
            fd, size = hr.open_entry(self.ws, parts[4], seq)
            try:
                return httpx.Response(200, content=os.read(fd, size + 1))
            finally:
                os.close(fd)
        return httpx.Response(404)


@pytest.fixture
def host(tmp_path, monkeypatch, hive_ws):
    from core import swarm_out
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path / "host-state")
    h = _Hive(hive_ws)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(h.handler), base_url="http://h"))
    return h


ROW = {"dispatch_id": DID, "hive_id": "h", "session_id": SID, "state": "stopped", "state_reason": "idle",
       "project_agent": {"brain": "P", "mode": "m", "revision": "r" * 64}}


def test_the_host_takes_each_entry_once_and_keeps_it(host, hive_ws):
    from core import hive_records as hr
    first = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert first["supported"] is False                              # no record there yet
    hr.seal_turn(hive_ws, SID, _turn_rows("one"))
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["supported"] and got["seq"] == 1 and not got["closed"] and got["error"] is None
    hr.seal_end(hive_ws, SID, "reviewed")
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["seq"] == 2 and got["closed"]
    n = len(host.calls)
    again = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert again["seq"] == 2 and len(host.calls) == n + 1           # one listing, no file taken twice
    assert [e["seq"] for e in hr.local_entries(DID)] == [1, 2]
    rows, complete, partial = hr.turn_rows(DID)
    assert rows[-1]["content"][0]["text"] == "one" and complete and not partial


def test_an_entry_that_fails_its_check_is_not_kept_and_is_retried(host, hive_ws):
    from core import hive_records as hr
    hr.seal_turn(hive_ws, SID, _turn_rows("one"))
    hr.seal_turn(hive_ws, SID, _turn_rows("one") + _turn_rows("two", i="b"))
    real = hr.session_dir(hive_ws, SID) / hr.entries(hive_ws, SID)["entries"][1]["file"]
    forged = b'{"rows": [{"role": "assistant", "content": [{"type": "text", "text": "forged"}]}]}'
    host.tamper[2] = forged + b" " * (real.stat().st_size - len(forged))     # same size, other bytes
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["seq"] == 1 and got["error"] == "sha_mismatch"
    assert "forged" not in json.dumps(hr.turn_rows(DID))
    del host.tamper[2]
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["seq"] == 2 and got["error"] is None


@pytest.mark.parametrize("bad,why", [
    ({"seq": 3, "kind": "turn", "file": "x", "sha256": "0" * 64, "size": 1}, "out_of_order"),
    ({"seq": 2, "kind": "exe", "file": "x", "sha256": "0" * 64, "size": 1}, "unknown_kind"),
    ({"seq": 2, "kind": "turn", "file": "x", "sha256": "0" * 64, "size": 10 ** 9}, "too_large"),
    ({"seq": 2, "kind": "bundle", "file": "x", "sha256": "0" * 64, "size": 1, "key": "../x"}, "bad_entry"),
])
def test_a_listing_that_breaks_the_rules_stops_the_pull(host, hive_ws, bad, why):
    from core import hive_records as hr
    hr.seal_turn(hive_ws, SID, _turn_rows("one"))
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    host.listing = [bad]
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["error"] == why and got["seq"] == 1


def test_results_come_from_the_kept_record_and_the_checks_stop_once_closed(host, hive_ws, monkeypatch, tmp_path):
    from core import hive_records as hr
    from core import swarm_out, swarm_out_results as sor
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path / "results")
    (tmp_path / "results").mkdir()

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(swarm_out, "_instance", instance)
    hr.seal_turn(hive_ws, SID, _turn_rows("the answer"))
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["result"]["report"] == "the answer" and rec["fetch_error"] is None
    assert "/api/chat/sessions" not in " ".join(host.calls)         # not the old way
    assert rec["records"] == {"seq": 1, "closed": False, "last_at": rec["records"]["last_at"], "error": None}
    assert sor.check_due(ROW, "running", rec)                        # not closed: every poll
    hr.seal_end(hive_ws, SID, "reviewed")
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["records"]["closed"]
    assert not sor.check_due(ROW, "running", rec)                    # just checked
    rec["checked_at"] = "2020-01-01T00:00:00Z"
    assert sor.check_due(ROW, "running", rec)                        # resumed sessions are seen later
    rec["records"]["last_at"] = "2020-01-01T00:00:00Z"
    assert not sor.check_due(ROW, "running", rec)                    # 14 days with nothing new
    # the Hive gone quiet: the kept result stays, read from here
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
        base_url="http://h"))
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["result"]["report"] == "the answer" and rec["fetch_error"] == "hive_silent"


def test_landing_takes_the_bundle_from_the_kept_record(host, hive_ws, monkeypatch, tmp_path):
    from core import hive_records as hr, hive_return
    tmp = hr.tmp_path(hive_ws, SID, "6" * 16)
    tmp.write_bytes(b"the commits")
    e = hr.append(hive_ws, SID, "bundle", src=tmp, key="6" * 16)
    hr.seal_turn(hive_ws, SID, _turn_rows("x"))
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    n = len(host.calls)
    b = {"key": "6" * 16, "sha256": e["sha256"], "size": e["size"], "seq": e["seq"]}
    dest = tmp_path / "taken" / "b.bundle"
    asyncio.run(hive_return._download(ROW, b, dest))
    assert dest.read_bytes() == b"the commits" and len(host.calls) == n   # nothing asked of the Hive
    with pytest.raises(hive_return.ReturnError):
        asyncio.run(hive_return._download(ROW, {**b, "sha256": "f" * 64}, tmp_path / "taken" / "c.bundle"))


def test_a_turns_bundle_is_sealed_after_its_thread_and_a_late_one_never(hive_ws, monkeypatch):
    """I9: the turn's coroutine seals what the bundling thread returned; a result that
    comes back after the turn gave up on it is not sealed."""
    import time as _t
    from core import hive_bundle, hive_records as hr
    from core.session_router import SessionRouter
    import core.project_registry as pr
    import database
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")

    class _Sessions:
        async def get(self, sid):
            return {"metadata": {"task_repos": [{"url": "https://github.com/acme/lib.git", "ref": "main"}]},
                    "created_at": None}
    monkeypatch.setattr(database, "db", SimpleNamespace(sessions=_Sessions()))
    k = "7" * 16
    monkeypatch.setattr("core.task_repos.repo_facts", lambda *a: [{"dir": "x", "commits_total": 1}])

    def bundled(ws, sid, facts, delay=0.0):
        _t.sleep(delay)
        tmp = hr.tmp_path(ws, sid, k)
        tmp.write_bytes(b"c")
        return [{**facts[0], "bundle": {"key": k, "tmp": str(tmp)}}]

    class _Self:
        TASK_REPO_FACTS_TIMEOUT_S = 30
        TASK_REPO_BUNDLES_TIMEOUT_S = 5
    monkeypatch.setattr(hive_bundle, "add_bundles", bundled)
    facts = asyncio.run(SessionRouter._task_repo_facts(_Self(), SID))
    assert facts[0]["bundle"]["seq"] == 1 and hr.latest_bundle(hive_ws, SID, k).read_bytes() == b"c"
    _Self.TASK_REPO_BUNDLES_TIMEOUT_S = 0.1
    monkeypatch.setattr(hive_bundle, "add_bundles", lambda ws, sid, f: bundled(ws, sid, f, 0.5))
    facts = asyncio.run(SessionRouter._task_repo_facts(_Self(), SID))
    _t.sleep(0.8)                                                    # the thread finished meanwhile
    assert facts[0]["bundle"].get("error") and len(hr.entries(hive_ws, SID)["entries"]) == 1


# ── review round 1 ───────────────────────────────────────────────────────────

def test_a_torn_line_in_the_kept_manifest_is_cut_before_the_next_entry(host, hive_ws):
    from core import hive_records as hr
    hr.seal_turn(hive_ws, SID, _turn_rows("one"))
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    with open(hr.local_dir(DID) / hr.MANIFEST, "a") as f:
        f.write('{"seq": 2, "ki')                                  # a crash mid-append here
    hr.seal_turn(hive_ws, SID, _turn_rows("two"))
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["seq"] == 2 and [e["seq"] for e in hr.local_entries(DID)] == [1, 2]


def test_a_run_keeps_no_more_than_its_budget(host, hive_ws, monkeypatch):
    from core import hive_records as hr
    monkeypatch.setattr(hr, "MAX_KEPT_ENTRIES", 2)
    for i in range(4):
        hr.append(hive_ws, SID, "closed", data=b"{}")
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["seq"] == 2 and got["error"] == "record_too_large"
    monkeypatch.setattr(hr, "MAX_KEPT_ENTRIES", 100)
    monkeypatch.setattr(hr, "MAX_KEPT_BYTES", 3)
    assert asyncio.run(hr.pull(ROW, {"id": "h"}))["error"] == "record_too_large"


def test_a_listing_larger_than_its_cap_is_not_read(host, hive_ws, monkeypatch):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    monkeypatch.setattr(hr, "LISTING_MAX", 10)
    assert asyncio.run(hr.pull(ROW, {"id": "h"}))["supported"] is False   # nothing kept: the old way
    monkeypatch.setattr(hr, "LISTING_MAX", 256 * 1024)
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    hr.append(hive_ws, SID, "closed", data=b"{}")
    monkeypatch.setattr(hr, "LISTING_MAX", 10)
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["error"] == "unexpected_body" and got["seq"] == 1


def test_the_kept_time_is_this_machines_and_a_restarted_record_is_said(host, hive_ws):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    host.listing = None
    real = host.handler

    def lying(req):
        r = real(req)
        if req.url.path.endswith(SID) and r.status_code == 200:
            body = r.json()
            for e in body["entries"]:
                e["at"] = "2000-01-01T00:00:00Z"
            return httpx.Response(200, json=body)
        return r
    host.handler = lying
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert hr.local_entries(DID)[0]["at"] != "2000-01-01T00:00:00Z"
    import shutil
    shutil.rmtree(hr.session_dir(hive_ws, SID))                    # swept there, then a new turn
    hr.append(hive_ws, SID, "closed", data=b"{}")
    hr.append(hive_ws, SID, "closed", data=b"{}")
    assert asyncio.run(hr.pull(ROW, {"id": "h"}))["error"] is None  # last 2 > cursor 1: not known
    shutil.rmtree(hr.session_dir(hive_ws, SID))
    hr.append(hive_ws, SID, "closed", data=b"{}")
    assert asyncio.run(hr.pull(ROW, {"id": "h"}))["error"] == "record_gone"


def test_a_record_path_is_never_taken_from_the_manifest_or_through_a_link(hive_ws, tmp_path):
    from core import hive_records as hr
    secret = tmp_path / "secret"
    secret.write_text("private")
    hr.append(hive_ws, SID, "closed", data=b"{}")
    d = hr.session_dir(hive_ws, SID)
    with open(d / hr.MANIFEST, "a") as f:
        f.write(json.dumps({"seq": 2, "kind": "bundle", "key": "8" * 16, "file": str(secret),
                            "sha256": "0" * 64, "size": 7}) + "\n")
    assert hr.latest_bundle(hive_ws, SID, "8" * 16) == d / ("000002-bundle-" + "8" * 16 + ".bundle")
    with pytest.raises(FileNotFoundError):
        hr.open_entry(hive_ws, SID, 2)                              # no such file under its own name
    other = tmp_path / "elsewhere"
    other.mkdir()
    import shutil
    shutil.rmtree(hive_ws / "Hive" / "records")
    (hive_ws / "Hive" / "records").symlink_to(other)
    with pytest.raises(ValueError):
        hr.session_dir(hive_ws, SID)
    (other / SID).mkdir()
    old = time.time() - 40 * 86400
    os.utime(other / SID, (old, old))
    assert hr.sweep(hive_ws) == {"removed": 0, "tmp": 0} and (other / SID).exists()


def test_the_host_sweeps_its_kept_records(host, hive_ws):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    d = hr.local_dir(DID)
    now = time.time()
    assert hr.sweep_host(now) == {"removed": 0, "tmp": 0}
    for p in d.iterdir():
        os.utime(p, (now - 31 * 86400, now - 31 * 86400))
    assert hr.sweep_host(now) == {"removed": 1, "tmp": 0} and not d.exists()


def test_a_record_not_starting_at_the_first_turn_reads_its_result_the_old_way(host, hive_ws, monkeypatch, tmp_path):
    from core import hive_records as hr
    from core import swarm_out, swarm_out_results as sor
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path / "results")
    (tmp_path / "results").mkdir()

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(swarm_out, "_instance", instance)
    old_and_new = _turn_rows("before the upgrade", i="a") + _turn_rows("after", i="b")
    monkeypatch.setattr(hr, "TURN_ROWS", 4)                        # a session longer than what was read
    hr.seal_turn(hive_ws, SID, old_and_new[4:])                     # not first_turn
    real = host.handler

    def with_messages(req):
        if req.url.path.startswith("/api/chat/sessions/"):
            host.calls.append(req.url.path)
            return httpx.Response(200, json=old_and_new)
        return real(req)
    host.handler = with_messages
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["result"]["turns"] == 2 and rec["records"]["seq"] == 1
    assert any(c.startswith("/api/chat/sessions/") for c in host.calls)
    assert not any(c.startswith("/api/hive/lessons/") for c in host.calls)   # lessons: the record's


def test_the_records_path_queues_its_lessons(host, hive_ws, monkeypatch, tmp_path):
    """A run's record brings its lessons, and they go to this machine's CDF at once
    (design 2026-10-30-cdf-agent-decides D13), source S6."""
    from core import hive_records as hr
    from core import swarm_out, swarm_out_results as sor
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path / "results")
    (tmp_path / "results").mkdir()

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(swarm_out, "_instance", instance)
    hr.seal_turn(hive_ws, SID, _turn_rows("x"))
    hr.append(hive_ws, SID, "lessons", data=json.dumps({"lessons": [{"unit": _pitfall("rec")}]}).encode())
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["lessons"]["pending"] == 0 and len(rec["lessons"]["candidates"]) == 1
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        assert conn.execute("SELECT COUNT(*) FROM ingestion_inbox WHERE source_v2='S6'").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_job_a_restart_failed_closes_its_record(hive_ws, monkeypatch):
    from core import hive_records as hr
    from core.ingestion import s2_jobs
    from core.ingestion.store import connect_rw
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    s2_jobs.request(SID, "session_end")
    conn = connect_rw()
    conn.execute("UPDATE ingestion_s2_jobs SET state='running', attempts=? WHERE session_id=?",
                 (s2_jobs.MAX_ATTEMPTS, SID))
    conn.commit()
    conn.close()
    from core.ingestion.worker import S2JobExecutor
    ex = S2JobExecutor(extract=lambda sid: None)
    ex._stop.set()                                  # run the start-up part only
    ex._run()
    reasons = [json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text()).get("reason")
               for e in hr.entries(hive_ws, SID)["entries"]]
    assert reasons == ["review_failed"]


# ── through a real turn ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_fails", [False, True])
async def test_a_real_turn_on_a_hive_seals_its_own_rows(hive_ws, monkeypatch, outcome_fails):
    from unittest.mock import patch
    from core import hive_records as hr, session_router as srm
    from core.turn_writer import TurnWriter
    from tests.test_turn_outcome import _router, _send, _turn
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    sid = f"sess-rec-real-{int(outcome_fails)}"
    router = _router()
    done = [{"type": "result", "subtype": "success", "duration_ms": 9}]
    await _turn(router, sid, _send(done))                            # turn 1
    if outcome_fails:
        with patch.object(TurnWriter, "record_outcome", side_effect=OSError("disk")):
            await _turn(router, sid, _send(done), client_id=f"{sid}-2")   # turn 2: no outcome
    else:
        await _turn(router, sid, _send(done), client_id=f"{sid}-2")      # a new message id
    await asyncio.gather(*list(srm._SEAL_TASKS))
    es = [e for e in hr.entries(hive_ws, sid)["entries"] if e["kind"] == "turn"]
    assert len(es) == 2
    bodies = [json.loads((hr.session_dir(hive_ws, sid) / e["file"]).read_text()) for e in es]
    outcomes = [sum(1 for r in b["rows"] for x in r["content"] if x.get("type") == "turn_outcome") for b in bodies]
    users = [sum(1 for r in b["rows"] if r["role"] == "user") for b in bodies]
    assert bodies[0]["first_turn"] and not bodies[1]["first_turn"]
    assert outcomes == ([1, 0] if outcome_fails else [1, 1]) and users == [1, 1]   # never the turn before


def test_a_streamed_listing_larger_than_its_cap_is_not_read(host, hive_ws, monkeypatch):
    from core import hive_records as hr
    hr.append(hive_ws, SID, "closed", data=b"{}")
    asyncio.run(hr.pull(ROW, {"id": "h"}))

    async def endless():          # a valid listing: only the cap can refuse it
        yield b'{"entries": [], "last": 1, "pad": "'
        for _ in range(100):
            yield b"x" * 10_000
        yield b'"}'
    real = host.handler

    def streamed(req):
        if req.url.path == f"/api/hive/records/{SID}":
            return httpx.Response(200, content=endless())          # no Content-Length
        return real(req)
    host.handler = streamed
    monkeypatch.setattr(hr, "LISTING_MAX", 50_000)
    assert asyncio.run(hr.pull(ROW, {"id": "h"}))["error"] == "unexpected_body"


def test_a_bundle_is_taken_only_for_its_own_repository(host, hive_ws, tmp_path):
    from core import hive_records as hr, hive_return
    tmp = hr.tmp_path(hive_ws, SID, "9" * 16)
    tmp.write_bytes(b"repo B's commits")
    e = hr.append(hive_ws, SID, "bundle", src=tmp, key="9" * 16)
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    b = {"key": "a" * 16, "sha256": e["sha256"], "size": e["size"], "seq": e["seq"]}   # repo A's fact
    assert hive_return._from_record(DID, b, tmp_path / "x.bundle") is False


# ── review round 2 ───────────────────────────────────────────────────────────

def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_a_turn_without_a_new_commit_seals_no_second_bundle(hive_ws):
    from core import hive_bundle, hive_records as hr
    repo = hive_ws / "Hive" / "repos" / "x"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "a").write_text("a")
    _git(repo, "add", "a")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "b").write_text("b")
    _git(repo, "add", "b")
    _git(repo, "commit", "-qm", "work")

    def turn_end():
        fact = {"dir": "Hive/repos/x", "base": base, "head": _git(repo, "rev-parse", "HEAD"), "commits_total": 1}
        fact["bundle"] = hive_bundle.make(hive_ws, SID, fact)
        return hive_bundle.seal_bundles(hive_ws, SID, [fact])[0]["bundle"]
    first, again = turn_end(), turn_end()
    assert first["seq"] == 1 and again == first                     # the sealed one stands
    assert [e["kind"] for e in hr.entries(hive_ws, SID)["entries"]] == ["bundle"]
    (repo / "c").write_text("c")
    _git(repo, "add", "c")
    _git(repo, "commit", "-qm", "more")
    assert turn_end()["seq"] == 2                                   # the branch moved: a new bundle


def _turn_with(i, seqs, k):
    rows = _turn_rows(i, i=i)
    rows[-1]["content"][1]["repos"] = [{"dir": "x", "bundle": {"key": k, "seq": s}} for s in seqs]
    return rows


def test_the_host_keeps_only_the_bundles_the_newest_outcome_refers_to(host, hive_ws):
    from core import hive_records as hr
    k = "8" * 16

    def bundle(body):
        t = hr.tmp_path(hive_ws, SID, k)
        t.write_bytes(body)
        return hr.append(hive_ws, SID, "bundle", src=t, key=k)["seq"]
    rows: list = []
    b1 = bundle(b"one")                                           # seq 1
    rows += _turn_with("a", [b1], k)
    hr.seal_turn(hive_ws, SID, rows)                              # seq 2
    b3 = bundle(b"one+two")                                       # seq 3: superseded below
    b4 = bundle(b"one+two+three")                                 # seq 4
    rows += _turn_with("b", [b4], k)
    hr.seal_turn(hive_ws, SID, rows)                              # seq 5
    b6 = bundle(b"after the newest outcome")                      # seq 6: a later turn's
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    kept = {s: hr.bundle_path(DID, s, k) is not None for s in (b1, b3, b4, b6)}
    assert kept == {b1: False, b3: False, b4: True, b6: True}
    assert [e["seq"] for e in hr.local_entries(DID)] == [1, 2, 3, 4, 5, 6]     # the lines stay
    d = hr.local_dir(DID)
    on_disk = sum(p.stat().st_size for p in d.iterdir() if p.name[0].isdigit())
    assert hr._kept_bytes(d, hr.local_entries(DID)) == on_disk


def test_an_outcome_referring_to_an_older_bundle_keeps_it(host, hive_ws):
    from core import hive_records as hr
    k = "9" * 16
    seqs = []
    for body in (b"kept", b"moved, never referred to"):
        t = hr.tmp_path(hive_ws, SID, k)
        t.write_bytes(body)
        seqs.append(hr.append(hive_ws, SID, "bundle", src=t, key=k)["seq"])
    hr.seal_turn(hive_ws, SID, _turn_with("a", [seqs[0]], k))
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert hr.bundle_path(DID, seqs[0], k) is not None and hr.bundle_path(DID, seqs[1], k) is None


def test_a_download_stops_at_the_size_the_hive_listed(host, hive_ws):
    from core import hive_records as hr
    hr.seal_turn(hive_ws, SID, _turn_rows("one"))
    size = hr.entries(hive_ws, SID)["entries"][0]["size"]
    host.tamper[1] = b"x" * (size * 3)                            # more than it listed, under the kind's cap
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["error"] == "too_large" and not list(hr.local_dir(DID).glob(".tmp-*"))


def test_a_sweep_leaves_a_record_being_sealed(hive_ws):
    from core import hive_records as hr
    from utils.file_lock import flock_exclusive, flock_unlock
    hr.append(hive_ws, SID, "closed", data=b"{}")
    d = hr.session_dir(hive_ws, SID)
    now = time.time()
    with open(d / ".lock", "a+") as f:
        flock_exclusive(f)
        for p in d.iterdir():
            os.utime(p, (now - 31 * 86400, now - 31 * 86400))
        try:
            assert hr.sweep(hive_ws, now)["removed"] == 0 and d.exists()
        finally:
            flock_unlock(f)
    assert hr.sweep(hive_ws, now)["removed"] == 1 and not d.exists()


def test_a_sessions_turns_are_sealed_one_at_a_time_in_order(hive_ws, monkeypatch):
    """A seal leaving the lock while another waits on it must not drop the lock: a third
    seal would get a fresh one and run beside the waiting one."""
    from core import hive_records as hr, session_router as srm
    from core.session_router import SessionRouter
    import core.project_registry as pr
    import database
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    active, peak, order = [0], [0], []

    class _Msgs:
        async def list_by_session_paginated(self, sid, limit=None):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            await asyncio.sleep(0.05)
            active[0] -= 1
            return []
    monkeypatch.setattr(database, "db", SimpleNamespace(messages=_Msgs()))
    monkeypatch.setattr(hr, "seal_turn", lambda ws, sid, rows, until=None: order.append(until))
    router = SimpleNamespace(_seal_turn_record=lambda *a: SessionRouter._seal_turn_record(router, *a))

    async def go():
        a = asyncio.ensure_future(SessionRouter._seal_turn_record(router, SID, "1"))
        b = asyncio.ensure_future(SessionRouter._seal_turn_record(router, SID, "2"))
        third = []
        a.add_done_callback(lambda _t: third.append(
            asyncio.ensure_future(SessionRouter._seal_turn_record(router, SID, "3"))))
        await asyncio.gather(a, b)
        await asyncio.sleep(0)
        await asyncio.gather(*third)
    asyncio.run(go())
    assert order == ["1", "2", "3"] and peak[0] == 1
    assert not srm._SEAL_LOCKS                                      # dropped once nobody needs it


@pytest.mark.asyncio
async def test_a_cancel_in_the_writers_close_still_seals_the_turn(hive_ws, monkeypatch):
    from unittest.mock import patch
    from core import hive_records as hr, session_router as srm
    from core.turn_writer import TurnWriter
    from tests.test_turn_outcome import _router, _send, _turn
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    sid = "sess-rec-close-cancel"
    real = TurnWriter.close

    async def cancelled(self):
        await real(self)
        raise asyncio.CancelledError()
    with patch.object(TurnWriter, "close", cancelled):
        with pytest.raises(asyncio.CancelledError):
            await _turn(_router(), sid, _send([{"type": "result", "subtype": "success"}]))
    await asyncio.gather(*list(srm._SEAL_TASKS))
    assert [e["kind"] for e in hr.entries(hive_ws, sid)["entries"]] == ["turn"]


@pytest.mark.asyncio
async def test_a_turn_whose_outcome_write_failed_after_marking_it_is_still_its_own(hive_ws, monkeypatch):
    """``_outcome_recorded`` is set before the write; a write that then fails leaves a
    turn without an outcome, and the record must not hold the turn before it again."""
    from unittest.mock import patch
    from core import hive_records as hr, session_router as srm
    from database import db
    from tests.test_turn_outcome import _router, _send, _turn
    import core.project_registry as pr
    monkeypatch.setattr(pr, "get_swarmws", lambda: hive_ws)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    sid = "sess-rec-write-fails"
    router = _router()
    done = [{"type": "result", "subtype": "success"}]
    await _turn(router, sid, _send(done))
    with patch.object(db.messages, "append_to_turn_row", side_effect=OSError("disk")):
        await _turn(router, sid, _send(done), client_id=f"{sid}-2")
    await asyncio.gather(*list(srm._SEAL_TASKS))
    es = [e for e in hr.entries(hive_ws, sid)["entries"] if e["kind"] == "turn"]
    bodies = [json.loads((hr.session_dir(hive_ws, sid) / e["file"]).read_text()) for e in es]
    assert [sum(1 for r in b["rows"] if r["role"] == "user") for b in bodies] == [1, 1]


def test_a_turn_whose_marker_row_is_gone_is_not_the_sessions_first(hive_ws):
    """The row an earlier entry ended at was removed (a row never delivered is deleted):
    what is read is not the session's beginning, however few rows there are."""
    from core import hive_records as hr
    hr.seal_turn(hive_ws, SID, _turn_rows("first", i="a"))
    hr.seal_turn(hive_ws, SID, _turn_rows("second", i="b"))        # turn 1's rows are gone
    es = hr.entries(hive_ws, SID)["entries"]
    bodies = [json.loads((hr.session_dir(hive_ws, SID) / e["file"]).read_text()) for e in es]
    assert [b["first_turn"] for b in bodies] == [True, False]


def test_lessons_need_no_one_to_take_them():
    from core import hive_lessons, swarm_out_results as sor
    from routers import swarm_out as so_router
    assert not hasattr(hive_lessons, "accept") and not hasattr(sor, "accept_lessons")
    assert not hasattr(so_router, "lessons_accept")


# ── a question answered after its turn was sealed (design 2026-10-31-swarm-out-single-loop-fixes §2.5) ──

def _asked(i="q"):
    """A turn that stops on a question (the answer comes after the turn ended)."""
    return [
        {"id": f"u{i}", "role": "user", "created_at": "2026-10-29T01:00:00Z", "content": [{"type": "text", "text": "go"}]},
        {"id": f"a{i}", "role": "assistant", "created_at": "2026-10-29T01:00:01Z", "content": [
            {"type": "ask_user_question", "toolUseId": "toolu_q", "questions": [{"question": "A or B?"}]},
            {"type": "turn_outcome", "ended": "paused", "at": "2026-10-29T01:00:01+00:00"}]},
    ]


def _answered(rows):
    out = json.loads(json.dumps(rows))
    out[1]["content"][0]["answers"] = {"A or B?": "A"}
    out[1]["content"][0]["answered_by"] = "person"
    return out


def test_an_answer_given_after_its_turn_was_sealed_reaches_the_summary(host, hive_ws):
    from core import hive_records as hr
    from core import swarm_out_results as sor
    asked = _asked()
    hr.seal_turn(hive_ws, SID, asked)                         # the turn stops on the question
    # record_answer wrote the answer onto the stored row; the next turn is sealed after it
    hr.seal_turn(hive_ws, SID, _answered(asked) + _turn_rows("chose A", i="b"))
    second = json.loads((hr.session_dir(hive_ws, SID) / hr.entries(hive_ws, SID)["entries"][1]["file"]).read_text())
    assert second["answers"] == [{"toolUseId": "toolu_q", "answers": {"A or B?": "A"}, "answered_by": "person"}]
    assert "toolu_q" not in json.dumps(second["rows"])         # the question row is not sealed twice
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    rows, complete, _partial = hr.turn_rows(DID)
    (block,) = [b for r in rows for b in r["content"] if b.get("type") == "ask_user_question"]
    assert block["answers"] == {"A or B?": "A"} and block["answered_by"] == "person"
    human = sor.summarize(rows, False, None)["work"]["human"]
    assert (human["answered_questions"], human["unanswered"]) == (1, 0)


def test_a_later_answer_whose_question_is_not_read_adds_nothing(host, hive_ws, monkeypatch):
    from core import hive_records as hr
    from core import swarm_out_results as sor
    asked = _asked()
    hr.seal_turn(hive_ws, SID, asked)
    hr.seal_turn(hive_ws, SID, _answered(asked) + _turn_rows("chose A", i="b"))
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    monkeypatch.setattr(hr, "TURN_READ_BYTES", 1)              # only the newest entry is read
    rows, _complete, partial = hr.turn_rows(DID)
    assert partial and not [b for r in rows for b in r["content"] if b.get("type") == "ask_user_question"]
    human = sor.summarize(rows, True, None)["work"]["human"]
    assert (human["answered_questions"], human["unanswered"]) == (0, 0)


def test_a_too_large_turn_drops_earlier_answers_before_its_own_rows(monkeypatch):
    from core import hive_records as hr
    answers = [{"toolUseId": f"t{i}", "answers": {"q": "a" * 1500}, "answered_by": "person"} for i in range(40)]
    rows = _turn_rows("the end")
    monkeypatch.setitem(hr.MAX_BYTES, "turn", len(hr.turn_projection(rows)) + 100)
    got = json.loads(hr.turn_projection(rows, answers=answers))
    assert "answers" not in got and got["rows"][-1]["content"][0]["text"] == "the end"
    assert len(got["rows"]) == len(rows)                       # the turn's own rows stay
    assert len(hr.later_answers([{"content": [{"type": "ask_user_question", "toolUseId": f"t{i}",
                                                "answers": {"q": "a"}}]} for i in range(80)])) == hr.MAX_LATER_ANSWERS
