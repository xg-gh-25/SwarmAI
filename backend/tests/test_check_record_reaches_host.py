"""A check the Hive ran through scripts/task_checks shows its recorded result on the
Host's run card, on both carriers.

The whole path, with no step replaced: on the Hive side the real ``task_checks run``
writes the record (``Hive/work/<work_id>/checks/checks.jsonl``) and the real producer
serves it: the EC2 Hive's ``GET /api/hive/work/<id>/checks`` (routers/hive_brains
``work_checks``), the AgentCore microVM's ``bridge/<sid>/checks.json``
(core/hive_agentcore ``_checks_body``). On the Host side the real ``fetch`` reads it
(core/swarm_out_results ``_take_check_records`` / core/carriers/agentcore ``outputs``)
and ``public`` gives the statuses the card shows. Producer and consumer were each tested
against their own idea of the bridge body before, so the AgentCore Host read a shape no
Hive writes and showed every check as ``not_seen``.

Questions: (1) on each carrier, is a check recorded with exit 0 ``ran``, one with another
exit ``errored``, and one the record does not hold ``not_seen``; (2) are the lines of
another work order in the same checks.json never taken as this dispatch's; (3) does a
dispatch sent without checks by id still read no record and infer its statuses.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest import mock

import httpx
import pytest
import yaml

from core import hive_agentcore, hive_workspace as hw, swarm_out as so, swarm_out_results as res
from core.carriers import agentcore as ac
from scripts import task_checks
from tests.test_elastic_dispatch_e2e import _close_round, _dispatch, world  # noqa: F401 — the fixture
from tests.test_swarm_out_results import INST, A, T, U, out
from tests.test_task_spec import DEF, ws  # noqa: F401 — the Brain fixture

OTHER = "so_" + "b" * 12           # another work order on the same Hive


def _hive_runs(hive_ws: Path, work_id: str, commands: dict[str, str], run: list[str], monkeypatch) -> None:
    """The Hive side: a work order with these checks by id (as core/hive_workspace keeps
    it), and the real ``task_checks run`` of each id in *run*."""
    rel = hw.repo_rel(work_id, "lib")
    (hive_ws / rel).mkdir(parents=True)
    order = {"work_id": work_id, "repos": [{"name": "lib", "dir": rel}],
             "checks": {"setup": [], "validations": [
                 {"id": i, "command": c, "repo": "lib", "cwd": ""} for i, c in commands.items()]}}
    path = hw._order_path(work_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(order), encoding="utf-8")
    monkeypatch.setattr(task_checks, "_workspace", lambda: hive_ws)
    for cid in run:
        code = task_checks.main(["run", work_id, cid])
        assert code == (0 if commands[cid] == "true" else 1)


def _card(rec: dict, row: dict) -> dict:
    """What the run card shows of the checks (``/runs``)."""
    pub = res.public(rec, agent=row["project_agent"])
    return {"checks": {c["id"]: c["status"] for c in pub["checks"]},
            "from_record": pub["checks_from_record"], "inferred": pub["checks_inferred"]}


# ── AgentCore: the round's checks.json in S3 ─────────────────────────────────

def _two_checks(world):  # noqa: F811 — the imported fixture
    """The Brain's code-change mode with two checks: ``tests`` and ``lint``."""
    doc = json.loads(json.dumps(DEF))
    for m in doc["modes"]:
        if m.get("id") == "code-change":
            m["validations"] = [{"id": "tests", "command": "pytest -q", "source": "ci.yml", "repo": "lib"},
                                {"id": "lint", "command": "ruff check", "source": "ci.yml", "repo": "lib"}]
    (world.ws / "Projects" / "Lib" / "project-agent.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))


@pytest.mark.parametrize("command,status", [("true", "ran"), ("exit 3", "errored")])
def test_agentcore_round_shows_the_checks_task_checks_recorded(world, monkeypatch, command, status):  # noqa: F811
    _two_checks(world)
    out_ = _dispatch(world)
    did = out_["dispatch_id"]
    sid = ac.session_id_for(did, 1)
    row = so._log_row_of(did)
    assert row["project_agent"]["checks_by_id"] is True
    assert [v["id"] for v in row["project_agent"]["validations"]] == ["tests", "lint"]
    # the round's microVM: its agent ran ``tests`` through task_checks, never ``lint``;
    # the bridge wrote checks.json as it does on each pass
    hive_ws = world.tmp / "hive-ws"
    _hive_runs(hive_ws, did, {"tests": command, "lint": "true"}, ["tests"], monkeypatch)
    world.s3.put(f"bridge/{sid}/checks.json", hive_agentcore._checks_body(hive_ws))
    _close_round(world, sid, "chat-round-1")
    rec = asyncio.run(res.fetch(row))
    assert rec["fetch_error"] is None
    assert [x["id"] for x in rec["check_records"]] == ["tests", "tests"]          # started, ended
    assert _card(rec, row) == {"checks": {"tests": status, "lint": "not_seen"},
                               "from_record": True, "inferred": False}


def test_agentcore_reads_only_the_dispatchs_own_order(world, monkeypatch):  # noqa: F811
    _two_checks(world)
    out_ = _dispatch(world)
    did = out_["dispatch_id"]
    sid = ac.session_id_for(did, 1)
    row = so._log_row_of(did)
    hive_ws = world.tmp / "hive-ws"
    # only another order ran its checks: nothing in checks.json is this dispatch's
    _hive_runs(hive_ws, OTHER, {"tests": "true", "lint": "exit 4"}, ["tests", "lint"], monkeypatch)
    world.s3.put(f"bridge/{sid}/checks.json", hive_agentcore._checks_body(hive_ws))
    _close_round(world, sid, "chat-round-1")
    rec = asyncio.run(res.fetch(row))
    assert rec["check_records"] is None
    assert _card(rec, row)["checks"] == {"tests": "not_seen", "lint": "not_seen"}
    # then this dispatch's own order ran ``lint``: only its line is read, whatever the other holds
    _hive_runs(hive_ws, did, {"tests": "true", "lint": "true"}, ["lint"], monkeypatch)
    body = json.loads(hive_agentcore._checks_body(hive_ws))
    assert {o["work_id"] for o in body["orders"]} == {did, OTHER}
    world.s3.put(f"bridge/{sid}/checks.json", body)
    rec = asyncio.run(res.fetch(row))
    assert _card(rec, row)["checks"] == {"tests": "not_seen", "lint": "ran"}
    assert ac._check_lines(body, OTHER) != ac._check_lines(body, did)
    assert ac._check_lines({"checks": [{"id": "lint", "exit": 0}]}, did) is None   # not the bridge's shape


# ── EC2: the Hive's work checks route ───────────────────────────────────────

DID = "so_" + "c" * 12
SID = "sess-ec2"


@pytest.fixture
def ec2(tmp_path, monkeypatch):
    """An EC2 Hive: its routes behind the Host's client, the Hive's own route function
    serving the check record from its workspace."""
    from routers import hive_brains
    hive_ws = tmp_path / "hive-ws"
    hive_ws.mkdir()
    monkeypatch.setattr(hive_brains, "_workspace", lambda: hive_ws)
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == f"/api/hive/work/{DID}/checks":
            from fastapi import HTTPException
            with mock.patch.dict(os.environ, {"SWARMAI_MODE": "hive"}):
                try:
                    return httpx.Response(200, json=await hive_brains.work_checks(DID))
                except HTTPException as e:
                    return httpx.Response(e.status_code, json={"detail": e.detail})
        if path == f"/api/chat/sessions/{SID}/messages":
            return httpx.Response(200, json=[U(), A(T("done"), out())], headers={"etag": '"e1"'})
        return httpx.Response(404, json={})

    async def fake_instance(ref):
        return so._validate_instance(INST, {"id": "a1"})

    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url=so._base(inst), headers=so._auth(inst),
                                 transport=httpx.MockTransport(handler))
    monkeypatch.setattr(so, "_instance", fake_instance)
    monkeypatch.setattr(so, "_client", client)
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)
    return hive_ws, seen


