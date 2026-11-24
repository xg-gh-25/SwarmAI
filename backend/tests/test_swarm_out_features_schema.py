"""A task whose Brain has Feature Map walks goes only to a Hive that reads the repo's
features.yaml schema (core/swarm_out, core/carriers).

Questions: (1) is the need the highest schema a repo's feature folder with walks declares,
and none without walks; (2) does a Hive's admission state give its features schema (none
from an older Hive, read as schema 1) and its build; (3) is a running Hive below the need
blocked (EC2 and SSH), a stopped one not held back by it, and Elastic Workers blocked only when
the need is above this Host's own schema; (4) does opening a round on a Hive below the need
raise LocationUnavailable before anything is sent, without the Hive being remembered as
unable to take other tasks.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import swarm_out
from core.carriers import ec2, placement, ssh
from core.carriers.base import (EC2, FEATURES_SCHEMA, TIER_COLD, TIER_WARM, LocationUnavailable, Needs,
                                Offer)
from core.feature_map import files
from core.feature_map.files import SCHEMA_MAJOR

WALK = "  - {id: home, title: Home, target: web, spec: specs/home.spec.ts}\n"


def _folder(root, schema: int, walks: bool = True):
    f = root / ".swarmai" / "features"
    f.mkdir(parents=True)
    (f / "features.yaml").write_text(f"schema: {schema}\nfeatures:\n" + (WALK if walks else "  []\n"))
    return f


# ── (1) the need ──

def test_the_need_is_the_highest_schema_a_folder_with_walks_declares(tmp_path):
    ws = tmp_path / "ws"
    (ws / "Projects" / "B").mkdir(parents=True)
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    _folder(a, 1)
    _folder(b, 2)
    _folder(c, 2, walks=False)
    assert files.declared_schema(None, "a", a) == 1
    assert files.declared_schema(None, "c", c) is None         # no walks: nothing to replay
    assert files.declared_schema(None, "x", tmp_path / "none") is None
    spec = {"repos": [{"name": "a", "checkout": str(a)}, {"name": "b", "checkout": str(b)},
                      {"name": "c", "checkout": str(c)}, {"name": "d", "checkout": None}]}
    assert swarm_out._features_need(ws, "B", spec) == 2
    assert swarm_out._features_need(ws, "B", {"repos": spec["repos"][2:]}) is None
    # a folder kept in the Brain (features: brain) counts too
    brain = files.brain_folder(ws / "Projects" / "B", "d")
    brain.mkdir(parents=True)
    (brain / "features.yaml").write_text("schema: 3\nfeatures:\n" + WALK)
    assert swarm_out._features_need(ws, "B", spec) == 3


# ── (2) what a Hive says ──

def test_admission_gives_the_features_schema_and_the_build():
    base = {"streaming_count": 0, "saturated": False, "work_orders": True, "fixed_reserved": True,
            "zero_touch": True}
    old = ec2.parse_admission(base)
    assert old["features_schema"] is None and old["build_id"] is None
    from core import build_identity as bi
    ident = {"commit": "a" * 40, "digest": "b" * 64}
    ident["id"] = bi._tool().make_id(ident["commit"], ident["digest"])
    new = ec2.parse_admission({**base, "features_schema": 2, "build": ident})
    assert new["features_schema"] == 2 and new["build_id"] == ident["id"]
    forged = ec2.parse_admission({**base, "features_schema": True, "build": {**ident, "id": "f" * 16}})
    assert forged["features_schema"] is None and forged["build_id"] is None
    assert ssh.parse_admission({**base, "features_schema": 2})["features_schema"] == 2


# ── (3) placement ──

def _probe(schema):
    return {"streaming": 0, "saturated": False, "memory_percent": 10.0, "capable": True,
            "features_schema": schema, "build_id": None}


def test_a_running_hive_below_the_need_is_blocked_and_a_stopped_one_still_offered():
    need = Needs(features_schema=2)
    assert ec2.running_blocked(_probe(1), need) == FEATURES_SCHEMA
    assert ec2.running_blocked(_probe(None), need) == FEATURES_SCHEMA         # an older Hive: 1
    assert ec2.running_blocked(_probe(2), need) is None
    assert ec2.running_blocked(_probe(1), Needs()) is None                    # no walks: no need
    cfg = {"general_tasks": True}
    assert ssh.blocked(_probe(1), cfg, need) == FEATURES_SCHEMA
    assert ssh.blocked(_probe(2), cfg, need) is None

    old = Offer(EC2, "h1", "old", TIER_WARM, blocked=FEATURES_SCHEMA)
    current = Offer(EC2, "h2", "current", TIER_WARM)
    assert placement.place(need, [old, current]).offer.name == "current"
    # the only running Hive cannot read the repo's walks: a stopped Hive is started instead
    # (it is updated to the Host's build when it is behind, hive_idle.wake)
    stopped = Offer(EC2, "h3", "stopped", TIER_COLD, start_s=600.0)
    got = placement.place(need, [old, stopped])
    assert got.offer is not None and got.offer.name == "stopped"
    assert ("old", FEATURES_SCHEMA) in got.skipped
    # a schema not even this Host reads: no Hive started for it (it could not read it either)
    beyond = Needs(features_schema=SCHEMA_MAJOR + 1)
    got = placement.place(beyond, [old, stopped])
    assert got.offer is None and ("stopped", placement.RUNNING_NOT_FULL) in got.skipped


def test_elastic_workers_reads_the_hosts_schema_only_on_the_hosts_build(monkeypatch):
    from core.carriers import agentcore as ac
    monkeypatch.setattr(ac, "_host_version", lambda: "2.5.5-b0123456789abcdef")
    host_image = "2.5.5-b0123456789abcdef"
    assert ac.features_blocked(Needs(features_schema=SCHEMA_MAJOR), host_image) is None
    assert ac.features_blocked(Needs(), "2.5.5") is None
    assert ac.features_blocked(Needs(features_schema=SCHEMA_MAJOR + 1), host_image) == FEATURES_SCHEMA
    # on another tag (the version's own image, another build) what it reads is not known: 1
    assert ac.features_blocked(Needs(features_schema=2), "2.5.5") == FEATURES_SCHEMA
    assert ac.features_blocked(Needs(features_schema=1), "2.5.5") is None
    # another build of the Host's version is not refused as out of date; another version is
    assert not ac.image_behind("2.5.5", host_image) and not ac.image_behind("2.5.5-bffffffffffffffff", host_image)
    assert ac.image_behind("2.5.4-b0123456789abcdef", host_image)


# ── (4) the open ──

def _answer(monkeypatch, body: dict, seen: list):
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"saturated": False, "work_orders": True, "fixed_reserved": True,
                                         "zero_touch": True, **body})
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url="http://hive", transport=httpx.MockTransport(handler)))


def test_opening_a_round_on_a_hive_below_the_need_sends_nothing(monkeypatch):
    seen: list = []
    _answer(monkeypatch, {"features_schema": 1}, seen)
    with pytest.raises(LocationUnavailable) as exc:
        asyncio.run(swarm_out._require_work_orders({"name": "old"}, Needs(features_schema=2)))
    assert exc.value.why == FEATURES_SCHEMA and "features schema 2" in str(exc.value)
    assert seen == ["/api/chat/sessions/admission-state"]
    # without walks the same Hive takes the task; with a schema it reads, too
    assert asyncio.run(swarm_out._require_work_orders({"name": "old"}))["features_schema"] == 1
    _answer(monkeypatch, {"features_schema": 2}, seen)
    assert asyncio.run(swarm_out._require_work_orders({"name": "new"}, Needs(features_schema=2)))


def test_the_open_refusal_is_not_remembered_as_unable(monkeypatch):
    remembered: list = []
    monkeypatch.setattr(ec2, "_remember", lambda found: remembered.append(found))
    loc = ec2.Ec2Location("h1", "old")

    async def up(deadline, held=frozenset()):
        return {"id": "h1", "name": "old", "version": "2.5.5"}
    monkeypatch.setattr(loc, "up", up)
    seen_needs: list = []

    async def require(inst, needs=None):
        seen_needs.append(needs)
        raise swarm_out.HiveUnavailable("Hive old reads features schema 1; the task needs 2", FEATURES_SCHEMA)
    monkeypatch.setattr(swarm_out, "_require_work_orders", require)
    need = Needs(features_schema=2)
    with pytest.raises(LocationUnavailable):
        asyncio.run(loc.open_run("d1", 1, None, need))
    assert seen_needs == [need] and remembered == []
