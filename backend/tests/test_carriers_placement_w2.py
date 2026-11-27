"""Placement rules beyond the lead's contract tests (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §4.4): reason words, the named / held /
blocked filters, D5's "out of date or unreachable is not full", total determinism, no I/O,
and what open_with_failover records after a failover or a refusal."""
import asyncio
import builtins
import itertools
import socket

import pytest

from core.carriers.base import (AGENTCORE, EC2, TIER_COLD, TIER_ELASTIC, TIER_WARM,
                                LocationUnavailable, Needs, Offer, RunHandle)
from core.carriers.placement import open_with_failover, place


def warm(name, open_runs=0, streaming=0, mem=10.0, in_flight=0, blocked=None, start_s=0.0):
    return Offer(EC2, f"id-{name}", name, TIER_WARM, open_runs=open_runs, in_flight=in_flight,
                 streaming=streaming, memory_percent=mem, blocked=blocked, start_s=start_s)


def cold(name, open_runs=0, blocked=None):
    return Offer(EC2, f"id-{name}", name, TIER_COLD, open_runs=open_runs, start_s=270.0, blocked=blocked)


def elastic(blocked=None, name="elastic"):
    return Offer(AGENTCORE, f"id-{name}", name, TIER_ELASTIC, start_s=60.0,
                 brain_lock_scope="run", blocked=blocked)


def _handle(o):
    return RunHandle(carrier=o.carrier, location_id=o.location_id, round=1, run_ref=None)


def test_reason_words_follow_the_card():
    assert place(Needs(), [warm("w1", open_runs=2), cold("c1")]).reason == "only_free"
    assert place(Needs(), [warm("w1"), warm("w2", open_runs=1)]).reason == "idle"
    p = place(Needs(), [warm("w1", open_runs=1, in_flight=1, streaming=1),
                        warm("w2", open_runs=1, in_flight=1, streaming=3)])
    assert p.offer.name == "w1" and p.reason == "least_busy:2"
    p = place(Needs(), [warm("w1", open_runs=1, streaming=4), warm("w2", open_runs=2)])
    assert p.offer.name == "w1" and p.reason == "least_busy:4"
    assert place(Needs(), [cold("c1")]).reason == "started"


def test_order_and_skipped_do_not_depend_on_input_order():
    offers = [warm("w1", open_runs=1), warm("w2", open_runs=1), elastic(), cold("c1"),
              warm("w3", blocked="saturated"), elastic(blocked="at_quota", name="elastic-b")]
    results = {place(Needs(), list(p)) for p in itertools.permutations(offers)}
    assert len(results) == 1
    (p,) = results
    assert [s[0] for s in p.skipped] == sorted(s[0] for s in p.skipped)
    assert ("c1", "not_needed") in p.skipped                    # a warm Hive has room


def test_named_by_id_in_any_tier_and_others_left_out():
    p = place(Needs(named="id-c1"), [warm("w1"), cold("c1")])
    assert p.offer.name == "c1" and p.layer == TIER_COLD and p.reason == "named"
    assert p.candidates == () and p.skipped == (("w1", "not_named"),)
    p = place(Needs(named="w1"), [warm("w1", blocked="out_of_date"), elastic()])
    assert p.offer is None
    assert ("w1", "out_of_date") in p.skipped and ("elastic", "not_named") in p.skipped


def test_held_is_by_machine_only_and_by_id_or_name():
    p = place(Needs(held=frozenset({"id-w1"})), [warm("w1"), warm("w2", open_runs=2)])
    assert p.offer.name == "w2" and ("w1", "held") in p.skipped
    p = place(Needs(held=frozenset({"elastic"})), [elastic()])
    assert p.offer.name == "elastic"                            # a run-scoped carrier holds no lock


def test_unreachable_running_hive_starts_no_stopped_one():
    p = place(Needs(), [warm("w1", blocked="saturated"), warm("w2", blocked="unreachable"), cold("c1")])
    assert p.offer is None and ("c1", "running_not_full") in p.skipped
    p = place(Needs(), [elastic(blocked="at_quota"), cold("c1")])
    assert p.offer.name == "c1" and p.reason == "started"       # D5: no running Hive, no elastic
    p = place(Needs(), [warm("w1", blocked="saturated"), elastic(blocked="throttled"), cold("c1")])
    assert p.offer.name == "c1"


def test_place_does_no_io(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("place did I/O")
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    p = place(Needs(held=frozenset({"w9"})), [warm("w1"), elastic(), cold("c1")])
    assert p.offer.name == "elastic"


def test_failover_records_where_the_run_was_opened():
    p = place(Needs(), [warm("w1"), warm("w2", open_runs=1), elastic(blocked="at_quota")])

    async def first_full(o):
        if o.name == "w1":
            raise LocationUnavailable("saturated")
        return _handle(o)
    handle, done = asyncio.run(open_with_failover(p, first_full, lambda: 400.0))
    assert handle.location_id == "id-w2"
    assert done.offer.name == "w2" and done.layer == TIER_WARM and done.reason == "least_busy:1"
    assert done.candidates == ()
    assert done.record()["location"] == "w2" and done.record()["tried"] == [["w1", "saturated"]]
    only_cold = place(Needs(), [cold("c1")])
    handle, same = asyncio.run(open_with_failover(only_cold, lambda o: _ok(o), lambda: 0.0))
    assert same == only_cold and handle.location_id == "id-c1"  # the chosen offer needs no time check


async def _ok(o):
    return _handle(o)


def test_refusal_carries_tried_and_passes_over_a_slow_candidate():
    slow = warm("w2", open_runs=1, start_s=500.0)
    p = place(Needs(), [warm("w1"), slow, elastic()])
    seen = []

    async def unavailable(o):
        seen.append(o.name)
        raise LocationUnavailable("busy")
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(open_with_failover(p, unavailable, lambda: 100.0))
    assert seen == ["elastic", "w1"]                            # w2 does not fit, w1 still does
    msg = str(e.value)
    assert "elastic: busy" in msg and "w1: busy" in msg and "w2: not tried" in msg
    assert e.value.placement.tried == (("elastic", "busy"), ("w1", "busy"))


def test_nothing_to_open_raises_with_every_skipped_reason():
    p = place(Needs(), [warm("w1", blocked="out_of_date"), elastic(blocked="at_quota")])
    called = []

    async def never(o):
        called.append(o)
        return _handle(o)
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(open_with_failover(p, never, lambda: 400.0))
    assert called == [] and "w1: out_of_date" in str(e.value) and "elastic: at_quota" in str(e.value)