def _ec2_row(checks_by_id: bool = True) -> dict:
    return {"dispatch_id": DID, "hive_id": "h1", "session_id": SID, "state": "stopped", "state_reason": "idle",
            "project_agent": {"brain": "Lib", "mode": "code-change", "execution": "native", "returns": "commits",
                              "checks_by_id": checks_by_id,
                              "validations": [{"id": "tests", "command": "pytest -q"},
                                              {"id": "lint", "command": "ruff check"},
                                              {"id": "types", "command": "npx tsc --noEmit"}]}}


def test_ec2_hive_shows_the_checks_task_checks_recorded(ec2, monkeypatch):
    hive_ws, seen = ec2
    _hive_runs(hive_ws, DID, {"tests": "true", "lint": "exit 2", "types": "true"}, ["tests", "lint"], monkeypatch)
    row = _ec2_row()
    rec = asyncio.run(res.fetch(row))
    assert f"/api/hive/work/{DID}/checks" in seen and rec["fetch_error"] is None
    assert _card(rec, row) == {"checks": {"tests": "ran", "lint": "errored", "types": "not_seen"},
                               "from_record": True, "inferred": False}


def test_a_dispatch_without_checks_by_id_reads_no_record(ec2, monkeypatch):
    hive_ws, seen = ec2
    _hive_runs(hive_ws, DID, {"tests": "true", "lint": "true", "types": "true"}, ["tests"], monkeypatch)
    row = _ec2_row(checks_by_id=False)
    rec = asyncio.run(res.fetch(row))
    assert f"/api/hive/work/{DID}/checks" not in seen
    card = _card(rec, row)
    assert card["inferred"] is True and card["from_record"] is False
    assert card["checks"] == {"tests": "not_seen", "lint": "not_seen", "types": "not_seen"}
