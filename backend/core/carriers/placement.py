"""Deterministic placement over carriers' offers (design 2026-11-20-swarm-out-multi-carrier §4.4).

``place(needs, offers)`` is a pure function: no I/O, equal inputs give an equal Placement.
``open_with_failover`` tries the chosen offer and then the next candidates, moving on only
after ``LocationUnavailable`` (raised before anything was written to the location) and only
while the shared dispatch deadline leaves enough time for the next candidate's start.
"""
from __future__ import annotations

import dataclasses
from typing import Awaitable, Callable, Optional

from core.carriers.base import (TIER_COLD, TIER_ELASTIC, TIER_WARM, LocationUnavailable, Needs,
                                Offer, Placement, RunHandle)

_TIERS = (TIER_WARM, TIER_ELASTIC, TIER_COLD)

#: why a stopped EC2 Hive was left out although nothing filtered it (D5)
NOT_NEEDED = "not_needed"              # a running Hive with room can take the task
RUNNING_NOT_FULL = "running_not_full"  # a running Hive is unusable for a reason other than full


def _order(o: Offer) -> tuple:
    # load_key ends with the name; carrier and id break a tie between equal names so the
    # order never depends on the input order
    return (o.tier, o.load_key(), o.carrier, o.location_id)


def _busy(o: Offer) -> int:
    return max(o.open_runs + o.in_flight, o.streaming)


def _reason(o: Offer, usable_warm: int) -> str:
    """The card's word for choosing ``o`` among ``usable_warm`` warm offers."""
    if o.tier == TIER_ELASTIC:
        return "elastic"
    if o.tier == TIER_COLD:
        return "started"
    if usable_warm == 1:
        return "only_free"
    busy = _busy(o)
    return "idle" if busy == 0 else f"least_busy:{busy}"


def _filtered(needs: Needs, o: Offer) -> Optional[str]:
    """Why a hard condition leaves ``o`` out, or None (design §4.4 step 1)."""
    if needs.private and not o.reaches_private:
        return "private"
    if o.brain_lock_scope == "machine" and (o.location_id in needs.held or o.name in needs.held):
        return "held"
    if needs.named is not None and needs.named not in (o.name, o.location_id):
        return "not_named"
    if o.tier not in _TIERS:
        return "unknown_tier"
    if o.blocked:
        return str(o.blocked)
    return None


def place(needs: Needs, offers: list[Offer]) -> Placement:
    """Filter (private reach, Brain lock by machine, a named location, a blocked offer), then
    order by tier (warm EC2 with room, elastic, stopped EC2) and within a tier by
    ``Offer.load_key``. A stopped EC2 Hive is a candidate only when no running EC2 Hive can
    take the task and either none was offered or every blocked one was left out as
    saturated, not out of date or unreachable (D5); with an elastic offer it comes after it,
    as the failover when the elastic runtime is throttled. A named location is taken in
    whatever tier it is. ``skipped``: (name, reason) of every offer left out, by name."""
    skipped: list[tuple[str, str]] = []
    usable: list[Offer] = []
    for o in offers:
        why = _filtered(needs, o)
        if why is None:
            usable.append(o)
        else:
            skipped.append((o.name, why))
    usable.sort(key=_order)

    if needs.named is None:
        warm = [o for o in usable if o.tier == TIER_WARM]
        # D5 compares a stopped Hive with the running ones of its own carrier: another
        # carrier's warm location that is blocked (an SSH machine whose certificate expired)
        # says nothing about whether starting a stopped EC2 Hive helps (design
        # docs/design/2026-11-24-swarm-out-ssh-machine.md §4.4)
        cold_kinds = {o.carrier for o in usable if o.tier == TIER_COLD}
        running = [o for o in offers if o.tier == TIER_WARM and o.carrier in cold_kinds]
        blocked_running = [o.blocked for o in running if o.blocked]
        if warm:
            cold_why: Optional[str] = NOT_NEEDED
        elif running and any(b != "saturated" for b in blocked_running):
            cold_why = RUNNING_NOT_FULL
        else:
            cold_why = None
        if cold_why is not None:
            skipped.extend((o.name, cold_why) for o in usable if o.tier == TIER_COLD)
            usable = [o for o in usable if o.tier != TIER_COLD]
        usable_warm = len(warm)
    else:
        usable_warm = 1

    skipped.sort()
    if not usable:
        return Placement(offer=None, layer=None, reason="none", candidates=(), skipped=tuple(skipped))
    best = usable[0]
    reason = "named" if needs.named is not None else _reason(best, usable_warm)
    return Placement(offer=best, layer=best.tier, reason=reason, candidates=tuple(usable[1:]),
                     skipped=tuple(skipped))


def _unavailable(message: str, placement: Placement) -> LocationUnavailable:
    err = LocationUnavailable(message)
    err.placement = placement
    return err


async def open_with_failover(placement: Placement,
                             open_fn: Callable[[Offer], Awaitable[RunHandle]],
                             time_left: Callable[[], float]) -> tuple[RunHandle, Placement]:
    """Open the chosen offer; on LocationUnavailable try the next candidate whose
    ``start_s`` fits ``time_left()`` (a candidate that does not fit is passed over, a later
    one may still fit). Any other exception propagates at once: something may already have
    been written to that location (D6). Returns the handle and the placement with ``tried``
    filled ((name, str(error)) of each location that was unavailable) and, after a failover,
    ``offer`` / ``layer`` / ``reason`` / ``candidates`` describing the location the run was
    actually opened on. When every candidate was unavailable or out of time, raises
    LocationUnavailable whose message names each one with its reason; its ``placement``
    carries ``tried``."""
    if placement.offer is None:
        names = "; ".join(f"{n}: {why}" for n, why in placement.skipped) or "no location offered"
        raise _unavailable(f"no location can take the task ({names})", placement)
    order = (placement.offer, *placement.candidates)
    usable_warm = sum(1 for o in order if o.tier == TIER_WARM)
    tried: list[tuple[str, str]] = []
    passed: list[str] = []
    for i, o in enumerate(order):
        if i > 0:
            left = time_left()
            if left < o.start_s:
                passed.append(f"{o.name}: not tried, it needs {o.start_s:.0f}s to start "
                              f"and {max(left, 0.0):.0f}s were left")
                continue
        try:
            handle = await open_fn(o)
        except LocationUnavailable as e:
            tried.append((o.name, str(e)))
            continue
        if i == 0:
            done = dataclasses.replace(placement, tried=tuple(tried))
        else:
            done = dataclasses.replace(placement, offer=o, layer=o.tier,
                                       reason=_reason(o, usable_warm), candidates=order[i + 1:],
                                       tried=tuple(tried))
        return handle, done
    parts = [f"{n}: {why}" for n, why in tried] + passed
    raise _unavailable("no location could take the task (" + "; ".join(parts) + ")",
                       dataclasses.replace(placement, tried=tuple(tried)))
