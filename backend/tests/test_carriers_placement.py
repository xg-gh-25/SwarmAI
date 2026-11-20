"""Placement contract tests (design docs/design/2026-11-20-swarm-out-multi-carrier.md §4.4;
requirements R1.1-R1.7 of run_fd94fed6). Written by the lead before the fan-out: they fail
until work package W2 implements core.carriers.placement."""
import asyncio

import pytest

from core.carriers.base import (AGENTCORE, EC2, TIER_COLD, TIER_ELASTIC, TIER_WARM,
                                LocationUnavailable, Needs, Offer, RunHandle)
from core.carriers.placement import open_with_failover, place


def warm(name, open_runs=0, streaming=0, mem=10.0, in_flight=0, blocked=None):
    return Offer(EC2, f"id-{name}", name, TIER_WARM, open_runs=open_runs, in_flight=in_flight,
                 streaming=streaming, memory_percent=mem, blocked=blocked)


def cold(name, open_runs=0, blocked=None):
    return Offer(EC2, f"id-{name}", name, TIER_COLD, open_runs=open_runs, start_s=270.0, blocked=blocked)


def elastic(blocked=None):
    return Offer(AGENTCORE, "id-elastic", "elastic", TIER_ELASTIC, start_s=60.0,
                 brain_lock_scope="run", blocked=blocked)


def test_place_is_deterministic_and_orders_tiers():
    offers = [cold("c1"), elastic(), warm("w2", open_runs=1), warm("w1")]
    a, b = place(Needs(), list(offers)), place(Needs(), list(reversed(offers)))
    assert a == b
    assert a.offer.name == "w1" and a.layer == TIER_WARM
    assert [o.name for o in a.candidates] == ["w2", "elastic"]          # cold not a candidate: warm has room
    no_warm = place(Needs(), [cold("c1"), elastic()])
    assert no_warm.offer.name == "elastic" and no_warm.reason == "elastic"


def test_within_tier_load_key_counts_in_flight():
    p = place(Needs(), [warm("a", in_flight=1), warm("b")])
    assert p.offer.name == "b"


def test_stopped_hive_only_when_running_saturated():
    sat = place(Needs(), [warm("w1", blocked="saturated"), cold("c1")])
    assert sat.offer.name == "c1" and sat.layer == TIER_COLD and sat.reason == "started"
    assert ("w1", "saturated") in sat.skipped
    old = place(Needs(), [warm("w1", blocked="out_of_date"), cold("c1")])
    assert old.offer is None                                            # D5: out of date is not full
    with_elastic = place(Needs(), [warm("w1", blocked="saturated"), elastic(), cold("c1")])
    assert with_elastic.offer.name == "elastic"
    assert [o.name for o in with_elastic.candidates] == ["c1"]
    only_cold = place(Needs(), [cold("c1", open_runs=1), cold("c0")])
    assert only_cold.offer.name == "c0"                                 # no Hive running: start one


def test_filters_private_named_and_held():
    p = place(Needs(private=("intranet",)), [warm("w1"), elastic()])
    assert p.offer is None and len(p.skipped) == 2
    p = place(Needs(named="w2"), [warm("w1"), warm("w2", open_runs=3)])
    assert p.offer.name == "w2" and p.reason == "named" and p.candidates == ()
    p = place(Needs(held=frozenset({"w1"})), [warm("w1"), elastic()])
    assert p.offer.name == "elastic"                                    # the lock is per machine, not per run


def test_nothing_fits_names_every_offer():
    p = place(Needs(), [warm("w1", blocked="out_of_date"), elastic(blocked="at_quota")])
    assert p.offer is None
    assert {n for n, _ in p.skipped} == {"w1", "elastic"}


def _handle(o):
    return RunHandle(carrier=o.carrier, location_id=o.location_id, round=1, run_ref=None)


def test_failover_only_before_write():
    p = place(Needs(), [warm("w1"), elastic()])
    seen = []

    async def unavailable_then_ok(o):
        seen.append(o.name)
        if o.name == "w1":
            raise LocationUnavailable("saturated")
        return _handle(o)
    handle, done = asyncio.run(open_with_failover(p, unavailable_then_ok, lambda: 400.0))
    assert handle.carrier == AGENTCORE and seen == ["w1", "elastic"]
    assert done.tried == (("w1", "saturated"),)

    async def write_failed(o):
        seen.append(o.name)
        raise RuntimeError("the Brain sync failed")
    seen.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(open_with_failover(p, write_failed, lambda: 400.0))
    assert seen == ["w1"]                                               # no failover after a write


def test_failover_respects_the_deadline_and_names_everyone():
    p = place(Needs(), [warm("w1"), elastic()])

    async def unavailable(o):
        raise LocationUnavailable(f"{o.name} busy")
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(open_with_failover(p, unavailable, lambda: 30.0))    # elastic needs 60 s
    assert "w1" in str(e.value)
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(open_with_failover(p, unavailable, lambda: 400.0))
    assert "w1" in str(e.value) and "elastic" in str(e.value)
